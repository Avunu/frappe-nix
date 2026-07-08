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
                mailpit
                curl
                file
                git
                gnused
                htop
                jq
                just
                pv
              ]
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

                MAILPIT_SMTP_PORT = "1025";
                MAILPIT_HTTP_PORT = "8025";

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
              // (lib.optionalAttrs (cfg.siteName != "") {
                FRAPPE_SITE = cfg.siteName;
              })
              // cfg.extraEnv;

            enterShell = ''
              # Initialize git submodules if needed
              if git submodule status 2>/dev/null | grep -q '^-'; then
                echo "Initializing git submodules..."
                git submodule update --init --recursive
              fi

              # Create required directories
              mkdir -p "$DEVENV_STATE/mariadb" "$DEVENV_STATE/sockets" logs config/pids

              # Symlink the Nix-built Python env to ./env where bench expects it
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

              mailpit.exec = ''
                exec ${pkgs.mailpit}/bin/mailpit \
                  --smtp 127.0.0.1:''${MAILPIT_SMTP_PORT:-1025} \
                  --listen 127.0.0.1:''${MAILPIT_HTTP_PORT:-8025} \
                  --database "$DEVENV_STATE/mailpit.db"
              '';
            };

            process.managers.process-compose.settings.processes = {
              web.depends_on = {
                mysql.condition = "process_started";
                redis.condition = "process_started";
              };
              scheduler.depends_on.mysql.condition = "process_started";
              worker.depends_on = {
                mysql.condition = "process_started";
                redis.condition = "process_started";
              };
              socketio.depends_on.redis.condition = "process_started";
              watch.depends_on.web.condition = "process_started";
            };

            scripts = scripts // cfg.extraScripts;
          };
      };
  };
}
