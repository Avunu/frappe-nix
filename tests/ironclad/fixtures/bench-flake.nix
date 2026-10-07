# A bench flake built with frappe-nix's flakeModule, for checks that read the
# evaluated perSystem config (option values, devenv's processes and scripts)
# without building or entering a shell. Its `self` is its own, as an app's
# would be: handing it frappe-nix's `self` would let the module reach back into
# the very checks that evaluate it.
#
#   import ./fixtures/bench-flake.nix { inherit self pkgs; } perSystemModule
#   → the perSystem `config` for this system
{ self, pkgs }:
perSystem:
let
  inherit (pkgs.stdenv.hostPlatform) system;
  root = ./bench-dev-group;
  inputs = {
    self = innerSelf;
    frappe-nix = self;
    # An app's flake follows frappe-nix's nixpkgs.
    inherit (self.inputs) nixpkgs;
  };
  outputs = self.lib.mkFlake { inherit inputs; } {
    imports = [ self.flakeModules.default ];
    systems = [ system ];
    debug = true;
    inherit perSystem;
  };
  innerSelf = outputs // {
    _type = "flake";
    outPath = root;
    inherit inputs;
    sourceInfo.outPath = root;
  };
in
outputs.allSystems.${system}.config
