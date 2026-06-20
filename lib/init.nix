# Scaffolder for new frappe-nix benches — the `nix run` entry point.
#
# Produces a `frappe-init` executable that interactively (or via flags) writes a
# new bench: a thin frappe-nix wrapper flake + apps as git submodules, with the
# python/node pinned from lib/frappe-presets.json.
{ pkgs }:

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
  ];
  # The script is a plain .sh file (no Nix-string escaping); bake the presets
  # file and template dir store paths in via placeholders.
  text = builtins.replaceStrings
    [ "@PRESETS@" "@TEMPLATE@" ]
    [ "${./frappe-presets.json}" "${../templates/bench}" ]
    (builtins.readFile ./frappe-init.sh);
}
