# services.frappe-nix.sites.<site>.settings as an RFC 42 block, at evaluation
# (python-devops-nix docs/conventions.md, Framework config blocks):
#
#   * `settings` shows the keys the module computes from the typed options, so
#     it is the site_config.json the init unit writes, less the secrets;
#   * a host's `settings.<key>` replaces a computed (mkDefault) key without
#     mkForce, and nested attribute sets from several definitions merge key by
#     key instead of the last one replacing the rest;
#   * a db_name other than database.name, or a db_type other than mariadb,
#     still evaluates (1.x) but warns, naming what breaks; an equal value is
#     silent;
#   * a value JSON cannot hold (a function) is refused;
#   * the declared host_name is null and left out of the file when unset.
#
# Pure evaluation against a stub bench that is never built, like
# tests/production-unit.nix.
{
  self,
  pkgs,
  checks,
}:

let
  inherit (pkgs) lib;

  site = "erp.example.com";

  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          pythonEnv = pkgs.emptyDirectory;
          inherit (pkgs) nodejs;
          appsPath = _: "/stub/apps";
        };
      }
      ''
        mkdir -p $out/bench
      '';

  # The NixOS configuration with `top` over services.frappe-nix, and each of
  # `extra` a further module for the site, so a setting can be defined twice
  # the way two host modules would.
  eval =
    top: extra:
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      inherit (pkgs.stdenv.hostPlatform) system;
      modules = [
        self.nixosModules.default
        {
          nixpkgs.pkgs = pkgs;
          system.stateVersion = lib.trivial.release;
          boot.isContainer = true;
          services.frappe-nix = {
            enable = true;
            package = stubBench;
            redis.createLocally = true;
            sites.${site} = {
              enable = true;
              database.createLocally = true;
              nginx.enable = true;
            };
          }
          // top;
        }
      ]
      ++ map (m: { services.frappe-nix.sites.${site} = m; }) extra;
    }).config;

  settingsOf = extra: (eval { } extra).services.frappe-nix.sites.${site}.settings;

  # The module's settings warnings for a configuration, as the words that
  # matter: which key, and that it names the snapshot and 2.0.0.
  settingsWarnings =
    extra:
    map (w: {
      key = builtins.head (builtins.match ".*settings\\.(db_[a-z]+) is.*" w);
      namesSnapshot = lib.hasInfix "snapshot" w;
      namesTwo = lib.hasInfix "error in frappe-nix 2.0.0" w;
    }) (lib.filter (lib.hasInfix ".settings.db_") (eval { } extra).warnings);

  # Whether evaluating the whole of `settings` succeeds: false for a type error
  # or a conflict.
  evaluates = extra: (builtins.tryEval (builtins.deepSeq (settingsOf extra) true)).success;

  defaults = settingsOf [ ];

  overridden = settingsOf [
    {
      settings = {
        db_host = "10.0.0.5";
        socketio_port = 9100;
        redis_cache = "redis://cache:6379";
      };
    }
  ];

  nested = settingsOf [
    { settings.workers.custom.timeout = 8000; }
    {
      settings.workers.custom.background_workers = 2;
      settings.workers.long.timeout = 1500;
    }
  ];

  # The rename alias still lands in the same block.
  aliased = settingsOf [ { extraConfig.host_name = "https://${site}"; } ];

  # The realtime socket key, under the split units and the unified runtime.
  udsUnder =
    runtime:
    (eval { runtime.enable = runtime; } [
      { socketio.socketPath = "/run/frappe-erp/socketio.sock"; }
    ]).services.frappe-nix.sites.${site}.settings.socketio_uds or null;
in
{
  settings-nixos = checks.eval "settings-nixos" {
    expected = {
      computed = {
        db_host = "127.0.0.1";
        db_name = "erp_example_com";
        db_port = 3306;
        db_socket = "/run/mysqld/mysqld.sock";
        db_type = "mariadb";
        db_user = "erp_example_com";
        host_name = null;
        redis_cache = "redis://127.0.0.1:13000";
        redis_queue = "redis://127.0.0.1:13000";
        redis_socketio = "redis://127.0.0.1:13000";
        socketio_port = 9000;
      };
      override = {
        db_host = "10.0.0.5";
        socketio_port = 9100;
        redis_cache = "redis://cache:6379";
        # The keys it did not set keep their computed value.
        redis_queue = "redis://127.0.0.1:13000";
      };
      nested = {
        custom = {
          background_workers = 2;
          timeout = 8000;
        };
        long.timeout = 1500;
      };
      aliasedHostName = "https://${site}";
      socketioUds = {
        split = "/run/frappe-erp/socketio.sock";
        runtime = null;
      };
      evaluates = {
        otherDbName = true;
        function = false;
        nestedFunction = false;
      };
      otherDbNameValue = "other";
      warnings = {
        defaults = [ ];
        sameDbName = [ ];
        sameDbType = [ ];
        otherDbName = [
          {
            key = "db_name";
            namesSnapshot = true;
            namesTwo = true;
          }
        ];
        otherDbType = [
          {
            key = "db_type";
            namesSnapshot = true;
            namesTwo = true;
          }
        ];
      };
    };
    actual = {
      computed = defaults;
      override = {
        inherit (overridden)
          db_host
          socketio_port
          redis_cache
          redis_queue
          ;
      };
      nested = nested.workers;
      aliasedHostName = aliased.host_name;
      socketioUds = {
        split = udsUnder false;
        runtime = udsUnder true;
      };
      evaluates = {
        otherDbName = evaluates [ { settings.db_name = "other"; } ];
        function = evaluates [ { settings.hook = x: x; } ];
        nestedFunction = evaluates [ { settings.workers.custom.timeout = _: 1; } ];
      };
      otherDbNameValue = (settingsOf [ { settings.db_name = "other"; } ]).db_name;
      warnings = {
        defaults = settingsWarnings [ ];
        sameDbName = settingsWarnings [ { settings.db_name = "erp_example_com"; } ];
        sameDbType = settingsWarnings [ { settings.db_type = "mariadb"; } ];
        otherDbName = settingsWarnings [ { settings.db_name = "other"; } ];
        otherDbType = settingsWarnings [ { settings.db_type = "postgres"; } ];
      };
    };
  };
}
