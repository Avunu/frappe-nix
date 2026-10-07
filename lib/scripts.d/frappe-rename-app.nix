# `frappe-rename-app`: rename a Frappe app in place (docs/ironclad/spec.md
# §5.10). Both modes: the code half runs in an app's repository, the site half
# against a site of this bench (bench mode included, where the renamed app is a
# submodule).
#
#   frappe-rename-app code --from OLD --to NEW [--dry-run]       (any Python)
#   frappe-rename-app --site S OLD=NEW … [--dry-run] [--scan] [--yes]
#                                              (the bench's, from its sites/)
#
# The dev shell's reconcile-apps runs the site half itself for
# `frappe-nix.renamedApps`, before it installs anything (modules/devenv.nix).
{
  lib,
  pkgs,
  pythonBin,
  ...
}:
let
  tool = ../rename/frappe_rename_app.py;
  # The pairs replaced rather than renamed, once ironclad/apps.json (N4) exists;
  # the tool knows jailbreak → data_steward without it.
  appsJson = ../../ironclad/apps.json;
in
{
  frappe-rename-app = {
    exec = ''
      ${lib.optionalString (builtins.pathExists appsJson) ''
        export FRAPPE_RENAME_APPS_JSON=${appsJson}
      ''}
      if [ "''${1:-}" = code ]; then
        exec ${lib.getExe pkgs.python3} ${tool} "$@"
      fi
      cd "$FRAPPE_BENCH_ROOT/sites" || exit 1
      exec ${pythonBin} ${tool} "$@"
    '';
    description = "Rename a Frappe app in place: `code --from OLD --to NEW` in its repository, `--site S OLD=NEW` on a site before migrate.";
  };
}
