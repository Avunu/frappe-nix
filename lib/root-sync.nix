# Keeps a bench's workspace root — pyproject.toml, and uv.lock when the root
# moved — in step with what this frappe-nix expects of one, on shell entry:
# `frappe-nix-root-sync <bench-root>`. The logic is Python,
# py/frappe_nix_tools/frappe_nix_tools/apps/root_sync.py,
# whose docstring has the reasoning; this is its glue: the store paths it
# needs, as the JSON FRAPPE_NIX_CLI_CONFIG names.
#
# `python -I -m` from an environment that holds frappe-nix-tools, as
# lib/bench-cli.nix runs the bench commands: not the console script, whose
# wrapper would put its own PATH ahead of the dev shell's (uv is taken from
# PATH on purpose: the dev shell's own writes the lock).

{
  pkgs,
  # lib/build-deps.nix's `sync`, as a path: after the lock, the managed
  # [tool.uv.extra-build-dependencies] block in step with it. Null skips it.
  buildDepsSync ? null,
  # frappe-nix-tools (lib/standards/outputs.nix).
  frappeNixTools ? (import ./standards/outputs.nix { inherit pkgs; }).frappeNixTools,
}:

let
  workspaceTool = import ./workspace-tool.nix { inherit pkgs; };
  python = pkgs.python314.withPackages (_: [ frappeNixTools ]);
  # What root_sync.py reads (frappe_nix_tools/apps/root_sync.py, KEYS).
  config = pkgs.writeText "frappe-nix-root-sync.json" (
    builtins.toJSON {
      workspace = "${workspaceTool}/bin/frappe-nix-workspace";
      # Unrendered: its tokens are all inside strings, so it parses, and
      # ensure-root reads only the token-free tables from it.
      template = "${../templates/bench/pyproject.toml}";
      inherit buildDepsSync;
    }
  );
in
pkgs.writeShellApplication {
  name = "frappe-nix-root-sync";
  text = ''
    FRAPPE_NIX_CLI_CONFIG=${config} exec ${python}/bin/python3 -I -m frappe_nix_tools.apps.root_sync "$@"
  '';
}
