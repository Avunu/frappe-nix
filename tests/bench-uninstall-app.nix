# `bench-uninstall-app`, rendered from lib/scripts.nix and driven against
# file:// remotes and a stub bench: the inverse of tests/bench-get-app.nix —
# what gets torn out of .gitmodules, pyproject.toml and sites/, and what is
# correctly left alone when another site still has the app installed.
{ pkgs }:

let
  inherit (pkgs) lib;

  rendered =
    (import ../lib/scripts.nix {
      inherit lib pkgs;
      appsWithNode = [ ];
      benchBin = "bench";
    }).bench-uninstall-app.exec;
in
{
  bench-uninstall-app =
    pkgs.runCommand "frappe-nix-bench-uninstall-app-check"
      {
        nativeBuildInputs = with pkgs; [
          git
          jq
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
        bash ${./bench-uninstall-app.sh} "$scriptPath" | tee "$out"
      '';
}
