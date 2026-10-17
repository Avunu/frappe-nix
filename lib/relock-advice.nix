# How a bench re-locks: the last paragraph of the stale-lock message
# (python-devops-nix's lib.python.lockAudit, wrapped round every virtualenv in
# lib/python.nix). App mode, whose workspace root is generated into the store
# and has no `uv lock` to run, passes its own (modules/devenv.nix).
''
  This is what an app bump looks like: a submodule moved to a commit whose
  pyproject.toml declares something new, and uv.lock was not regenerated.
  Re-lock the workspace from the bench root:

      uv lock

  This error blocks the dev shell, so `uv` is not on PATH unless you are
  already in one. Without it:

      nix run .#relock
''
