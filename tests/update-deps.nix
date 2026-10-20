# `update-deps`, rendered from lib/scripts.nix — once for a bench, once for app
# mode — and driven against stub `yarn`, `uv` and lock-generator binaries.
#
# What is asserted is how yarn is called: in a bench the apps are other people's
# repositories and a rewritten yarn.lock is a modified file their next
# `git checkout` refuses over. Rendered here for the same reason as
# bench-update: devenv never shellchecks a `scripts.<n>.exec` body.
{ pkgs }:

let
  inherit (pkgs) lib;
  inherit (import ../lib/standards/outputs.nix { inherit pkgs; }) frappeNixTools;

  render =
    appMode:
    (import ../lib/scripts.nix {
      inherit lib pkgs appMode;
      appsWithNode = [
        "alpha"
        "beta"
      ];
      benchBin = "bench";
      # The Python command the shim execs (frappe-nix-bench, lib/bench-cli.nix).
      benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
    })."update-deps".exec;
in
{
  update-deps =
    pkgs.runCommand "frappe-nix-update-deps-check"
      {
        nativeBuildInputs = with pkgs; [
          coreutils
          shellcheck
        ];
        benchScript = render false;
        appScript = render true;
        passAsFile = [
          "benchScript"
          "appScript"
        ];
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$benchScriptPath" "$appScriptPath"
        bash ${./update-deps.sh} "$benchScriptPath" "$appScriptPath" | tee "$out"
      '';
}
