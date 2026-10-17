# `frappe-test`: the app's tests the way CI runs them (docs/app-standards/spec.md
# §5.1). App mode only: it tests the app this repository is. A dev-shell command
# for every app-mode user: in an app without [tool.frappe-nix] it reads no
# configuration and `--ci` runs the recommended profile's stages.
#
# The command is Python, `frappe_nix_tools.app_tools.frappe_test` (PLAN.md
# Track D, D6), run as `python -I -m` from an environment that holds
# frappe-nix-tools, as frappe-init is (lib/init.nix): a console script's
# wrapper would put the package's own bin/ and python3.14 on the PATH every
# stage inherits. This wrapper carries what it calls that an app-mode shell
# need not have: `frappe-nix` (which an app that has not opted in has no other
# way to reach) and nixfmt, statix and deadnix for stage 8, all pinned by
# frappe-nix's nixpkgs, with the PATH the bash version had, which the app's
# shell checks inherit. The dev shell supplies the rest (devenv,
# process-compose, provision-site, uv).
{
  lib,
  pkgs,
  appMode,
  ...
}:
let
  inherit (import ../standards/outputs.nix { inherit pkgs; }) tools frappeNixTools;
  python = frappeNixTools.pythonModule.withPackages (_: [ frappeNixTools ]);
  frappeTest = pkgs.writeShellApplication {
    name = "frappe-test";
    runtimeInputs = with pkgs; [
      coreutils
      curl
      git
      gnugrep
      gnused
      jq
      tools.frappe-nix
      nixfmt
      statix
      deadnix
    ];
    text = ''
      exec ${python}/bin/python3 -I -m frappe_nix_tools.app_tools.frappe_test "$@"
    '';
  };
in
lib.optionalAttrs appMode {
  frappe-test = {
    exec = ''exec ${lib.getExe frappeTest} "$@"'';
    description = "Run the app's tests the way CI does: coverage scoped to the app, the testmap, the composition check (--ci: the stages the app's configuration turns on).";
  };
}
