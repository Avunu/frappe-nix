# OCI container builds for Frappe bench projects via nix-oci.
#
# Defines the standard set of Frappe production containers:
# web, scheduler, workers, socketio, nginx, bench-cli.
{
  lib,
  flake-parts-lib,
  inputs,
  ...
}:

{
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
          inherit (cfg) workspaceRoot nodejs nodeOverrides;
          inherit (pythonEnvs) prodPythonEnv;
        };

        containerRuntimeDeps = with pkgs; [
          coreutils
          bashInteractive
          gnused
          gnugrep
          findutils
          which
          cacert
          file
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

        containerEnvList = [
          "FRAPPE_BENCH_ROOT=/bench"
          "SITES_PATH=/bench/sites"
          "PYTHONPATH=${benchInfra.appsPath "/bench"}"
          "DEV_SERVER=0"
          "FRAPPE_ENV_TYPE=production"
          "FRAPPE_STREAM_LOGGING=1"
          "FRAPPE_TUNE_GC=1"
          "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
          "LD_LIBRARY_PATH=${
            lib.makeLibraryPath [
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
            ]
          }"
        ] ++ (lib.optional (cfg.siteName != "") "FRAPPE_SITE=${cfg.siteName}");

        containerEntrypoint = pkgs.writeShellScript "frappe-entrypoint" ''
          set -euo pipefail
          mkdir -p /bench/logs /bench/config/pids
          exec "$@"
        '';

        socketioEntrypoint = pkgs.writeShellScript "socketio-entrypoint" ''
          set -euo pipefail
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
                paths = containerRuntimeDeps ++ [ pythonEnvs.prodPythonEnv ] ++ extraPaths;
                pathsToLink = [
                  "/bin"
                  "/lib"
                  "/share"
                  "/etc"
                ];
              })
              benchInfra.benchRoot
            ];
            fakeRootCommands = ''
              mkdir -p /tmp /bench/logs /bench/config/pids
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
            "${pythonEnvs.prodPythonEnv}/bin/gunicorn"
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
            "${pythonEnvs.prodPythonEnv}/bin/bench"
            "schedule"
          ];
        };

        packages.worker-default = mkFrappeContainer {
          name = "worker-default";
          cmd = [
            "${pythonEnvs.prodPythonEnv}/bin/bench"
            "worker"
            "--queue"
            "default"
          ];
        };

        packages.worker-short = mkFrappeContainer {
          name = "worker-short";
          cmd = [
            "${pythonEnvs.prodPythonEnv}/bin/bench"
            "worker"
            "--queue"
            "short"
          ];
        };

        packages.worker-long = mkFrappeContainer {
          name = "worker-long";
          cmd = [
            "${pythonEnvs.prodPythonEnv}/bin/bench"
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
                cfg.nodejs
              ];
              pathsToLink = [
                "/bin"
                "/lib"
                "/share"
                "/etc"
              ];
            })
            benchInfra.benchRoot
          ];
          config = {
            Entrypoint = [ "${socketioEntrypoint}" ];
            Cmd = [
              "${cfg.nodejs}/bin/node"
              "/bench/apps/frappe/socketio.js"
            ];
            WorkingDir = "/bench";
            ExposedPorts = {
              "9000/tcp" = { };
            };
            Env = [
              "NODE_ENV=production"
              "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            ] ++ (lib.optional (cfg.siteName != "") "FRAPPE_SITE=${cfg.siteName}");
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
            benchInfra.benchRoot
          ];
          fakeRootCommands = ''
            mkdir -p /tmp/nginx /var/log/nginx /var/cache/nginx
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
            "${pythonEnvs.prodPythonEnv}/bin/bench"
            "--help"
          ];
          extraPaths = [ cfg.nodejs ];
        };
      };
  };
}
