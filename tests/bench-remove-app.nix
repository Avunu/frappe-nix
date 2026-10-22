# `bench-remove-app`, rendered from lib/scripts.nix and driven against file://
# remotes and a stub bench: the inverse of tests/bench-get-app.nix — what gets
# torn out of .gitmodules, pyproject.toml and sites/, what it refuses to touch,
# and that the umbrella `bench` routes remove-app to it while leaving
# uninstall-app to the real bench.
#
# bench-remove-app is a shim that execs the Python command (frappe-nix-bench
# remove-app, py/frappe_nix_tools/frappe_nix_tools/apps/remove_app.py) with
# the configuration lib/scripts.nix generates.
{ pkgs, checks }:

let
  inherit (pkgs) lib;
  inherit (import ../lib/standards/outputs.nix { inherit pkgs; }) frappeNixTools;

  scripts = import ../lib/scripts.nix {
    inherit lib pkgs;
    appsWithNode = [ ];
    benchBin = "bench";
    benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
  };
  workspaceTool = import ../lib/workspace-tool.nix { inherit pkgs; };
in
{
  bench-remove-app =
    checks.script "bench-remove-app"
      {
        tools = with pkgs; [
          git
          jq
          coreutils
          python3
          shellcheck
        ];
        drvArgs = {
          script = scripts.bench-remove-app.exec;
          dispatch = scripts.bench.exec;
          passAsFile = [
            "script"
            "dispatch"
          ];
        };
      }
      ''
        export HOME="$PWD" WORKSPACE_TOOL=${workspaceTool}/bin/frappe-nix-workspace
        shellcheck -s bash -S warning -e SC2317 "$scriptPath"
        bash ${./bench-remove-app.sh} "$scriptPath" "$dispatchPath" | tee "$out"
      '';
}
