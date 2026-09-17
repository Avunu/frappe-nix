# The `reconcile-apps` script, rendered from lib/scripts.nix and driven
# against a stub bench — the app-install half of issue #32's reconcile.
#
# Rendering it here rather than testing lib/scripts.nix's output by
# inspection is the point: devenv never shellchecks a `scripts.<n>.exec`
# body, so without this it would ship unlinted and unexercised, same as
# tests/bench-restore.nix.
{ pkgs }:

let
  inherit (pkgs) lib;

  render =
    (import ../lib/scripts.nix {
      inherit lib pkgs;
      appsWithNode = [ ];
      benchBin = "bench";
    }).reconcile-apps.exec;
in
{
  reconcile-apps =
    pkgs.runCommand "frappe-nix-reconcile-apps-check"
      {
        nativeBuildInputs = with pkgs; [
          jq
          coreutils
          shellcheck
        ];
        rendered = render;
        passAsFile = [ "rendered" ];
      }
      ''
        export HOME="$PWD"

        # devenv would ship this unlinted; lint it here instead.
        shellcheck -s bash -S warning -e SC2317 "$renderedPath"

        bash ${./reconcile-apps.sh} "$renderedPath" | tee "$out"
      '';
}
