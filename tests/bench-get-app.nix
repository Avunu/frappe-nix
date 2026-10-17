# `bench-get-app`, rendered from lib/scripts.nix and driven against file://
# remotes: what lands in .gitmodules (the branch above all — a submodule
# registered without one is never pulled), in pyproject.toml and in sites/.
#
# The script is a shim that execs the Python command (frappe-nix-bench
# get-app, py/frappe_nix_tools/frappe_nix_tools/apps/get_app.py) with the
# configuration lib/scripts.nix generates, so this drives the real thing: the
# shim, the generated JSON, the command and frappe-nix-sources' clone.
{ pkgs, python-devops-nix }:

let
  inherit (pkgs) lib;
  inherit (import ../lib/standards/outputs.nix { inherit pkgs; }) frappeNixTools;
  shellTools = import ../lib/shell-tools.nix {
    inherit pkgs;
    base = python-devops-nix.lib;
  };

  rendered =
    (import ../lib/scripts.nix {
      inherit lib pkgs;
      appsWithNode = [ ];
      benchBin = "bench";
      benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
      sources = lib.getExe shellTools.sources;
    }).bench-get-app.exec;
  workspaceTool = import ../lib/workspace-tool.nix { inherit pkgs; };
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
        export HOME="$PWD" WORKSPACE_TOOL=${workspaceTool}/bin/frappe-nix-workspace
        shellcheck -s bash -S warning -e SC2317 "$scriptPath"
        bash ${./bench-get-app.sh} "$scriptPath" | tee "$out"
      '';
}
