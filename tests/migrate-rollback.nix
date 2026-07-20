# NixOS VM test for the safe deploy-time migration (services.frappe.migrate.*).
#
# It deliberately STUBS `bench migrate` — Frappe's own migration correctness is
# out of scope. What is under test is the module's safety machinery in
# mkSiteMigrate: pre-migrate snapshot, rollback-on-failure, maintenance-mode
# handling, and the build-guard marker — exercised against a real MariaDB.
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
  #   migrate                     -> succeeds, unless /run/fail-migrate exists, in
  #                                  which case it simulates a PARTIAL failure
  #                                  (creates a table + inserts a row, then exits 1)
  #   schedule|worker             -> sleep (so the long-running units stay active)
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
        if [ -e /run/fail-migrate ]; then
          echo "fake bench: simulating a partial, failing migration" >&2
          ${mysql} --socket="$SOCK" -u"$DBUSER" "$DBNAME" \
            -e "CREATE TABLE IF NOT EXISTS partial (id INT); INSERT INTO canary (v) VALUES (2);"
          exit 1
        fi
        echo "fake bench: migrate OK"
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
    paths = [ fakeBench fakeGunicorn ];
  };
  stubNode = pkgs.writeShellScriptBin "node" "exec sleep infinity";

  # Minimal stand-in for `builtBench`: just the on-disk layout frappe-init
  # expects plus the passthru the NixOS module reads.
  stubBench = pkgs.runCommand "stub-bench" {
    passthru = {
      pythonEnv = stubPyEnv;
      nodejs = stubNode;
      appsPath = benchDir: "${benchDir}/apps";
      appNames = [ "frappe" ];
      extraPackages = [ ];
    };
  } ''
    mkdir -p $out/bench/apps $out/bench/env $out/bench/config $out/bench/sites
    echo '{}' > $out/bench/sites/common_site_config.json
    : > $out/bench/config/.keep
  '';

  cfgPath = "/var/lib/frappe/${siteName}/sites/${siteName}/site_config.json";
  markerPath = "/var/lib/frappe/${siteName}/.frappe-migrate-build";
  migrateUnit = "frappe-migrate-${siteName}.service";
in
{
  name = "frappe-migrate-rollback";

  nodes.machine = { ... }: {
    imports = [ self.nixosModules.default ];
    virtualisation.memorySize = 2048;
    environment.systemPackages = [ pkgs.jq pkgs.mariadb ];

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

    # First deploy: the migration succeeds and the build marker is recorded.
    machine.wait_for_unit("${migrateUnit}")
    machine.succeed("test -e ${markerPath}")

    # Establish a known pre-migrate state S0: canary holds a single row v=1.
    machine.succeed(
        "${mysql} ${dbName} -e 'CREATE TABLE canary (v INT); INSERT INTO canary VALUES (1);'"
    )

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

    # A clean redeploy path recovers: allow migrate to succeed again.
    machine.succeed("rm -f /run/fail-migrate")
    machine.succeed("systemctl restart ${migrateUnit}")
    machine.wait_for_unit("${migrateUnit}")
    machine.succeed("test -e ${markerPath}")
    assert machine.succeed("jq -r .maintenance_mode ${cfgPath}").strip() == "0"
  '';
}
