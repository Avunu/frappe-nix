# NixOS VM test for the safe deploy-time migration (services.frappe.migrate.*).
#
# It deliberately STUBS `bench migrate` — Frappe's own migration correctness is
# out of scope. What is under test is the module's safety machinery in
# mkSiteMigrate: the uninstalled-site guard, pre-migrate snapshot,
# rollback-on-failure, maintenance-mode handling, and the build-guard marker —
# exercised against a real MariaDB.
#
# Run: nix build .#checks.x86_64-linux.migrate-rollback -L
{ self, pkgs }:
let
  siteName = "test.local";
  dbName = "test_local";
  dbPass = "testpass";
  dbPassFile = pkgs.writeText "frappe-test-dbpass" dbPass;

  jq = "${pkgs.jq}/bin/jq";
  mysql = "${pkgs.mariadb}/bin/mysql";

  # Stub `bench`: implements just the subcommands the units invoke.
  #   set-maintenance-mode on|off -> writes maintenance_mode into site_config.json
  #   migrate                     -> writes a public file, as after_migrate does
  #                                  when it regenerates the Website Theme CSS,
  #                                  then succeeds, unless /run/fail-migrate
  #                                  exists, in which case it simulates a PARTIAL
  #                                  failure (creates a table + inserts a row,
  #                                  then exits 1)
  #   schedule|worker             -> sleep (so the long-running units stay active)
  #   admin-probe [EXIT]          -> what an operator's `bench` from a root shell
  #                                  does: reads the site's DB credentials, writes
  #                                  a new file under the site (who ran it, the
  #                                  query result, stdin, whether stdin was a
  #                                  tty), then exits EXIT (default 0)
  # It connects to the DB exactly like Frappe does: over the unix socket.
  fakeBench = pkgs.writeShellScriptBin "bench" ''
    set -u
    SITE="''${FRAPPE_SITE:-}"
    rest=()
    while [ $# -gt 0 ]; do
      case "$1" in
        --site) SITE="$2"; shift 2 ;;
        *) rest+=("$1"); shift ;;
      esac
    done
    set -- ''${rest[@]+"''${rest[@]}"}
    CMD="''${1:-}"; shift 2>/dev/null || true
    CONFIG="''${SITES_PATH:-/var/lib/frappe/sites}/$SITE/site_config.json"

    case "$CMD" in
      set-maintenance-mode)
        state="''${1:-off}"; val=0; [ "$state" = "on" ] && val=1
        tmp="$(mktemp)"
        ${jq} --argjson v "$val" '.maintenance_mode = $v' "$CONFIG" > "$tmp"
        mv "$tmp" "$CONFIG"
        ;;
      migrate)
        DBNAME="$(${jq} -r '.db_name' "$CONFIG")"
        DBUSER="$(${jq} -r '.db_user' "$CONFIG")"
        SOCK="$(${jq} -r '.db_socket // "/run/mysqld/mysqld.sock"' "$CONFIG")"
        export MYSQL_PWD="$(${jq} -r '.db_password // empty' "$CONFIG")"
        THEME_DIR="$(dirname "$CONFIG")/public/files/website_theme"
        mkdir -p "$THEME_DIR"
        echo 'body {}' > "$THEME_DIR/theme_test.css"
        if [ -e /run/fail-migrate ]; then
          echo "fake bench: simulating a partial, failing migration" >&2
          ${mysql} --socket="$SOCK" -u"$DBUSER" "$DBNAME" \
            -e "CREATE TABLE IF NOT EXISTS partial (id INT); INSERT INTO canary (v) VALUES (2);"
          exit 1
        fi
        echo "fake bench: migrate OK"
        ;;
      admin-probe)
        DBNAME="$(${jq} -r '.db_name' "$CONFIG")"
        DBUSER="$(${jq} -r '.db_user' "$CONFIG")"
        SOCK="$(${jq} -r '.db_socket // "/run/mysqld/mysqld.sock"' "$CONFIG")"
        export MYSQL_PWD="$(${jq} -r '.db_password // empty' "$CONFIG")"
        OUT="$(dirname "$CONFIG")/private/files/admin-probe"
        mkdir -p "$OUT"
        if [ -t 0 ]; then tty=tty; else tty=notty; fi
        {
          echo "user=$(id -un)"
          echo "canary=$(${mysql} --socket="$SOCK" -u"$DBUSER" -N "$DBNAME" -e 'SELECT v FROM canary')"
          echo "tty=$tty"
          if [ "$tty" = notty ]; then echo "stdin=$(cat)"; fi
        } > "$OUT/$tty.txt"
        exit "''${1:-0}"
        ;;
      schedule|worker)
        exec sleep infinity
        ;;
      *)
        : # ignore any other subcommand in the stub
        ;;
    esac
  '';

  fakeGunicorn = pkgs.writeShellScriptBin "gunicorn" "exec sleep infinity";
  stubPyEnv = pkgs.buildEnv {
    name = "stub-frappe-pyenv";
    paths = [
      fakeBench
      fakeGunicorn
    ];
  };
  stubNode = pkgs.writeShellScriptBin "node" "exec sleep infinity";

  # Minimal stand-in for `builtBench`: just the on-disk layout frappe-init
  # expects plus the passthru the NixOS module reads.
  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          pythonEnv = stubPyEnv;
          nodejs = stubNode;
          appsPath = benchDir: "${benchDir}/apps";
          appNames = [ "frappe" ];
          extraPackages = [ ];
        };
      }
      ''
        mkdir -p $out/bench/apps $out/bench/env $out/bench/config $out/bench/sites
        echo '{}' > $out/bench/sites/common_site_config.json
        # The registry a real package carries (lib/bench.nix).
        printf 'frappe\n' > $out/bench/sites/apps.txt
        printf '{"frappe": {"idx": 1}}\n' > $out/bench/sites/apps.json
        : > $out/bench/config/.keep
      '';

  cfgPath = "/var/lib/frappe/${siteName}/sites/${siteName}/site_config.json";
  themeDir = "/var/lib/frappe/${siteName}/sites/${siteName}/public/files/website_theme";
  snapDir = "/var/lib/frappe/${siteName}/snapshots";
  markerPath = "/var/lib/frappe/${siteName}/.frappe-migrate-build";
  migrateUnit = "frappe-migrate-${siteName}.service";
  probeDir = "/var/lib/frappe/${siteName}/sites/${siteName}/private/files/admin-probe";
