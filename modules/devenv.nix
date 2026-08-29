# devenv shell module for Frappe bench projects.
# Configures services, processes, environment, and scripts.
#
# `topLevel@` captures the flake-level module arguments so the perSystem body
# below can reach `topLevel.config.frappe-nix.secrets` — declared in
# ./secrets.nix, which has to be top-level because agenix-shell's own secret
# options are. The inner functions rebind `config` to the perSystem config, so
# the outer one needs a name of its own.
topLevel@{
  lib,
  flake-parts-lib,
  inputs,
  ...
}:
let
  inherit (lib) mkOption mkEnableOption types literalExpression;
  inherit (flake-parts-lib) mkPerSystemOption;

  # Per-bench port offset, 0..899, hashed from the bench name.
  #
  # NOT from the project path, though that is the obvious choice and is what
  # devenv itself hashes for DEVENV_RUNTIME. The web port has to be written into
  # sites/common_site_config.json — realtime/utils.js reads webserver_port
  # straight out of the JSON and no env var can reach it — and that file is
  # committed. A path-derived port would therefore differ in every clone and
  # dirty the worktree forever. `benchName` is committed in the bench's own
  # flake.nix, so every clone on every machine derives the same number.
  #
  # Two clones of one bench running at once is then devenv's port allocator's
  # problem, which is exactly what it is for.
  portOffsetFor =
    benchName:
    lib.mod (lib.fromHexString (builtins.substring 0 4 (builtins.hashString "sha256" benchName))) 900;
