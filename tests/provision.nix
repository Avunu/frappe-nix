# `bench provision` and its alias `provision-site`, rendered from
# lib/scripts.nix and driven against a stub bench and a stub frappe-nix-ports:
# what it creates and installs, in what order, what it prints, and its exit
# statuses. Rendered here because devenv never shellchecks a
# `scripts.<n>.exec` body.
#
# Both exec the Python command (frappe-nix-bench provision,
# py/frappe_nix_tools/frappe_nix_tools/apps/provision.py) with the
# configuration lib/scripts.nix generates: `bench provision` from the umbrella
# wrapper's dispatch, provision-site from a shim that says it is deprecated.
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
in
{
  provision =
    checks.script "provision"
      {
        tools = with pkgs; [
          coreutils
          gnugrep
          shellcheck
        ];
        drvArgs = {
          script = scripts.provision-site.exec;
          dispatch = scripts.bench.exec;
          passAsFile = [
            "script"
            "dispatch"
          ];
        };
      }
      ''
        export HOME="$PWD"
        # The dispatcher is not linted here: its unguarded cd is tests/bench-remove-app.nix's too.
        shellcheck -s bash -S warning -e SC2317 "$scriptPath"
        bash ${./provision.sh} "$scriptPath" "$dispatchPath" | tee "$out"
      '';
}
