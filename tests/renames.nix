# Track B's renames (python-devops-nix PLAN.md, Track B; CONVENTIONS.md): each
# old name still reaches the new option, and is reported for the deprecation
# warning. At evaluation; nothing is built.
#
#   renames-devenv   frappe-nix.benchName → name, siteName → defaultSite; the
#                    new names set devenv's `name` and FRAPPE_SITE, and the old
#                    ones are listed in frappe-nix.deprecations (lib.warn).
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
}
