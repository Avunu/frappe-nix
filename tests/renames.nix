# Track B's renames (python-devops-nix PLAN.md, Track B; CONVENTIONS.md): each
# old name still reaches the new option, and is reported for the deprecation
# warning. At evaluation; nothing is built.
#
#   renames-devenv   frappe-nix.benchName → name, siteName → defaultSite; the
#                    new names set devenv's `name` and FRAPPE_SITE, and the old
#                    ones are listed in frappe-nix.deprecations (lib.warn).
#   renames-option-tree  the development shell option tree (CONVENTIONS.md):
#                    runtime.dev → watch.python, assets.reassert → watch.reassert,
#                    devguard.mail.{host,port,httpPort,pop3.*} → mailpit.*, each
#                    reaching the new option and listed for its warning; and the
#                    new defaults: Mailpit runs with the mail guard off and orders
#                    the server after it, the runtime always serves /assets and
#                    reloads only with watch.python, and ports.* are per service.
#   renames-nixos    services.frappe → services.frappe-nix, as a whole tree, and
#                    sites.<site>.extraConfig{,Files} → settings{,Files}: the
#                    old names set the same units (nested mkIf and mkDefault
#                    included) and each adds a NixOS warning.
{
  self,
  pkgs,
  inputs,
}:

let
  inherit (pkgs) lib;
  checks = inputs.python-devops-nix.lib.checks.mk {
    inherit pkgs;
    product = "frappe-nix";
  };

  # A bench through the real module: tests/standards/fixtures/bench-flake.nix.
  benchWith =
    frappe-nix:
    import ./standards/fixtures/bench-flake.nix { inherit self pkgs; } (_: {
      frappe-nix = {
        enable = true;
        workspaceRoot = ./standards/fixtures/bench-dev-group;
        # The fixture's requires-python.
        python = pkgs.python314;
      }
      // frappe-nix;
    });

  # The option paths named in frappe-nix.deprecations, sorted.
  deprecated =
    config:
    lib.sort builtins.lessThan (
      map (
        message: builtins.head (builtins.match "The option `([^']*)'.*" message)
      ) config.frappe-nix.deprecations
    );

  # services.frappe-nix (or what `path` names) on a stub bench that is never
  # built, as tests/offline-migrate-unit.nix does.
  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          inherit (pkgs) nodejs;
          pythonEnv = pkgs.emptyDirectory;
          appsPath = _: "/stub/apps";
        };
      }
      ''
        mkdir -p $out/bench
      '';
  site = "erp.example.com";
  nixosWith =
    path: module:
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      inherit (pkgs.stdenv.hostPlatform) system;
      modules = [
        self.nixosModules.default
        {
          nixpkgs.pkgs = pkgs;
          system.stateVersion = lib.trivial.release;
        }
        (lib.setAttrByPath path module)
      ];
    }).config;
  # What a deployment sees: the frappe units, with their ExecStart and
  # environment, and the warnings.
  units =
    config:
    let
      names = lib.filter (lib.hasPrefix "frappe") (lib.attrNames config.systemd.services);
    in
    {
      units = lib.genAttrs names (n: {
        inherit (config.systemd.services.${n}) environment;
        ExecStart = config.systemd.services.${n}.serviceConfig.ExecStart or null;
      });
      # "<old> -> <new>", for each rename warning.
      warnings = lib.sort builtins.lessThan (
        map (
          w: lib.concatStringsSep " -> " (builtins.match "The option `([^']*)'.* renamed to `([^']*)'.*" w)
        ) (lib.filter (lib.hasInfix "has been renamed") config.warnings)
      );
    };
  production = productionWith {
    settings.host_name = "https://${site}";
    settings.workers.custom.timeout = 8000;
    settingsFiles = [ "/run/secrets/site-config" ];
  };
  # The names before, defined twice as a consumer does, so the alias merges.
  productionBefore = productionWith {
    extraConfig.host_name = "https://${site}";
    extraConfig.workers.custom.timeout = 8000;
    extraConfigFiles = [ "/run/secrets/site-config" ];
  };
  productionWith = siteSettings: {
    enable = true;
    package = stubBench;
    redis.createLocally = true;
    # A nested mkIf and a mkDefault: the alias has to carry both to the leaf.
    runtime.webThreads = lib.mkIf true 3;
    logging.level = lib.mkDefault "info";
    sites.${site} = {
      enable = true;
      database.createLocally = true;
      database.passwordFile = "/run/secrets/db-password";
      nginx.enable = true;
    }
    // siteSettings;
  };

  identity = config: {
    inherit (config.frappe-nix) name defaultSite;
    devenvName = config.devenv.shells.default.name;
    site = config.devenv.shells.default.env.FRAPPE_SITE or null;
    deprecated = deprecated config;
  };
