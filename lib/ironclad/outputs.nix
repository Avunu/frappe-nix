# The Ironclad tools as flake outputs: every lib/ironclad/tools/<name>.nix
# becomes `packages.<name>` and `apps.<name>` (docs/ironclad/spec.md §5), so
# `nix run github:Avunu/frappe-nix#<name>` works, and in an app-mode flake
# `nix run .#<name>` (modules/devenv.nix merges the same apps there).
#
# A tool file is a function of { pkgs, lib, ironclad } (take `...`) returning a
# derivation with meta.mainProgram; adding a tool is adding a file, and neither
# flake.nix nor this loader changes. `toolsDir` is a parameter only so that
# tests/ironclad/hookpoints.nix can point it at a fixture directory.
{
  pkgs,
  toolsDir ? ./tools,
}:

let
  inherit (pkgs) lib;

  ironclad = pkgs.python314Packages.callPackage ./package.nix { };

  toolFiles = lib.filterAttrs (name: type: type == "regular" && lib.hasSuffix ".nix" name) (
    builtins.readDir toolsDir
  );

  packages = lib.mapAttrs' (
    file: _:
    lib.nameValuePair (lib.removeSuffix ".nix" file) (
      import (toolsDir + "/${file}") { inherit pkgs lib ironclad; }
    )
  ) toolFiles;
in
{
  inherit ironclad packages;

  apps = lib.mapAttrs (name: drv: {
    type = "app";
    program = lib.getExe drv;
    meta.description = drv.meta.description or "frappe-nix Ironclad tool ${name}";
  }) packages;
}
