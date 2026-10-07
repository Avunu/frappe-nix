# `frappe-nix <command>`: the Python dispatcher (py/frappe_nix_tools), with git
# and gh on its PATH (the `repo` command group calls `gh api`; pins and the repo
# helpers call git). They are appended, so a git or gh already on PATH wins.
#
# A wrapper around its bin/frappe-nix, not the Python package: a
# buildPythonPackage propagates python3 and its dependencies, and in the
# app-mode dev shell python's setup hook would append their site-packages
# (nixpkgs' jinja2, packaging, tomlkit, ...) to PYTHONPATH, ahead of the bench
# venv's locked ones. The script keeps its own sys.path, so it runs the same
# through the wrapper.
{
  pkgs,
  lib,
  frappeNixTools,
  ...
}:
pkgs.runCommand "frappe-nix-${frappeNixTools.version}"
  {
    inherit (frappeNixTools) version;
    nativeBuildInputs = [ pkgs.makeWrapper ];
    meta = {
      inherit (frappeNixTools.meta) description;
      mainProgram = "frappe-nix";
    };
  }
  ''
    makeWrapper ${lib.getExe frappeNixTools} "$out/bin/frappe-nix" \
      --suffix PATH : ${
        lib.makeBinPath [
          pkgs.git
          pkgs.gh
        ]
      }
  ''
