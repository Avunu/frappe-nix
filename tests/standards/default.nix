# The app standards' flake checks (docs/app-standards/spec.md §7).
#
# Every other tests/standards/<area>.nix is a function of
# { pkgs, lib, self, inputs, frappeNixTools, checks } (take `...`) returning an
# attrset of checks, each registered with `checks` (python-devops-nix's
# lib.checks.mk, so its derivation is frappe-nix-standards-<x>-check), and
# its owner adds it without editing this file or flake.nix. They are merged
# into the flake's `checks`, plus `standards-all`, a linkFarm of all of them,
# which is the one name check.yml builds.
#
# Every check is named `standards-<something>` (and none `standards-all`), and
# no check of frappe-nix's own is, so flake.nix's `// import ./tests/standards`
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
    # python-devops-nix's helper, which names each check
    # frappe-nix-<x>-check (python-devops-nix docs/conventions.md, Tests).
    checks = inputs.python-devops-nix.lib.checks.mk {
      inherit pkgs;
      product = "frappe-nix";
    };
    inherit (import ../../lib/standards/outputs.nix { inherit pkgs; }) frappeNixTools;
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
      clash = builtins.attrNames (builtins.intersectAttrs added (acc // { standards-all = null; }));
      unprefixed = builtins.filter (name: !lib.hasPrefix "standards-" name) (builtins.attrNames added);
    in
    lib.throwIf (clash != [ ]) "tests/standards/${file} redefines ${lib.concatStringsSep ", " clash}" (
      lib.throwIf (unprefixed != [ ])
        "tests/standards/${file}: ${lib.concatStringsSep ", " unprefixed} must be named standards-<name>"
        (acc // added)
    )
  ) { } areas;
in
checks
// {
  standards-all = pkgs.linkFarm (args.checks.checkName "standards-all") (
    lib.mapAttrsToList (name: path: { inherit name path; }) checks
  );
}
