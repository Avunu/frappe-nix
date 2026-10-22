# `bench-update --pull`, rendered from lib/scripts.nix and driven against a
# fixture bench with every shape an apps/<x> can take: a registered submodule
# with a remote to pull from, a local app (committed source), and a nested
# repository that was `git add`ed as-is — a gitlink with no .gitmodules entry,
# which `git submodule foreach` used to die on before pulling anything.
#
# Rendered here for the same reason as bench-restore: devenv never shellchecks
# a `scripts.<n>.exec` body.
{ pkgs, checks }:

let
  inherit (pkgs) lib;
  inherit (import ../lib/standards/outputs.nix { inherit pkgs; }) frappeNixTools;

  render =
    appMode:
    (import ../lib/scripts.nix {
      inherit lib pkgs appMode;
      # node-verify and node-modules are stubs on PATH in the test.
      appsWithNode = [ "frappe" ];
      benchBin = "bench";
      # The Python command the shim execs (frappe-nix-bench, lib/bench-cli.nix).
      benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
      # The lock generator is a stub on PATH in the test; the exclusion must
      # reach it as a flag.
      nodeNestedFrontendExcludes = [ "alpha/desk" ];
    }).bench-update.exec;
in
{
  bench-update =
    checks.script "bench-update"
      {
        tools = with pkgs; [
          git
          jq
          coreutils
          shellcheck
        ];
        drvArgs = {
          script = render false;
          appScript = render true;
          passAsFile = [
            "script"
            "appScript"
          ];
        };
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$scriptPath" "$appScriptPath"
        bash ${./bench-update.sh} "$scriptPath" "$appScriptPath" | tee "$out"
      '';
}
