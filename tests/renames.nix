# Track B's renames (python-devops-nix PLAN.md, Track B; CONVENTIONS.md): each
# old name still reaches the new option, and is reported for the deprecation
# warning. At evaluation; nothing is built.
#
#   renames-devenv   frappe-nix.benchName → name, siteName → defaultSite; the
#                    new names set devenv's `name` and FRAPPE_SITE, and the old
#                    ones are listed in frappe-nix.deprecations (lib.warn).
#   renames-nixos    services.frappe → services.frappe-nix, as a whole tree: the
#                    old path sets the same units (nested mkIf and mkDefault
#                    included) and adds NixOS's rename warning.
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
      warnings = map (
        w: lib.concatStringsSep " -> " (builtins.match "The option `([^']*)'.* renamed to `([^']*)'.*" w)
      ) (lib.filter (lib.hasInfix "has been renamed") config.warnings);
    };
  production = {
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
    };
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

  renames-nixos = checks.eval "renames-nixos" {
    expected = {
      inherit (units (nixosWith [ "services" "frappe-nix" ] production)) units;
      warnings = [ "services.frappe -> services.frappe-nix" ];
      webThreads = 3;
      level = "info";
    };
    actual =
      let
        old = nixosWith [ "services" "frappe" ] production;
      in
      units old
      // {
        webThreads = old.services.frappe-nix.runtime.webThreads;
        level = old.services.frappe-nix.logging.level;
      };
  };
}
