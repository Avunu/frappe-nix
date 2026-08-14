# Top-level flake-parts module for frappe-nix.
# Imports sub-modules and defines the option namespace.
{ inputs, ... }:
{
  imports = [
    inputs.devenv.flakeModule
    # Imported here rather than by the consumer, so a bench declares only
    # `frappe-nix.secrets` and never agenix-shell directly.
    #
    # Safe against a consumer that also imports it: flake-parts deduplicates
    # modules by `key`, which importApply sets to the store path of
    # agenix-shell's own module file — and lib.mkFlake's `self.inputs //
    # consumerInputs` merge makes both resolve to the same path. The one way to
    # break it is reaching past that merge (frappe-nix.inputs.agenix-shell.…)
    # while pinning a different revision, which yields two paths, two keys, and
    # an "already declared" throw.
    inputs.agenix-shell.flakeModules.default
    ./secrets.nix
    ./devenv.nix
    ./containers.nix
  ];
  # NOTE: ./nixos.nix is a standalone NixOS module, not a flake-parts module.
  # It is surfaced via flake.nixosModules.default (see ../flake.nix), so it must
  # NOT be imported here.
}
