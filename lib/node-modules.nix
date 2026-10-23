# frappe-nix-node-modules <bench-root> <app>...: keeps each app's mutable dev
# node_modules in step with the app's manifests, reinstalling an app whose
# package.json or yarn.lock (its own or a nested frontend's) moved since the
# last install. Run on every shell entry and in front of every build.
#
# The logic is Python, py/frappe_nix_tools/frappe_nix_tools/deps/node_modules.py
# whose docstring has the reasoning; this is its glue.
# yarn is taken from PATH on purpose: the dev shell pins its own nodejs and
# yarn via `frappe-nix.nodejs`.
{
  pkgs,
  frappeNixTools ? (import ./standards/outputs.nix { inherit pkgs; }).frappeNixTools,
}:

import ./py-tool.nix {
  inherit pkgs frappeNixTools;
  name = "frappe-nix-node-modules";
  module = "frappe_nix_tools.deps.node_modules";
}
