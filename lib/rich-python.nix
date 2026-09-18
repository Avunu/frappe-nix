# A nixpkgs Python interpreter with `rich` usable standalone -- for
# frappe-nix's own small pieces of tooling (currently just the dev-shell
# welcome banner, lib/banner.py) that have nothing to do with a *consuming*
# bench's own uv-managed Python dependency graph, and so have no business
# being added to it.
#
# Usage:
#   import ./lib/rich-python.nix { inherit pkgs python; }                     # rich only
#   import ./lib/rich-python.nix { inherit pkgs python; extraPackages = ps: [ ps.click ]; }

{
  pkgs,
  python,
  extraPackages ? (_ps: [ ]),
}:

python.withPackages (ps: [ ps.rich ] ++ extraPackages ps)
