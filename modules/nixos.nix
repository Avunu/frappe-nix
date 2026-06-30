# NixOS systemd service module for Frappe bench production deployment.
#
# Multi-tenant: each site gets its own systemd unit instances and config.
# Stack (python, node, apps, assets) comes from the bench package's passthru —
# the module never takes pythonEnv/nodejs/benchRoot as options.
#
# Example:
#   services.frappe = {
#     enable  = true;
#     package = inputs.bench.packages.x86_64-linux.default;
#     sites."mysite.example.com" = {
#       enable = true;
#       database.createLocally = true;
#       database.passwordFile  = config.age.secrets.db-pass.path;
#       encryptionKeyFile      = config.age.secrets.enc-key.path;
#       nginx.enable           = true;
#     };
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
    mapAttrs
    mapAttrsToList
    nameValuePair
    filterAttrs
    concatStringsSep
    optionalAttrs
    optionalString
    ;

  cfg = config.services.frappe;

  enabledSites = filterAttrs (_: s: s.enable) cfg.sites;

  # Resolve the effective package for a site (per-site override or top-level).
  sitePackage = siteCfg: if siteCfg.package != null then siteCfg.package else cfg.package;

  # Derive interpreter paths from a bench package's passthru.
  pkgBenchDir = pkg: "${pkg}/bench";
  pkgPythonEnv = pkg: pkg.passthru.pythonEnv;
  pkgNodejs = pkg: pkg.passthru.nodejs;
  pkgAppsPath = pkg: pkg.passthru.appsPath (pkgBenchDir pkg);

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

  # Env vars that depend only on the resolved package (interpreters, SSL,
  # library path) and not on any particular site's config. Shared by every
  # systemd unit's `environment=` (via siteEnv) and by the imperative
  # `bench` CLI wrapper, so a var like GIT_PYTHON_REFRESH only needs setting
  # once instead of being kept in sync by hand in two places.
  mkCoreEnv = pkg: {
    FRAPPE_BENCH_ROOT = pkgBenchDir pkg;
    DEV_SERVER = "0";
    FRAPPE_ENV_TYPE = "production";
    SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
    LD_LIBRARY_PATH = libraryPath;
    # GitPython probes `git` on import; skip it, we ship git on PATH ourselves.
    GIT_PYTHON_REFRESH = "none";
  };

  # Per-site environment. The package (and therefore interpreters) can differ
  # per site, so this is a function of (siteName, siteCfg).
  siteEnv = name: siteCfg:
    let
      pkg = sitePackage siteCfg;
    in
    mkCoreEnv pkg
    // {
      SITES_PATH = "${siteCfg.siteDir}/sites";
      FRAPPE_SITE = name;
      FRAPPE_STREAM_LOGGING = "1";
      FRAPPE_TUNE_GC = "1";

      FRAPPE_DB_HOST = siteCfg.database.host;
      FRAPPE_DB_PORT = toString siteCfg.database.port;
      FRAPPE_DB_TYPE = "mariadb";

      FRAPPE_REDIS_CACHE = siteCfg.redis.cacheUrl;
      FRAPPE_REDIS_QUEUE = siteCfg.redis.queueUrl;
      FRAPPE_REDIS_SOCKETIO = siteCfg.redis.socketioUrl;
    }
    // optionalAttrs (siteCfg.database.socket != "") {
      FRAPPE_DB_SOCKET = siteCfg.database.socket;
    }
    // cfg.extraEnv;

  # Packages on PATH for every Frappe service (git needed by GitPython).
  servicePath = [ pkgs.git ];

  # Secret-bearing files for a site's init unit, keyed for both
  # systemd LoadCredential= and the jq merge expression below. Source files
  # (e.g. agenix's /run/agenix/*) are typically root:root 0400 — LoadCredential
  # has systemd (root) read them and re-expose them under $CREDENTIALS_DIRECTORY
  # owned by the unit's own User/Group, so the unit never needs direct access
  # to the original file.
  mkSiteCredentials = siteCfg:
    (lib.optional (siteCfg.database.passwordFile != null)
      { file = siteCfg.database.passwordFile; key = "db_password"; })
    ++ (lib.optional (siteCfg.encryptionKeyFile != null)
      { file = siteCfg.encryptionKeyFile; key = "encryption_key"; })
    ++ lib.imap1 (i: f: { file = f; key = "extra_config_${toString i}"; })
      siteCfg.extraConfigFiles;

  # Script wrapper that sets PYTHONPATH from the package's apps and execs.
  mkExec = pkg: name: cmd:
    let benchDir = pkgBenchDir pkg; in
    pkgs.writeShellScript "frappe-${name}" ''
      set -euo pipefail
      export PYTHONPATH="${pkgAppsPath pkg}"
      cd ${benchDir}
      exec ${cmd}
    '';

  # Per-site init script: assemble runtime bench tree, symlink assets,
  # seed sites dir, and synthesize site_config.json (merging secrets).
  mkSiteInit = name: siteCfg:
    let
      pkg = sitePackage siteCfg;
      benchDir = pkgBenchDir pkg;
      sitesPath = "${siteCfg.siteDir}/sites";
      runtimeBenchDir = "${siteCfg.siteDir}/bench";

      # Base site_config.json from Nix options (no secrets).
      baseConfig = {
        db_host = siteCfg.database.host;
        db_port = siteCfg.database.port;
        db_type = "mariadb";
        db_name = siteCfg.database.name;
        db_user = siteCfg.database.user;
        redis_cache = siteCfg.redis.cacheUrl;
        redis_queue = siteCfg.redis.queueUrl;
        redis_socketio = siteCfg.redis.socketioUrl;
      }
      // optionalAttrs (siteCfg.database.socket != "") {
        db_socket = siteCfg.database.socket;
      }
      // siteCfg.extraConfig;

      baseConfigFile = pkgs.writeText "site-config-${name}.json"
        (builtins.toJSON baseConfig);

      # Secrets merged via systemd LoadCredential — see mkSiteCredentials.
      secretFiles =
        (lib.optional (siteCfg.database.passwordFile != null)
          { file = siteCfg.database.passwordFile; key = "db_password"; })
        ++ (lib.optional (siteCfg.encryptionKeyFile != null)
          { file = siteCfg.encryptionKeyFile; key = "encryption_key"; });

    in
    pkgs.writeShellScript "frappe-init-${name}" ''
      set -euo pipefail

      # Assemble runtime bench tree.
      mkdir -p ${runtimeBenchDir}/logs
      ln -sfn ${benchDir}/apps   ${runtimeBenchDir}/apps
      ln -sfn ${benchDir}/env    ${runtimeBenchDir}/env
      ln -sfn ${benchDir}/config ${runtimeBenchDir}/config
      ln -sfn ${sitesPath}       ${runtimeBenchDir}/sites

      # Seed sites directory.
      mkdir -p ${sitesPath}
      for f in apps.json apps.txt common_site_config.json; do
        if [ ! -e "${sitesPath}/$f" ] && [ -e "${benchDir}/sites/$f" ]; then
          cp "${benchDir}/sites/$f" "${sitesPath}/$f"
        fi
      done

      # Symlink compiled assets from the package.
      if [ -d "${benchDir}/sites/assets" ]; then
        ln -sfn ${benchDir}/sites/assets ${sitesPath}/assets
      fi

      # Create site directory.
      mkdir -p ${sitesPath}/${name}

      # Synthesize site_config.json: base config + secrets + extra files.
      # Secret values are read from $CREDENTIALS_DIRECTORY (populated by
      # systemd's LoadCredential= on this unit) rather than the original
      # source paths, so this script never needs read access to those.
      ${let
        # Read secret values into env vars.
        readSecrets = concatStringsSep "\n" (
          map (s: ''SECRET_${lib.toUpper (builtins.replaceStrings ["-" "."] ["_" "_"] s.key)}="$(cat "$CREDENTIALS_DIRECTORY/${s.key}")"'')
            secretFiles
        );
        exportSecrets = concatStringsSep "\n" (
          map (s: ''export SECRET_${lib.toUpper (builtins.replaceStrings ["-" "."] ["_" "_"] s.key)}'')
            secretFiles
        );

        # Build jq expression.
        jqExpr = let
          base = ".";
          withSecrets = concatStringsSep " | " (
            map (s: ''. + {"${s.key}": $ENV.SECRET_${lib.toUpper (builtins.replaceStrings ["-" "."] ["_" "_"] s.key)}}'')
              secretFiles
          );
          extraMerges = lib.imap1 (i: _f: ". * $extra${toString i}") siteCfg.extraConfigFiles;
        in
          concatStringsSep " | " (
            [ base ]
            ++ lib.optional (secretFiles != []) withSecrets
            ++ extraMerges
          );

        extraSlurpArgs = concatStringsSep " " (
          lib.imap1 (i: _f: ''--slurpfile extra${toString i} "$CREDENTIALS_DIRECTORY/extra_config_${toString i}"'')
            siteCfg.extraConfigFiles
        );
      in ''
        ${readSecrets}
        ${exportSecrets}
        ${pkgs.jq}/bin/jq '${jqExpr}' ${extraSlurpArgs} ${baseConfigFile} \
          > ${sitesPath}/${name}/site_config.json
        chmod 0600 ${sitesPath}/${name}/site_config.json
      ''}
    '';

  # Generate all systemd services for a single site.
  mkSiteServices = name: siteCfg:
    let
      pkg = sitePackage siteCfg;
      pyEnv = pkgPythonEnv pkg;
      node = pkgNodejs pkg;
      benchDir = pkgBenchDir pkg;
      env = siteEnv name siteCfg;
      benchBin = "${pyEnv}/bin/bench";

      initName = "frappe-init-${name}";
      dependsOn = {
        after = [ "${initName}.service" ];
        requires = [ "${initName}.service" ];
      };

      mkService = { description, execStart, extra ? {} }:
        {
          inherit description;
          after = [ "network.target" ] ++ (extra.after or []);
          requires = extra.requires or [];
          wantedBy = [ "multi-user.target" ];
          environment = env;
          path = servicePath;
          serviceConfig = {
            User = cfg.user;
            Group = cfg.group;
            WorkingDirectory = "${siteCfg.siteDir}/sites";
            ExecStart = execStart;
            Restart = "always";
            RestartSec = "5";
          };
        };

      workerUnits = lib.listToAttrs (
        map (queue: nameValuePair "frappe-worker-${queue}-${name}" (
          mkService {
            description = "Frappe worker (${queue}) for ${name}";
            execStart = mkExec pkg "worker-${queue}-${name}"
              "${benchBin} worker --queue ${queue}";
            extra = dependsOn;
          }
        )) cfg.workers
      );
    in
    {
      "${initName}" = {
        description = "Frappe init for site ${name}";
        wantedBy = [ "multi-user.target" ];
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          User = cfg.user;
          Group = cfg.group;
          # Source secret files (e.g. agenix's root:root 0400 outputs) are read
          # by systemd (root) and re-exposed under $CREDENTIALS_DIRECTORY owned
          # by cfg.user — the unit never needs direct read access to them.
          LoadCredential = map (s: "${s.key}:${s.file}") (mkSiteCredentials siteCfg);
          ExecStart = mkSiteInit name siteCfg;
        };
      };

      "frappe-web-${name}" = mkService {
        description = "Frappe web (gunicorn) for ${name}";
        execStart = mkExec pkg "web-${name}" ''
          ${pyEnv}/bin/gunicorn \
            --bind 0.0.0.0:${toString siteCfg.web.port} \
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
        extra = dependsOn;
      };

      "frappe-scheduler-${name}" = mkService {
        description = "Frappe scheduler for ${name}";
        execStart = mkExec pkg "scheduler-${name}" "${benchBin} schedule";
        extra = dependsOn;
      };

      "frappe-socketio-${name}" = mkService {
        description = "Frappe SocketIO for ${name}";
        execStart = mkExec pkg "socketio-${name}"
          "${node}/bin/node ${benchDir}/apps/frappe/socketio.js";
        extra = dependsOn;
      };
    } // workerUnits;

  # bench CLI wrapper — defaults FRAPPE_SITE to the sole enabled site.
  # Uses the top-level package for interpreter discovery.
  siteNames = builtins.attrNames enabledSites;
  singleSite = if builtins.length siteNames == 1 then builtins.head siteNames else null;

  benchCli =
    let
      pkg = cfg.package;
      benchDir = pkgBenchDir pkg;
      pyEnv = pkgPythonEnv pkg;
      benchBin = "${pyEnv}/bin/bench";
      coreEnvExports = concatStringsSep "\n" (
        mapAttrsToList (k: v: "export ${k}=${lib.escapeShellArg v}") (mkCoreEnv pkg)
      );
    in
    pkgs.writeShellScriptBin "bench" ''
      set -euo pipefail

      ${optionalString (singleSite != null) ''
        export FRAPPE_SITE=''${FRAPPE_SITE:-${singleSite}}
      ''}

      export PYTHONPATH="${pkgAppsPath pkg}"
      ${coreEnvExports}

      # Resolve SITES_PATH and runtime bench dir from the site's siteDir.
      RUNTIME_BENCH=""
      ${concatStringsSep "\n" (mapAttrsToList (name: siteCfg: ''
        if [ "''${FRAPPE_SITE:-}" = "${name}" ]; then
          export SITES_PATH="${siteCfg.siteDir}/sites"
          RUNTIME_BENCH="${siteCfg.siteDir}/bench"
        fi
      '') enabledSites)}
      export SITES_PATH=''${SITES_PATH:-/var/lib/frappe/sites}
      RUNTIME_BENCH=''${RUNTIME_BENCH:-/var/lib/frappe/bench}

      # Ensure the mutable runtime bench tree exists (mirrors frappe-init).
      mkdir -p "$RUNTIME_BENCH"/logs
      ln -sfn ${benchDir}/apps   "$RUNTIME_BENCH"/apps   2>/dev/null || true
      ln -sfn ${benchDir}/env    "$RUNTIME_BENCH"/env    2>/dev/null || true
      ln -sfn ${benchDir}/config "$RUNTIME_BENCH"/config 2>/dev/null || true
      ln -sfn "$SITES_PATH"      "$RUNTIME_BENCH"/sites  2>/dev/null || true

      SITE_FLAG=""
      if [ -n "''${FRAPPE_SITE:-}" ]; then
        SITE_FLAG="--site $FRAPPE_SITE"
      fi

      cd "$RUNTIME_BENCH"

      case "''${1:-}" in
        restore)
          shift
          if [ -z "''${1:-}" ]; then
            echo "Usage: bench restore <sql-file-path> [options]"
            exit 1
          fi
          SQL_FILE="$1"; shift
          exec ${benchBin} $SITE_FLAG restore "$SQL_FILE" "$@"
          ;;
        migrate|console|clear-cache)
          CMD="$1"; shift
          exec ${benchBin} $SITE_FLAG "$CMD" "$@"
          ;;
        *)
          exec ${benchBin} "$@"
          ;;
      esac
    '';

  # Per-site nginx virtualHost config.
  mkSiteNginxVhost = name: siteCfg:
    let
      pkg = sitePackage siteCfg;
      benchDir = pkgBenchDir pkg;
    in
    {
      root = "${siteCfg.siteDir}/sites";
      locations = {
        "/assets/" = {
          extraConfig = ''
            try_files $uri =404;
            add_header Cache-Control "max-age=31536000";
          '';
        };
        "/socket.io" = {
          proxyPass = "http://127.0.0.1:${toString siteCfg.socketio.port}";
          proxyWebsockets = true;
        };
        "/" = {
          proxyPass = "http://127.0.0.1:${toString siteCfg.web.port}";
        };
      };
    };

  # Site submodule option definition.
  siteModule = types.submodule ({ name, ... }: {
    options = {
      enable = mkEnableOption "this Frappe site";

      package = mkOption {
        type = types.nullOr types.package;
        default = null;
        description = "Per-site bench package override. Defaults to services.frappe.package.";
      };

      siteDir = mkOption {
        type = types.str;
        default = "/var/lib/frappe/${name}";
        description = "State directory for this site.";
      };

      web.port = mkOption {
        type = types.port;
        default = 8000;
        description = "Gunicorn listen port for this site.";
      };

      socketio.port = mkOption {
        type = types.port;
        default = 9000;
        description = "SocketIO listen port for this site.";
      };

      database = {
        createLocally = mkEnableOption "a local MariaDB database for this site";
        host = mkOption {
          type = types.str;
          default = "127.0.0.1";
        };
        port = mkOption {
          type = types.port;
          default = 3306;
        };
        socket = mkOption {
          type = types.str;
          default = "/run/mysqld/mysqld.sock";
          description = "Database unix socket (empty to disable socket auth).";
        };
        name = mkOption {
          type = types.str;
          default = builtins.replaceStrings ["." "-"] ["_" "_"] name;
          description = "Database name. Defaults to site name with dots/hyphens replaced by underscores.";
        };
        user = mkOption {
          type = types.str;
          default = builtins.replaceStrings ["." "-"] ["_" "_"] name;
          description = "Database user. Defaults to site name with dots/hyphens replaced by underscores.";
        };
        passwordFile = mkOption {
          type = types.nullOr types.path;
          default = null;
          description = "File containing the database password. Merged into site_config.json at activation.";
        };
      };

      redis = {
        cacheUrl = mkOption {
          type = types.str;
          default = "redis://127.0.0.1:13000";
        };
        queueUrl = mkOption {
          type = types.str;
          default = "redis://127.0.0.1:13000";
        };
        socketioUrl = mkOption {
          type = types.str;
          default = "redis://127.0.0.1:13000";
        };
      };

      encryptionKeyFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "File containing the Frappe encryption key. Merged into site_config.json at activation.";
      };

      extraConfig = mkOption {
        type = types.attrsOf types.anything;
        default = {};
        description = "Extra keys merged into the base site_config.json (Nix values, no secrets).";
      };

      extraConfigFiles = mkOption {
        type = types.listOf types.path;
        default = [];
        description = "JSON files deep-merged into site_config.json at activation (for secrets).";
      };

      nginx.enable = mkEnableOption "an nginx virtualHost for this site";
    };
  });

