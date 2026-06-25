# OCI container builds for Frappe bench projects.
#
# Consumes builtBench (compiled assets + passthru interpreters) from devenv.nix.
# All containers are site-parameterized: set FRAPPE_SITE at runtime.
# The entrypoint performs activation-time config synthesis (same pattern as
# frappe-init-<site> in the NixOS module) — reading DB/Redis params from env
# vars and merging secret files from /secrets/ into site_config.json.
#
# Volume contract:
#   /bench/sites  — mount a persistent volume here for site state
#   /secrets/     — mount secret files (db_password, encryption_key, *.json)
{
  lib,
  ...
}:

{
  config = {
    perSystem =
      { config, pkgs, lib, system, ... }:
      let
        cfg = config.frappe-nix;

        builtBench = config.packages.builtBench;
        pyEnv = builtBench.passthru.pythonEnv;
        nodejs = builtBench.passthru.nodejs;
        appsPath = builtBench.passthru.appsPath "${builtBench}/bench";

        containerRuntimeDeps = with pkgs; [
          coreutils
          bashInteractive
          gnused
          gnugrep
          findutils
          which
          cacert
          file
          jq
          wkhtmltopdf
          chromium
          libjpeg
          libpng
          zlib
          cairo
          pango
          gdk-pixbuf
          harfbuzz
          fontconfig
          freetype
          openssl
          libffi
          (cfg.mariadb.package).client
          liberation_ttf
          noto-fonts
        ] ++ cfg.extraContainerRuntimeDeps;

        libraryPath = lib.makeLibraryPath [
          pkgs.zlib
          pkgs.openssl
          pkgs.libffi
          pkgs.file.out
          (cfg.mariadb.package).client
          pkgs.cairo
          pkgs.pango
          pkgs.gdk-pixbuf
          pkgs.harfbuzz
          pkgs.fontconfig
          pkgs.freetype
          pkgs.libjpeg
          pkgs.libpng
        ];

        containerEnvList = [
          "FRAPPE_BENCH_ROOT=/bench"
          "SITES_PATH=/bench/sites"
          "PYTHONPATH=${appsPath}"
          "DEV_SERVER=0"
          "FRAPPE_ENV_TYPE=production"
          "FRAPPE_STREAM_LOGGING=1"
          "FRAPPE_TUNE_GC=1"
          "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
          "LD_LIBRARY_PATH=${libraryPath}"
        ];

        # Config-synthesis entrypoint, mirroring frappe-init-<site> from nixos.nix.
        # Reads site params from env vars, merges secrets from /secrets/, writes
        # site_config.json into the mounted /bench/sites/<site>/ volume.
        containerEntrypoint = pkgs.writeShellScript "frappe-entrypoint" ''
          set -euo pipefail

          SITE="''${FRAPPE_SITE:?FRAPPE_SITE must be set}"
          SITES_DIR=/bench/sites
          SITE_DIR="$SITES_DIR/$SITE"

          mkdir -p /bench/logs /bench/config/pids "$SITE_DIR"

          # Seed sites directory from the package.
          PKG_SITES="${builtBench}/bench/sites"
          for f in apps.json apps.txt common_site_config.json; do
            if [ ! -e "$SITES_DIR/$f" ] && [ -e "$PKG_SITES/$f" ]; then
              cp "$PKG_SITES/$f" "$SITES_DIR/$f"
            fi
          done

          # Symlink compiled assets from the package into the mounted volume.
          if [ -d "$PKG_SITES/assets" ]; then
            ln -sfn "$PKG_SITES/assets" "$SITES_DIR/assets"
          fi

          # Synthesize site_config.json from env vars + mounted secrets.
          ${pkgs.jq}/bin/jq -n \
            --arg db_host "''${FRAPPE_DB_HOST:-127.0.0.1}" \
            --arg db_port "''${FRAPPE_DB_PORT:-3306}" \
            --arg db_type "''${FRAPPE_DB_TYPE:-mariadb}" \
            --arg db_name "''${FRAPPE_DB_NAME:-''${SITE//./_}}" \
            --arg db_user "''${FRAPPE_DB_USER:-''${SITE//./_}}" \
            --arg redis_cache "''${FRAPPE_REDIS_CACHE:-redis://127.0.0.1:13000}" \
            --arg redis_queue "''${FRAPPE_REDIS_QUEUE:-redis://127.0.0.1:13000}" \
            --arg redis_socketio "''${FRAPPE_REDIS_SOCKETIO:-redis://127.0.0.1:13000}" \
            '{
              db_host: $db_host,
              db_port: ($db_port | tonumber),
              db_type: $db_type,
              db_name: $db_name,
              db_user: $db_user,
              redis_cache: $redis_cache,
              redis_queue: $redis_queue,
              redis_socketio: $redis_socketio
            }' > "$SITE_DIR/site_config.json.tmp"

          # Merge db_password from secret file if mounted.
          if [ -f /secrets/db_password ]; then
            SECRET_DB_PASSWORD="$(cat /secrets/db_password)"
            export SECRET_DB_PASSWORD
            ${pkgs.jq}/bin/jq '. + {db_password: $ENV.SECRET_DB_PASSWORD}' \
              "$SITE_DIR/site_config.json.tmp" > "$SITE_DIR/site_config.json.tmp2"
            mv "$SITE_DIR/site_config.json.tmp2" "$SITE_DIR/site_config.json.tmp"
          fi

          # Merge encryption_key from secret file if mounted.
          if [ -f /secrets/encryption_key ]; then
            SECRET_ENCRYPTION_KEY="$(cat /secrets/encryption_key)"
            export SECRET_ENCRYPTION_KEY
            ${pkgs.jq}/bin/jq '. + {encryption_key: $ENV.SECRET_ENCRYPTION_KEY}' \
              "$SITE_DIR/site_config.json.tmp" > "$SITE_DIR/site_config.json.tmp2"
            mv "$SITE_DIR/site_config.json.tmp2" "$SITE_DIR/site_config.json.tmp"
          fi

          # Merge any extra JSON config files from /secrets/*.json.
          for f in /secrets/*.json; do
            [ -f "$f" ] || continue
            ${pkgs.jq}/bin/jq -s '.[0] * .[1]' \
              "$SITE_DIR/site_config.json.tmp" "$f" > "$SITE_DIR/site_config.json.tmp2"
            mv "$SITE_DIR/site_config.json.tmp2" "$SITE_DIR/site_config.json.tmp"
          done

          # Merge FRAPPE_DB_SOCKET if set.
          if [ -n "''${FRAPPE_DB_SOCKET:-}" ]; then
            ${pkgs.jq}/bin/jq --arg s "$FRAPPE_DB_SOCKET" '. + {db_socket: $s}' \
              "$SITE_DIR/site_config.json.tmp" > "$SITE_DIR/site_config.json.tmp2"
            mv "$SITE_DIR/site_config.json.tmp2" "$SITE_DIR/site_config.json.tmp"
          fi

          mv "$SITE_DIR/site_config.json.tmp" "$SITE_DIR/site_config.json"
          chmod 0600 "$SITE_DIR/site_config.json"

          exec "$@"
        '';

        socketioEntrypoint = pkgs.writeShellScript "socketio-entrypoint" ''
          set -euo pipefail

          SITE="''${FRAPPE_SITE:?FRAPPE_SITE must be set}"
          SITES_DIR=/bench/sites

          # Symlink assets so socketio can resolve paths.
          PKG_SITES="${builtBench}/bench/sites"
          if [ -d "$PKG_SITES/assets" ]; then
            ln -sfn "$PKG_SITES/assets" "$SITES_DIR/assets"
          fi

          exec "$@"
        '';

        nginxEntrypoint = pkgs.writeShellScript "nginx-entrypoint" ''
          set -euo pipefail
          mkdir -p /tmp/nginx /var/log/nginx /var/cache/nginx
          exec "$@"
        '';

        prefix = cfg.benchName;

        mkFrappeContainer =
          {
            name,
            cmd,
            workingDir ? "/bench/sites",
            extraPaths ? [ ],
            extraEnv ? [ ],
            exposedPorts ? { },
          }:
          pkgs.dockerTools.buildLayeredImage {
            name = "${prefix}/${name}";
            tag = "latest";
            maxLayers = 125;
            contents = [
              (pkgs.buildEnv {
                name = "${prefix}-${name}-env";
                paths = containerRuntimeDeps ++ [ pyEnv ] ++ extraPaths;
                pathsToLink = [
                  "/bin"
                  "/lib"
                  "/share"
                  "/etc"
                ];
              })
              builtBench
            ];
            enableFakechroot = true;
            fakeRootCommands = ''
              mkdir -p /tmp /bench/logs /bench/config/pids /bench/sites /secrets
              chmod 1777 /tmp
            '';
            config = {
              Entrypoint = [ "${containerEntrypoint}" ];
              Cmd = cmd;
              WorkingDir = workingDir;
              ExposedPorts = exposedPorts;
              Env = containerEnvList ++ extraEnv;
            };
          };

      in
      lib.mkIf (cfg.enable && cfg.containers.enable) {
        packages.web = mkFrappeContainer {
          name = "web";
          cmd = [
            "${pyEnv}/bin/gunicorn"
            "--bind"
            "0.0.0.0:8000"
            "--workers"
            "4"
            "--max-requests"
            "5000"
            "--max-requests-jitter"
            "500"
            "--timeout"
            "120"
            "--preload"
            "--graceful-timeout"
            "30"
            "--keep-alive"
            "5"
            "--access-logfile"
            "-"
            "--error-logfile"
            "-"
            "frappe.app:application"
          ];
          exposedPorts = {
            "8000/tcp" = { };
          };
        };

        packages.scheduler = mkFrappeContainer {
          name = "scheduler";
          cmd = [
            "${pyEnv}/bin/bench"
            "schedule"
          ];
        };

        packages.worker-default = mkFrappeContainer {
          name = "worker-default";
          cmd = [
            "${pyEnv}/bin/bench"
            "worker"
            "--queue"
            "default"
          ];
        };

        packages.worker-short = mkFrappeContainer {
          name = "worker-short";
          cmd = [
            "${pyEnv}/bin/bench"
            "worker"
            "--queue"
            "short"
          ];
        };

        packages.worker-long = mkFrappeContainer {
          name = "worker-long";
          cmd = [
            "${pyEnv}/bin/bench"
            "worker"
            "--queue"
            "long"
          ];
        };

        packages.socketio = pkgs.dockerTools.buildLayeredImage {
          name = "${prefix}/socketio";
          tag = "latest";
          maxLayers = 125;
          contents = [
            (pkgs.buildEnv {
              name = "${prefix}-socketio-env";
              paths = with pkgs; [
                coreutils
                bashInteractive
                cacert
                nodejs
              ];
              pathsToLink = [
                "/bin"
                "/lib"
                "/share"
                "/etc"
              ];
            })
            builtBench
          ];
          enableFakechroot = true;
          fakeRootCommands = ''
            mkdir -p /bench/sites
          '';
          config = {
            Entrypoint = [ "${socketioEntrypoint}" ];
            Cmd = [
              "${nodejs}/bin/node"
              "/bench/apps/frappe/socketio.js"
            ];
            WorkingDir = "/bench";
            ExposedPorts = {
              "9000/tcp" = { };
            };
            Env = [
              "NODE_ENV=production"
              "FRAPPE_BENCH_ROOT=/bench"
              "SITES_PATH=/bench/sites"
              "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            ];
          };
        };

        packages.nginx = pkgs.dockerTools.buildLayeredImage {
          name = "${prefix}/nginx";
          tag = "latest";
          maxLayers = 125;
          contents = [
            (pkgs.buildEnv {
              name = "${prefix}-nginx-env";
              paths = with pkgs; [
                coreutils
                bashInteractive
                nginx
              ];
              pathsToLink = [
                "/bin"
                "/lib"
                "/share"
                "/etc"
              ];
            })
            builtBench
          ];
          enableFakechroot = true;
          fakeRootCommands = ''
            mkdir -p /tmp/nginx /var/log/nginx /var/cache/nginx /bench/sites
            chmod 1777 /tmp
          '';
          config = {
            Entrypoint = [ "${nginxEntrypoint}" ];
            Cmd = [
              "${pkgs.nginx}/bin/nginx"
              "-c"
              "/bench/config/nginx.conf"
              "-g"
              "daemon off;"
            ];
            WorkingDir = "/bench";
            ExposedPorts = {
              "80/tcp" = { };
            };
          };
        };

        packages.bench-cli = mkFrappeContainer {
          name = "bench";
          cmd = [
            "${pyEnv}/bin/bench"
            "--help"
          ];
          extraPaths = [ nodejs ];
        };
      };
  };
}
