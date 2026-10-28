# `bench provision`, rendered from lib/scripts.nix and driven against a stub
# bench and a stub frappe-nix-ports:
# what it creates and installs, in what order, what it prints, and its exit
# statuses. Rendered here because devenv never shellchecks a
# `scripts.<n>.exec` body.
#
# The umbrella wrapper's dispatch execs the Python command (frappe-nix-bench
# provision, py/frappe_nix_tools/frappe_nix_tools/apps/provision.py) with the
# configuration lib/scripts.nix generates. provision-site, its 1.x alias, went
# in 2.0.0.
{
  pkgs,
  checks,
  python-devops-nix,
}:

let
  inherit (pkgs) lib;
  base = python-devops-nix.lib;
  inherit (import ../lib/standards/outputs.nix { inherit pkgs base; }) frappeNixTools;

  scripts = import ../lib/scripts.nix {
    inherit lib pkgs base;
    appsWithNode = [ ];
    benchBin = "bench";
    benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
  };
in
# The 1.x alias is gone.
assert !(scripts ? provision-site);
{
  provision =
    checks.script "provision"
      {
        tools = with pkgs; [
          coreutils
          gnugrep
        ];
        drvArgs = {
          dispatch = scripts.bench.exec;
          passAsFile = [ "dispatch" ];
        };
      }
      ''
        export HOME="$PWD"
        # The dispatcher is not linted here: its unguarded cd is tests/bench-remove-app.nix's too.
        bash ${./provision.sh} "$dispatchPath" | tee "$out"
      '';
}
