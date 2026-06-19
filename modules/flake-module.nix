# Top-level flake-parts module for frappe-nix.
# Imports sub-modules and defines the option namespace.
{ inputs, ... }:
{
  imports = [
    inputs.devenv.flakeModule
    ./devenv.nix
    ./containers.nix
  ];
  # NOTE: ./nixos.nix is a standalone NixOS module, not a flake-parts module.
  # It is surfaced via flake.nixosModules.default (see ../flake.nix), so it must
  # NOT be imported here.
}
