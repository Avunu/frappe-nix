# Scaffolder / migrator for frappe-nix benches — the `nix run` entry point.
#
# Produces a `frappe-init` executable that either writes a new bench (a thin
# frappe-nix wrapper flake + apps as git submodules, with python/node pinned
# from lib/frappe-presets.json) or converts an existing `bench init` bench in
# place. The mode is detected from the target directory.
{ pkgs }:

let
  inherit (pkgs) lib;

  workspaceTool = import ./workspace-tool.nix { inherit pkgs; };

  # `ironclad` itself (the bin/ link, which propagates nothing), for --sync,
  # --check and the app scaffold. Sync's phase B also runs uv, yarn and node
  # (tools/uv.lock, yarn.lock), so they come along: `nix run …#frappe-init --
  # --sync` then needs only nix and git (spec §3.3), and the dev-shell
  # re-entry is only the fallback for a bare `ironclad sync`.
  ironclad = (import ./ironclad/outputs.nix { inherit pkgs; }).packages.ironclad;

  # Concatenated rather than sourced at runtime: writeShellApplication runs
  # shellcheck over the produced file, and a `source` would hide every
  # cross-file definition from it. main.sh must come last — it is the only file
  # with top-level code.
  sources = [
    ./sh/common.sh
    ./sh/detect.sh
    ./sh/template.sh
    ./sh/secrets.sh
    ./sh/apps.sh
    ./sh/pipeline.sh
    ./sh/init.sh
    ./sh/app-init.sh
    ./sh/app-sync.sh
    ./sh/migrate.sh
    ./sh/main.sh
  ];
in
pkgs.writeShellApplication {
  name = "frappe-init";
  runtimeInputs = with pkgs; [
    git
    uv
    gum
    jq
    gawk
    gnused
    gnugrep
    coreutils
    findutils
    diffutils
    workspaceTool
    ironclad
    nodejs_24
    yarn
    # uv lock --project tools resolves for requires-python >=3.14.
    python314
  ];
  # The scripts are plain .sh files (no Nix-string escaping); bake the presets
  # file and template dir store paths in via placeholders.
  text =
    builtins.replaceStrings
      [ "@PRESETS@" "@TEMPLATE@" "@APP_TEMPLATE@" ]
      [
        "${./frappe-presets.json}"
        "${../templates/bench}"
        "${../templates/app}"
      ]
      (lib.concatMapStringsSep "\n" builtins.readFile sources);
}
