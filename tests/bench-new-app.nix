# `bench-new-app`, rendered from lib/scripts.nix and driven against a stub
# upstream scaffolder: what is registered in pyproject.toml and sites/, and
# what it refuses.
{
  pkgs,
  checks,
  python-devops-nix,
}:

let
  inherit (pkgs) lib;
  base = python-devops-nix.lib;
  inherit (import ../lib/standards/outputs.nix { inherit pkgs base; }) frappeNixTools;

  rendered =
    (import ../lib/scripts.nix {
      inherit lib pkgs base;
      appsWithNode = [ ];
      benchBin = "bench";
      # The Python command the shim execs (frappe-nix-bench, lib/bench-cli.nix).
      benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
    }).bench-new-app.exec;
in
{
  bench-new-app =
    checks.script "bench-new-app"
      {
        tools = with pkgs; [
          coreutils
          python3
          shellcheck
        ];
        drvArgs = {
          script = rendered;
          passAsFile = [ "script" ];
        };
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$scriptPath"
        bash ${./bench-new-app.sh} "$scriptPath" | tee "$out"
      '';
}