in
{
  options.services.frappe = {
    enable = mkEnableOption "Frappe bench production deployment (systemd)";

    package = mkOption {
      type = types.package;
      description = "Default bench package (builtBench). Sites inherit this unless they set their own.";
    };

    user = mkOption {
      type = types.str;
      default = "frappe";
    };

    group = mkOption {
      type = types.str;
      default = "frappe";
    };

    web.workers = mkOption {
      type = types.int;
      default = 4;
      description = "Number of gunicorn workers (shared across sites).";
    };

    workers = mkOption {
      type = types.listOf types.str;
      default = [ "default" "short" "long" ];
      description = "Background worker queues to run per site.";
    };

    database = {
      createLocally = mkEnableOption "a local MariaDB instance (aggregate: enabled if any site requests it)";
      package = mkOption {
        type = types.package;
        default = pkgs.mariadb;
        description = "MariaDB package (client library on LD_LIBRARY_PATH).";
      };
    };

    redis = {
      createLocally = mkEnableOption "a local Redis instance for Frappe";
      port = mkOption {
        type = types.port;
        default = 13000;
      };
    };

    extraEnv = mkOption {
      type = types.attrsOf types.str;
      default = {};
      description = "Additional environment variables for all Frappe services.";
    };

    sites = mkOption {
      type = types.attrsOf siteModule;
      default = {};
      description = "Per-site configuration. Each key is the site name (FQDN).";
    };
  };

  config = mkIf cfg.enable (mkMerge [
    {
      environment.systemPackages = [ benchCli pkgs.git ];

      users.users = mkIf (cfg.user == "frappe") {
        frappe = {
          isSystemUser = true;
          group = cfg.group;
          home = "/var/lib/frappe";
          description = "Frappe service user";
        };
      };
      users.groups = mkIf (cfg.group == "frappe") {
        frappe = {};
      };

      # Generate per-site systemd services.
      systemd.services = lib.mkMerge (
        mapAttrsToList (name: siteCfg: mkSiteServices name siteCfg) enabledSites
      );

      # Per-site tmpfiles rules to ensure siteDir exists with correct ownership.
      systemd.tmpfiles.rules = mapAttrsToList
        (name: siteCfg: "d ${siteCfg.siteDir} 0750 ${cfg.user} ${cfg.group} -")
        enabledSites;
    }

    # Aggregate database.createLocally: enable MariaDB if any site requests it
    # or if the top-level toggle is on.
    (mkIf (cfg.database.createLocally ||
           lib.any (s: s.database.createLocally) (builtins.attrValues enabledSites)) {
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

    # Per-site nginx virtualHosts.
    (mkIf (lib.any (s: s.nginx.enable) (builtins.attrValues enabledSites)) {
      services.nginx = {
        enable = true;
        recommendedProxySettings = true;
        recommendedGzipSettings = true;
        virtualHosts = mapAttrs (name: siteCfg: mkSiteNginxVhost name siteCfg)
          (filterAttrs (_: s: s.nginx.enable) enabledSites);
      };
    })
  ]);
}
