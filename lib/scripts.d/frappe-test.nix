# `frappe-test`: the app's tests the way CI runs them (docs/app-standards/spec.md
# §5.1). App mode only: it tests the app this repository is. A dev-shell command
# for every app-mode user: in an app without [tool.frappe-nix] it reads no
# configuration and `--ci` runs the recommended profile's stages.
#
# The script is lib/sh/frappe-test.sh, built with writeShellApplication so
# shellcheck runs over it. It carries what it calls that an app-mode shell need
# not have: `frappe-nix` (which an app that has not opted in has no other way
# to reach) and nixfmt, statix and deadnix for stage 8, all pinned by
# frappe-nix's nixpkgs. The dev shell supplies the rest (devenv,
# process-compose, provision-site, uv).
{
  lib,
  pkgs,
  appMode,
  ...
}:
let
  inherit (import ../standards/outputs.nix { inherit pkgs; }) tools;
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
    # Not errexit: every stage after the tests runs even when one failed, and
    # each records its own verdict.
    bashOptions = [
      "nounset"
      "pipefail"
    ];
    text = builtins.readFile ../sh/frappe-test.sh;
  };
in
lib.optionalAttrs appMode {
  frappe-test = {
    exec = ''exec ${lib.getExe frappeTest} "$@"'';
    description = "Run the app's tests the way CI does: coverage scoped to the app, the testmap, the composition check (--ci: the stages the app's configuration turns on).";
  };
}
