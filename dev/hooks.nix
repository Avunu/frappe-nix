# The git hooks, in one place: the dev shell installs them (prek, via devenv's
# git-hooks module) and the flake's `pre-commit` check runs the same set in the
# sandbox, so a commit and CI cannot disagree about what passes.
#
# The Python tools come from the dev env, which means their versions are
# dev/uv.lock's — ruff's in particular is Frappe's own pin (see
# sync-frappe-upstream), so formatting matches upstream byte for byte.
{ pkgs, env }:

{
  # Fixtures stand in for third-party apps and benches, byte for byte; patches
  # carry trailing whitespace that is part of the hunk; locks are generated.
  excludes = [
    "^tests/fixtures/"
    # Scaffolding with @PLACEHOLDERS@: not valid Nix or TOML until frappe-init
    # fills them in.
    "^templates/"
    "\\.(patch|diff)$"
    "(^|/)(uv|flake|yarn)\\.lock$"
  ];

  hooks = {
    # Python. CodeSorter reorders definitions, so ruff runs after it on the
    # result, and the formatter last.
    #
    # Manual only (`prek run codesorter --hook-stage manual`): besides
    # definitions it sorts the string keys of every dict literal and the
    # keyword arguments of every call, `dict(...)` included, and a dict's order
    # is its iteration order. Here that is the key order of the apps.json entries
    # frappe-nix writes to match bench's, and the order of apps (install order,
    # sites/apps.txt) — the apps-registry, migrate-classic and bench-watch
    # checks all fail after a full pass. It has no per-site opt-out to mark
    # those, so it cannot run unattended.
    codesorter = {
      enable = true;
      name = "codesorter";
      entry = "${env}/bin/codesorter";
      types = [ "python" ];
      require_serial = true;
      before = [ "ruff" ];
      stages = [ "manual" ];
    };
    ruff = {
      enable = true;
      package = env;
      before = [ "ruff-format" ];
    };
    ruff-format.enable = true;
    # The whole tree, not the staged files: changing one module can break a
    # caller in another. --python is explicit because the sandboxed check has no
    # ./env symlink for ty.toml's default to find.
    ty = {
      enable = true;
      name = "ty";
      entry = "${env}/bin/ty check --python ${env}";
      files = "(\\.py|^ty\\.toml|^dev/uv\\.lock)$";
      pass_filenames = false;
    };
    # Local only: re-resolving needs the network. CI catches a stale lock at
    # evaluation instead (lib/lock-audit.nix, through dev/env.nix).
    uv-lock = {
      enable = true;
      entry = "${pkgs.uv}/bin/uv lock --check --project dev";
      files = "^(dev/(pyproject\\.toml|uv\\.lock)|runtime/pyproject\\.toml)$";
    };

    # Nix.
    nixfmt.enable = true;
    statix = {
      enable = true;
      # statix walks the tree itself (pass_filenames = false), so the excludes
      # above do not reach it.
      settings.ignore = [
        "tests/fixtures/*"
        "templates/*"
      ];
    };
    deadnix.enable = true;

    # Frappe's own pre-commit-hooks set, less no-commit-to-branch (a Frappe
    # release-branch policy, not a code check).
    trim-trailing-whitespace.enable = true;
    check-merge-conflicts.enable = true;
    check-python.enable = true;
    check-json.enable = true;
    check-toml.enable = true;
    check-yaml.enable = true;
    python-debug-statements.enable = true;
  };
}
