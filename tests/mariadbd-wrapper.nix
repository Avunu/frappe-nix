# The mariadbd the dev shell runs — the real server, started through
# lib/mariadbd-wrapper.nix the way devenv starts it: `--defaults-file` first,
# then `--datadir` and `--basedir`, with MYSQL_HOME, MYSQL_UNIX_PORT and
# DEVENV_STATE in the environment.
#
# The wrapper adds an argument of its own (--tmpdir), and mariadbd only honours
# --defaults-file as its *first* one. A stub cannot catch getting that wrong —
# only the server can, and the failure is a dev shell whose database never
# starts ("unknown variable 'defaults-file=…'"). So: start it, and ask it.
{ pkgs }:

let
  mariadb = pkgs.mariadb;
  wrapped = import ../lib/mariadbd-wrapper.nix {
    inherit (pkgs) lib;
    inherit pkgs mariadb;
  };
in
{
  mariadbd-wrapper =
    pkgs.runCommand "frappe-nix-mariadbd-wrapper-check"
      {
        nativeBuildInputs = [
          pkgs.coreutils
          pkgs.procps
        ];
      }
      ''
        export HOME="$PWD"
        state="$PWD/state"
        export DEVENV_STATE="$state"
        export MYSQL_HOME="$state/mysql"
        export MYSQL_UNIX_PORT="$PWD/mysql.sock"
        mkdir -p "$MYSQL_HOME"

        # What devenv renders: a store config file, handed over by flag.
        cat > my.cnf <<CNF
        [mysqld]
        skip-networking
        socket=$MYSQL_UNIX_PORT
        innodb-buffer-pool-size=64M
        innodb-log-file-size=16M
        CNF

        ${mariadb}/bin/mariadb-install-db --defaults-file="$PWD/my.cnf" \
          --datadir="$MYSQL_HOME" --basedir=${mariadb} \
          --auth-root-authentication-method=normal > install.log 2>&1 \
          || { cat install.log; exit 1; }

        # devenv's own command line, through the wrapper.
        ${wrapped}/bin/mariadbd --defaults-file="$PWD/my.cnf" \
          --datadir="$MYSQL_HOME" --basedir=${mariadb} > server.log 2>&1 &
        pid=$!
        trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true' EXIT

        for _ in $(seq 1 60); do
          [ -S "$MYSQL_UNIX_PORT" ] && break
          kill -0 "$pid" 2>/dev/null || { echo "  ✗ mariadbd exited before it listened"; cat server.log; exit 1; }
          sleep 1
        done
        [ -S "$MYSQL_UNIX_PORT" ] || { echo "  ✗ mariadbd never listened"; cat server.log; exit 1; }
        echo "  ✓ starts through the wrapper with devenv's arguments"

        q() { ${mariadb}/bin/mariadb --defaults-file=/dev/null -S "$MYSQL_UNIX_PORT" -u root -N -B -e "$1"; }

        got="$(q 'SELECT @@tmpdir')"
        if [ "$got" = "$state/mysql-tmp" ]; then
          echo "  ✓ its temp files go under DEVENV_STATE, not /tmp"
        else
          echo "  ✗ tmpdir is '$got', expected '$state/mysql-tmp'"; exit 1
        fi
        [ -d "$state/mysql-tmp" ] && echo "  ✓ the directory is created for it" \
          || { echo "  ✗ $state/mysql-tmp was not created"; exit 1; }

        # The datadir flag still reaches the server: the wrapper only adds.
        [ "$(q 'SELECT @@datadir')" = "$MYSQL_HOME/" ] && echo "  ✓ devenv's own arguments still apply" \
          || { echo "  ✗ datadir changed"; exit 1; }

        echo "all checks passed" | tee "$out"
      '';
}
