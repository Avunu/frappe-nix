# `bench-remove-app`, rendered from lib/scripts.nix and driven against file://
# remotes and a stub bench: the inverse of tests/bench-get-app.nix — what gets
# torn out of .gitmodules, pyproject.toml and sites/, what it refuses to touch,
# and that the umbrella `bench` routes remove-app to it while leaving
# uninstall-app to the real bench.
{ pkgs }:

let
  inherit (pkgs) lib;

  scripts = import ../lib/scripts.nix {
    inherit lib pkgs;
    appsWithNode = [ ];
    benchBin = "bench";
  };
in
{
  bench-remove-app =
    pkgs.runCommand "frappe-nix-bench-remove-app-check"
      {
        nativeBuildInputs = with pkgs; [
          git
          jq
          coreutils
          python3
          shellcheck
        ];
        script = scripts.bench-remove-app.exec;
        dispatch = scripts.bench.exec;
        passAsFile = [
          "script"
          "dispatch"
        ];
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$scriptPath"
        bash ${./bench-remove-app.sh} "$scriptPath" "$dispatchPath" | tee "$out"
      '';
}
