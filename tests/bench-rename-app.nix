# `bench-rename-app`, rendered from lib/scripts.nix and driven against file://
# remotes and a stub bench: the bench's half of renaming an app — what moves
# (the checkout, its clone, .gitmodules, the workspace, the registry), what it
# refuses before it changes anything, and that the umbrella `bench` routes
# rename-app to it.
#
# bench-rename-app is a shim that execs the Python command (frappe-nix-bench
# rename-app, py/frappe_nix_tools/frappe_nix_tools/apps/rename_app.py) with
# the configuration lib/scripts.nix generates.
{ pkgs }:

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
  bench-rename-app =
    pkgs.runCommand "frappe-nix-bench-rename-app-check"
      {
        nativeBuildInputs = with pkgs; [
          git
          jq
          coreutils
          python3
          shellcheck
        ];
        script = scripts.bench-rename-app.exec;
        dispatch = scripts.bench.exec;
        passAsFile = [
          "script"
          "dispatch"
        ];
      }
      ''
        export HOME="$PWD" WORKSPACE_TOOL=${workspaceTool}/bin/frappe-nix-workspace
        shellcheck -s bash -S warning -e SC2317 "$scriptPath"
        bash ${./bench-rename-app.sh} "$scriptPath" "$dispatchPath" | tee "$out"
      '';
}
