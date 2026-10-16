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

  # Stub `bench new-site`, for the path docs/production/operations.md gives an
  # operator. It connects the way Frappe does (PyMySQL, over the db_socket in
  # site_config.json, with the settings of frappe.database.mariadb.database's
  # get_connection_settings), and follows the steps of frappe.installer's
  # _new_site that touch the database or the disk:
  #   * refuses an existing site directory without --force (frappe-init has
  #     always made one, so --force is always needed on this module);
  #   * keeps the module's site_config.json (make_site_config writes only a
  #     missing one);
  #   * makes the site's directories (make_site_dirs);
  #   * with --setup-db, the default: setup_database's root connection, as
  #     --db-root-username (else "root") with --db-root-password (else empty,
  #     as an operator pressing Enter at the prompt), then CREATE USER IF NOT
  #     EXISTS, drop and create the database, GRANT;
  #   * bootstrap_database as the site's own user from site_config.json, with
  #     a canary table standing in for framework_mariadb.sql.
  # It does not run Frappe's installer, so it proves the access and ownership
  # each step needs, not that Frappe's schema installs. Nor does it model
  # _new_site's rollback, which on a failure removes the site directory.
  newSiteStub =
    pkgs.writers.writePython3 "new-site-stub"
      {
        libraries = [ pkgs.python3Packages.pymysql ];
        flakeIgnore = [ "E501" ];
      }
      ''
        import argparse
        import json
        import os
        import sys

        import pymysql

        p = argparse.ArgumentParser()
        p.add_argument("site")
        p.add_argument("--force", action="store_true")
        p.add_argument("--setup-db", dest="setup_db", action="store_true", default=True)
        p.add_argument("--no-setup-db", dest="setup_db", action="store_false")
        p.add_argument("--db-root-username")
        p.add_argument("--db-root-password")
        p.add_argument("--admin-password")
        a = p.parse_args()

        site_path = os.path.join(os.environ["SITES_PATH"], a.site)
        if not a.force and os.path.exists(site_path):
            sys.exit(f"Site {a.site} already exists, use `--force` to proceed anyway")
        with open(os.path.join(site_path, "site_config.json")) as f:
            conf = json.load(f)
        for d in ("public/files", "private/backups", "private/files", "locks", "logs"):
            os.makedirs(os.path.join(site_path, d), exist_ok=True)


        def connect(user, password, database=None):
            kw = {"user": user, "charset": "utf8mb4", "unix_socket": conf["db_socket"]}
            if password:
                kw["password"] = password
            if database:
                kw["database"] = database
            return pymysql.connect(**kw)


        db, user = conf["db_name"], conf["db_user"]
        if a.setup_db:
            root = connect(a.db_root_username or "root", a.db_root_password or "")
            with root.cursor() as c:
                c.execute(f"CREATE USER IF NOT EXISTS '{user}'@'localhost' IDENTIFIED BY '{conf['db_password']}'")
                c.execute(f"DROP DATABASE IF EXISTS `{db}`")
                c.execute(f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                c.execute(f"GRANT ALL PRIVILEGES ON `{db}`.* TO '{user}'@'localhost'")
            root.close()

        site = connect(user, conf.get("db_password"), db)
        with site.cursor() as c:
            c.execute("CREATE TABLE canary (v INT)")
            c.execute("INSERT INTO canary VALUES (1)")
        site.commit()
        print(f"stub new-site: {a.site} installed")
      '';

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
      new-site)
        exec ${newSiteStub} "$@"
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
  siteDir = "/var/lib/frappe/${siteName}/sites/${siteName}";
  probeDir = "${siteDir}/private/files/admin-probe";
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

    # Install the site the way docs/production/operations.md says, from a root
    # shell. The canary table it creates stands in for the Frappe schema and is
    # the known pre-migrate state S0: a single row v=1.
    #
    # Frappe's default makes the database and its user over a MariaDB root
    # connection, and on this module's MariaDB root has no password, only
    # unix_socket auth, which admits OS root and nobody else. A root `bench`
    # used to get in that way; it now runs as frappe, which cannot. The module
    # has already made the database and its user (ensureDatabases,
    # ensureUsers, the password sync), so the root connection is not needed.
    root_connect = (
        "${pkgs.python3.withPackages (ps: [ ps.pymysql ])}/bin/python3 -c "
        "'import pymysql; pymysql.connect(unix_socket=\"/run/mysqld/mysqld.sock\", user=\"root\")'"
    )
    machine.succeed(root_connect)
    machine.fail(f"runuser -u frappe -- {root_connect}")
    # So the wrapper adds --no-setup-db for a site declared in services.frappe,
    # unless the caller passes a database flag of their own. Then it passes
    # the command through untouched, and here Frappe's root login is refused,
    # leaving nothing behind.
    status, out = machine.execute(
        "bench new-site ${siteName} --force --db-root-username root --admin-password admin </dev/null 2>&1"
    )
    assert status != 0, out
    assert "adding --no-setup-db" not in out, out
    assert "Access denied for user 'root'" in out, out
    machine.succeed("${mysql} -N ${dbName} -e 'SHOW TABLES' | wc -l | grep -qx 0")
    # Without --force it refuses: frappe-init has made the site directory. The
    # wrapper never adds --force.
    machine.fail("bench new-site ${siteName} --admin-password admin </dev/null")
    # The documented command, and the wrapper says what it added.
    out = machine.succeed("bench new-site ${siteName} --force --admin-password admin </dev/null 2>&1")
    assert "adding --no-setup-db" in out, out
    assert machine.succeed("${mysql} -N ${dbName} -e 'SELECT v FROM canary'").strip() == "1"
    # Everything under the site is the service user's, the new directories too.
    machine.succeed("test -d ${siteDir}/private/backups")
    machine.succeed("test -z \"$(find ${siteDir} ! -user frappe -o ! -group frappe)\"")

    # With a schema present the same build now migrates for real and records
    # the marker.
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
