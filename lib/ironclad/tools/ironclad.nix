# `ironclad <command>`: the Python dispatcher itself (py/ironclad).
#
# Only its bin/ironclad, not the Python package: a buildPythonPackage
# propagates python3 and its dependencies, and in the app-mode dev shell
# python's setup hook would append their site-packages (nixpkgs' jinja2,
# packaging, tomlkit, ...) to PYTHONPATH, ahead of the bench venv's locked
# ones. The script keeps its own sys.path, so it runs the same through the link.
{
  pkgs,
  lib,
  ironclad,
  ...
}:
pkgs.runCommand "ironclad-${ironclad.version}"
  {
    inherit (ironclad) version;
    meta = {
      inherit (ironclad.meta) description;
      mainProgram = "ironclad";
    };
  }
  ''
    mkdir -p "$out/bin"
    ln -s ${lib.getExe ironclad} "$out/bin/ironclad"
  ''
