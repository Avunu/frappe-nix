# Top-level flake-parts module for frappe-nix.
# Imports sub-modules and defines the option namespace.
{ inputs, ... }:
{
  imports = [
    inputs.devenv.flakeModule
    ./devenv.nix
    ./containers.nix
    ./nixos.nix
  ];
}
