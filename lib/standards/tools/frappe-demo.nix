# `frappe-demo` (docs/app-standards/spec.md §5.4): a repeatable demo site,
# `frappe_nix_tools.app_tools.frappe_demo` (PLAN.md Track D, D6) around the bench-side
# lib/demo/frappe_demo.py, whose store path it reads from FRAPPE_NIX_CLI_CONFIG. Run as
# `python -I -m`, as frappe-init is (lib/init.nix), so no console-script wrapper changes
# the PATH the bench's commands inherit. A dev-shell command of an opted-in app only
# (lib/standards/shell.nix puts every tool here on its PATH), so an app that has not opted in
# gets no new command (S35). It runs inside that shell, which supplies devenv,
# process-compose, bench (and its `bench provision`) and mariadb-admin.
{
  pkgs,
  lib,
  frappeNixTools,
  ...
}:
let
  frappeNix = import ./frappe-nix.nix { inherit pkgs lib frappeNixTools; };
  python = frappeNixTools.pythonModule.withPackages (_: [ frappeNixTools ]);
  # What frappe-demo reads (app_tools/config.py); it refuses a missing or an unknown key.
  config = pkgs.writeText "frappe-demo.json" (
    builtins.toJSON { demoPy = "${../../demo/frappe_demo.py}"; }
  );
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
  text = ''
    FRAPPE_NIX_CLI_CONFIG=${config} exec ${python}/bin/python3 -I -m frappe_nix_tools.app_tools.frappe_demo "$@"
  '';
  meta.description = "Build a repeatable demo site for a Frappe app (setup wizard, demo data, the app's demo hook)";
}
