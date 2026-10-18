# `bench-new-app`, rendered from lib/scripts.nix and driven against a stub
# upstream scaffolder: what is registered in pyproject.toml and sites/, and
# what it refuses.
{ pkgs }:

let
  inherit (pkgs) lib;
  inherit (import ../lib/standards/outputs.nix { inherit pkgs; }) frappeNixTools;

  rendered =
    (import ../lib/scripts.nix {
      inherit lib pkgs;
      appsWithNode = [ ];
      benchBin = "bench";
      # The Python command the shim execs (frappe-nix-bench, lib/bench-cli.nix).
      benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
    }).bench-new-app.exec;
in
{
  bench-new-app =
    pkgs.runCommand "frappe-nix-bench-new-app-check"
      {
        nativeBuildInputs = with pkgs; [
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
        bash ${./bench-new-app.sh} "$scriptPath" | tee "$out"
      '';
}
