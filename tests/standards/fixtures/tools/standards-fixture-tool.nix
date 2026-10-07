# A stand-in lib/standards/tools file: the loader must turn it into
# packages.standards-fixture-tool and apps.standards-fixture-tool.
{ pkgs, frappeNixTools, ... }:
pkgs.writeShellScriptBin "standards-fixture-tool" ''
  exec ${pkgs.lib.getExe frappeNixTools} --version
''
