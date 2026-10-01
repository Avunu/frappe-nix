# The mariadbd the dev shell runs: the stock server behind a wrapper that reaps
# an orphaned predecessor and keeps its temp files on disk. Its own file so
# tests/mariadbd-wrapper.nix can start the real thing through it.
{
  lib,
  pkgs,
  mariadb,
}:

let
  # A mariadbd orphaned by a previous `devenv up` — one that outlived
  # its process-compose and wedged — owns $MYSQL_UNIX_PORT and, in TCP
  # mode, the allocated port, so the next run can bind neither.
  # devenv's allocator does not rescue this: on an eval-cache replay it
  # hands the cached port straight back without probing it
  # (PortAllocator::allocate_exact under allow_in_use, which
  # reserve_running_ports sets whenever processes look live). So reap
  # the corpse rather than hope to be allocated around it.
  mariadbdReaper = pkgs.writeShellScript "mariadbd-reap" ''
    set -uo pipefail
    PATH=${
      lib.makeBinPath [
        pkgs.procps
        pkgs.coreutils
      ]
    }:$PATH

    datadir="''${MYSQL_HOME:-}"
    sock="''${MYSQL_UNIX_PORT:-}"

    if [ -n "$datadir" ]; then
      # Selected by --datadir, never by process name alone: a
      # system MariaDB and a neighbouring bench's server are both
      # mariadbd, and neither is ours to kill. Same for the uid.
      # $$ matches too — devenv invokes this wrapper *with* --datadir.
      for pid in $(pgrep -u "$(id -u)" -f -- "--datadir=$datadir" 2>/dev/null || true); do
        [ "$pid" = "$$" ] && continue
        [ "$pid" = "$PPID" ] && continue
        # pgrep -f matches a command line, so on its own it would also
        # match anything that merely mentions the datadir. Require the
        # process to actually be a server before signalling it.
        comm="$(ps -o comm= -p "$pid" 2>/dev/null || true)"
        case "''${comm##*/}" in
          mariadbd | mysqld) ;;
          *) continue ;;
        esac
        echo "frappe-nix: reaping orphaned mariadbd (pid $pid) on $datadir" >&2
        # SIGTERM is mariadbd's clean-shutdown signal and needs no
        # credentials, which `mariadb-admin shutdown` would — and that
        # would hang anyway against the wedged server this exists for.
        kill -TERM "$pid" 2>/dev/null || true
        for _ in $(seq 1 30); do
          kill -0 "$pid" 2>/dev/null || break
          sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
          echo "frappe-nix: pid $pid ignored SIGTERM, sending SIGKILL" >&2
          kill -KILL "$pid" 2>/dev/null || true
        fi
      done
    fi

    # Only after the reap: any live owner is now gone, and
    # $DEVENV_RUNTIME is per-project so nothing else can hold this
    # socket. mariadbd refuses to start while the file is still there.
    if [ -n "$sock" ] && [ -S "$sock" ]; then
      rm -f "$sock"
    fi
  '';

  # devenv builds processes.mysql.exec around ${cfg.package}/bin/mariadbd
  # and gives us a store path we cannot append to without
  # re-implementing its first-run logic (mariadb-install-db, the
  # timezone import), so the hook goes on the binary itself. That also
  # puts it on the one path process-compose re-runs on *restart* — the
  # same reason socketio's `rm -f` lives in `exec` and not in a task.
  #
  # symlinkJoin rather than an override because devenv passes
  # --basedir=${cfg.package} to mariadbd: share/ (charsets, errmsg.sys,
  # the plugin dir) has to travel with bin/.
  mariadbWrapped = pkgs.symlinkJoin {
    # Keep the upstream name stem. devenv chooses the mariadb-* over
    # the mysql-* client spellings with
    # `getName cfg.package == getName pkgs.mariadb`, and a renamed join
    # flips it to the Oracle names, none of which exist here.
    name = "${lib.getName mariadb}-${lib.getVersion mariadb}";
    paths = [ mariadb ];
    postBuild = ''
      rm -f "$out/bin/mariadbd"
      ln -s ${pkgs.writeShellScriptBin "mariadbd" ''
        ${mariadbdReaper}

        # Its temp files on disk, beside the datadir, and not in /tmp —
        # a tmpfs on most desktops, a few GB of RAM. A table rebuild
        # sorts into files there as large as the table: `ALTER TABLE
        # tabVersion` on a restored production copy (2+ GB) filled a
        # 3.9 GB /tmp and died with InnoDB error 168 ("no space left
        # on device") midway through `bench migrate`. `innodb_tmpdir`
        # does not cover those files; `tmpdir` does, and it cannot be
        # set on a running server, so it goes on the command line.
        # Set here and not in `settings`: the config file is a store
        # path, and this directory is per-project.
        if [ -n "''${DEVENV_STATE:-}" ]; then
          mkdir -p "$DEVENV_STATE/mysql-tmp"
          # Last, not first: --defaults-file is only honoured as the
          # very first argument, and devenv passes it.
          set -- "$@" "--tmpdir=$DEVENV_STATE/mysql-tmp"
        fi
        exec ${mariadb}/bin/mariadbd "$@"
      ''}/bin/mariadbd "$out/bin/mariadbd"
    '';
  };

in
mariadbWrapped
