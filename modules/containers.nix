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
#
# Every image carries the OCI labels, the ones that serve HTTP have a
# HEALTHCHECK, and with containers.nonRoot they run as frappe (uid/gid 1000)
# (python-devops-nix docs/conventions.md, Images). Those parts, with the
# image names, /etc/passwd and the writable directories, are
# python-devops-nix's lib.containers; the entrypoints, the probes and what
# each image holds are frappe-nix's. The outputs are container-<role>; the
# role-named outputs they replaced stay as aliases until 2.0.0.
{ inputs, ... }:

{
  config = {
    perSystem =
      {
        config,
        pkgs,
        lib,
        ...
      }:
      let
        cfg = config.frappe-nix;

        builtBench = config.packages.builtBench;
        pyEnv = builtBench.passthru.pythonEnv;
        nodejs = builtBench.passthru.nodejs;
        appsPath = builtBench.passthru.appsPath "${builtBench}/bench";

        containerRuntimeDeps =
          with pkgs;
          [
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
            cfg.mariadb.package.client
            liberation_ttf
            noto-fonts
          ]
          ++ cfg.extraContainerRuntimeDeps;

        libraryPath = lib.makeLibraryPath [
          pkgs.zlib
          pkgs.openssl
          pkgs.libffi
          pkgs.file.out
          cfg.mariadb.package.client
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
          # frappe-bench's `bench` imports GitPython at startup, which raises
          # when no git is on PATH. The images have none and need none: their
          # apps are store paths, not checkouts, so get-app, update and
          # switch-to-branch (bench's only git users) have nothing to act on.
          # Quiet keeps the import from failing `bench`, and with it the split
          # set's scheduler and workers, which run `bench schedule`/`worker`.
          "GIT_PYTHON_REFRESH=quiet"
          "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
          "LD_LIBRARY_PATH=${libraryPath}"
        ]
        # BLAS and OpenMP pools at one thread, as the NixOS units have them
        # (python-devops-nix's lib.python.threadCaps).
        ++ lib.mapAttrsToList (
          name: value: "${name}=${value}"
        ) inputs.python-devops-nix.lib.python.threadCaps;

        # Config-synthesis entrypoint, mirroring frappe-init-<site> from nixos.nix.
        # Reads site params from env vars, merges secrets from /secrets/, writes
        # site_config.json into the mounted /bench/sites/<site>/ volume.
        containerEntrypoint = pkgs.writeShellScript "frappe-entrypoint" ''
          set -euo pipefail

          SITE="''${FRAPPE_SITE:?FRAPPE_SITE must be set}"
          SITES_DIR=/bench/sites
          SITE_DIR="$SITES_DIR/$SITE"

          mkdir -p /bench/logs /bench/config/pids "$SITE_DIR"

          # The app registry is the package's: links into the image, refreshed
          # on every start, like the assets below. A stale copy would hide an
          # app from frappe.get_all_apps(); `ln -sfn` also replaces the regular
          # file an older image's copy-once seed left in the volume.
          PKG_SITES="${builtBench}/bench/sites"
          for f in apps.txt apps.json; do
            if [ -e "$PKG_SITES/$f" ]; then
              ln -sfn "$PKG_SITES/$f" "$SITES_DIR/$f"
            fi
          done

          # common_site_config.json is the operator's once seeded.
          if [ ! -e "$SITES_DIR/common_site_config.json" ] \
             && [ -e "$PKG_SITES/common_site_config.json" ]; then
            cp "$PKG_SITES/common_site_config.json" "$SITES_DIR/common_site_config.json"
          fi

          # The Redis URLs are bench-wide too: the split set's workers serve
          # every site, so they read redis_queue from common_site_config.json
          # rather than a site's config, and the socketio image (which has no
          # FRAPPE_REDIS_* of its own) reads it there as well. Without these
          # keys Frappe falls back to its own default ports (11311 for the
          # queue), not the ones the site was given. Merged into whatever the
          # file already holds: a variable that is set wins, a key the
          # operator set is kept, and a missing key gets the same default
          # the site config gets. Written only when it changes, through a
          # temporary file unique to this container and a rename, so
          # containers sharing the sites volume never see a partial file.
          COMMON="$SITES_DIR/common_site_config.json"
          if [ -s "$COMMON" ]; then current="$(cat "$COMMON")"; else current='{}'; fi
          merged="$(printf '%s' "$current" | ${pkgs.jq}/bin/jq -S \
            --arg cache "''${FRAPPE_REDIS_CACHE-}" --arg cache_set "''${FRAPPE_REDIS_CACHE+1}" \
            --arg queue "''${FRAPPE_REDIS_QUEUE-}" --arg queue_set "''${FRAPPE_REDIS_QUEUE+1}" \
            --arg socketio "''${FRAPPE_REDIS_SOCKETIO-}" --arg socketio_set "''${FRAPPE_REDIS_SOCKETIO+1}" \
            'def put(k; set; v):
               if set == "1" then .[k] = v
               elif has(k) then .
               else .[k] = "redis://127.0.0.1:13000" end;
             put("redis_cache"; $cache_set; $cache)
             | put("redis_queue"; $queue_set; $queue)
             | put("redis_socketio"; $socketio_set; $socketio)')"
          if [ "$merged" != "$(printf '%s' "$current" | ${pkgs.jq}/bin/jq -S .)" ]; then
            tmp="$COMMON.tmp.''${HOSTNAME:-container}.$$"
            printf '%s\n' "$merged" > "$tmp"
            # Not secret enough to hide from the image's other user: Frappe's
            # own bench writes this file world-readable too.
            chmod 0644 "$tmp"
            mv -f "$tmp" "$COMMON"
          fi

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

          # Likewise the realtime socket. node_utils.js reads FRAPPE_SOCKETIO_UDS
          # directly, so this is only for anything that reads the config instead
          # — but it keeps the two in step, and a socketio container sharing a
          # volume with nginx needs no published port at all.
          if [ -n "''${FRAPPE_SOCKETIO_UDS:-}" ]; then
            ${pkgs.jq}/bin/jq --arg s "$FRAPPE_SOCKETIO_UDS" '. + {socketio_uds: $s}' \
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

        prefix = cfg.name;

        # The image parts frappe-nix shares with odoo-nix (python-devops-nix's
        # lib.containers).
        #
        # Images are <name>/<role>, and containers.registry prefixes that:
        # "ghcr.io/avunu" (or "ghcr.io/avunu/") names them
        # ghcr.io/avunu/<name>/<role>; "" leaves them bare. Only image names
        # take it, not the store paths named after prefix.
        #
        # With containers.nonRoot (opt-in through 1.x, the default in 2.0.0)
        # the images run as frappe, uid and gid 1000, the ones frappe_docker
        # uses; root otherwise, as before.
        #
        # The labels' revision is frappe-nix's own commit when the bench's
        # flake has it as the `frappe-nix` input, as the scaffold does.
        # containers.labels overrides any of them, for a bench that labels its
        # images with its own repository and version.
        home = "/home/frappe";
        containers = inputs.python-devops-nix.lib.containers.mk {
          inherit pkgs home;
          label = "frappe-nix";
          inherit (cfg) name;
          user = "frappe";
          inherit (cfg.containers) nonRoot registry;
          source = "https://github.com/Avunu/frappe-nix";
          version = lib.trim (builtins.readFile ../version.txt);
          licenses = "MIT";
          revision = (inputs.frappe-nix or { }).rev or null;
          labelOverrides = cfg.containers.labels;
        };
        inherit (containers)
          imageName
          labels
          user
          homeEnv
          healthcheck
          # buildLayeredImage, with the image config readable without a build
          # (tests/containers-registry.nix).
          buildImage
          ;

        # A frappe command (`bench <args>` on a bench host), run the way
        # `bench` runs it: frappe-bench's frappe_cmd execs
        # `python -m frappe.utils.bench_helper frappe <args>` from sites/,
        # which is the images' default WorkingDir. Not `bench` itself: it
        # refuses to run as root, which the images are unless
        # containers.nonRoot, so the split set's scheduler and workers exited
        # at start.
        frappeCmd =
          args:
          [
            "${pyEnv}/bin/python"
            "-m"
            "frappe.utils.bench_helper"
            "frappe"
          ]
          ++ args;

        # Frappe's readiness endpoint, asked for the container's site: Frappe
        # picks the site from the Host header. The image has no curl; its
        # Python does.
        pingProbe = pkgs.writeScript "frappe-healthcheck" ''
          #!${pyEnv}/bin/python
          import os, sys, urllib.request

          site = os.environ.get("FRAPPE_SITE", "localhost")
          request = urllib.request.Request(
              "http://127.0.0.1:8000/api/method/ping", headers={"Host": site}
          )
          try:
              with urllib.request.urlopen(request, timeout=8) as response:
                  sys.exit(0 if response.status == 200 else 1)
          except Exception as error:
              print(f"frappe-healthcheck: {error}", file=sys.stderr)
              sys.exit(1)
        '';

        mkFrappeContainer =
          {
            name,
            cmd,
            workingDir ? "/bench/sites",
            extraPaths ? [ ],
            extraEnv ? [ ],
            exposedPorts ? { },
            probe ? null,
          }:
          buildImage {
            name = imageName name;
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
              containers.users
              builtBench
            ];
            enableFakechroot = true;
            fakeRootCommands = containers.fakeRootCommands {
              dirs = [ "/secrets" ];
              writable = [
                "/bench/logs"
                # frappe's scheduler takes its lock in config/ (scheduler_process).
                "/bench/config"
                "/bench/config/pids"
                "/bench/sites"
                home
              ];
            };
            config = {
              User = user;
              Entrypoint = [ "${containerEntrypoint}" ];
              Cmd = cmd;
              WorkingDir = workingDir;
              ExposedPorts = exposedPorts;
              Env = containerEnvList ++ homeEnv ++ extraEnv;
              Labels = labels name;
            }
            // lib.optionalAttrs (probe != null) { Healthcheck = healthcheck probe; };
          };

        # The realtime server answers an Engine.IO handshake before any
        # namespace (and so any session) is involved.
        socketioProbe = pkgs.writeText "socketio-healthcheck.js" ''
          const http = require("http");
          const req = http.get(
            { host: "127.0.0.1", port: 9000, path: "/socket.io/?EIO=4&transport=polling", timeout: 8000 },
            (res) => process.exit(res.statusCode === 200 ? 0 : 1),
          );
          req.on("timeout", () => req.destroy(new Error("timed out")));
          req.on("error", (error) => {
            console.error(`socketio-healthcheck: ''${error.message}`);
            process.exit(1);
          });
        '';

        # nginx answering at all: the configuration is the bench's own, so the
        # probe asks nothing of it but a status line. The runtime behind it has
        # its own healthcheck. Bash's /dev/tcp, since the image has no curl.
        nginxProbe = pkgs.writeShellScript "nginx-healthcheck" ''
          exec 3<>/dev/tcp/127.0.0.1/80 || exit 1
          printf 'GET / HTTP/1.0\r\nHost: localhost\r\n\r\n' >&3
          IFS= read -r -t 8 status <&3 || exit 1
          [[ $status == HTTP/* ]]
        '';

        # Images by role. The packages are container-<role>.
        images =
          (
            if cfg.runtime.enable then
              {
                # One image for the whole site: the web app, realtime, the background
                # jobs and the scheduler. Replaces web + scheduler + worker-* +
                # socketio, and carries no Node at all.
                #
                # WorkingDir is /bench, not /bench/sites: the runtime chdirs into
                # sites/ itself, and frappe's execute_job re-inits per job with
                # sites_path=".".
                runtime = mkFrappeContainer {
                  name = "runtime";
                  workingDir = "/bench";
                  cmd = [
                    "${pyEnv}/bin/frappe-runtime"
                    "--host"
                    "0.0.0.0"
                    "--port"
                    "8000"
                    "--job-threads"
                    "4"
                  ];
                  exposedPorts = {
                    "8000/tcp" = { };
                  };
                  probe = [ "${pingProbe}" ];
                };

              }
            else
              {
                web = mkFrappeContainer {
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
                  probe = [ "${pingProbe}" ];
                };

                scheduler = mkFrappeContainer {
                  name = "scheduler";
                  cmd = frappeCmd [ "schedule" ];
                };

                worker-default = mkFrappeContainer {
                  name = "worker-default";
                  cmd = frappeCmd [
                    "worker"
                    "--queue"
                    "default"
                  ];
                };

                worker-short = mkFrappeContainer {
                  name = "worker-short";
                  cmd = frappeCmd [
                    "worker"
                    "--queue"
                    "short"
                  ];
                };

                worker-long = mkFrappeContainer {
                  name = "worker-long";
                  cmd = frappeCmd [
                    "worker"
                    "--queue"
                    "long"
                  ];
                };

                # Node's realtime server. Only built when the unified runtime is off — it is
                # the last thing in the runtime closure that needs Node at all.
                socketio = buildImage {
                  name = imageName "socketio";
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
                    containers.users
                    builtBench
                  ];
                  enableFakechroot = true;
                  fakeRootCommands = containers.fakeRootCommands {
                    writable = [
                      "/bench/sites"
                      home
                    ];
                  };
                  config = {
                    User = user;
                    Labels = labels "socketio";
                    Healthcheck = healthcheck [
                      "${nodejs}/bin/node"
                      "${socketioProbe}"
                    ];
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
                    ]
                    ++ homeEnv;
                  };
                };

              }
          )
          // {
            nginx = buildImage {
              name = imageName "nginx";
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
                containers.users
                builtBench
              ];
              enableFakechroot = true;
              # nixpkgs' nginx keeps its pid file in /var/log/nginx and its temp
              # files in /tmp.
              fakeRootCommands = containers.fakeRootCommands {
                writable = [
                  "/tmp/nginx"
                  "/var/log/nginx"
                  "/var/cache/nginx"
                  "/bench/sites"
                  home
                ];
              };
              config = {
                User = user;
                Labels = labels "nginx";
                Healthcheck = healthcheck [ "${nginxProbe}" ];
                Env = homeEnv;
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

            # One-off commands: no server, so no healthcheck.
            bench = mkFrappeContainer {
              name = "bench";
              cmd = [
                "${pyEnv}/bin/bench"
                "--help"
              ];
              extraPaths = [ nodejs ];
            };
          };

        # The outputs before python-devops-nix's container-<role> convention:
        # the role itself, and bench-cli for the bench image. Kept, warning
        # when used, until frappe-nix 2.0.0. The warning sits on what a build
        # or a reference reads (drvPath, outPath), not on the attribute, so
        # `nix flake show` stays quiet; `nix flake check` evaluates every
        # package, so a bench that uses none of them sets
        # containers.aliases = false.
        alias =
          old: role:
          let
            image = images.${role};
            warn = lib.warn "frappe-nix: the package `${old}' has been renamed `container-${role}'. The old name works through frappe-nix 1.x and goes in 2.0.0; set frappe-nix.containers.aliases = false once nothing uses it.";
          in
          image
          // {
            drvPath = warn image.drvPath;
            outPath = warn image.outPath;
          };
        oldName = role: if role == "bench" then "bench-cli" else role;
        aliases = lib.mapAttrs' (
          role: _: lib.nameValuePair (oldName role) (alias (oldName role) role)
        ) images;

      in
      lib.mkIf (cfg.enable && cfg.containers.enable) {
        # Whole attrsets, not lib.mkIf per package: `packages` is an attrsOf,
        # and mkIf false keeps the attribute *name* while removing its
        # definition. `nix flake show` enumerates those names and dies with
        # "option ... was accessed but has no value defined".
        packages =
          lib.mapAttrs' (role: image: lib.nameValuePair "container-${role}" image) images
          // lib.optionalAttrs cfg.containers.aliases aliases;
      };
  };
}
