# `frappe-rename-app`: rename a Frappe app in place (docs/app-standards/spec.md
# §5.10). Both modes: the code half runs in an app's repository, the site half
# against a site of this bench (bench mode included, where the renamed app is a
# submodule).
#
#   frappe-rename-app code --from OLD --to NEW [--fleet FILE | --profile REF | --no-replace-check]
#                          [--dry-run]
#                                  (frappe-nix-tools' interpreter: the code half
#                                  reads the app's profile for its replace pairs)
#   frappe-rename-app --site S OLD=NEW … [--dry-run] [--scan] [--yes]
#                                  (the bench's, from its sites/)
#
# frappe-nix knows no replace pair itself: they come from the app's resolved
# profile ([[replace-apps]]) or a fleet file. The dev shell's reconcile-apps
# runs the site half itself for `frappe-nix.renamedApps`, before it installs
# anything (modules/devenv.nix).
{
  lib,
  pkgs,
  pythonBin,
  ...
}:
let
  tool = ../rename/frappe_rename_app.py;
  inherit (import ../standards/outputs.nix { inherit pkgs; }) frappeNixTools;
  # The interpreter frappe-nix-tools is built for, with it importable.
  codePython = frappeNixTools.pythonModule.withPackages (_: [ frappeNixTools ]);
in
{
  frappe-rename-app = {
    exec = ''
      if [ "''${1:-}" = code ]; then
        exec ${lib.getExe codePython} ${tool} "$@"
      fi
      cd "$FRAPPE_BENCH_ROOT/sites" || exit 1
      exec ${pythonBin} ${tool} "$@"
    '';
    description = "Rename a Frappe app in place: `code --from OLD --to NEW` in its repository, `--site S OLD=NEW` on a site before migrate.";
  };
}
