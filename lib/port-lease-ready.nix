# `frappe-nix-lease-ready RUNTIME`: whether a bench's port lease is up, as
# `frappe-nix-port-lease ready` decides it, without starting Python; see
# lib/port-lease-ready.sh. pkgs.flock (discoteq's, 28 KiB) on Linux and darwin
# alike, so the check and its exit codes are one program everywhere.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-lease-ready";
  runtimeInputs = [ pkgs.flock ];
  text = builtins.readFile ./port-lease-ready.sh;
}
