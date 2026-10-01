# Keeps the dev shell's MariaDB datadir off btrfs copy-on-write.
#
# InnoDB rewrites 16 KB pages in place, all day. On a copy-on-write filesystem
# no write lands in place: each one allocates a new extent, rewrites the
# checksum and extent trees above it, and — on a filesystem mounted with
# compression, which btrfs desktops usually are — compresses the page first.
# The table files fragment into hundreds of extents within days of a restore,
# and every fsync InnoDB issues commits a btrfs transaction that other
# programs' fsyncs then queue behind. `bench migrate`, `bench restore` and the
# test suite are mostly fsyncs.
#
# The standard remedy is the NOCOW attribute (`chattr +C`), which turns off
# copy-on-write, data checksums and compression for the files it covers. It is
# only honoured on a file that is still empty, so it is set on the *directory*
# before anything is written there and every file created inside inherits it.
#
# `prepare` does that on shell entry, for a datadir that does not exist yet or
# is still empty. A datadir that already holds a database cannot be fixed in
# place — the attribute does not reach existing data — so `prepare` only says
# so, and `migrate` does the copy, with the server stopped.
#
# Not btrfs: both subcommands are silent no-ops.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-db-nocow";
  runtimeInputs = with pkgs; [
    coreutils
    e2fsprogs # chattr, lsattr
    gnugrep
    procps # pgrep
  ];
  text = ''
    usage() {
      echo "usage: frappe-nix-db-nocow prepare <dir>..." >&2
      echo "       frappe-nix-db-nocow migrate <datadir>" >&2
      exit 2
    }

    on_btrfs() {
      [ "$(stat -f -c %T "$1" 2>/dev/null)" = btrfs ]
    }

    # lsattr prints the flags as one word ahead of the name; C is NOCOW.
    has_nocow() {
      lsattr -d -- "$1" 2>/dev/null | cut -d' ' -f1 | grep -q C
    }

    is_empty() {
      [ -z "$(ls -A -- "$1" 2>/dev/null)" ]
    }

    prepare() {
      local dir
      for dir in "$@"; do
        dir=''${dir%/}
        mkdir -p -- "$dir"
        on_btrfs "$dir" || continue
        has_nocow "$dir" && continue

        if is_empty "$dir"; then
          chattr +C -- "$dir"
          continue
        fi

        echo "⚠  $dir holds data on btrfs with copy-on-write on."
        echo "   Every database write is copied and compressed, which makes migrate,"
        echo "   restore and tests slow and stalls other programs' saves. To fix it,"
        echo "   stop \`devenv up\` and run:  frappe-nix-db-nocow migrate \"$dir\""
      done
    }

    migrate() {
      [ "$#" -eq 1 ] || usage
      local dir
      dir=$(realpath -- "$1")

      if [ ! -d "$dir" ]; then
        echo "frappe-nix-db-nocow: $dir is not a directory" >&2
        exit 1
      fi
      if ! on_btrfs "$dir"; then
        echo "$dir is not on btrfs; nothing to do."
        exit 0
      fi
      if has_nocow "$dir"; then
        echo "$dir already has copy-on-write off; nothing to do."
        exit 0
      fi

      # Same selection as the mariadbd reaper: by --datadir and uid, never by
      # process name, so a neighbouring bench's server does not block this.
      if pgrep -u "$(id -u)" -f -- "--datadir=$dir" >/dev/null 2>&1; then
        echo "frappe-nix-db-nocow: a server is running on $dir — stop \`devenv up\` first." >&2
        exit 1
      fi

      local need avail
      need=$(du -sb -- "$dir" | cut -f1)
      avail=$(df -B1 --output=avail -- "$dir" | tail -n 1 | tr -d ' ')
      # The copy and the original coexist until the swap at the end, and a
      # btrfs that fills completely is far harder to recover than a refusal.
      if [ "$avail" -lt $((need + need / 10 + 1073741824)) ]; then
        echo "frappe-nix-db-nocow: need about $((need / 1048576)) MiB free beside $dir, have $((avail / 1048576)) MiB." >&2
        exit 1
      fi

      # Not local: the EXIT trap below has to see it however the script ends.
      tmp="$dir.nocow-$$"
      backup="$dir.cow-backup-$(date +%Y%m%d-%H%M%S)"

      trap 'rm -rf -- "$tmp"' EXIT
      mkdir -- "$tmp"
      chattr +C -- "$tmp"

      echo "Copying $((need / 1048576)) MiB into a copy-on-write-free directory…"
      # --reflink=never: a reflink would share the original's copy-on-write
      # extents, which is the one thing this copy exists to get rid of (btrfs
      # refuses to clone between COW and NOCOW files anyway). cp does not carry
      # inode flags across, so every copied file takes +C from $tmp.
      cp -a --reflink=never -- "$dir/." "$tmp/"
      sync -f -- "$tmp"

      mv -- "$dir" "$backup"
      mv -- "$tmp" "$dir"
      trap - EXIT

      echo "Done. The original is kept at:"
      echo "  $backup"
      echo "Start \`devenv up\`, check the site works, then remove it:"
      echo "  rm -rf \"$backup\""
    }

    [ "$#" -ge 1 ] || usage
    cmd=$1
    shift
    case $cmd in
      prepare) prepare "$@" ;;
      migrate) migrate "$@" ;;
      *) usage ;;
    esac
  '';
}
