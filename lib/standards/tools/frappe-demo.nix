# `frappe-demo` (docs/app-standards/spec.md §5.4): a repeatable demo site, lib/sh/frappe-demo.sh
# around the bench-side lib/demo/frappe_demo.py. A dev-shell command of an opted-in app only
# (lib/standards/shell.nix puts every tool here on its PATH), so an app that has not opted in
# gets no new command (S35). It runs inside that shell, which supplies devenv,
# process-compose, provision-site, bench and mariadb-admin.
{
  pkgs,
  lib,
  frappeNixTools,
  ...
}:
let
  frappeNix = import ./frappe-nix.nix { inherit pkgs lib frappeNixTools; };
in
pkgs.writeShellApplication {
  name = "frappe-demo";
  runtimeInputs = [
    pkgs.coreutils
    pkgs.curl
    pkgs.git
    pkgs.jq
    pkgs.nodejs_24
    frappeNix
  ];
  text = builtins.replaceStrings [ "@FRAPPE_DEMO_PY@" ] [ "${../../demo/frappe_demo.py}" ] (
    builtins.readFile ../../sh/frappe-demo.sh
  );
  meta.description = "Build a repeatable demo site for a Frappe app (setup wizard, demo data, the app's demo hook)";
}
