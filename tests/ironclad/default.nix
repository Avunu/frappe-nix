# The Ironclad platform's flake checks (docs/ironclad/spec.md §7).
#
# Every other tests/ironclad/<area>.nix is a function of
# { pkgs, lib, self, inputs, ironclad } (take `...`) returning an attrset of
# checks, and its owner adds it without editing this file or flake.nix. They are
# merged into the flake's `checks`, plus `ironclad-all`, a linkFarm of all of
# them, which is the one name check.yml builds.
#
# Every check is named `ironclad-<something>` (and none `ironclad-all`), and no
# check of frappe-nix's own is, so flake.nix's `// import ./tests/ironclad`
# can never replace one of them silently. Two areas defining the same check, or
# a name outside that prefix, fail evaluation. `areasDir` is a parameter only
# so that hookpoints.nix can point it at a fixture directory.
{
  pkgs,
  self,
  inputs,
  areasDir ? ./.,
}:

let
  inherit (pkgs) lib;

  args = {
    inherit
      pkgs
      lib
      self
      inputs
      ;
    inherit (import ../../lib/ironclad/outputs.nix { inherit pkgs; }) ironclad;
  };

  areas = builtins.attrNames (
    lib.filterAttrs (
      name: type: type == "regular" && lib.hasSuffix ".nix" name && name != "default.nix"
    ) (builtins.readDir areasDir)
  );

  checks = lib.foldl' (
    acc: file:
    let
      added = import (areasDir + "/${file}") args;
      clash = builtins.attrNames (builtins.intersectAttrs added (acc // { ironclad-all = null; }));
      unprefixed = builtins.filter (name: !lib.hasPrefix "ironclad-" name) (builtins.attrNames added);
    in
    lib.throwIf (clash != [ ]) "tests/ironclad/${file} redefines ${lib.concatStringsSep ", " clash}" (
      lib.throwIf (unprefixed != [ ])
        "tests/ironclad/${file}: ${lib.concatStringsSep ", " unprefixed} must be named ironclad-<name>"
        (acc // added)
    )
  ) { } areas;
in
checks
// {
  ironclad-all = pkgs.linkFarm "ironclad-all" (
    lib.mapAttrsToList (name: path: { inherit name path; }) checks
  );
}
