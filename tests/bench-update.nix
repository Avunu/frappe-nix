# `bench-update --pull`, rendered from lib/scripts.nix and driven against a
# fixture bench with every shape an apps/<x> can take: a registered submodule
# with a remote to pull from, a local app (committed source), and a nested
# repository that was `git add`ed as-is — a gitlink with no .gitmodules entry,
# which `git submodule foreach` used to die on before pulling anything.
#
# Rendered here for the same reason as bench-restore: devenv never shellchecks
# a `scripts.<n>.exec` body.
{ pkgs }:

let
  inherit (pkgs) lib;

  rendered =
    (import ../lib/scripts.nix {
      inherit lib pkgs;
      appsWithNode = [ ];
      benchBin = "bench";
    }).bench-update.exec;
in
{
  bench-update = pkgs.runCommand "frappe-nix-bench-update-check" {
    nativeBuildInputs = with pkgs; [
      git
      jq
      coreutils
      shellcheck
    ];
    script = rendered;
    passAsFile = [ "script" ];
  } ''
    export HOME="$PWD"
    shellcheck -s bash -S warning -e SC2317 "$scriptPath"
    bash ${./bench-update.sh} "$scriptPath" | tee "$out"
  '';
}
