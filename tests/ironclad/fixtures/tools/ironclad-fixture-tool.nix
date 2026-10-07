# A stand-in lib/ironclad/tools file: the loader must turn it into
# packages.ironclad-fixture-tool and apps.ironclad-fixture-tool.
{ pkgs, ironclad, ... }:
pkgs.writeShellScriptBin "ironclad-fixture-tool" ''
  exec ${pkgs.lib.getExe ironclad} --version
''
