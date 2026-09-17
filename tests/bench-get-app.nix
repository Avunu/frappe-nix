# `bench-get-app`, rendered from lib/scripts.nix and driven against file://
# remotes: what lands in .gitmodules (the branch above all — a submodule
# registered without one is never pulled), in pyproject.toml and in sites/.
#
# Rendered here for the same reason as bench-update: devenv never shellchecks
# a `scripts.<n>.exec` body.
{ pkgs }:

let
  inherit (pkgs) lib;

  rendered =
    (import ../lib/scripts.nix {
      inherit lib pkgs;
      appsWithNode = [ ];
      benchBin = "bench";
    }).bench-get-app.exec;
in
{
  bench-get-app =
    pkgs.runCommand "frappe-nix-bench-get-app-check"
      {
        nativeBuildInputs = with pkgs; [
          git
          coreutils
          python3
          shellcheck
        ];
        script = rendered;
        passAsFile = [ "script" ];
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$scriptPath"
        bash ${./bench-get-app.sh} "$scriptPath" | tee "$out"
      '';
}
