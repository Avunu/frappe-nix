# The Ironclad platform's flake checks (docs/ironclad/spec.md §7).
#
# Every other tests/ironclad/<area>.nix is a function of
# { pkgs, lib, self, inputs, ironclad } (take `...`) returning an attrset of
# checks, and its owner adds it without editing this file or flake.nix. They are
# merged into the flake's `checks`, plus `ironclad-all`, a linkFarm of all of
# them, which is the one name check.yml builds.
{
  pkgs,
  self,
  inputs,
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
    ) (builtins.readDir ./.)
  );

  checks = lib.foldl' (
    acc: file:
    let
      added = import (./. + "/${file}") args;
      clash = builtins.attrNames (builtins.intersectAttrs added acc);
    in
    lib.throwIf (clash != [ ]) "tests/ironclad/${file} redefines ${lib.concatStringsSep ", " clash}" (
      acc // added
    )
  ) { } areas;
in
checks
// {
  ironclad-all = pkgs.linkFarm "ironclad-all" (
    lib.mapAttrsToList (name: path: { inherit name path; }) checks
  );
}
