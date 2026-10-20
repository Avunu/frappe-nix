# The flake reference a project's flake.lock pins for one of its inputs, read
# from the lock alone: the project's flake is not evaluated, because on a fresh
# clone it cannot be until its submodules are checked out (lib/bootstrap.sh).
# The lock's node for an input need not carry the input's name ("frappe-nix_2"),
# so it is looked up through the root node. Throws when the lock has no such
# input, or one that follows another (a list, not a node name).
#
#   import ./lib/bootstrap-lock.nix { lock = /path/to/flake.lock; input = "frappe-nix"; }
{
  lock,
  input,
}:
let
  inherit (builtins.fromJSON (builtins.readFile lock)) nodes;
  node = nodes.root.inputs.${input} or null;
in
if builtins.isString node then
  builtins.flakeRefToString nodes.${node}.locked
else
  throw "${toString lock}: no input ${input} of its own"
