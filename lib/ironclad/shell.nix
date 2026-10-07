# What the Ironclad platform adds to an app-mode dev shell
# (docs/ironclad/spec.md §1.2). modules/devenv.nix merges it in when
# `frappe-nix.app.enable` is set:
#
#   packages    every lib/ironclad/tools/*.nix tool (`ironclad`, and the
#               frappe-listing, frappe-icon, … wrappers as they land) plus
#               `frappe-init`, so `frappe-init --sync` runs the frappe-nix the
#               app's flake.lock pins;
#   apps        the same tools as `nix run .#<tool>`, and `.#frappe-init`;
#   enterShell  per-clone git settings the managed files rely on.
{ pkgs }:

let
  outputs = import ./outputs.nix { inherit pkgs; };
  frappeInit = import ../init.nix { inherit pkgs; };
in
{
  packages = builtins.attrValues outputs.packages ++ [ frappeInit ];

  apps = outputs.apps // {
    frappe-init = {
      type = "app";
      program = "${frappeInit}/bin/frappe-init";
      meta.description = "Sync this app's frappe-nix managed files (frappe-init --sync / --check)";
    };
  };

  # Runs from the repository root. `git blame` skips the mass reformats
  # .git-blame-ignore-revs lists (spec §2.20); set only when it differs, so a
  # clone's own setting is not rewritten on every entry.
  enterShell = ''
    if [ -f "''${DEVENV_ROOT:-.}/.git-blame-ignore-revs" ] \
      && [ "$(git -C "''${DEVENV_ROOT:-.}" config --get blame.ignoreRevsFile || true)" != ".git-blame-ignore-revs" ]; then
      git -C "''${DEVENV_ROOT:-.}" config blame.ignoreRevsFile .git-blame-ignore-revs || true
    fi
  '';
}
