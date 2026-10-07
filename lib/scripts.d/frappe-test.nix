# `frappe-test`: the app's tests the way CI runs them (docs/ironclad/spec.md
# §5.1). App mode only: it tests the app this repository is. The script is
# lib/sh/frappe-test.sh, built with writeShellApplication so shellcheck runs
# over it; the dev shell supplies the rest of what it calls (devenv,
# process-compose, provision-site, ironclad, uv, nixfmt, statix, deadnix).
{
  lib,
  pkgs,
  appMode,
  ...
}:
let
  frappeTest = pkgs.writeShellApplication {
    name = "frappe-test";
    runtimeInputs = with pkgs; [
      coreutils
      curl
      git
      gnugrep
      gnused
      jq
      util-linux
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
    description = "Run the app's tests the way CI does: coverage scoped to the app, the testmap, the composition check (--ci: ty, the Nix linters, the shell checks).";
  };
}
