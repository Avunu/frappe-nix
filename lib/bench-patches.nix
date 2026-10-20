# frappe-nix-bench-patches <shipped-patches.txt> [bench-root]: records every
# patch frappe-bench ships as done in the bench root's patches.txt, as `bench
# init` would, so `bench update` does not die importing bench.patches.v3 (a
# module bench deleted in 2022 and still lists). Run from enterShell.
#
# The logic is Python, py/frappe_nix_tools/frappe_nix_tools/deps/bench_patches.py
# (PLAN.md, Track D, D4), whose docstring has the reasoning; this is its glue.
{
  pkgs,
  frappeNixTools ? (import ./standards/outputs.nix { inherit pkgs; }).frappeNixTools,
}:

import ./py-tool.nix {
  inherit pkgs frappeNixTools;
  name = "frappe-nix-bench-patches";
  module = "frappe_nix_tools.deps.bench_patches";
}