in
{
  name = "frappe-migrate-rollback";

  nodes.machine = { ... }: {
    imports = [ self.nixosModules.default ];
    virtualisation.memorySize = 2048;
    environment.systemPackages = [
      pkgs.jq
      pkgs.mariadb
    ];

    services.frappe = {
      enable = true;
      package = stubBench;
      sites."${siteName}" = {
        enable = true;
        database.createLocally = true;
        database.passwordFile = dbPassFile;
      };
    };
  };

  testScript = ''
    start_all()
    machine.wait_for_unit("multi-user.target")

    # First deploy against an EMPTY database: the site has never been installed,
    # so the migration is skipped rather than failing activation, and no build
    # marker is recorded (the deploy after the restore must still migrate).
    machine.wait_for_unit("${migrateUnit}")
    machine.succeed("journalctl -u ${migrateUnit} | grep -q 'the site is not installed'")
    # ...at warning priority, with the <4> prefix consumed rather than printed.
    machine.succeed("journalctl -u ${migrateUnit} -p warning | grep -q '^.*: frappe-migrate(.*the site is not installed'")
    machine.fail("test -e ${markerPath}")

    # Install the site -- canary stands in for the Frappe schema -- and
    # establish a known pre-migrate state S0: a single row v=1. With a schema
    # present the same build now migrates for real and records the marker.
    machine.succeed(
        "${mysql} ${dbName} -e 'CREATE TABLE canary (v INT); INSERT INTO canary VALUES (1);'"
    )
    machine.succeed("systemctl restart ${migrateUnit}")
    machine.succeed("test -e ${markerPath}")

    # The snapshot is a full dump: owner-only, in an owner-only directory. What
    # migrate itself writes is not: nginx serves public/files as a member of the
    # service group, so a theme written owner-only is a 403 on every page.
    assert machine.succeed("stat -c %a ${snapDir}").strip() == "700"
    assert machine.succeed("stat -c %a ${snapDir}/premigrate-*.sql.gz").strip() == "600"
    for path, need in [("${themeDir}", 0o050), ("${themeDir}/theme_test.css", 0o040)]:
        mode = int(machine.succeed(f"stat -c %a {path}").strip(), 8)
        assert mode & need == need, f"{path} is {mode:o}, which nginx cannot read through the group"

    # Force the next migration to fail (and mutate the DB); drop the marker so
    # the build guard does not short-circuit the re-run.
    machine.succeed("touch /run/fail-migrate")
    machine.succeed("rm -f ${markerPath}")
    machine.fail("systemctl restart ${migrateUnit}")

    # Rollback must have restored S0: the partial-migration table is gone and
    # canary is back to exactly one row with v=1.
    machine.succeed(
        "${mysql} -N ${dbName} -e "
        "\"SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema='${dbName}' AND table_name='partial'\" | grep -qx 0"
    )
    assert machine.succeed("${mysql} -N ${dbName} -e 'SELECT COUNT(*) FROM canary'").strip() == "1"
    assert machine.succeed("${mysql} -N ${dbName} -e 'SELECT v FROM canary'").strip() == "1"

    # Failure posture: unit failed, site left in maintenance mode, marker not advanced,
    # and the failure is loud in the journal.
    machine.succeed("systemctl is-failed ${migrateUnit}")
    assert machine.succeed("jq -r .maintenance_mode ${cfgPath}").strip() == "1"
    machine.fail("test -e ${markerPath}")
    machine.succeed("journalctl -u ${migrateUnit} | grep -q 'MIGRATION FAILED'")
    # Findable by priority, not only by wording: `journalctl -p err` is what a
    # log store's alert filters on.
    machine.succeed("journalctl -u ${migrateUnit} -p err | grep -q 'MIGRATION FAILED'")
    machine.succeed("journalctl -u ${migrateUnit} -p err | grep -q 'site left in maintenance mode'")

    # A clean redeploy path recovers: allow migrate to succeed again.
    machine.succeed("rm -f /run/fail-migrate")
    machine.succeed("systemctl restart ${migrateUnit}")
    machine.wait_for_unit("${migrateUnit}")
    machine.succeed("test -e ${markerPath}")
    assert machine.succeed("jq -r .maintenance_mode ${cfgPath}").strip() == "0"

    # The `bench` an operator runs from a root shell runs as the service user,
    # so what it writes under the site is the service's, not root's. Piped:
    # stdin reaches the command, and the site's DB credentials (in its 0600
    # site_config.json) work.
    machine.succeed("echo from-stdin | bench admin-probe")
    machine.succeed("stat -c %U:%G ${probeDir} ${probeDir}/notty.txt | sort -u | grep -qx frappe:frappe")
    probe = machine.succeed("cat ${probeDir}/notty.txt")
    for line in ("user=frappe", "canary=1", "tty=notty", "stdin=from-stdin"):
        assert line in probe.splitlines(), probe
    # The command's exit status is the wrapper's.
    status, _ = machine.execute("bench admin-probe 7 </dev/null")
    assert status == 7, f"bench exited {status}, not the command's 7"
    # On a terminal it gets one, so `bench console` stays interactive.
    machine.succeed("script -qec 'bench admin-probe' /dev/null")
    machine.succeed("stat -c %U ${probeDir}/tty.txt | grep -qx frappe")
    machine.succeed("grep -qx tty=tty ${probeDir}/tty.txt")
    # The service user itself runs it in place, with the same result.
    machine.succeed("rm -r ${probeDir}")
    machine.succeed("echo again | runuser -u frappe -- bench admin-probe")
    machine.succeed("grep -qx stdin=again ${probeDir}/notty.txt")
  '';
}
