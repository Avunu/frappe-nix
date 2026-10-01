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

  render =
    appMode:
    (import ../lib/scripts.nix {
      inherit lib pkgs appMode;
      appsWithNode = [ "alpha" ];
      benchBin = "bench";
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
        # SC2164: the body has no `set -e`, so its first line (`cd` to the bench
        # root) is flagged. Not what this check is about; left as it is.
        shellcheck -s bash -S warning -e SC2317 -e SC2164 "$benchScriptPath" "$appScriptPath"
        bash ${./update-deps.sh} "$benchScriptPath" "$appScriptPath" | tee "$out"
      '';
}
