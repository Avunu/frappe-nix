# `provision-site`, rendered from lib/scripts.nix and driven against a stub
# bench and a stub frappe-nix-ports: what it creates and installs, in what
# order, what it prints, and its exit statuses. Rendered here because devenv
# never shellchecks a `scripts.<n>.exec` body.
{ pkgs }:

let
  inherit (pkgs) lib;

  scripts = import ../lib/scripts.nix {
    inherit lib pkgs;
    appsWithNode = [ ];
    benchBin = "bench";
  };
in
{
  provision =
    pkgs.runCommand "frappe-nix-provision-check"
      {
        nativeBuildInputs = with pkgs; [
          coreutils
          gnugrep
          shellcheck
        ];
        script = scripts.provision-site.exec;
        passAsFile = [ "script" ];
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$scriptPath"
        bash ${./provision.sh} "$scriptPath" | tee "$out"
      '';
}
