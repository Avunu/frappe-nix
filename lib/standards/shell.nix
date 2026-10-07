# What the app standards add to an app-mode dev shell
# (docs/app-standards/spec.md §1.2, S35). modules/devenv.nix merges it in when
# `frappe-nix.app.enable` is set, passing the app's pyproject.toml:
#
#   optedIn     whether the app opted in: its pyproject.toml has a
#               [tool.frappe-nix] table. Everything below is empty without it,
#               so an app that has not opted in sees no change. Exported for
#               modules/devenv.nix's other opt-in-gated parts.
#   packages    every lib/standards/tools/*.nix tool (`frappe-nix`, and the
#               frappe-listing, frappe-icon, … wrappers as they land) plus
#               `frappe-init`, so `frappe-init --sync` runs the frappe-nix the
#               app's flake.lock pins;
#   apps        the same tools as `nix run .#<tool>`, and `.#frappe-init`.
#               (Before opting in, `nix run github:Avunu/frappe-nix#frappe-init
#               -- --sync --standards <profile>` or adding the table by hand
#               opts an app in; its flake outputs stay exactly main's.)
#   enterShell  per-clone git settings the managed files rely on.
#   devenvModule
#               the devenv module modules/devenv.nix imports into an
#               app-mode shell: `packages` and, only when opted in,
#               `enterShell` (a definition of its own, so an app that has
#               not opted in gets main's enterShell byte for byte). Its `key`
#               lets tests/standards/hookpoints.nix find it among the
#               shell's imports.
#
# The opt-in test is a line match on the file's text, never a TOML parse: Nix's
# fromTOML rejects valid TOML (datetimes), and its error escapes
# builtins.tryEval, so a parse would break evaluation for apps that never opted
# in. A line opts in when it opens [tool.frappe-nix], a subtable of it, or an
# array of tables under it; a commented-out line does not. frappe-nix-tools
# applies the same match (common/pyproject.py) and refuses, exit 2, a
# pyproject.toml whose TOML table and header line disagree (a quoted key,
# spaces in the brackets, dotted keys under [tool], a header in a string), so
# the shell and the tools never differ silently on whether an app opted in.
{
  pkgs,
  # The app's pyproject.toml (a path), or null: not opted in.
  pyproject ? null,
}:

let
  inherit (pkgs) lib;

  outputs = import ./outputs.nix { inherit pkgs; };
  frappeInit = import ../init.nix { inherit pkgs; };

  optInLine = line: builtins.match "[[:space:]]*\\[{1,2}tool\\.frappe-nix[].].*" line != null;
  optedInText =
    text: builtins.any (line: builtins.isString line && optInLine line) (builtins.split "\n" text);
  optedIn =
    pyproject != null && builtins.pathExists pyproject && optedInText (builtins.readFile pyproject);

  frappeInitApp = {
    frappe-init = {
      type = "app";
      program = "${frappeInit}/bin/frappe-init";
      meta.description = "Scaffold or sync this app (frappe-init --sync / --check / --standards)";
    };
  };

  packages = lib.optionals optedIn (builtins.attrValues outputs.tools ++ [ frappeInit ]);

  # Runs from the repository root. `git blame` skips the mass reformats
  # .git-blame-ignore-revs lists (spec §2.20); set only when it differs, so a
  # clone's own setting is not rewritten on every entry.
  enterShell = lib.optionalString optedIn ''
    if [ -f "''${DEVENV_ROOT:-.}/.git-blame-ignore-revs" ] \
      && [ "$(git -C "''${DEVENV_ROOT:-.}" config --get blame.ignoreRevsFile || true)" != ".git-blame-ignore-revs" ]; then
      git -C "''${DEVENV_ROOT:-.}" config blame.ignoreRevsFile .git-blame-ignore-revs || true
    fi
  '';
in
{
  inherit
    optedIn
    optedInText
    packages
    enterShell
    ;

  apps = lib.optionalAttrs optedIn (outputs.apps // frappeInitApp);

  devenvModule = {
    key = "frappe-nix:lib/standards/shell.nix";
    inherit packages;
    enterShell = lib.mkIf optedIn enterShell;
  };
}
