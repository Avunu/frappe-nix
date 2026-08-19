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
  ];
  # The scripts are plain .sh files (no Nix-string escaping); bake the presets
  # file and template dir store paths in via placeholders.
  text = builtins.replaceStrings [ "@PRESETS@" "@TEMPLATE@" ] [
    "${./frappe-presets.json}"
    "${../templates/bench}"
  ] (lib.concatMapStringsSep "\n" builtins.readFile sources);
}
