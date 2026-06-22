# Top-level flake-parts module for frappe-nix.
# Imports sub-modules and defines the option namespace.
# Receives frappe-nix's own inputs via closure from flake.nix.
{ frappe-nix-inputs }:
{ inputs, ... }:
{
  imports = [
    inputs.devenv.flakeModule
    ./devenv.nix
    ./containers.nix
    ./nixos.nix
  ];

  _module.args.frappe-nix-inputs = frappe-nix-inputs;
}
