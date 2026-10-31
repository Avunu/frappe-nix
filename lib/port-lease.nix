# `frappe-nix-port-lease`: lib/port-lease.py, which leases a dev bench's TCP
# ports at `devenv up` and holds them while it runs (the frappe-ports process).
# Stdlib only, on the minimal interpreter: it is the first process every
# `devenv up` starts, and the preflight before it.
#
# Isolated (-I). The script is a file directly under /nix/store, and Python
# puts a script's directory first on sys.path: every import the tool made
# looked in /nix/store first, listing it whole, and again whenever its mtime
# had moved since the last import. On a host where Nix adds and deletes store
# paths all the time (a CI runner host) that is every import: one start-up
# listed a 300,000-entry directory 59 times and took 6.8 s instead of 0.2 s on
# an idle workstation, 25 s at a million entries, and the holder on the loaded
# runner host had not written ports.json after 300 s. -I leaves the script's
# directory off sys.path, and PYTHONPATH (the bench's apps, in a dev shell)
# with it: nothing but the interpreter's own stdlib is searched.
{ pkgs }:

let
  # The interpreter as the tool runs it; tests/port-lease.sh runs a copy of the
  # script beside decoy modules with exactly this.
  python = "${pkgs.python3Minimal}/bin/python3 -I";
in
(pkgs.writeShellApplication {
  name = "frappe-nix-port-lease";
  text = ''
    exec ${python} ${./port-lease.py} "$@"
  '';
}).overrideAttrs
  (old: {
    passthru = (old.passthru or { }) // {
      inherit python;
    };
  })
