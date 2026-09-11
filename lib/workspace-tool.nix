# frappe-nix-workspace — the one implementation of the app-registration
# contract between apps/, pyproject.toml ([tool.uv.workspace].members +
# [tool.uv.sources]) and sites/apps.{txt,json}.
#
# Shared by the scaffolder/migrator (lib/init.nix), the dev-shell scripts
# (lib/scripts.nix), the dev shell itself (modules/devenv.nix) and the bench
# package build (lib/bench.nix), so `frappe-init`, `bench-get-app`,
# `bench-update` and `nix build` cannot drift apart on what a bench's
# registered apps are.
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
