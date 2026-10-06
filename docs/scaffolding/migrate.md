---
title: Migrate an existing bench
description: Convert a classic bench init bench, or a half-converted one, into a frappe-nix repository in place, without deleting anything.
order: 2
tags: [scaffolding, migrate, bench, submodules]
updated: 2026-10-06
---

The same entry point converts a classic `bench init` bench, or a half-converted one, into a frappe-nix repository. It detects the mode from the directory, so from inside a bench you run the same command as for a new one. Do a dry run first to read the plan.

```bash
cd ~/frappe-bench
nix run github:Avunu/frappe-nix -- --dry-run    # inspect the plan first
nix run github:Avunu/frappe-nix                 # then migrate
```

It is a **reconciler, not a converter**. It probes what the bench already has, adds only what is missing, repairs drift and never deletes. Running it on an already-migrated bench is a no-op.

Nothing is committed. The result is staged, so `git diff --cached` is your review. Add `--commit` to commit it.

> [!NOTE]
> The migrator supports `version-15` and newer. Older apps ship a `setup.py` with no `pyproject.toml`, so they cannot be uv workspace members, and the Python and Node pins differ. Upgrade the bench first with `bench switch-to-branch version-15`, or pass `--frappe-version version-15` to proceed anyway.

## What it does

Concretely, it:

- **Detects the Frappe version** from the branch, then the bench's `sites/apps.json`, then `frappe.__version__`, and pins Python and Node from the matching [preset](README.md#versions-and-presets).
- **Initializes git** at the bench root if needed, and registers each app under `apps/` as a **git submodule pinned at its current commit**. Nothing is fast-forwarded. It records the app's actual branch in `.gitmodules`, without which `bench-update --pull` silently skips the app, and adds an `origin` alias when the app only has `upstream`.
- **Vendors apps with no usable remote.** The nested `.git` moves to `.frappe-nix-backup/` with a provenance JSON, and the source is committed into the bench. An untracked nested repository is invisible to the flake and would vanish from the build.
- **Writes the project files:** `flake.nix`, `pyproject.toml`, `.envrc` and `uv.lock`. It merges into an existing `pyproject.toml` rather than replacing it, and shims a `pyproject.toml` for vendored apps that only ship `setup.py`.
- **Regenerates `sites/apps.txt` and `sites/apps.json`** from the workspace members it just registered. See [Apps in a bench](../development/apps.md#the-app-registry). An app that could not become a member is on `PYTHONPATH` but not registered, and the report says so.
- **Reconciles `sites/common_site_config.json`.** It forces the per-bench web and socketio port that the dev shell derives from the bench name and preserves everything else. It drops production-only keys (`host_name`, `http_port`, `restart_*`) and keys the socket setup supersedes (`db_host`, `db_port`, the `redis_*` URLs, `file_watcher_port`). It blanks `mariadb_root_password`, since the file is about to be committed.
- **Extends `.gitignore`** with a managed block so `sites/*/site_config.json`, site `private/` and `public/` data, `Procfile`, `patches.txt`, `config/*.conf` and `node_modules` stay out of git. It then verifies with `git check-ignore` that nothing the build needs got excluded.
- **Moves a classic `env/` virtualenv** to `.frappe-nix-backup/`. A real `env/` directory silently defeats the dev shell's `ln -sfn` and leaves `bench` on the stale interpreter.

> [!WARNING]
> The migrator **reports a tracked `sites/*/site_config.json`**. That file holds the site's encryption key, database password and object-storage credentials. The managed `.gitignore` block excludes the path, but git keeps honoring an index entry regardless, so adding the rule changes nothing until the file is untracked. The migrator never deletes, so it prints the `git rm --cached` command and leaves the decision to you. Treat every credential the file has held as disclosed: the values are in the repository's history, not only its worktree, so untracking protects the next commit and nothing before it. Rotate them.

## Flags

| Flag                      | Effect                                                                                                                                 |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `--dry-run`               | Print the full plan (per-app disposition, warnings) and exit.                                                                          |
| `-y`, `--yes`             | Skip the confirmation. Required to migrate without a terminal.                                                                         |
| `--frappe-version <V>`    | Override version detection.                                                                                                            |
| `--migrate`, `--init`     | Force the mode instead of detecting it.                                                                                                |
| `--vendor <A,B>`          | Vendor these apps even though they have a remote.                                                                                      |
| `--no-vendor`             | Abort instead of vendoring an app with no usable remote.                                                                               |
| `--allow-file-remotes`    | Accept filesystem paths as submodule URLs. This breaks other clones.                                                                   |
| `--legacy-apps <POLICY>`  | `shim`, `skip` or `abort`, for apps with only a `setup.py`. A skipped app is not a workspace member, so it is not in `sites/apps.txt`. |
| `--strict`                | Treat dirty or unpushed apps as errors.                                                                                                |
| `--keep-db-root-password` | Do not blank `mariadb_root_password`.                                                                                                  |
| `--commit[=<MESSAGE>]`    | Commit the migration instead of only staging it.                                                                                       |
| `--absorb-gitdirs`        | Run `git submodule absorbgitdirs` after registering the apps.                                                                          |
| `--site <SITE>`           | Default site. Needed without a terminal when the bench has several sites and no `default_site`.                                        |

The complete list is in [Scaffolder reference](../reference/scaffolder.md).

## After the migration

1. Review what was staged: `git diff --cached --stat`.
2. If any app ships a `package.json` without a `yarn.lock`, run `bench-update --node-locks` inside the dev shell before the first `nix build`. The fallback lock is not generated by the migration, because it needs the network and yarn. Evaluation tells you which apps, if any.
3. Enter the shell with `direnv allow` and start the stack with `devenv up`. If the bench has no site yet, `provision-site` creates one. If you declare [secrets](../development/secrets.md) first, `bench restore` can clone the site from the latest production backup instead.

## What it cannot fix

- An app pinned at a commit that is on no remote branch builds on your machine and nowhere else.
- An app whose remote is unreachable will fail on a fresh clone of the bench.

Both are reported as warnings, and `--strict` turns the first into an error.

A `setup.py`-only app that is a _submodule_ cannot be shimmed, because a generated `pyproject.toml` would sit outside the pinned commit. Vendor it with `--vendor <APP>`, or fix it upstream.

The bench root must be its own git repository. A bench nested inside another repository is reported, and `--force` is needed to create a nested repository anyway.
