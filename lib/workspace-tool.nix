# frappe-nix-workspace — the one implementation of the app-registration
# contract between apps/, pyproject.toml ([tool.uv.workspace].members +
# [tool.uv.sources]) and sites/apps.txt.
#
# Shared by the scaffolder/migrator (lib/init.nix) and the dev-shell scripts
# (lib/scripts.nix), so `frappe-init`, `bench-get-app` and `bench-new-app`
# cannot drift apart.
{ pkgs }:

let
  pythonToml = pkgs.python3.withPackages (ps: [ ps.tomlkit ]);
in
# Not writers.writePython3Bin: that runs its own linter with its own opinions at
# build time. A shebang'd text file is dependency-free and stable.
pkgs.writeTextFile {
  name = "frappe-nix-workspace";
  destination = "/bin/frappe-nix-workspace";
  executable = true;
  text = "#!${pythonToml}/bin/python3\n" + builtins.readFile ./frappe-workspace.py;
}
