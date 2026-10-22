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
#               app's flake.lock pins; `prek`, which runs the prek the app's
#               tools/uv.lock pins (the README's `prek install`); and nixfmt,
#               statix and deadnix, what
#               `frappe-test --nix-lint` runs over the app's flake.nix and
#               nix/*.nix (spec §5.1 stage 8), for interactive use;
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
# spaces in the brackets, dotted keys under [tool], a header in a string). It
# reads the file's bytes with their line endings, as builtins.readFile does, so
# a file whose lines end in a lone CR (one line here) is a TOML error there.
# The shell and the tools thus never differ silently on whether an app opted in.
{
  pkgs,
  # The app's pyproject.toml (a path), or null: not opted in.
  pyproject ? null,
  # For frappe-init's first clone of an app (lib/init.nix); null in the
  # checks that only read this file's shape.
  python-devops-nix ? null,
}:

let
  inherit (pkgs) lib;

  outputs = import ./outputs.nix { inherit pkgs; };
  frappeInit = import ../init.nix { inherit pkgs python-devops-nix; };

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

  # The hook runner the managed .pre-commit-config.yaml is written for, at the version
  # the app pins in tools/uv.lock (S1): the same prek CI's lint job runs as
  # `uv run --frozen --project tools prek`, never a second pin. The README's
  # `prek install` and a bare `prek run` need it on PATH; `prek install` writes the
  # synced binary's own path into the git hook. From the app's root (DEVENV_ROOT),
  # whatever the working directory, as tools/ is there.
  prek = pkgs.writeShellApplication {
    name = "prek";
    runtimeInputs = [
      pkgs.git
      pkgs.uv
    ];
    text = ''
      root="''${DEVENV_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
      if [ ! -f "$root/tools/pyproject.toml" ]; then
        echo "prek: $root/tools/pyproject.toml is missing; it pins prek while .pre-commit-config.yaml is live (frappe-init --sync)" >&2
        exit 3
      fi
      # uv run finds a command on PATH after the project's environment: with no prek
      # pinned there, that is this wrapper again.
      if [ -n "''${FRAPPE_NIX_PREK_WRAPPER:-}" ]; then
        echo "prek: $root/tools/pyproject.toml does not pin prek (frappe-init --sync adds it while .pre-commit-config.yaml is live)" >&2
        exit 3
      fi
      export FRAPPE_NIX_PREK_WRAPPER=1
      exec uv run --frozen --project "$root/tools" prek "$@"
    '';
    meta.description = "prek, as the app's tools/uv.lock pins it";
  };

  packages = lib.optionals optedIn (
    builtins.attrValues outputs.tools
    ++ [
      frappeInit
      prek
    ]
    # Pinned by frappe-nix's nixpkgs; frappe-test carries its own copies
    # (lib/scripts.d/frappe-test.nix), so its stage 8 needs none of these.
    ++ [
      pkgs.nixfmt
      pkgs.statix
      pkgs.deadnix
    ]
  );

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
