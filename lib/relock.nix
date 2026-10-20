# `nix run .#relock` (and `relock` in the dev shell): regenerates what a
# repository commits on frappe-nix's behalf — frappe-nix's one consumer-facing
# relock. In a bench, from its root: uv.lock (and the managed build-dependency
# block), then node-locks/. In app mode: <lockDir>/uv.lock from a writable copy
# of the workspace the flake assembles, and <lockDir>/node-locks/, both staged.
#
# The logic is Python, py/frappe_nix_tools/frappe_nix_tools/deps/relock.py
# (PLAN.md, Track D, D4), whose docstring has the reasoning; this is its glue:
# the tools it runs on PATH, and what it needs to know as the JSON
# FRAPPE_NIX_CLI_CONFIG names.
#
# A flake app, deliberately outside every other output's dependency graph: it
# has to evaluate when nothing that touches the Python workspace can, which is
# exactly when it is needed (a stale or missing lock fails evaluation).
{
  pkgs,
  lib ? pkgs.lib,
  appMode,
  # The uv, nodejs and yarn the locks are resolved with: the dev shell's own,
  # so a fallback is resolved by the yarn that will install it.
  uv ? pkgs.uv,
  nodejs ? pkgs.nodejs,
  yarn ? pkgs.yarn,
  # lib/node-locks.nix's frappe-nix-node-locks.
  nodeLocksTool,
  # The nested frontends the lock generator must leave alone.
  nodeNestedFrontendExcludes ? [ ],
  # lib/build-deps.nix's sync, after the lock (a bench); null runs nothing.
  buildDepsSyncBin ? null,
  # App mode: the assembled workspace without its lock, and where the
  # repository keeps the locks (frappe-nix.app.lockDir).
  workspace ? null,
  lockDir ? "nix",
  # frappe-nix-tools (lib/standards/outputs.nix).
  frappeNixTools ? (import ./standards/outputs.nix { inherit pkgs; }).frappeNixTools,
}:

let
  python = pkgs.python314.withPackages (_: [ frappeNixTools ]);
  # What relock.py reads (frappe_nix_tools/deps/config.py, RelockConfig).
  config = pkgs.writeText "frappe-nix-relock.json" (
    builtins.toJSON {
      inherit appMode lockDir nodeNestedFrontendExcludes;
      nodeLocks = lib.getExe' nodeLocksTool "frappe-nix-node-locks";
      buildDepsSync = buildDepsSyncBin;
      workspace = if appMode then "${workspace}" else null;
    }
  );
in
pkgs.writeShellApplication {
  name = "frappe-nix-relock";
  runtimeInputs = [
    uv
    nodejs
    yarn
  ]
  ++ lib.optional appMode pkgs.git;
  text = ''
    FRAPPE_NIX_CLI_CONFIG=${config} exec ${python}/bin/python3 -I -m frappe_nix_tools.deps.relock "$@"
  '';
}
