# frappe-nix-node-locks [--exclude=<app/subdir>]... [--command=<cmd>]
# <bench-root> <locks-dir> [<target>...]: writes node-locks/<target>/yarn.lock,
# the fallback lock for a node target that ships no yarn.lock of its own (and
# the forced ones), with the stamp that keeps regenerating it cheap.
#
# The logic is Python, py/frappe_nix_tools/frappe_nix_tools/deps/node_locks.py
# (PLAN.md, Track D, D4), whose docstring has the reasoning; this is its glue.
# yarn is taken from PATH on purpose: the dev shell pins its own nodejs and
# yarn via `frappe-nix.nodejs`, and `relock` puts the same on PATH, so the lock
# is resolved by the yarn that will install it.
{
  pkgs,
  frappeNixTools ? (import ./standards/outputs.nix { inherit pkgs; }).frappeNixTools,
}:

import ./py-tool.nix {
  inherit pkgs frappeNixTools;
  name = "frappe-nix-node-locks";
  module = "frappe_nix_tools.deps.node_locks";
}
