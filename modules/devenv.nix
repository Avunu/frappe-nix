# devenv shell module for Frappe bench projects.
# Configures services, processes, environment, and scripts.
{
  lib,
  flake-parts-lib,
  inputs,
  ...
}:
let
  inherit (lib) mkOption mkEnableOption types;
  inherit (flake-parts-lib) mkPerSystemOption;
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
              default = 1025;
              description = "Catcher SMTP port — drives both Mailpit and Frappe.";
            };

            httpPort = mkOption {
              type = types.port;
              default = 8025;
              description = "Mailpit web UI port.";
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
                default = 1110;
                description = "Mailpit POP3 port.";
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
        };

        benchInfra = import ../lib/bench.nix {
          inherit pkgs lib;
          inherit (cfg) workspaceRoot nodejs nodeOverrides nodeOfflineHashes extraPackages;
          inherit (pythonEnvs) prodPythonEnv;
        };

        scripts = import ../lib/scripts.nix {
          inherit lib pkgs;
          inherit (benchInfra) appsWithNode;
          benchBin = "${pythonEnvs.devPythonEnv}/bin/bench";
        };

      in
      lib.mkIf cfg.enable {
        packages.prodPythonEnv = pythonEnvs.prodPythonEnv;
        packages.devPythonEnv = pythonEnvs.devPythonEnv;
        # Unbuilt /bench tree — used by containers.nix and as input to builtBench.
        packages.benchRoot = benchInfra.benchRoot;
        # Production-ready bench with compiled assets + passthru interpreters.
        packages.builtBench = benchInfra.builtBench;
        packages.default = benchInfra.builtBench;

        devenv.shells.default =
          { config, pkgs, ... }:
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

                FRAPPE_DB_HOST = "127.0.0.1";
                FRAPPE_DB_PORT = "3306";
                FRAPPE_DB_TYPE = "mariadb";

                FRAPPE_REDIS_CACHE = "redis://localhost:13000";
                FRAPPE_REDIS_QUEUE = "redis://localhost:13000";
                FRAPPE_REDIS_SOCKETIO = "redis://localhost:13000";

                FRAPPE_WEBSERVER_PORT = "8000";
                FRAPPE_SOCKETIO_PORT = "9000";
                FRAPPE_FILE_WATCHER_PORT = "6787";

                FRAPPE_DB_SOCKET = config.env.DEVENV_RUNTIME + "/mysql.sock";
                FRAPPE_SOCKETS_DIR = config.env.DEVENV_STATE + "/sockets";
                FRAPPE_WEB_SOCKET = config.env.DEVENV_STATE + "/sockets/frappe.sock";

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
              // (lib.optionalAttrs mailEnabled {
                # Consumed by the mailpit process. Frappe reads the matching
                # values from its baked-in config instead, so that it stays
                # redirected even outside the devenv environment; the
                # FRAPPE_DEVGUARD_* variables override those when set.
                MAILPIT_SMTP_HOST = mc.host;
                MAILPIT_SMTP_PORT = toString mc.smtpPort;
                MAILPIT_HTTP_PORT = toString mc.httpPort;
                MAILPIT_POP3_PORT = toString mc.pop3.port;
              })
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

              # Create required directories
              mkdir -p "$DEVENV_STATE/mariadb" "$DEVENV_STATE/sockets" logs config/pids

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

              # Install node_modules for each app (mutable, dev-friendly).
              #
              # A `.frappe-nix-installed` sentinel inside node_modules is written
              # ONLY after a fully-successful `yarn install` — including the app's
              # postinstall, which is where nested vite frontends get their deps
              # (erpnext/banking, hrms/frontend+roster, helpdesk/frontend, …).
              # So if a postinstall fails (or the app gained a nested frontend
              # after the first install), the app is retried on the next shell
              # entry instead of being silently left without `vite` on PATH.
              ${lib.concatStringsSep "\n" (
                map (app: ''
                  _nm="apps/${app}/node_modules"
                  if [ -L "$_nm" ] && readlink "$_nm" | grep -q '/nix/store'; then
                    echo "Replacing Nix store node_modules symlink for ${app}..."
                    rm "$_nm"
                  fi
                  if [ ! -e "$_nm/.frappe-nix-installed" ]; then
                    echo "Installing node_modules for ${app} (incl. nested frontends)..."
                    _log=$(mktemp)
                    if (cd "apps/${app}" && yarn install --frozen-lockfile) > "$_log" 2>&1; then
                      touch "$_nm/.frappe-nix-installed"
                      echo "  ✓ ${app}"
                    else
                      echo "  ⚠  yarn install failed for ${app} (will retry next shell entry):" >&2
                      tail -20 "$_log" >&2
                    fi
                    rm -f "$_log"
                  fi
                '') benchInfra.appsWithNode
              )}

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
              ${lib.optionalString mailEnabled ''
                echo "  Mail: ALL outgoing email → Mailpit (http://${mc.host}:${toString mc.httpPort})"
                echo "        incoming (IMAP/POP3) is ${
                  if mc.pop3.enable then "served from Mailpit POP3 :${toString mc.pop3.port}" else "blocked"
                }"
              ''}
              ${lib.optionalString (cfg.siteName != "") ''
                echo "  Site: ${cfg.siteName}"
              ''}
              echo ""
            '';

            services.mysql = {
              enable = true;
              package = cfg.mariadb.package;
              settings = {
                mysqld = {
                  character-set-server = "utf8mb4";
                  collation-server = "utf8mb4_unicode_ci";
                  skip-character-set-client-handshake = true;
                  innodb-buffer-pool-size = "256M";
                  innodb-log-file-size = "64M";
                  max-connections = 200;
                  innodb-read-only-compressed = "OFF";
                  port = 3306;
                  bind-address = "127.0.0.1";
                };
              };
              initialDatabases = cfg.mariadb.initialDatabases;
            };

            services.redis = {
              enable = true;
              port = 13000;
            };

            processes = {
              web.exec = ''
                exec ${pythonEnvs.devPythonEnv}/bin/bench serve --port ''${FRAPPE_WEBSERVER_PORT:-8000}
              '';

              scheduler.exec = ''
                exec ${pythonEnvs.devPythonEnv}/bin/bench schedule
              '';

              worker.exec = ''
                exec ${pythonEnvs.devPythonEnv}/bin/bench worker
              '';

              socketio.exec = ''
                rm -f "$DEVENV_STATE/sockets/socketio.sock"
                exec ${cfg.nodejs}/bin/node apps/frappe/socketio.js
              '';

              watch.exec = ''
                exec ${pythonEnvs.devPythonEnv}/bin/bench watch
              '';

            }
            // lib.optionalAttrs mailEnabled {
              mailpit.exec =
                let
                  bind = "\${MAILPIT_SMTP_HOST:-${mc.host}}";
                  flags = [
                    "--smtp ${bind}:\${MAILPIT_SMTP_PORT:-${toString mc.smtpPort}}"
                    "--listen ${bind}:\${MAILPIT_HTTP_PORT:-${toString mc.httpPort}}"
                  ]
                  ++ lib.optionals mc.pop3.enable [
                    "--pop3 ${bind}:\${MAILPIT_POP3_PORT:-${toString mc.pop3.port}}"
                    "--pop3-auth-file ${mailpitPop3Auth}"
                  ]
                  ++ [ ''--database "$DEVENV_STATE/mailpit.db"'' ];
                in
                ''
                  exec ${pkgs.mailpit}/bin/mailpit \
                    ${lib.concatStringsSep " \\\n    " flags}
                '';
            };

            process.managers.process-compose.settings.processes =
              let
                # Anything that can send mail waits for the catcher, so the
                # first send of a session doesn't hit a closed port.
                needsMailpit = lib.optionalAttrs mailEnabled {
                  mailpit.condition = "process_started";
                };
              in
              {
                web.depends_on = {
                  mysql.condition = "process_started";
                  redis.condition = "process_started";
                }
                // needsMailpit;
                scheduler.depends_on = {
                  mysql.condition = "process_started";
                }
                // needsMailpit;
                worker.depends_on = {
                  mysql.condition = "process_started";
                  redis.condition = "process_started";
                }
                // needsMailpit;
                socketio.depends_on.redis.condition = "process_started";
                watch.depends_on.web.condition = "process_started";
              };

            scripts = scripts // cfg.extraScripts;
          };
      };
  };
}