in
{
  renames-devenv = checks.eval "renames-devenv" {
    expected = {
      old = {
        name = "renamed-bench";
        defaultSite = "old.localhost";
        devenvName = "renamed-bench";
        site = "old.localhost";
        deprecated = [
          "frappe-nix.benchName"
          "frappe-nix.siteName"
        ];
      };
      new = {
        name = "new-bench";
        defaultSite = "new.localhost";
        devenvName = "new-bench";
        site = "new.localhost";
        deprecated = [ ];
      };
    };
    actual = {
      old = identity (benchWith {
        benchName = "renamed-bench";
        siteName = "old.localhost";
      });
      new = identity (benchWith {
        name = "new-bench";
        defaultSite = "new.localhost";
      });
    };
  };

  renames-option-tree =
    let
      base = {
        name = "option-tree";
        ports.offset = 123;
      };
      old = benchWith (
        base
        // {
          runtime.dev = false;
          assets.reassert.debounceMs = 500;
          devguard.mail = {
            host = "127.0.0.2";
            port = 2525;
            httpPort = 2526;
            pop3 = {
              enable = true;
              port = 2527;
              user = "u";
              password = "p";
            };
          };
        }
      );
      new = benchWith (
        base
        // {
          watch.python = false;
          watch.reassert.debounceMs = 500;
          mailpit = {
            host = "127.0.0.2";
            port = 2525;
            httpPort = 2526;
            pop3 = {
              enable = true;
              port = 2527;
              user = "u";
              password = "p";
            };
          };
        }
      );
      defaults = benchWith base;
      guardOff = benchWith (base // { devguard.mail.enable = false; });
      forced = benchWith (base // { ports.smtp = lib.mkForce 2999; });
      tree = config: {
        inherit (config.frappe-nix) mailpit;
        python = config.frappe-nix.watch.python;
        debounceMs = config.frappe-nix.watch.reassert.debounceMs;
      };
      shell = config: config.devenv.shells.default;
      # The fixture's lock has no frappe-runtime, so its shell runs the split
      # processes: `bench serve` is the web server there.
      webExec = config: (shell config).processes.web.exec;
      devenvSource = builtins.readFile ../modules/devenv.nix;
      # Not lib.hasInfix: that is builtins.match ".*X.*", and on a 4000-line
      # file the regex engine's recursion overflows the C stack on some
      # builds of Nix ("stack overflow", no trace). tests/standards/runtime.nix
      # does the same.
      contains = needle: haystack: builtins.replaceStrings [ needle ] [ "" ] haystack != haystack;
    in
    checks.eval "renames-option-tree" {
      expected = {
        old = tree new // {
          deprecated = [
            "frappe-nix.assets.reassert.debounceMs"
            "frappe-nix.devguard.mail.host"
            "frappe-nix.devguard.mail.httpPort"
            "frappe-nix.devguard.mail.pop3.enable"
            "frappe-nix.devguard.mail.pop3.password"
            "frappe-nix.devguard.mail.pop3.port"
            "frappe-nix.devguard.mail.pop3.user"
            "frappe-nix.devguard.mail.port"
            "frappe-nix.runtime.dev"
          ];
        };
        new.deprecated = [ ];
        ports = {
          http = 8123;
          db = 3429;
          socketio = 9123;
          smtp = 19123;
          mailUi = 20123;
          pop3 = 21123;
          mailpit = {
            port = 19123;
            httpPort = 20123;
            pop3 = 21123;
          };
        };
        forcedSmtp = 2999;
        guardOff = {
          enable = true;
          process = true;
          webAfterMailpit = true;
        };
        watchPython = {
          defaultReloads = true;
          offReloads = false;
          runtimeServesAssetsAlways = true;
          runtimeDevFollowsWatchPython = true;
        };
      };
      actual = {
        old = tree old // {
          deprecated = deprecated old;
        };
        new.deprecated = deprecated new;
        ports = {
          inherit (defaults.frappe-nix.ports)
            http
            db
            socketio
            smtp
            mailUi
            pop3
            ;
          mailpit = {
            inherit (defaults.frappe-nix.mailpit) port httpPort;
            pop3 = defaults.frappe-nix.mailpit.pop3.port;
          };
        };
        forcedSmtp = forced.frappe-nix.mailpit.port;
        guardOff = {
          inherit (guardOff.frappe-nix.mailpit) enable;
          process = (shell guardOff).processes ? mailpit;
          webAfterMailpit = lib.elem "devenv:processes:mailpit" (shell guardOff).processes.web.after;
        };
        watchPython = {
          defaultReloads = !(lib.hasInfix "--noreload" (webExec defaults));
          offReloads = !(lib.hasInfix "--noreload" (webExec new));
          # The runtime's exec, as written in the module (no fixture locks it).
          runtimeServesAssetsAlways = contains "--serve-assets''" devenvSource;
          runtimeDevFollowsWatchPython = contains ''lib.optionalString cfg.watch.python " --dev"'' devenvSource;
        };
      };
    };

  renames-nixos = checks.eval "renames-nixos" {
    expected = {
      inherit (units (nixosWith [ "services" "frappe-nix" ] production)) units;
      warnings = [
        "services.frappe -> services.frappe-nix"
        ''services.frappe-nix.sites."${site}".extraConfig -> services.frappe-nix.sites."${site}".settings''
        ''services.frappe-nix.sites."${site}".extraConfigFiles -> services.frappe-nix.sites."${site}".settingsFiles''
      ];
      siteConfig = {
        host_name = "https://${site}";
        workers.custom.timeout = 8000;
      };
      credentials = [ "extra_config_1:/run/secrets/site-config" ];
      webThreads = 3;
      level = "info";
    };
    actual =
      let
        old = nixosWith [ "services" "frappe" ] productionBefore;
        oldSite = old.services.frappe-nix.sites.${site};
      in
      units old
      // {
        siteConfig = { inherit (oldSite.settings) host_name workers; };
        credentials = lib.filter (lib.hasPrefix "extra_config") (
          lib.toList old.systemd.services."frappe-init-${site}".serviceConfig.LoadCredential
        );
        webThreads = old.services.frappe-nix.runtime.webThreads;
        level = old.services.frappe-nix.logging.level;
      };
  };
}
