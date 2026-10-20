# `frappe-nix-port-lease`: lib/port-lease.py, which leases a dev bench's TCP
# ports at `devenv up` and holds them while it runs (the frappe-ports process).
# Stdlib only, on the minimal interpreter: it is the first process every
# `devenv up` starts, and the preflight before it.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-port-lease";
  text = ''
    exec ${pkgs.python3Minimal}/bin/python3 ${./port-lease.py} "$@"
  '';
}
