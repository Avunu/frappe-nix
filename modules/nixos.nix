# NixOS systemd service module for Frappe bench production deployment.
#
# This is a *standalone NixOS module* (not a flake-parts module). Surface it via
# `nixosModules.default` in flake.nix and import it into a nixosConfiguration.
#
# It takes the two heavy artifacts as inputs rather than building them itself,
# so it has no dependency on uv2nix / pyproject-nix / flake-parts:
#   - benchRoot  : the assembled /bench tree (frappe-nix lib/bench.nix, exported
#                  as packages.benchRoot)
#   - pythonEnv  : the production virtualenv (packages.prodPythonEnv)
#
# Example:
#   services.frappe = {
#     enable      = true;
#     benchRoot   = self.packages.x86_64-linux.benchRoot;
#     pythonEnv   = self.packages.x86_64-linux.prodPythonEnv;
#     defaultSite = "mysite.localhost";
#     nginx.enable = true;
#     redis.createLocally = true;
#     database.createLocally = true;
#   };
{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    mkOption
    mkEnableOption
    mkIf
    mkMerge
    types
    ;

  cfg = config.services.frappe;

  benchDir = "${cfg.benchRoot}/bench";
  sitesPath = "/var/lib/frappe/sites";

  libraryPath = lib.makeLibraryPath [
    pkgs.zlib
    pkgs.openssl
    pkgs.libffi
    pkgs.file.out
    cfg.database.package.client
    pkgs.cairo
    pkgs.pango
    pkgs.gdk-pixbuf
    pkgs.harfbuzz
    pkgs.fontconfig
    pkgs.freetype
    pkgs.libjpeg
    pkgs.libpng
  ];

  # Environment shared by every Frappe unit, mirroring the keys baked into the
  # OCI images (see modules/containers.nix containerEnvList).
  commonEnv =
    {
      FRAPPE_BENCH_ROOT = benchDir;
      SITES_PATH = sitesPath;
      DEV_SERVER = "0";
      FRAPPE_ENV_TYPE = "production";
      FRAPPE_STREAM_LOGGING = "1";
      FRAPPE_TUNE_GC = "1";

      FRAPPE_DB_HOST = cfg.database.host;
      FRAPPE_DB_PORT = toString cfg.database.port;
      FRAPPE_DB_TYPE = "mariadb";

      FRAPPE_REDIS_CACHE = cfg.redis.cacheUrl;
      FRAPPE_REDIS_QUEUE = cfg.redis.queueUrl;
      FRAPPE_REDIS_SOCKETIO = cfg.redis.socketioUrl;

      SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
      LD_LIBRARY_PATH = libraryPath;
    }
    // lib.optionalAttrs (cfg.database.socket != "") {
      FRAPPE_DB_SOCKET = cfg.database.socket;
    }
    // lib.optionalAttrs (cfg.defaultSite != "") {
      FRAPPE_SITE = cfg.defaultSite;
    }
    // cfg.extraEnv;

  # Build PYTHONPATH from the apps in benchRoot at runtime. benchRoot is a
  # derivation, so we can't readDir it at eval time; glob it in the wrapper
  # instead. Workspace apps (frappe, erpnext, …) must resolve from their source
  # copies in /bench/apps so their non-Python assets and entry points are found.
  mkExec =
    name: cmd:
    pkgs.writeShellScript "frappe-${name}" ''
      set -euo pipefail
      PYTHONPATH=""
      for d in ${benchDir}/apps/*; do
        PYTHONPATH="$PYTHONPATH''${PYTHONPATH:+:}$d"
      done
      export PYTHONPATH
      cd ${sitesPath}
      exec ${cmd}
    '';

  # Seed the mutable sites/ state dir from the read-only store benchRoot.
  seedSites = pkgs.writeShellScript "frappe-seed-sites" ''
    set -euo pipefail
    mkdir -p ${sitesPath}
    for f in apps.json apps.txt common_site_config.json; do
      if [ ! -e "${sitesPath}/$f" ] && [ -e "${benchDir}/sites/$f" ]; then
        cp "${benchDir}/sites/$f" "${sitesPath}/$f"
      fi
    done
  '';

  dependsOnInit = {
    after = [ "frappe-init.service" ];
    requires = [ "frappe-init.service" ];
  };

  mkFrappeService =
    {
      description,
      execStart,
      after ? [ ],
      requires ? [ ],
    }:
    {
      inherit description;
      after = [ "network.target" ] ++ after;
      inherit requires;
      wantedBy = [ "multi-user.target" ];
      environment = commonEnv;
      serviceConfig = {
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = sitesPath;
        StateDirectory = "frappe";
        ExecStart = execStart;
        Restart = "always";
        RestartSec = "5";
      };
    };

  workerServices = lib.listToAttrs (
    map (queue: {
      name = "frappe-worker-${queue}";
      value = mkFrappeService (
        {
          description = "Frappe background worker (${queue} queue)";
          execStart = mkExec "worker-${queue}" "${cfg.pythonEnv}/bin/bench worker --queue ${queue}";
        }
        // dependsOnInit
      );
    }) cfg.workers
  );

in
{
  options.services.frappe = {
    enable = mkEnableOption "Frappe bench production deployment (systemd)";

    benchRoot = mkOption {
      type = types.package;
      description = "The assembled /bench tree derivation (frappe-nix packages.benchRoot).";
    };

    pythonEnv = mkOption {
      type = types.package;
      description = "The production Python virtualenv (frappe-nix packages.prodPythonEnv).";
    };

    nodejs = mkOption {
      type = types.package;
      default = pkgs.nodejs_24;
      description = "Node.js package used to run socketio.";
    };

    defaultSite = mkOption {
      type = types.str;
      default = "";
      description = "Default site (FRAPPE_SITE). Empty for multi-tenancy.";
      example = "mysite.localhost";
    };

    user = mkOption {
      type = types.str;
      default = "frappe";
      description = "User the Frappe services run as.";
    };

    group = mkOption {
      type = types.str;
      default = "frappe";
      description = "Group the Frappe services run as.";
    };

    web = {
      port = mkOption {
        type = types.port;
        default = 8000;
        description = "Gunicorn listen port.";
      };
      workers = mkOption {
        type = types.int;
        default = 4;
        description = "Number of gunicorn workers.";
      };
    };

    socketio.port = mkOption {
      type = types.port;
      default = 9000;
      description = "SocketIO listen port.";
    };

    workers = mkOption {
      type = types.listOf types.str;
      default = [
        "default"
        "short"
        "long"
      ];
      description = "Background worker queues to run (one systemd service each).";
    };

    database = {
      createLocally = mkEnableOption "a local MariaDB instance for Frappe";
      package = mkOption {
        type = types.package;
        default = pkgs.mariadb;
        description = "MariaDB package (used for the client library on LD_LIBRARY_PATH).";
      };
      host = mkOption {
        type = types.str;
        default = "127.0.0.1";
        description = "Database host.";
      };
      port = mkOption {
        type = types.port;
        default = 3306;
        description = "Database port.";
      };
      socket = mkOption {
        type = types.str;
        default = "/run/mysqld/mysqld.sock";
        description = "Database unix socket (empty to disable socket auth).";
      };
    };

    redis = {
      createLocally = mkEnableOption "a local Redis instance for Frappe";
      port = mkOption {
        type = types.port;
        default = 13000;
        description = "Port for the locally-created Redis instance.";
      };
      cacheUrl = mkOption {
        type = types.str;
        default = "redis://127.0.0.1:13000";
        description = "Redis cache URL.";
      };
      queueUrl = mkOption {
        type = types.str;
        default = "redis://127.0.0.1:13000";
        description = "Redis queue URL.";
      };
      socketioUrl = mkOption {
        type = types.str;
        default = "redis://127.0.0.1:13000";
        description = "Redis socketio URL.";
      };
    };

    nginx = {
      enable = mkEnableOption "an nginx reverse proxy + static asset vhost for Frappe";
    };

    extraEnv = mkOption {
      type = types.attrsOf types.str;
      default = { };
      description = "Additional environment variables for the Frappe services.";
    };
  };

  config = mkIf cfg.enable (mkMerge [
    {
      users.users = mkIf (cfg.user == "frappe") {
        frappe = {
          isSystemUser = true;
          group = cfg.group;
          home = "/var/lib/frappe";
          description = "Frappe service user";
        };
      };
      users.groups = mkIf (cfg.group == "frappe") {
        frappe = { };
      };

      systemd.services = {
        # Oneshot init: seed the mutable sites/ dir from benchRoot.
        frappe-init = {
          description = "Frappe bench init (seed sites state)";
          wantedBy = [ "multi-user.target" ];
          serviceConfig = {
            Type = "oneshot";
            RemainAfterExit = true;
            User = cfg.user;
            Group = cfg.group;
            StateDirectory = "frappe";
            ExecStart = seedSites;
          };
        };

        frappe-web = mkFrappeService (
          {
            description = "Frappe web server (gunicorn)";
            execStart = mkExec "web" ''
              ${cfg.pythonEnv}/bin/gunicorn \
                --bind 0.0.0.0:${toString cfg.web.port} \
                --workers ${toString cfg.web.workers} \
                --max-requests 5000 \
                --max-requests-jitter 500 \
                --timeout 120 \
                --preload \
                --graceful-timeout 30 \
                --keep-alive 5 \
                --access-logfile - \
                --error-logfile - \
                frappe.app:application'';
          }
          // dependsOnInit
        );

        frappe-scheduler = mkFrappeService (
          {
            description = "Frappe scheduler";
            execStart = mkExec "scheduler" "${cfg.pythonEnv}/bin/bench schedule";
          }
          // dependsOnInit
        );

        frappe-socketio = mkFrappeService (
          {
            description = "Frappe SocketIO realtime server";
            execStart = mkExec "socketio" "${cfg.nodejs}/bin/node ${benchDir}/apps/frappe/socketio.js";
          }
          // dependsOnInit
        );
      }
      // workerServices;
    }

    (mkIf cfg.database.createLocally {
      services.mysql = {
        enable = true;
        package = cfg.database.package;
        settings.mysqld = {
          character-set-server = "utf8mb4";
          collation-server = "utf8mb4_unicode_ci";
          skip-character-set-client-handshake = true;
          innodb-read-only-compressed = "OFF";
        };
      };
    })

    (mkIf cfg.redis.createLocally {
      services.redis.servers.frappe = {
        enable = true;
        port = cfg.redis.port;
        bind = "127.0.0.1";
      };
    })

    (mkIf cfg.nginx.enable {
      services.nginx = {
        enable = true;
        recommendedProxySettings = true;
        recommendedGzipSettings = true;
        virtualHosts.${if cfg.defaultSite != "" then cfg.defaultSite else "_"} = {
          default = true;
          root = "${benchDir}/sites";
          locations = {
            "/assets/" = {
              extraConfig = ''
                try_files $uri =404;
                add_header Cache-Control "max-age=31536000";
              '';
            };
            "/socket.io" = {
              proxyPass = "http://127.0.0.1:${toString cfg.socketio.port}";
              proxyWebsockets = true;
            };
            "/" = {
              proxyPass = "http://127.0.0.1:${toString cfg.web.port}";
            };
          };
        };
      };
    })
  ]);
}
