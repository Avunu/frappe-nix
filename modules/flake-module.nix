# Top-level flake-parts module for frappe-nix.
# Imports sub-modules and defines the option namespace.
{ inputs, ... }:
{
  imports = [
    inputs.devenv.flakeModule
    # Declares frappe-nix.secrets through python-devops-nix's secrets module,
    # which imports agenix-shell's flake module itself.
    ./secrets.nix
    ./devenv.nix
    ./containers.nix
  ];
  # NOTE: ./nixos.nix is a standalone NixOS module, not a flake-parts module.
  # It is surfaced via flake.nixosModules.default (see ../flake.nix), so it must
  # NOT be imported here.
}
