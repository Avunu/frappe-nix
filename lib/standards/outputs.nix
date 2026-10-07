# The app standards tools as flake outputs (docs/app-standards/spec.md §1.2, §5):
# `packages.frappe-nix-tools` is the Python package itself, and every
# lib/standards/tools/<name>.nix becomes `packages.<name>` and `apps.<name>`, so
# `nix run github:Avunu/frappe-nix#<name>` works, and in an opted-in app-mode
# flake `nix run .#<name>` (lib/standards/shell.nix merges the same apps there).
#
# A tool file is a function of { pkgs, lib, frappeNixTools } (take `...`)
# returning a derivation with meta.mainProgram that propagates no inputs (wrap a
# Python package's bin/, see tools/frappe-nix.nix); adding a tool is adding a
# file, and neither flake.nix nor this loader changes. `toolsDir` is a parameter
# only so that tests/standards/hookpoints.nix can point it at a fixture directory.
{
  pkgs,
  toolsDir ? ./tools,
}:

let
  inherit (pkgs) lib;

  frappeNixTools = pkgs.python314Packages.callPackage ./package.nix { };

  toolFiles = lib.filterAttrs (name: type: type == "regular" && lib.hasSuffix ".nix" name) (
    builtins.readDir toolsDir
  );

  # Names the flakes already define next to the tools: frappe-nix's packages and
  # apps, the package itself, the app-mode `frappe-init` app
  # (lib/standards/shell.nix) and the app-mode `relock` app (modules/devenv.nix).
  # A tool by one of these names would be shadowed in one flake and clash in the
  # other, so it is refused.
  reserved = [
    "default"
    "frappe-init"
    "frappe-nix-tools"
    "backup-fetch"
    "relock"
  ];
  taken = lib.intersectLists reserved (map (lib.removeSuffix ".nix") (builtins.attrNames toolFiles));

  tools =
    lib.throwIf (taken != [ ])
      "lib/standards/tools: ${lib.concatStringsSep ", " taken} is a name frappe-nix already uses"
      (
        lib.mapAttrs' (
          file: _:
          lib.nameValuePair (lib.removeSuffix ".nix" file) (
            import (toolsDir + "/${file}") { inherit pkgs lib frappeNixTools; }
          )
        ) toolFiles
      );
in
{
  inherit frappeNixTools tools;

  packages = tools // {
    frappe-nix-tools = frappeNixTools;
  };

  apps = lib.mapAttrs (name: drv: {
    type = "app";
    program = lib.getExe drv;
    meta.description = drv.meta.description or "frappe-nix app standards tool ${name}";
  }) tools;
}
