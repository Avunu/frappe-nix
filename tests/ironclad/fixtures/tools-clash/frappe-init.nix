# A lib/ironclad/tools file named like an output frappe-nix already has: the
# loader must refuse it rather than let one flake shadow it silently.
{ pkgs, ... }:
pkgs.writeShellScriptBin "frappe-init" "true"
