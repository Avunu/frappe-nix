# The `frappe-nix-bench` command (py/frappe_nix_tools, bench_cli.py), run the
# way frappe-init is (lib/init.nix): `python -I -m` from an environment that
# holds the package, not the package's console script. nixpkgs' wrapper for a
# console script prepends python3.14's bin/ and the package's own bin/ (an
# unwrapped frappe-nix) to PATH, and every command restore runs (bench,
# bench-migrate, the fetch, a python3 named on PATH) would inherit that, ahead
# of the dev shell's own. -I also keeps the bench venv's PYTHONPATH out.
{ pkgs, frappeNixTools }:

let
  python = pkgs.python314.withPackages (_: [ frappeNixTools ]);
in
pkgs.writeShellScript "frappe-nix-bench" ''
  exec ${python}/bin/python3 -I -m frappe_nix_tools.bench_cli "$@"
''
