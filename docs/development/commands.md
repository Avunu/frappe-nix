---
title: Everyday commands
description: The bench wrapper that redirects the commands needing frappe-nix handling, and the scripts behind it, from provision-site to bench-update.
order: 1
tags: [bench, scripts, cli]
updated: 2026-10-06
---

The shell ships an umbrella **`bench` wrapper**. It shadows the virtualenv's `bench` (devenv wraps scripts with `lib.hiPrioSet`, so it wins on `PATH`) and transparently redirects the subcommands that need frappe-nix handling. You run normal `bench` commands and the right thing happens on a read-only Nix store, in a uv workspace, with git submodules.

## What the wrapper redirects

| You run                                                                | Redirected to              | Why                                                                                                                                                                                  |
| ---------------------------------------------------------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `bench update …`                                                       | `bench-update`             | Vanilla update pip-installs and assumes upstream remotes.                                                                                                                            |
| `bench build …`                                                        | `bench-build`              | Brings `node_modules` back in step with the apps first.                                                                                                                              |
| `bench setup requirements …`                                           | `bench-setup-requirements` | Verifies and repairs `node_modules` and the yarn cache, installs what is missing, checks the Python requirements are locked. See [The development shell](README.md#check-on-demand). |
| `bench get-app [--branch <B>] <URL_OR_ALIAS>`                          | `bench-get-app`            | A git submodule and a uv workspace member instead of pip.                                                                                                                            |
| `bench new-app`                                                        | `bench-new-app`            | Scaffold plus uv workspace, skipping the failing pip step.                                                                                                                           |
| `bench remove-app [--force] [--no-backup]`                             | `bench-remove-app`         | Submodule, `.gitmodules` and uv workspace instead of `apps.txt` and pip.                                                                                                             |
| `bench restore [<FILE>]`                                               | `bench-restore`            | Injects the MariaDB root credentials. With no file, fetches the latest production backup.                                                                                            |
| `bench new-site`                                                       | the real bench             | Injects `--db-socket` and `--db-root-username root`, so site creation is non-interactive.                                                                                            |
| `bench migrate`, `console`, `clear-cache`                              | `bench-*`                  | Inject `--site $FRAPPE_SITE`.                                                                                                                                                        |
| everything else (`serve`, `install-app`, `uninstall-app`, `--help`, …) | the real bench             | Unchanged.                                                                                                                                                                           |

Two caveats follow from this:

- Redirected commands follow the frappe-nix scripts' flags, not vanilla bench's. `bench update` takes `--pull`, `--migrate`, `--build` or `--node-locks`, not `--reset`.
- Interception is subcommand-first, so `bench --site X migrate`, with a global option before the subcommand, passes straight through to the real bench.

Recursion is avoided with a `_FRAPPE_BENCH_RAW` environment guard. The specialized scripts export it and the wrapper checks it, so a script's own nested `bench …` calls reach the real CLI, whether you run `bench update` or `bench-update` directly. To reach the real command on purpose, use `_FRAPPE_BENCH_RAW=1 bench update --reset`.

The wrapper wins only while it is first on `PATH`. `source env/bin/activate`, or an editor that activates `./env` in the terminals it opens, puts the virtualenv's own `bin/` ahead of it. `frappe_benchcli` ([`lib/benchcli`](../../lib/benchcli)), grafted into the dev virtualenv, covers that: the virtualenv's `bench update` and `bench build` hand over to `bench-update` and `bench-build` with the same arguments, saying so on stderr. `_FRAPPE_BENCH_RAW=1` bypasses it as it does the wrapper, and outside the dev shell, where those scripts are not on `PATH`, it refuses with the reason instead of running the stock command. Only those two commands are handed over, as only they fail outright or skip a step here. Everything else behaves as the virtualenv's `bench` always did.

> [!NOTE]
> The real `bench update` stays reachable, so the shell keeps its first step working. `bench update` starts with `bench.patches.run()`, which executes every entry in the `patches.txt` that frappe-bench ships and that the **bench root's** `patches.txt` does not record as done. bench deleted the v3 and v4 patch modules in 2022 but still lists them, so a bench root with no record dies immediately on `ModuleNotFoundError: No module named 'bench.patches.v3'`. It stays dead, because the failed run rewrites the root file as one empty byte. `bench init` avoids this by copying the shipped list in verbatim. frappe-nix never runs `bench init`, and the file is gitignored, so `enterShell` reconciles it instead, on every shell entry, non-destructively, and silently unless it changes something. See [`lib/bench-patches.nix`](../../lib/bench-patches.nix) for why every patch is recorded as done, not only the two that cannot import.

## The scripts

These back the wrapper and are also callable directly.

| Script                                                                          | Description                                                                                                                                                                     |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `provision-site [ADMIN_PASSWORD]`                                               | Create `$FRAPPE_SITE` and install every app from `sites/apps.txt`. The Administrator password defaults to `admin`.                                                              |
| `reconcile-apps [SITE]`                                                         | Install whatever `sites/apps.txt` names that the site does not have installed yet. Idempotent. Also runs automatically; see [Installed-app drift](apps.md#installed-app-drift). |
| `bench-update [--pull\|--migrate\|--build\|--node-locks]`                       | The submodule-aware replacement for `bench update`. See below.                                                                                                                  |
| `bench-migrate`, `bench-build`, `bench-clear-cache`, `bench-console`            | Thin bench wrappers that honor `$FRAPPE_SITE`. With `offlineMigrate.enable`, `bench-migrate` runs `bench-offline-migrate` first and does not migrate if it fails.               |
| `bench-offline-migrate [--plan\|--dry-run] [--threshold <ROWS>] [-- <PT_ARGS>]` | Alter the large tables the next migrate would alter, online. `--plan` changes nothing and needs no percona-toolkit. See [Migrate large tables online](online-migrations.md).    |
| `bench-restore [<FILE>\|--at <STAMP>\|--list]`                                  | Restore from a SQL backup, or from the latest one in the object store. See [Restore a production backup](restore-from-production.md).                                           |
| `setup-backup-access`                                                           | Prompt for the object-store credentials, test them against the bucket, and write `backup-access.age`.                                                                           |
| `edit-secret`                                                                   | Decrypt a secret into `$EDITOR` and re-encrypt it to the declared recipients. Reads stdin when it is not a terminal, so a secret can be piped in.                               |
| `rekey-secrets`                                                                 | Re-encrypt every secret after changing recipients.                                                                                                                              |
| `check-secrets [<NAME>]`                                                        | Verify the `.age` files match the declared recipients. With a name, explain why you cannot decrypt one.                                                                         |
| `bench-get-app [--branch <B>] <URL_OR_ALIAS>`                                   | Add an app as a git submodule and register it. See below.                                                                                                                       |
| `bench-new-app`                                                                 | Scaffold a new app as a local app (committed source, no nested git) and register it the same way.                                                                               |
| `bench-remove-app [--force] [--no-backup]`                                      | The inverse of `bench-get-app`. See below.                                                                                                                                      |
| `update-deps`                                                                   | Re-lock and sync Python (uv) and Node (yarn) across all apps, then refresh the fallback locks in `node-locks/`.                                                                 |

### bench-update

`bench-update` is the submodule-aware replacement for `bench update`:

- `--pull` fetches each submodule's `.gitmodules` branch from the remote that carries its declared URL, because `origin` is often a developer's fork.
- It refuses to discard local commits. A shallow clone whose pin and tip share no history at all, the shape a depth-limited fetch leaves behind and what git shows as a phantom "1 ahead", is deepened back to the pin's date and re-checked first, since that is never a local commit.
- It clears the one kind of local change that is only tool output: an uncommitted `yarn.lock` or `package-lock.json` that the pull would overwrite (its `package.json` unedited), or an untracked one that upstream adds. It says which. Every other edit is carried across.
- When git still refuses, it skips only that app, shows git's reason and lists the apps not updated when it finishes. The rest of the pull, the registry, migrate and build still run.
- It checks out a registered submodule that has no checkout (a fresh clone's, a deinitialized one's) before pulling it with the rest, and skips local apps and reports stray repos.
- It then regenerates `sites/apps.json` for the new pins, the fallback locks in `node-locks/` for the apps without a `yarn.lock` whose `package.json` moved, and re-locks the workspace (`uv lock`) when a `pyproject.toml` did.
- `--node-locks [<TARGET>…]` regenerates `node-locks/` for every app and nested frontend without a `yarn.lock` of its own. A named target gets a lock forced over the `yarn.lock` it ships.

In [app mode](../scaffolding/app-mode.md) only `--migrate` and `--build` are available.

### bench-get-app

`bench-get-app` adds an app as a git submodule and registers it in the uv workspace and in `sites/apps.{txt,json}`.

- `helpdesk` resolves to `frappe/helpdesk`. `owner/repo` and full URLs also work.
- The branch, from `--branch` or else the remote's default, is recorded in `.gitmodules`, which is what `bench-update --pull` follows.
- The directory is named for the Frappe app, the package holding `hooks.py`, not for the repository as stock `bench get-app` does. `frappe/flow_client` installs as `apps/flow`, since frappe imports `flow.hooks`. The name is read from a throwaway clone of the remote, with no checkout and no file contents, before anything in the bench is touched. A repository with no `hooks.py`, or several packages and none named for it, keeps the repository's name, with a warning.

### bench-remove-app

`bench-remove-app` is the inverse, for the bench as a whole:

- It deinitializes the submodule and drops it from `.gitmodules`, staging only that removal so other uncommitted `.gitmodules` edits stay as they were.
- It unregisters the app from the uv workspace and `sites/apps.{txt,json}`, drops `node-locks/` and `sites/assets/`, and runs `uv lock`.
- A local app is moved to `.frappe-nix-backup/<APP>-<STAMP>` unless you pass `--no-backup`. A submodule is not backed up, since `bench-get-app` re-adds it.
- It refuses while any site still has the app installed, or cannot say whether it does, and while the submodule has uncommitted changes. `--force` skips both checks.

Taking an app out of one site is still the real `bench --site <SITE> uninstall-app <APP>`. With `appsReconcile.enable` on, though, the next `devenv up` reinstalls anything `sites/apps.txt` still lists.

### update-deps

In a bench the Node step is `yarn install --pure-lockfile`. It installs from `package.json` but never rewrites an app's own `yarn.lock`, which belongs to the app's repository. To update one on purpose, run `yarn install` in that app. An app's postinstall can still rewrite a nested frontend's lock here, which `bench-update --pull` discards when the pull would overwrite it. In app mode the lock is yours and is written as before.
