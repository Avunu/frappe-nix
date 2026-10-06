# frappe-nix's own dev shell (`.envrc`: `use flake . --no-pure-eval`).
#
# What a bench gets from frappe-nix, pointed back at frappe-nix: an `env/`
# virtualenv with the upstream Frappe that dev/uv.lock pins, so the editor and ty
# resolve `import frappe` in runtime/ and lib/; and prek, wired to the commit
# hook on entry for .pre-commit-config.yaml. That file is plain pre-commit
# config, not generated from here: it works the same without this shell.
#
# No services. The parts of frappe-nix that need a database are exercised by its
# flake checks and NixOS VM tests, not by this shell.
{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:

let
  dev = import ./env.nix { inherit pkgs inputs; };
  root = config.devenv.root;
  system = pkgs.stdenv.hostPlatform.system;
in
{
  # devenv's flake integration has no root of its own. `use flake` evaluates
  # from the checkout; a pure `nix flake check` has no PWD and only needs the
  # shell to evaluate, not to be entered.
  devenv.root =
    let
      pwd = builtins.getEnv "PWD";
    in
    if pwd != "" then pwd else "/nonexistent/frappe-nix";

  packages = [
    dev.devPythonEnv
    pkgs.prek
    pkgs.uv
    pkgs.nixfmt
    pkgs.statix
    pkgs.deadnix
    pkgs.shellcheck
  ];

  languages.nix = {
    enable = true;
    lsp.package = pkgs.nixd;
  };

  env = {
    # The working tree's packages ahead of site-packages: the interpreter
    # imports the code being edited, not a copy. ty.toml lists the same roots.
    PYTHONPATH = lib.concatMapStringsSep ":" (r: "${root}/${r}") dev.sourceRoots;
    # The editable frappe-runtime install expands this at interpreter start.
    FRAPPE_NIX_DEV_ROOT = "${root}/dev";
    # For reading, and for go-to-definition outside the editor.
    FRAPPE_UPSTREAM_SRC = "${dev.frappeSrc}";
    UV_PYTHON = "${dev.python}/bin/python";
    UV_PYTHON_DOWNLOADS = "never";
    # uv is here to lock dev/, not to install into a store path.
    UV_NO_SYNC = "1";
  };

  scripts = {
    sync-frappe-upstream = {
      description = "Re-mirror Frappe's ruff config and ruff pins (dev/uv.lock, .pre-commit-config.yaml) from the revision dev/uv.lock pins";
      # Resolved through the flake rather than $FRAPPE_UPSTREAM_SRC, which is the
      # revision this shell was entered with: after `update-frappe` re-locks,
      # that is the old one.
      exec = ''
        set -euo pipefail
        src=$(nix build --no-link --print-out-paths "${root}#checks.${system}.frappe-upstream-config.frappeSrc")
        ${dev.frappeUpstream}/bin/frappe-upstream sync "$src" "${root}"
        uv lock --project "${root}/dev"
      '';
    };
    update-frappe = {
      description = "Move dev/uv.lock to the tip of Frappe's branch, then sync-frappe-upstream";
      exec = ''
        set -euo pipefail
        ${dev.frappeUpstream}/bin/frappe-upstream pin "${root}"
        uv lock --project "${root}/dev" --upgrade-package frappe
        sync-frappe-upstream
      '';
    };
  };

  enterShell = ''
    # Where a bench keeps its virtualenv, so editor settings carry over.
    ln -sfn ${dev.devPythonEnv} "${root}/env"

    # The commit hook for .pre-commit-config.yaml. Idempotent; a failure (say a
    # core.hooksPath of your own) is shown and does not stop the shell.
    if git rev-parse --git-dir >/dev/null 2>&1; then
      prek install >/dev/null || true
    fi
  '';
}