in
{
  options.perSystem = mkPerSystemOption (
    { config, pkgs, system, ... }:
    {
      options.frappe-nix = {
        enable = mkEnableOption "Frappe bench devenv shell";

        benchName = mkOption {
          type = types.str;
          description = "Project identifier used for environment names and container prefixes.";
          example = "pequea";
        };

        siteName = mkOption {
          type = types.str;
          default = "";
          description = "Default FRAPPE_SITE value. Empty string for multi-tenancy (user sets via .env).";
          example = "pequea.avu.nu";
        };

        workspaceRoot = mkOption {
          type = types.path;
          description = "Path to the bench workspace root (where pyproject.toml and apps/ live).";
        };

        python = mkOption {
          type = types.package;
          default = pkgs.python312;
          description = "Python interpreter package.";
        };

        nodejs = mkOption {
          type = types.package;
          default = pkgs.nodejs_22;
          description = "Node.js package for frontend builds and socketio.";
        };

        mariadb = {
          package = mkOption {
            type = types.package;
            default = pkgs.mariadb;
            description = "MariaDB package.";
          };

          initialDatabases = mkOption {
            type = types.listOf (types.attrsOf types.str);
            default = [ ];
            description = "List of databases to create on first start.";
            example = [
              { name = "mysite_db"; }
            ];
          };
        };

        sockets = {
          enable = mkOption {
            type = types.bool;
            default = true;
            description = ''
              Put every backing service this bench can on a unix socket, so
              several benches can run at once without fighting over ports.

              MariaDB, Redis, the realtime server and the web server all move to
              sockets under $DEVENV_RUNTIME, which devenv already gives each
              project uniquely. An nginx in front on a single TCP port routes
              /socket.io to the realtime socket and everything else to the web
              socket — the same shape services.frappe uses in production.

              That leaves one public TCP listener per bench, nginx's. MariaDB
              gets `skip-networking` and listens on nothing at all; FRAPPE_DB_HOST
              and FRAPPE_DB_PORT still name its allocated-but-unbound port, so an
              app that opens its own connection to conf.db_host:db_port fails
              against this bench rather than landing in a neighbouring bench's
              database. Mailpit's ports stay: it is Go and has no unix listener.

              Set false to keep every service on TCP; ports are still allocated
              dynamically, so benches still do not collide — they just use more
              ports and no nginx.

              Needs frappe >= 15.46 (or any v16), which is where the realtime
              server learned `socketio_uds` and the node redis client learned
              `unix://`.
            '';
          };
        };

        ports = {
          base = mkOption {
            type = types.nullOr types.port;
            default = null;
            description = ''
              First port this bench tries, overriding the value derived from
              `benchName`.

              By default the offset is a hash of `benchName`, so every bench
              lands somewhere different and every *clone* of one bench lands in
              the same place — which is what keeps the port out of
              sites/common_site_config.json's git diff. devenv's allocator walks
              forward from here if something is genuinely in the way, so this is
              a preference, not a reservation.

              Mailpit is offset from this by fixed amounts (see mailpitSmtpBase
              in modules/devenv.nix).
            '';
            example = 8200;
          };
        };

        pythonOverrides = mkOption {
          type = types.functionTo (types.functionTo types.attrs);
          default = _final: _prev: { };
          description = "Python package set overlay for system-library overrides.";
        };

        devguard = {
          enable = mkOption {
            type = types.bool;
            default = true;
            description = ''
              Guard rails that stop this development bench from reaching
              production services.

              A bench restored from a production backup carries working
              production credentials in its database and site_config.json.
              Left alone it will mail real customers, push its dev-mutated
              database over the production backup rotation, and delete
              production files out of an object store. Each guard closes one
              of those routes.

              The interception is grafted into the development virtualenv,
              below the Frappe app layer, so it needs no per-site app install
              and no site_config.json edits. Development only: it is never
              added to prodPythonEnv, the NixOS module, or the containers.

              Individual guards can be turned off for a single command with
              FRAPPE_DEVGUARD_DISABLE=mail,backups; FRAPPE_DEVGUARD_ENABLED=0
              disables all of them.
            '';
          };

          mail = {
            enable = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Route ALL outgoing mail from every site in this bench to
                Mailpit, and refuse incoming IMAP/POP3.

                The only guard with transport-level containment: it patches
                smtplib/imaplib/poplib, which know nothing about Frappe and so
                hold across upgrades and third-party apps.
              '';
            };

            host = mkOption {
              type = types.str;
              default = "127.0.0.1";
              description = "Interface Mailpit binds and Frappe is redirected to.";
            };

            smtpPort = mkOption {
              type = types.port;
              default = 19000 + portOffsetFor config.frappe-nix.benchName;
              defaultText = lib.literalMD "`19000` + a hash of `benchName`";
              description = ''
                Catcher SMTP port — drives both Mailpit and Frappe.

                Per-bench by default so several benches can catch mail at once,
                and kept clear of the 11000 range because that would cover
                11311, Frappe's own default redis_queue port.

                This is where devenv's allocator starts looking, so the running
                Mailpit may end up one or two higher; the resolved value is
                written to $DEVENV_RUNTIME/devguard-runtime.json, which
                frappe_devguard prefers over this baked-in one.
              '';
            };

            httpPort = mkOption {
              type = types.port;
              default = 20000 + portOffsetFor config.frappe-nix.benchName;
              defaultText = lib.literalMD "`20000` + a hash of `benchName`";
              description = "Mailpit web UI port. Per-bench by default; see smtpPort.";
            };

            sender = mkOption {
              type = types.str;
              default = "notifications@example.com";
              description = ''
                From address for the synthesised account used on sites that have
                no outgoing Email Account at all. Mail sent by code that names its
                own sender keeps that sender.
              '';
            };

            unmute = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Ignore `mute_emails` in site_config. A config restored from
                production often carries it, which would drop mail before it ever
                reached the catcher and read as "the mail guard is broken".
              '';
            };

            pop3 = {
              enable = mkOption {
                type = types.bool;
                default = false;
                description = ''
                  Serve incoming mail from Mailpit's POP3 listener instead of
                  blocking it, so the receive loop can be exercised end to end.

                  Off by default: Frappe's POP3 path issues DELE after fetching
                  and Mailpit honours it, so every message pulled into a site
                  disappears from the Mailpit UI.

                  With this disabled, IMAP/POP3 connections are refused outright —
                  which is the point, since a bench restored from production would
                  otherwise poll real mailboxes every 10 minutes, mark messages
                  seen, and fire auto-replies.
                '';
              };

              port = mkOption {
                type = types.port;
                default = 21000 + portOffsetFor config.frappe-nix.benchName;
                defaultText = lib.literalMD "`21000` + a hash of `benchName`";
                description = "Mailpit POP3 port. Per-bench by default; see smtpPort.";
              };

              user = mkOption {
                type = types.str;
                default = "dev";
                description = "POP3 username. Substituted for whatever the Email Account carries.";
              };

              password = mkOption {
                type = types.str;
                default = "dev";
                description = ''
                  POP3 password. Written to a world-readable file in the Nix
                  store, so treat it as a local development credential only.
                '';
              };
            };
          };

          backups = {
            enable = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Refuse to upload site backups to Dropbox, Amazon S3, Google
                Drive or Frappe Cloud.

                Local backups keep working: `bench backup`, `bench restore`,
                `trim-database`, `drop-site` and the desk Backups page are all
                untouched. Only egress is blocked — a bench restored from
                production would otherwise push its dev-mutated database, and
                the site_config.json inside it, over the production backup
                rotation.

                App-layer only: unlike the mail guard there is no
                transport-level containment behind this, so an unknown
                third-party uploader is not covered.
              '';
            };
          };

          objectstore = {
            enable = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Keep File writes and deletes on local disk instead of the
                configured S3-protocol object store.

                Forces the `cloud_storage` app's own `use_local` mode rather
                than blocking, so nothing breaks. Without it, a bench restored
                from production deletes real objects out of the production
                bucket — Frappe's own hourly `delete_old_exported_report_files`
                is enough to start that within an hour of `devenv up` — and
                overwrites others, since the keys carry no site prefix.

                Files inherited from the dump will 404 in dev, because their
                objects are not on local disk.
              '';
            };
          };

          integrations = {
            enable = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Refuse outbound HTTP made through
                `frappe.integrations.utils.make_request`, the funnel behind
                make_get_request and friends.

                Covers most gateways and third-party integrations in Frappe,
                ERPNext and the payments app — including
                `razorpay_settings.capture_payment`, which runs every minute
                with no enabled or sandbox check and can capture real money.
              '';
            };

            allowHosts = mkOption {
              type = types.listOf types.str;
              default = [ ];
              description = ''
                Hosts to permit anyway, for deliberate integration work.
                Loopback is always allowed.
              '';
              example = [ "api.sandbox.example.com" ];
            };
          };

          google = {
            enable = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Refuse Google API access (Calendar, Contacts, Drive) by
                blocking GoogleOAuth's service-object and token-refresh calls.

                Calendar and Contacts sync are not read-only: Frappe binds
                Event and Contact document events to write-back handlers, so a
                dev bench can create, mutate and delete real calendar entries.
              '';
            };
          };

          webhooks = {
            enable = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Drop outbound Webhook requests. Any scheduled job that saves a
                document fires the matching webhooks, and on a restored bench
                those point at production integrations.
              '';
            };
          };

          plaid = {
            enable = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Refuse Plaid bank synchronisation. Plaid uses its own SDK, so
                the integrations guard does not cover it.
              '';
            };
          };

          scheduler = {
            enable = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Refuse scheduled jobs that reach production services.

                The backstop for what the named guards do not know about: a
                third-party app's backup job, and Server Script scheduler
                events, which are arbitrary production Python carried in the
                database dump.
              '';
            };

            blockServerScripts = mkOption {
              type = types.bool;
              default = true;
              description = ''
                Skip Scheduled Job Types backed by a Server Script. Their
                method is a scrubbed script name rather than a dotted path, so
                blockedJobs cannot match them.
              '';
            };

            extraBlockedJobs = mkOption {
              type = types.listOf types.str;
              default = [ ];
              description = ''
                Additional `Scheduled Job Type.method` values to skip, matched
                exact-string and unioned with the built-in list.

                Never matched as a substring: `hourly_maintenance` also runs
                frappe.desk.page.backups.backups.delete_downloadable_backups,
                the purely local retention reaper.
              '';
              example = [ "myapp.tasks.push_backup_to_ftp" ];
            };
          };
        };

        extraDevPackages = mkOption {
          type = types.listOf types.package;
          default = [ ];
          description = "Additional packages for the dev shell.";
        };

        extraContainerRuntimeDeps = mkOption {
          type = types.listOf types.package;
          default = [ ];
          description = "Additional runtime packages for production containers.";
        };

        extraPackages = mkOption {
          type = types.listOf types.package;
          default = [ ];
          description = "Additional packages installed in both the dev shell and any production deployment of this package (via builtBench's passthru.extraPackages — see services.frappe.package in modules/nixos.nix).";
        };

        extraLibraryPaths = mkOption {
          type = types.listOf types.package;
          default = [ ];
          description = "Additional packages to add to LD_LIBRARY_PATH.";
        };

        restore = {
          enable = mkOption {
            type = types.bool;
            default = topLevel.config.frappe-nix.secrets.backupAccess.enable;
            defaultText = literalExpression "frappe-nix.secrets.backupAccess.enable";
            description = ''
              Let `bench restore` fetch the latest production backup when it is
              run with no file argument. Needs object-store credentials — by
              default, the bench's `backup-access` agenix secret.
            '';
          };

          prefix = mkOption {
            type = types.str;
            default = "";
            example = "Backups";
            description = ''
              Path inside the bucket that the backup folders sit under —
              Frappe's own `S3 Backup Settings.backup_path`.

              Normally left empty and carried in the `backup-access` secret as
              `BACKUPS_PREFIX` instead, alongside the bucket it belongs to:
              both are per-deployment facts that change together with the
              credentials, and keeping them together means a bench needs no Nix
              configuration to restore at all. `BACKUPS_PREFIX` wins over this.
            '';
          };

          withFiles = mkOption {
            type = types.enum [ "none" "private" "all" ];
            default = "none";
            description = ''
              Which file archives to pull down alongside the database.

              Off by default because they are routinely tens of gigabytes,
              while the database that makes a bench usable is a fraction of
              that. `bench restore --files` / `--private-files` override
              per-invocation.
            '';
          };

          carryConfigKeys = mkOption {
            type = types.listOf types.str;
            default = [ "encryption_key" "backup_encryption_key" ];
            description = ''
              Keys copied from the backup's own `site_config_backup.json` into
              the restored dev site.

              This is an allowlist, not a denylist: a denylist loses to the
              next app that invents `foo_api_secret`. The two defaults are the
              ones that make a restore usable rather than merely present —
              without `encryption_key` every stored password and API secret in
              the dump decrypts to nothing, and `backup_encryption_key` is what
              opens the next encrypted dump. Everything else production had
              (`host_name`, `db_*`, `mail_*`, `cloud_storage_settings`,
              `maintenance_mode`) is deliberately dropped.
            '';
          };

          migrate = mkOption {
            type = types.bool;
            default = true;
            description = "Run `bench migrate` after a successful restore.";
          };

          requireDevguard = mkOption {
            type = types.bool;
            default = true;
            description = ''
              Refuse to write production's `encryption_key` into a bench whose
              guard rails are switched off.

              Carrying that key is what makes a restore a real clone, and it is
              also what lets the bench decrypt every stored production
              credential in the dump. `devguard` is what keeps a bench holding
              those from mailing customers or deleting objects out of the
              production bucket — the README is explicit that it is a large
              reduction in blast radius, not an airgap. Failing closed here
              costs nothing and is the difference between a considered
              tradeoff and an accident.

              `bench restore --no-site-config` restores without the key
              instead; the site works, its stored credentials stay opaque.
            '';
          };
        };

        extraScripts = mkOption {
          type = types.attrsOf types.anything;
          default = { };
          description = "Additional devenv scripts to merge with the standard set.";
        };

        extraEnv = mkOption {
          type = types.attrsOf types.str;
          default = { };
          description = "Additional environment variables for the dev shell.";
        };

        nodeOverrides = mkOption {
          type = types.attrsOf types.attrs;
          default = { };
          description = ''
            Per-app attributes merged into the node_modules stdenv.mkDerivation
            (e.g. postPatch, extra nativeBuildInputs).
          '';
          example = {
            hrms = {
              postPatch = "rm -f frontend/yarn.lock";
            };
          };
        };

        nodeOfflineHashes = mkOption {
          type = types.attrsOf types.str;
          default = { };
          description = ''
            Per-app fetchYarnDeps offline-cache hashes, keyed by app name. The
            hash depends on the app's yarn.lock. To obtain one, leave it unset
            (defaults to lib.fakeHash), build benchRoot (or a container), and copy
            the reported `got: sha256-…` value here.
          '';
          example = {
            frappe = "sha256-AAAA…";
            erpnext = "sha256-BBBB…";
          };
        };

        containers = {
          enable = mkEnableOption "OCI container builds";
          registry = mkOption {
            type = types.str;
            default = "";
            description = "Container registry URL prefix.";
          };
        };
      };
    }
  );

  config = {
    perSystem =
      { config, pkgs, lib, system, ... }:
      let
        cfg = config.frappe-nix;

        overrides = import ../lib/overrides.nix;

        builtinOverrides = overrides.mysqlclient {
          inherit pkgs;
          mariadb = cfg.mariadb.package;
        };

        dg = cfg.devguard;
        mc = dg.mail;
        # Mailpit itself is only worth running when the mail guard will point
        # Frappe at it.
        mailEnabled = dg.enable && mc.enable;

        sockets = cfg.sockets.enable;

        portOffset = portOffsetFor cfg.benchName;

        # Every base below is where devenv's allocator starts looking, not a
        # reservation: it walks forward if something is genuinely in the way.
        webBase = if cfg.ports.base != null then cfg.ports.base else 8000 + portOffset;

        # Per-bench, for the same reason every other port here is: the allocator
        # that would move a second bench off a taken port only runs under
        # `devenv up`, so a shared base means two benches both *resolve* to 3306
        # and whichever answers first wins. See the mysqld settings below for
        # what connects over TCP at all when the socket is right there.
        dbBase = 3306 + portOffset;

        # These carry the per-bench offset through their option defaults, so a
        # consumer overriding devguard.mail.smtpPort still drives both Mailpit
        # and the address Frappe is redirected to, exactly as before.
        mailpitSmtpBase = mc.smtpPort;
        mailpitHttpBase = mc.httpPort;
        mailpitPop3Base = mc.pop3.port;

        # Render a Nix value as a Python literal, so the baked settings file
        # can mirror the option tree instead of being hand-spelled per key.
        toPy =
          value:
          if builtins.isBool value then
            (if value then "True" else "False")
          else if builtins.isInt value then
            toString value
          else if builtins.isString value then
            builtins.toJSON value
          else if builtins.isList value then
            "[" + lib.concatMapStringsSep ", " toPy value + "]"
          else if builtins.isAttrs value then
            "{"
            + lib.concatStringsSep ", " (
              lib.mapAttrsToList (name: inner: "${builtins.toJSON name}: ${toPy inner}") value
            )
            + "}"
          else
            throw "frappe-nix: cannot render ${builtins.typeOf value} as a Python literal";

        # Single source of truth: the same values reach Mailpit through
        # MAILPIT_* and Frappe through these baked defaults, so the two can
        # never drift. FRAPPE_DEVGUARD_* overrides them at runtime.
        devguardConfig = {
          enabled = dg.enable;
          guards = {
            mail = {
              enable = mc.enable;
              host = mc.host;
              port = mc.smtpPort;
              http_port = mc.httpPort;
              sender = mc.sender;
              unmute = mc.unmute;
              pop3_enabled = mc.pop3.enable;
              pop3_port = mc.pop3.port;
              pop3_user = mc.pop3.user;
              pop3_password = mc.pop3.password;
            };
            backups.enable = dg.backups.enable;
            objectstore.enable = dg.objectstore.enable;
            integrations = {
              enable = dg.integrations.enable;
              allow_hosts = dg.integrations.allowHosts;
            };
            google.enable = dg.google.enable;
            webhooks.enable = dg.webhooks.enable;
            plaid.enable = dg.plaid.enable;
            scheduler = {
              enable = dg.scheduler.enable;
              block_server_scripts = dg.scheduler.blockServerScripts;
              # Unioned with the built-in list, which lives in Python so the
              # two never drift.
              extra_blocked_jobs = dg.scheduler.extraBlockedJobs;
            };
          };
        };

        # Own content hash, so unrelated frappe-nix edits don't churn the
        # development virtualenv. Bytecode caches are filtered out so running
        # the tests in place can't change it either.
        devguardSrc = builtins.path {
          path = ../lib/devguard;
          name = "frappe-devguard-src";
          filter = path: _type: baseNameOf path != "__pycache__";
        };

        devguardBaked = pkgs.writeText "frappe-devguard-baked.py" ''
          # Generated by frappe-nix from `frappe-nix.devguard`. Every value is
          # overridden at runtime by the matching FRAPPE_DEVGUARD_* variable.
          BAKED = ${toPy devguardConfig}
        '';

        devguardPkg = pkgs.runCommand "frappe-devguard" { } ''
          mkdir -p "$out"
          cp -r ${devguardSrc}/frappe_devguard "$out/"
          chmod -R u+w "$out"
          cp ${devguardBaked} "$out/frappe_devguard/_baked.py"
        '';

        mailpitPop3Auth = pkgs.writeText "mailpit-pop3-auth" ''
          ${mc.pop3.user}:${mc.pop3.password}
        '';

        # Unlike devguard there is nothing to bake: every setting frappe_unixsock
        # reads is a runtime fact (the path of a socket some other process in
        # this bench is listening on), not a policy decision, so the environment
        # is the only source and there is nothing that could drift.
        unixsockPkg = pkgs.runCommand "frappe-unixsock" { } ''
          mkdir -p "$out"
          cp -r ${
            builtins.path {
              path = ../lib/unixsock;
              name = "frappe-unixsock-src";
              filter = path: _type: baseNameOf path != "__pycache__";
            }
          }/frappe_unixsock "$out/"
        '';

        pythonEnvs = import ../lib/python.nix {
          inherit pkgs lib;
          inherit (cfg) python workspaceRoot benchName;
          pyproject-nix = inputs.pyproject-nix;
          pyproject-build-systems = inputs.pyproject-build-systems;
          uv2nix = inputs.uv2nix;
          extraOverrides = lib.composeManyExtensions [
            builtinOverrides
            cfg.pythonOverrides
          ];
          devguard = if dg.enable then devguardPkg else null;
          # Both envs, and therefore builtBench, the containers and the NixOS
          # module. Independent of devguard.enable: turning the guards off must
          # not silently put the web server back on TCP.
          unixsock = unixsockPkg;
        };

        benchInfra = import ../lib/bench.nix {
          inherit pkgs lib;
          inherit (cfg) workspaceRoot nodejs nodeOverrides nodeOfflineHashes extraPackages;
          inherit (pythonEnvs) prodPythonEnv;
        };

        # Declared in modules/secrets.nix, which is top-level because
        # agenix-shell's own secret options are. Empty when secrets are off.
        secretsCfg = topLevel.config.frappe-nix.secrets;

        secretsTools = import ../lib/secrets-tools.nix {
          inherit lib pkgs;
          cfg = secretsCfg;
          schema = import ../lib/secrets-schema.nix { inherit lib; };
        };

        scripts = import ../lib/scripts.nix {
          inherit lib pkgs;
          inherit (benchInfra) appsWithNode;
          benchBin = "${pythonEnvs.devPythonEnv}/bin/bench";
          secrets = secretsTools;
          restore = cfg.restore // {
            fetch = "${backupFetch}/bin/frappe-nix-backup-fetch";
            # Sourced by bench-restore at the moment it needs the credentials,
            # not by enterShell — see lib/secrets-tools.nix for why.
            loadSecrets = lib.optionalString secretsTools.enabled (
              secretsTools.loadSecrets config.agenix-shell.installationScript
            );
            # Nix-time, not $FRAPPE_DEVGUARD_ENABLED: `devguard.enable = false`
            # is a persistent property of the bench, so every later `devenv up`
            # is unguarded too. A per-command disable is scoped to that command
            # and is nobody's problem.
            devguard = dg.enable;
          };
          nodeModulesBin = "${nodeModulesTool}/bin/frappe-nix-node-modules";
        };

        # The object-store half of `bench restore`, kept separate so shellcheck
        # sees it (devenv script bodies are never linted) and so the NixOS-side
        # restore can call the same discovery instead of copying it.
        backupFetch = import ../lib/backup-fetch.nix { inherit pkgs; };

        # Keeps `bench update` importable — see lib/bench-patches.nix for the
        # upstream hole it fills.
        benchPatchesTool = import ../lib/bench-patches.nix { inherit pkgs; };

        # Keeps `bench build` buildable — see lib/node-modules.nix for why the
        # install cannot simply be skipped once it has run.
        nodeModulesTool = import ../lib/node-modules.nix { inherit pkgs; };

        # `nix run .#relock` — the way out of a stale uv.lock.
        #
        # A stale lock fails at *evaluation*, so the dev shell that carries `uv`
        # is exactly what you cannot open; without this the fix needs a uv from
        # somewhere else entirely. nixpkgs' uv rather than the workspace's own
        # because the workspace's lives in the virtualenv that will not build.
        relockTool = pkgs.writeShellApplication {
          name = "frappe-nix-relock";
          runtimeInputs = [ pkgs.uv ];
          text = ''
            if [ ! -f pyproject.toml ] || [ ! -d apps ]; then
              echo "frappe-nix-relock: run this from the bench root (pyproject.toml + apps/)" >&2
              exit 1
            fi
            echo "Re-locking the Python workspace with $(uv --version)…"
            uv lock "$@"
            echo "✅ uv.lock updated — commit it, then re-enter the shell."
          '';
        };

        # The patch list frappe-bench ships. Resolved from the interpreter's own
        # sitePackages rather than globbed at runtime, so a python bump moves
        # this path loudly instead of silently turning the reconcile into a
        # no-op. Not `pathExists`-guarded: that would force the venv to build
        # during evaluation. The tool tolerates the file being absent, which is
        # what a bench whose pyproject.toml drops frappe-bench looks like.
        shippedBenchPatches = "${pythonEnvs.devPythonEnv}/${cfg.python.sitePackages}/bench/patches/patches.txt";

      in
      lib.mkIf cfg.enable {
        packages.prodPythonEnv = pythonEnvs.prodPythonEnv;
        packages.devPythonEnv = pythonEnvs.devPythonEnv;
        # Unbuilt /bench tree — used by containers.nix and as input to builtBench.
        packages.benchRoot = benchInfra.benchRoot;
        # Production-ready bench with compiled assets + passthru interpreters.
        packages.builtBench = benchInfra.builtBench;
        packages.default = benchInfra.builtBench;

        # Deliberately outside every other output's dependency graph: it has to
        # evaluate when nothing that touches the Python workspace can.
        apps.relock = {
          type = "app";
          program = "${relockTool}/bin/frappe-nix-relock";
        };

        devenv.shells.default =
          { config, pkgs, ... }:
          let
            # Every socket lives here, not in $DEVENV_STATE. DEVENV_RUNTIME is
            # ~27 bytes ($XDG_RUNTIME_DIR/devenv-<7 hex>, or /tmp/devenv-<7 hex>)
            # whereas $DEVENV_STATE is <project>/.devenv/state — which fits for a
            # shallow checkout and blows past the 108-byte sockaddr_un limit for
            # a deep one, as a bare "AF_UNIX path too long" from inside the
            # server. It is also created 0700, so the sockets are unreachable by
            # other users without any per-socket permission work.
            runtime = config.env.DEVENV_RUNTIME;

            # The port devenv allocated for mysqld, starting from dbBase, and
            # the same value it exports as MYSQL_TCP_PORT.
            #
            # Under sockets.enable nothing listens on it — skip-networking is
            # set — but it stays this bench's own number, and FRAPPE_DB_HOST/PORT
            # keep naming it precisely so a TCP client fails against *us* rather
            # than succeeding against a neighbour's 3306. See services.mysql.
            dbPort = config.processes.mysql.ports.main.value;

            mysqlSocket = "${runtime}/mysql.sock";
            redisSocket = "${runtime}/redis.sock";
            socketioSocket = "${runtime}/socketio.sock";
            webSocket = "${runtime}/web.sock";

            # Three slashes. `runtime` is absolute, so "unix://" + it gives
            # unix:///run/... — two would make node's
            # `connStr.replace("unix://","")` yield a *relative* path and
            # Python's urlparse yield a different wrong one, both silently.
            redisUrl = "unix://${redisSocket}";

            # The single public port: nginx when we are on sockets, otherwise
            # the web server itself. Read from the process that actually owns
            # the listener so the allocator's self-heal is followed.
            #
            # NB this is the *base* during a plain shell eval and the allocated
            # value only under `devenv up` — devenv enables the allocator for
            # `up` alone. Hence: never surface it in `env`, and let the up-task
            # (which does run with the allocator live) be the one writer.
            webPort =
              if sockets then
                config.processes.nginx.ports.main.value
              else
                config.processes.web.ports.main.value;

            redisCli =
              "${config.services.redis.package}/bin/redis-cli "
              + (if sockets then ''-s "${redisSocket}"'' else "-p ${toString config.services.redis.port}");

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
              PATH=${lib.makeBinPath [ pkgs.procps pkgs.coreutils ]}:$PATH

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
              name = "${lib.getName cfg.mariadb.package}-${lib.getVersion cfg.mariadb.package}";
              paths = [ cfg.mariadb.package ];
              postBuild = ''
                rm -f "$out/bin/mariadbd"
                ln -s ${
                  pkgs.writeShellScriptBin "mariadbd" ''
                    ${mariadbdReaper}
                    exec ${cfg.mariadb.package}/bin/mariadbd "$@"
                  ''
                }/bin/mariadbd "$out/bin/mariadbd"
              '';
            };

            # Keeps a client out of ~/.my.cnf and /etc/my.cnf, the way devenv's
            # own empty.cnf does for the probes it owns.
            emptyCnf = pkgs.writeText "empty.cnf" "";

            # Readiness for a process listening on a unix socket.
            #
            # devenv infers a TCP connectivity probe from a process's allocated
            # ports; a socket-only process has none, so `after = [ "…web" ]` —
            # which means @ready — has nothing to wait on and devenv refuses the
            # whole task graph. This gives it something.
            #
            # Deliberately not `curl -f`: any HTTP response means the server is
            # accepting connections, which is all a dependent needs to know. On a
            # bench whose site has not been created yet Frappe answers 500, and
            # requiring 2xx would keep nginx down until after `provision-site`.
            socketReady = socket: path: {
              exec = ''${pkgs.curl}/bin/curl -s -o /dev/null --max-time 4 --unix-socket "${socket}" http://localhost${path}'';
              initial_delay = 2;
              period = 5;
              probe_timeout = 5;
              # Generous: `bench serve` on a bench with a dozen apps spends a
              # while importing before it binds anything at all.
              failure_threshold = 60;
            };
          in
          {
            dotenv.enable = true;

            packages =
              with pkgs;
              [
                pythonEnvs.devPythonEnv

                # Build dependencies
                gcc
                pkg-config
                openssl
                zlib
                libffi

                # PDF/printing
                cups
                poppler-utils
                chromium
                wkhtmltopdf

                # Package managers
                uv

                # Dev tools
                curl
                file
                git
                gnused
                htop
                jq
                just
                pv
              ]
              ++ lib.optional mailEnabled pkgs.mailpit
              # ragenix + the recipient checker, once the bench declares secrets.
              ++ secretsTools.packages
              # The fetcher, plus gnupg for backups Frappe encrypted: it shells
              # out to gpg to decrypt those, and frappe-nix otherwise leaves
              # gnupg out of the closure (modules/nixos.nix's servicePath makes
              # the same call).
              ++ lib.optionals cfg.restore.enable [ backupFetch pkgs.gnupg ]
              ++ cfg.extraDevPackages
              ++ cfg.extraPackages;

            languages.javascript = {
              enable = true;
              package = cfg.nodejs;
              yarn = {
                enable = true;
                install.enable = false;
              };
            };

            env =
              {
                DEV_SERVER = "1";
                FRAPPE_ENV_TYPE = "development";
                FRAPPE_STREAM_LOGGING = "1";
                FRAPPE_TUNE_GC = "1";
                LIVE_RELOAD = "1";
                NO_SERVICE_RESTART = "1";

                USE_PROFILER = "";
                USE_PROXY = "";
                NO_STATICS = "";

                FRAPPE_DB_TYPE = "mariadb";

                # The database has been socket-only since long before the rest
                # of this — `bench new-site --db-socket` already wrote db_socket
                # into every site_config.json — so it needs no `sockets` guard.
                FRAPPE_DB_SOCKET = mysqlSocket;

                FRAPPE_BENCH_ROOT = config.devenv.root;
                SITES_PATH = config.devenv.root + "/sites";

                PYTHONPATH = benchInfra.appsPath config.devenv.root;
                REPO_ROOT = config.devenv.root;

                UV_PROJECT_ENVIRONMENT = config.env.DEVENV_STATE + "/uv-env";
                YARN_CACHE_FOLDER = config.env.DEVENV_STATE + "/yarn-cache";

                LD_LIBRARY_PATH = lib.makeLibraryPath (
                  [
                    pkgs.zlib
                    pkgs.openssl
                    pkgs.libffi
                    pkgs.file.out
                    pkgs.mariadb.client
                  ]
                  ++ cfg.extraLibraryPaths
                );
              }
              // {
                # frappe.db reads neither of these — db_socket is set, and
                # get_connection_settings drops host/port the moment it is. They
                # are here for everything that connects to `conf.db_host:db_port`
                # on its own (Insights' "Site DB", any app handed a data source),
                # which otherwise inherits frappe/config.py's 127.0.0.1:3306
                # default and lands in whichever bench happens to hold 3306.
                #
                # Under sockets.enable that port has no listener, so such a
                # client now gets ECONNREFUSED here instead of a silent success
                # against the wrong database. Do not drop these to match: an
                # unset FRAPPE_DB_PORT is what re-opens the 3306 hole.
                FRAPPE_DB_HOST = "127.0.0.1";
                FRAPPE_DB_PORT = toString dbPort;
              }
              // (
                if sockets then
                  {
                    FRAPPE_REDIS_CACHE = redisUrl;
                    FRAPPE_REDIS_QUEUE = redisUrl;

                    # Read by node_utils.js; realtime/index.js does
                    # `server.listen(uds || port)`.
                    FRAPPE_SOCKETIO_UDS = socketioSocket;
                    # Read by frappe_unixsock, which is the only way past
                    # frappe/app.py's hardcoded run_simple("0.0.0.0", int(port)).
                    FRAPPE_WEB_SOCKET = webSocket;
                  }
                else
                  {
                    # Fallback mode: still no fixed ports, just more of them.
                    FRAPPE_REDIS_CACHE = "redis://127.0.0.1:${toString config.services.redis.port}";
                    FRAPPE_REDIS_QUEUE = "redis://127.0.0.1:${toString config.services.redis.port}";
                  }
              )
              // (lib.optionalAttrs (cfg.siteName != "") {
                FRAPPE_SITE = cfg.siteName;
              })
              // cfg.extraEnv;

            enterShell = ''
              # Initialize the bench's direct app submodules (apps/*) if needed.
              #
              # NOT --recursive: Frappe apps frequently ship nested submodules
              # with broken/missing .gitmodules refs. Those have no role in
              # production, and recursing into them fails the init and breaks
              # shell startup. We only init the direct submodules of this bench.
              if git submodule status 2>/dev/null | grep -q '^-'; then
                echo "Initializing git submodules..."
                git submodule update --init
              fi

              # Create required directories. Frappe writes pids and lock files
              # into config/ and logs/, so both must be real and writable.
              # (No socket dir: every socket lives in $DEVENV_RUNTIME, which
              # devenv creates 0700 for us. And no $DEVENV_STATE/mariadb —
              # devenv's mysql module uses $DEVENV_STATE/mysql.)
              mkdir -p logs config/pids

              # Symlink the Nix-built Python env to ./env where bench expects it.
              #
              # A classic `bench init` bench has a real env/ directory (its own
              # virtualenv). `ln -sfn` does not replace a directory — it creates
              # env/<store-path> *inside* it — so the guard below would never be
              # satisfied and bench would keep resolving env/bin/python to the
              # stale virtualenv, silently. Move it aside first.
              if [ -e env ] && [ ! -L env ]; then
                echo "⚠  ./env is a real directory (a classic bench virtualenv)."
                mkdir -p .frappe-nix-backup
                mv env ".frappe-nix-backup/env-$(date +%s)"
                echo "   moved to .frappe-nix-backup/ — frappe-nix symlinks env/ to the Nix venv"
              fi
              if [ "$(readlink env 2>/dev/null)" != "${pythonEnvs.devPythonEnv}" ]; then
                ln -sfn "${pythonEnvs.devPythonEnv}" env
              fi

              # Record bench's own patches as already done, so `bench update`
              # does not die importing bench.patches.v3 — a module bench deleted
              # in 2022 but still lists in the patches.txt it ships. This is what
              # `bench init` writes and frappe-nix otherwise never would; see
              # lib/bench-patches.nix. Quiet unless it changes something.
              ${benchPatchesTool}/bin/frappe-nix-bench-patches \
                "${shippedBenchPatches}" .

              # Install node_modules for each app (mutable, dev-friendly).
              #
              # The install is skipped for an app whose manifests are unchanged
              # since the last successful one — including the nested vite
              # frontends its postinstall installs (erpnext/banking,
              # hrms/frontend+roster, helpdesk/desk, …), which is where the
              # interesting churn is. A failure here is a warning, not a dead
              # shell: it is `bench build` that needs node_modules, and it
              # re-runs this and refuses to build against a stale one.
              ${lib.optionalString (benchInfra.appsWithNode != [ ]) ''
                ${nodeModulesTool}/bin/frappe-nix-node-modules . ${
                  lib.escapeShellArgs benchInfra.appsWithNode
                } || true
              ''}

              echo ""
              echo "╔════════════════════════════════════════════════════════════╗"
              echo "║  ${cfg.benchName} Frappe Bench Development Environment"
              echo "╠════════════════════════════════════════════════════════════╣"
              echo "║  Start all services:  devenv up                           ║"
              ${lib.optionalString (cfg.siteName != "") ''
                echo "║  Default site: ${cfg.siteName}"
              ''}
              echo "║                                                            ║"
              echo "║  Common commands:                                          ║"
              echo "║    bench-update         # pull + migrate + build           ║"
              echo "║    bench-update --pull  # pull app submodules only         ║"
              echo "║    bench-migrate        # run DB migrations                ║"
              echo "║    bench-build          # build JS/CSS assets              ║"
              echo "║    bench-clear-cache    # clear Frappe cache               ║"
              echo "║    bench-console        # open Frappe Python REPL          ║"
              echo "╚════════════════════════════════════════════════════════════╝"
              echo ""
              echo "  Python: ${pythonEnvs.devPythonEnv}/bin/python"
              echo "  Bench root: $PWD"
              # Read back rather than interpolate: the port allocator only runs
              # for `devenv up`, so a value baked in here would be the base while
              # the running nginx might have moved on.
              _port="$(${pkgs.jq}/bin/jq -r '.webserver_port // empty' sites/common_site_config.json 2>/dev/null || true)"
              echo "  URL: http://127.0.0.1:''${_port:-${toString webBase}}"
              ${lib.optionalString sockets ''
                echo "  Sockets: $DEVENV_RUNTIME/{mysql,redis,socketio,web}.sock"
              ''}
              ${lib.optionalString mailEnabled ''
                echo "  Mail: ALL outgoing email → Mailpit (http://${mc.host}:${toString mailpitHttpBase})"
                echo "        incoming (IMAP/POP3) is ${
                  if mc.pop3.enable then "served from Mailpit POP3" else "blocked"
                }"
              ''}
              ${lib.optionalString (cfg.siteName != "") ''
                echo "  Site: ${cfg.siteName}"
              ''}
              echo ""
            '';

            services.mysql = {
              enable = true;
              # The reaping wrapper, not cfg.mariadb.package: this is the one
              # that reaches mariadbd. Everything else — the mysqlclient
              # override, LD_LIBRARY_PATH — keeps using the raw package.
              package = mariadbWrapped;
              settings = {
                mysqld = {
                  character-set-server = "utf8mb4";
                  collation-server = "utf8mb4_unicode_ci";
                  skip-character-set-client-handshake = true;
                  innodb-buffer-pool-size = "256M";
                  innodb-log-file-size = "64M";
                  max-connections = 200;
                  innodb-read-only-compressed = "OFF";
                }
                // (
                  # Frappe reaches the database over $DEVENV_RUNTIME/mysql.sock
                  # in both modes — get_connection_settings drops host/port
                  # entirely once db_socket is set — so the TCP listener was
                  # never for frappe. Under sockets.enable there is none.
                  #
                  # It used to be kept, on loopback, on this bench's own port,
                  # because an app that goes to `frappe.conf.db_host:db_port`
                  # rather than through frappe.db gets the 127.0.0.1:3306 default
                  # from frappe/config.py, and on a machine running a second
                  # bench that is *its* MariaDB. Insights' "Site DB" data source
                  # is one — ibis even rewrites host "localhost" back to
                  # 127.0.0.1 to force TCP past libmysqlclient's socket shortcut
                  # — and the neighbour it lands in fails `bench update`
                  # mid-migrate with an access-denied for a user it has never
                  # heard of.
                  #
                  # What actually closes that hole is not the listener, it is
                  # FRAPPE_DB_HOST/FRAPPE_DB_PORT naming this bench's own
                  # allocated port (see `env` below). With nothing behind it such
                  # a client gets ECONNREFUSED on a port that is provably ours,
                  # instead of a silent connection to a neighbour's database.
                  # Loud and local beats quiet and wrong.
                  #
                  # Dropping it also retires a failure class: the listener was
                  # the thing an orphaned mariadbd could hold against us, and
                  # devenv's allocator re-hands a cached port on an eval-cache
                  # replay rather than walking to the next free one. (The socket
                  # it can still hold — hence mariadbdReaper.)
                  #
                  # `port` stays in both modes, and not because anything binds
                  # it under sockets.enable. devenv's mysql module reads exactly
                  # this key — `hasPort` — to seed
                  # processes.mysql.ports.main.allocate, so dropping it silently
                  # reverts the allocator base to 3306, which is the one port
                  # this bench must never name. It is also what dbPort, and so
                  # FRAPPE_DB_PORT, resolves to. devenv then overwrites the value
                  # with the port it allocated; skip-networking wins over both.
                  {
                    port = dbBase; # allocator base, not a reservation
                  }
                  // (
                    if sockets then
                      { skip-networking = true; }
                    else
                      { bind-address = "127.0.0.1"; }
                  )
                );
              };
              initialDatabases = cfg.mariadb.initialDatabases;
            };

            # One instance serving both redis_cache and redis_queue. (There is no
            # third: redis_socketio was dropped from Frappe — realtime publishes
            # over redis_queue now — so setting it only left a dead config key.)
            services.redis = {
              enable = true;
              # port 0 is devenv's switch for "unix socket only": it emits
              # `unixsocket $DEVENV_RUNTIME/redis.sock` + `unixsocketperm 700`,
              # skips port allocation entirely, and moves its readiness probe to
              # `redis-cli -s`.
              port = if sockets then 0 else 13000;
            };

            # Mailpit is Go and has no unix listener, so it keeps two TCP ports.
            # devenv's own module runs both through the port allocator, which is
            # the whole reason to use it instead of the hand-rolled process this
            # replaces. POP3 is not one of its options, so that port is allocated
            # here and passed through additionalArgs.
            services.mailpit = lib.mkIf mailEnabled {
              enable = true;
              uiListenAddress = "${mc.host}:${toString mailpitHttpBase}";
              smtpListenAddress = "${mc.host}:${toString mailpitSmtpBase}";
              additionalArgs = lib.optionals mc.pop3.enable [
                "--pop3"
                "${mc.host}:${toString config.processes.mailpit.ports.pop3.value}"
                "--pop3-auth-file"
                "${mailpitPop3Auth}"
              ];
            };

            # The single public listener. Everything behind it is on a socket, so
            # this is the only port a browser — or another project — can see.
            #
            # Deliberately NOT a copy of the production vhost in modules/nixos.nix:
            # that one terminates behind an edge proxy on :443, this one *is* the
            # origin, and two of its headers are actively wrong here. See below.
            services.nginx = lib.mkIf sockets {
              enable = true;
              eventsConfig = "worker_connections 1024;";
              httpConfig = ''
                upstream frappe-web      { server unix:${webSocket}; }
                upstream frappe-socketio { server unix:${socketioSocket}; }

                map $http_upgrade $connection_upgrade {
                  default upgrade;
                  ""      close;
                }

                server {
                  listen 127.0.0.1:${toString webPort};

                  # nginx defaults to 1m; Frappe's own limit is 25m
                  # (frappe/app.py: request.max_content_length). Without this,
                  # every attachment over 1MB fails as a 413 Frappe never sees.
                  client_max_body_size 100m;

                  # The dev server has no timeout of its own, so nginx's default
                  # 60s would start failing slow reports, migrations and PDF
                  # renders that work today.
                  proxy_read_timeout 300s;
                  proxy_send_timeout 300s;
                  # Stream backup downloads straight through instead of spooling
                  # them into $DEVENV_STATE.
                  proxy_max_temp_file_size 0;

                  # $http_host, NOT $host: $host drops the port. Frappe builds the
                  # base URL for PDF rendering from request.host_url
                  # (frappe/utils/pdf.py), so with the port stripped the headless
                  # browser fetches /assets from :80 and every print format comes
                  # out unstyled.
                  proxy_set_header Host $http_host;
                  # Load-bearing, not decoration: an AF_UNIX peer has no address,
                  # so werkzeug reports REMOTE_ADDR as "<local>". frappe/auth.py's
                  # set_request_ip prefers this header, which is what keeps
                  # Activity Log, session IPs and 2FA allowlists meaningful.
                  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                  proxy_set_header X-Forwarded-Proto $scheme;

                  # NB no X-Frappe-Site-Name: frappe/app.py reads it *before*
                  # get_site_name(request.host), which would pin the bench to one
                  # site and break the documented siteName = "" multi-tenancy
                  # mode. With Host: localhost:<port>, socketio's authenticate.js
                  # already falls through to default_site.
                  #
                  # And no Origin override: production sets one only because a
                  # unix listener reports $scheme as http behind a TLS-terminating
                  # edge. Here there is no mismatch, and realtime/utils.js needs
                  # the browser's own Origin to rewrite.

                  location /socket.io {
                    proxy_pass http://frappe-socketio;
                    proxy_http_version 1.1;
                    proxy_set_header Upgrade $http_upgrade;
                    proxy_set_header Connection $connection_upgrade;
                    proxy_read_timeout 3600s;
                  }

                  # Everything else, /assets included, stays with werkzeug's
                  # application_with_statics — it already tracks what `bench watch`
                  # rebuilds, which a static nginx root would not.
                  location / {
                    proxy_pass http://frappe-web;
                  }
                }
              '';
            };

            # Ordering uses devenv's own `after`, not the raw process-compose
            # `depends_on` this replaces: `after` is honoured by whichever manager
            # is in play (the process-compose backend derives depends_on from it),
            # and it defaults to @ready rather than @started, so these now wait for
            # mysql's and redis's actual readiness probes instead of racing them.
            processes =
              let
                # Anything that can send mail waits for the catcher, so the first
                # send of a session doesn't hit a closed port.
                needsMailpit = lib.optional mailEnabled "devenv:processes:mailpit";
                needsConfig = [ "frappe:config" ];
              in
              {
                # devenv's own probe is `mariadb-admin ping` with no timeout at
                # either layer. Against a server that accepts the connection and
                # then never answers — exactly what a wedged orphan does — every
                # probe blocks forever, and they pile up unbounded until the
                # readiness deadline kills the process. `timeout` is the hard
                # guarantee (--connect-timeout only covers the connect, not the
                # read that actually hangs); probe_timeout lets process-compose
                # reap it too.
                mysql.ready = {
                  exec = lib.mkForce ''MYSQL_PWD="" ${pkgs.coreutils}/bin/timeout 5 ${cfg.mariadb.package}/bin/mariadb-admin --defaults-file=${emptyCnf} --connect-timeout=3 ping -u root --silent'';
                  probe_timeout = 10;
                  period = 5;
                  failure_threshold = 60;
                };

                web = {
                  # --port is ignored when FRAPPE_WEB_SOCKET is set
                  # (frappe_unixsock rewrites the bind address), but still passed:
                  # it is the port the site is actually reachable on, and it is
                  # what `bench serve` logs.
                  exec = ''
                    exec ${pythonEnvs.devPythonEnv}/bin/bench serve --port ${toString webPort}
                  '';
                  after = needsConfig ++ [
                    "devenv:processes:mysql"
                    "devenv:processes:redis"
                  ] ++ needsMailpit;
                }
                # Without nginx out front, the web server owns the public port.
                // lib.optionalAttrs (!sockets) { ports.main.allocate = webBase; }
                // lib.optionalAttrs sockets { ready = socketReady "$FRAPPE_WEB_SOCKET" "/"; };

                scheduler = {
                  exec = ''
                    exec ${pythonEnvs.devPythonEnv}/bin/bench schedule
                  '';
                  after = [
                    "devenv:processes:mysql"
                    # The scheduler enqueues; it has always needed redis and never
                    # declared it.
                    "devenv:processes:redis"
                  ] ++ needsMailpit;
                };

                worker = {
                  exec = ''
                    exec ${pythonEnvs.devPythonEnv}/bin/bench worker
                  '';
                  after = [
                    "devenv:processes:mysql"
                    "devenv:processes:redis"
                  ] ++ needsMailpit;
                };

                socketio = {
                  # node's server.listen() does not unlink a stale socket, so a
                  # crashed run would otherwise leave EADDRINUSE behind forever.
                  # This has to be in exec rather than a task: the manager re-runs
                  # exec on restart, not the task graph. (werkzeug needs no
                  # equivalent — it unlinks its own, and the reloader child
                  # inherits the fd instead of rebinding.)
                  exec = lib.optionalString sockets ''
                    rm -f "$FRAPPE_SOCKETIO_UDS"
                  '' + ''
                    exec ${cfg.nodejs}/bin/node apps/frappe/socketio.js
                  '';
                  after = needsConfig ++ [ "devenv:processes:redis" ];
                }
                // lib.optionalAttrs (!sockets) {
                  ports.main.allocate = 9000 + portOffset;
                  env.FRAPPE_SOCKETIO_PORT = toString config.processes.socketio.ports.main.value;
                }
                // lib.optionalAttrs sockets {
                  ready = socketReady "$FRAPPE_SOCKETIO_UDS" "/socket.io/";
                };

                watch = {
                  exec = ''
                    exec ${pythonEnvs.devPythonEnv}/bin/bench watch
                  '';
                  # After the config task as well as web: apps/wiki's frontend
                  # imports sites/common_site_config.json, so the file is a vite
                  # input and rewriting it under a running watcher is a rebuild.
                  after = needsConfig ++ [ "devenv:processes:web" ];
                };
              }
              // lib.optionalAttrs sockets {
                nginx = {
                  ports.main.allocate = webBase;
                  after = [
                    "devenv:processes:web"
                    "devenv:processes:socketio"
                  ];
                };
              }
              // lib.optionalAttrs mailEnabled {
                mailpit = {
                  # Everything that can send mail waits for the catcher, and
                  # "wait" means @ready — devenv's default — so it needs a probe
                  # of its own. The UI port is a real check: Mailpit only serves
                  # it once its listeners are up.
                  ready.http.get = {
                    host = mc.host;
                    port = config.processes.mailpit.ports.ui.value;
                    path = "/";
                  };
                }
                // lib.optionalAttrs mc.pop3.enable {
                  ports.pop3.allocate = mailpitPop3Base;
                };
              };

            # The one writer of the effective ports.
            #
            # webserver_port and socketio_port have to reach sites/common_site_config.json
            # — realtime/utils.js reads webserver_port straight out of the JSON and
            # node_utils.js has no env override for it, and on the Python side
            # boot.py copies socketio_port to the browser. Neither can come from
            # the environment.
            #
            # It runs here rather than in enterShell because devenv only enables
            # the port allocator for `devenv up`; a shell eval would write the
            # base port while the running nginx used the allocated one.
            tasks."frappe:config" = {
              exec = ''
                set -euo pipefail
                config="$FRAPPE_BENCH_ROOT/sites/common_site_config.json"
                port=${toString webPort}

                ${lib.optionalString mailEnabled ''
                  # Mailpit's ports come from the allocator too, and devguard
                  # cannot see them: it is loaded by a .pth in every interpreter,
                  # including ones started outside this shell. Hand it the
                  # resolved values here — it prefers them over the baked-in
                  # base, and falls back to the base (this bench's own range,
                  # never a shared 1025) when this file is out of reach.
                  printf '%s\n' ${
                    lib.escapeShellArg (
                      builtins.toJSON {
                        guards.mail = {
                          port = config.processes.mailpit.ports.smtp.value;
                          http_port = config.processes.mailpit.ports.ui.value;
                        }
                        // lib.optionalAttrs mc.pop3.enable {
                          pop3_port = config.processes.mailpit.ports.pop3.value;
                        };
                      }
                    )
                  } > "$DEVENV_RUNTIME/devguard-runtime.json"
                ''}

                [ -f "$config" ] || echo '{}' > "$config"

                tmp="$(mktemp)"
                ${pkgs.jq}/bin/jq --argjson port "$port" '
                  .webserver_port = $port
                  | .socketio_port = $port
                  # Transport keys the shell now owns through the environment.
                  # Left in place they are a trap: a stale redis://localhost:13000
                  # here is another project'"'"'s Redis, and it would be used by
                  # anything that reads the config without inheriting our env.
                  # They cannot simply be corrected in place either — the socket
                  # paths live under $DEVENV_RUNTIME, which is a hash of the
                  # project directory and so differs in every clone.
                  | del(.redis_cache, .redis_queue, .redis_socketio,
                        .db_host, .db_port,
                        .file_watcher_port)
                ' "$config" > "$tmp"

                # Compare before writing. The port is derived from benchName, so
                # after the first run this is a no-op and the committed file stays
                # clean; a diff later means the allocator genuinely had to move.
                if cmp -s "$tmp" "$config"; then
                  rm -f "$tmp"
                  exit 0
                fi

                echo "frappe-nix: serving on http://127.0.0.1:$port (updating sites/common_site_config.json)"
                mv "$tmp" "$config"

                # boot.py copies socketio_port into bootinfo and sessions.py
                # caches the whole bootinfo in redis, which survives restarts in
                # $DEVENV_STATE. Without this every already-logged-in browser
                # keeps dialling the old port, with no symptom but a socket.io
                # connect failure.
                keys="$(${redisCli} --scan --pattern '*bootinfo*' 2>/dev/null || true)"
                if [ -n "$keys" ]; then
                  echo "$keys" | while read -r key; do
                    [ -z "$key" ] || ${redisCli} del "$key" > /dev/null
                  done
                  echo "frappe-nix: cleared cached bootinfo so browsers pick up the new port"
                fi

                # apps/wiki's frontend imports socketio_port from this file at
                # *build* time, so its bundle still points at the old port.
                echo "frappe-nix: if you use the wiki SPA, run 'bench build --app wiki'"
              '';
              # Needs redis up to clear the cache, and must land before anything
              # reads the config — including `watch`, since the file is a vite
              # input for wiki.
              after = [ "devenv:processes:redis" ];
            };

            scripts = scripts // cfg.extraScripts;
          };
      };
  };
}
