# The names 1.x renamed and kept as aliases (python-devops-nix
# docs/conventions.md, Renames and the 1.x line), refused since 2.0.0, each
# naming its replacement; and the names they moved to. At evaluation; nothing
# is built.
#
#   removed-devenv   frappe-nix.benchName, siteName, restore.withFiles and
#                    containers.aliases each put a message on
#                    frappe-nix.removals naming the new option, and the shell
#                    does not evaluate; the new names set devenv's `name` and
#                    FRAPPE_SITE. lib.overrides' 1.x no-ops are refused too.
#   option-tree      the development shell option tree (docs/conventions.md):
#                    runtime.dev, assets.reassert.* and devguard.mail.{host,
#                    port,httpPort,pop3} are refused naming watch.* and
#                    mailpit.*; and the defaults: Mailpit runs with the mail
#                    guard off and orders the server after it, the runtime
#                    always serves /assets and reloads only with watch.python,
#                    and ports.* are per service.
#   removed-nixos    services.frappe, and sites.<site>.extraConfig{,Files}: each
#                    fails an assertion naming services.frappe-nix and
#                    settings{,Files}, and a host on the new names has none.
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

  # For a bench that sets removed names: each message by the option it names,
  # and whether the shell evaluates.
  refused = config: {
    messages = lib.listToAttrs (
      map (
        message: lib.nameValuePair (builtins.head (builtins.match "The option `([^']*)'.*" message)) message
      ) config.frappe-nix.removals
    );
    evaluates = (builtins.tryEval config.devenv.shells.default.env.FRAPPE_SITE).success;
  };
  # Whether each removed option's message names its replacement: `names` is
  # option = the replacement's text.
  namesEach = names: refusal: {
    inherit (refusal) evaluates;
    named = lib.mapAttrs (option: message: lib.hasInfix names.${option} message) refusal.messages;
  };
  # The expected side: every option names its replacement, and nothing evaluates.
  allNamed = names: {
    evaluates = false;
    named = lib.mapAttrs (_: _: true) names;
  };

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
  # The failed assertions that name a removed option: ours (removedSiteOption)
  # and NixOS's (mkRemovedOptionModule).
  removedAssertions =
    config:
    lib.sort builtins.lessThan (
      map (a: a.message) (
        lib.filter (
          a:
          !a.assertion
          && (lib.hasInfix "has been removed" a.message || lib.hasInfix "no longer has any effect" a.message)
        ) config.assertions
      )
    );
  productionWith = siteSettings: {
    enable = true;
    package = stubBench;
    redis.createLocally = true;
    sites.${site} = {
      enable = true;
      database.createLocally = true;
      database.passwordFile = "/run/secrets/db-password";
      nginx.enable = true;
    }
    // siteSettings;
  };

  identity = config: {
    inherit (config.frappe-nix) name defaultSite removals;
    devenvName = config.devenv.shells.default.name;
    site = config.devenv.shells.default.env.FRAPPE_SITE or null;
  };
in
{
  removed-devenv =
    let
      names = {
        "frappe-nix.benchName" = "`name`";
        "frappe-nix.siteName" = "`defaultSite`";
        "frappe-nix.restore.withFiles" = "restore.files";
        "frappe-nix.containers.aliases" = "container-<role>";
      };
    in
    checks.eval "removed-devenv" {
      expected = {
        old = allNamed names;
        new = {
          name = "new-bench";
          defaultSite = "new.localhost";
          devenvName = "new-bench";
          site = "new.localhost";
          removals = [ ];
        };
        overrides = {
          pycups = false;
          mysqlclient = true;
        };
      };
      actual = {
        old = namesEach names (
          refused (benchWith {
            name = "new-bench";
            benchName = "renamed-bench";
            siteName = "old.localhost";
            restore.withFiles = true;
            containers.aliases = false;
          })
        );
        new = identity (benchWith {
          name = "new-bench";
          defaultSite = "new.localhost";
        });
        overrides = {
          pycups = (builtins.tryEval (self.lib.overrides.pycups { inherit pkgs; })).success;
          mysqlclient =
            (builtins.tryEval (
              builtins.isFunction (
                self.lib.overrides.mysqlclient {
                  inherit pkgs;
                  inherit (pkgs) mariadb;
                }
              )
            )).success;
        };
      };
    };

  option-tree =
    let
      base = {
        name = "option-tree";
        ports.offset = 123;
      };
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
      removedNames = {
        "frappe-nix.runtime.dev" = "watch.python";
        "frappe-nix.assets" = "watch.reassert.{hooks,debounceMs}";
        "frappe-nix.devguard.mail.host" = "mailpit.host";
        "frappe-nix.devguard.mail.port" = "mailpit.port";
        "frappe-nix.devguard.mail.httpPort" = "mailpit.httpPort";
        "frappe-nix.devguard.mail.pop3" = "mailpit.pop3.";
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
            pop3.enable = true;
          };
        }
      );
      defaults = benchWith base;
      guardOff = benchWith (base // { devguard.mail.enable = false; });
      forced = benchWith (base // { ports.smtp = lib.mkForce 2999; });
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
    checks.eval "option-tree" {
      expected = {
        old = allNamed removedNames;
        new = {
          mailpit = {
            enable = true;
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
          python = false;
          debounceMs = 500;
          removals = [ ];
        };
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
        old = namesEach removedNames (refused old);
        new = {
          inherit (new.frappe-nix) mailpit removals;
          inherit (new.frappe-nix.watch) python;
          inherit (new.frappe-nix.watch.reassert) debounceMs;
        };
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

  removed-nixos = checks.eval "removed-nixos" {
    expected = {
      old = [ true ];
      oldSite = [
        true
        true
      ];
      new = [ ];
    };
    actual = {
      # NixOS's own message, naming the option, then ours, naming the new one.
      old = map (m: lib.hasInfix "`services.frappe'" m && lib.hasInfix "It is services.frappe-nix" m) (
        removedAssertions (nixosWith [ "services" "frappe" ] (productionWith { }))
      );
      # Each names the option it was and the one it is.
      oldSite =
        let
          messages = removedAssertions (
            nixosWith [ "services" "frappe-nix" ] (productionWith {
              extraConfig.host_name = "https://${site}";
              extraConfigFiles = [ "/run/secrets/site-config" ];
            })
          );
        in
        [
          (lib.any (
            m: lib.hasInfix ".extraConfig'" m && lib.hasInfix "sites.\"${site}\".settings'" m
          ) messages)
          (lib.any (
            m: lib.hasInfix ".extraConfigFiles'" m && lib.hasInfix "sites.\"${site}\".settingsFiles'" m
          ) messages)
        ];
      new = removedAssertions (
        nixosWith [ "services" "frappe-nix" ] (productionWith {
          settings.host_name = "https://${site}";
          settingsFiles = [ "/run/secrets/site-config" ];
        })
      );
    };
  };
}
