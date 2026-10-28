# The `reconcile-apps` script, rendered from lib/scripts.nix and driven
# against a stub bench — the app-install half of issue #32's reconcile.
#
# Rendering it here rather than testing lib/scripts.nix's output by
# inspection is the point: devenv never shellchecks a `scripts.<n>.exec`
# body, so without this it would ship unlinted and unexercised, same as
# tests/bench-restore.nix.
{
  pkgs,
  checks,
  python-devops-nix,
}:

let
  inherit (pkgs) lib;
  base = python-devops-nix.lib;
  inherit (import ../lib/standards/outputs.nix { inherit pkgs base; }) frappeNixTools;

  render =
    (import ../lib/scripts.nix {
      inherit lib pkgs base;
      appsWithNode = [ ];
      benchBin = "bench";
      # The Python command the shim execs (frappe-nix-bench, lib/bench-cli.nix).
      benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
    }).reconcile-apps.exec;
in
{
  reconcile-apps =
    checks.script "reconcile-apps"
      {
        tools = with pkgs; [
          jq
          coreutils
          shellcheck
        ];
        drvArgs = {
          rendered = render;
          passAsFile = [ "rendered" ];
        };
      }
      ''
        export HOME="$PWD"

        # devenv would ship this unlinted; lint it here instead.
        shellcheck -s bash -S warning -e SC2317 "$renderedPath"

        bash ${./reconcile-apps.sh} "$renderedPath" | tee "$out"
      '';
}
