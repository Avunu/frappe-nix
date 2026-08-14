# Reconciles a bench root's `patches.txt` with the patch list frappe-bench ships.
#
# `bench update` begins with `bench.patches.run()`, which imports and executes
# every entry in `<bench-package>/patches/patches.txt` that the bench root's own
# `patches.txt` does not already record as done. bench deleted the v3 and v4
# patch *modules* in 2022 (frappe/bench a84239d, "refactor: Bench — Drop patches
# of v3 & v4") but left their names in the shipped list, so a bench root without
# that record dies on the very first entry:
#
#     ModuleNotFoundError: No module named 'bench.patches.v3'
#
# and then stays dead: `run()`'s `finally` rewrites the root file from the
# still-empty executed list, so the bench root gains a one-byte `patches.txt` and
# every retry fails identically.
#
# A classic bench never hits this because `bench init` copies the shipped list
# into the bench root verbatim (`Bench.setup.patches`, bench/bench.py) — a bench
# created today is born with all ten marked done. frappe-nix builds its benches
# from Nix and never runs `bench init`, and `patches.txt` is gitignored, so the
# record is absent both in a fresh scaffold and in every fresh clone of an
# existing bench. Hence this, run from the dev shell's `enterShell`.
#
# Recording *all* of them — not just the v3/v4 entries that cannot import — is
# deliberate, and is what `bench init` does. Every surviving (v5) patch does
# something frappe-nix owns: `sudo bench setup supervisor` and `sudo bench setup
# sudoers`, the backup crontab, `live_reload` in `common_site_config.json` (a
# reconciled, committed file here), relocating `archived_sites/`. Under Nix they
# are not historical no-ops, they are wrong.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-bench-patches";
  runtimeInputs = with pkgs; [
    coreutils
    gnugrep
  ];
  text = ''
    if [ "$#" -lt 1 ]; then
      echo "usage: frappe-nix-bench-patches <shipped-patches.txt> [bench-root]" >&2
      exit 2
    fi

    shipped=$1
    root=''${2:-.}
    target="$root/patches.txt"

    # No frappe-bench in this environment: nothing here claims to run these
    # patches, so there is nothing to record.
    [ -f "$shipped" ] || exit 0

    missing=()
    # A bare `read -r line` with the default IFS trims leading and trailing
    # whitespace and assigns the rest of the line intact — bench's own strip().
    # The `|| [ -n "$line" ]` picks up a final line with no trailing newline.
    while read -r line || [ -n "$line" ]; do
      [ -n "$line" ] || continue
      # bench skips only lines that *start* with '#', then compares what is left
      # verbatim against the bench root file. The trailing "#2" on
      # `bench.patches.v4.install_yarn #2` is therefore part of the entry, not a
      # comment — record the line exactly as bench will compare it.
      case $line in '#'*) continue ;; esac
      if [ ! -f "$target" ] || ! grep -qxF -- "$line" "$target"; then
        missing+=("$line")
      fi
    done < "$shipped"

    if [ ''${#missing[@]} -eq 0 ]; then
      exit 0
    fi

    # Rewrite rather than append: a bench left behind by a failed `bench update`
    # holds a lone blank line, and appending to a file with no trailing newline
    # would splice two entries into one. Existing entries keep their order — a
    # migrated bench's record of what it really ran is the one thing here that
    # cannot be reconstructed.
    tmp=$(mktemp "$target.XXXXXX")
    if [ -f "$target" ]; then
      grep -v '^[[:space:]]*$' -- "$target" >> "$tmp" || true
    fi
    printf '%s\n' "''${missing[@]}" >> "$tmp"
    mv -- "$tmp" "$target"

    echo "patches.txt: recorded ''${#missing[@]} bench patch(es) as done (frappe-nix benches do not run them)"
  '';
}
