---
title: Everyday commands
description: The bench wrapper that redirects the commands needing frappe-nix handling, and the scripts behind it, from bench provision to bench-update.
order: 1
tags: [bench, scripts, cli]
updated: 2026-10-21
---

The shell ships an umbrella **`bench` wrapper**. It shadows the virtualenv's `bench` (devenv wraps scripts with `lib.hiPrioSet`, so it wins on `PATH`) and transparently redirects the subcommands that need frappe-nix handling. You run normal `bench` commands and the right thing happens on a read-only Nix store, in a uv workspace, with git submodules. `frappe` is the same umbrella under the framework's name (`frappe migrate` is `bench migrate`), and `frappe ui` opens the [menu](menu.md).

## What the wrapper redirects

| You run                                                                | Redirected to              | Why                                                                                                                                                                                  |
| ---------------------------------------------------------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `bench update …`                                                       | `bench-update`             | Vanilla update pip-installs and assumes upstream remotes.                                                                                                                            |
| `bench build …`                                                        | `bench-build`              | Brings `node_modules` back in step with the apps first.                                                                                                                              |
| `bench setup requirements …`                                           | `bench-setup-requirements` | Verifies and repairs `node_modules` and the yarn cache, installs what is missing, checks the Python requirements are locked. See [The development shell](README.md#check-on-demand). |
| `bench get-app [--branch <B>] <URL_OR_ALIAS>`                          | `bench-get-app`            | A git submodule and a uv workspace member instead of pip.                                                                                                                            |
| `bench new-app`                                                        | `bench-new-app`            | Scaffold plus uv workspace, skipping the failing pip step.                                                                                                                           |
| `bench remove-app [--force] [--no-backup]`                             | `bench-remove-app`         | Submodule, `.gitmodules` and uv workspace instead of `apps.txt` and pip.                                                                                                             |
| `bench rename-app [--no-pull] <OLD> <NEW>`                             | `bench-rename-app`         | Moves the submodule, its clone and the workspace member instead of removing and re-adding them.                                                                                      |
| `bench restore [<FILE>]`                                               | `bench-restore`            | Injects the MariaDB root credentials. With no file, fetches the latest production backup.                                                                                            |
| `bench provision [ADMIN_PASSWORD]`                                     | frappe-nix's own           | Creates `$FRAPPE_SITE` and installs every app from `sites/apps.txt`. Not an upstream command. See below.                                                                            |
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
| `provision-site [ADMIN_PASSWORD]`                                               | `bench provision`'s name before frappe-nix 1.2, kept as an alias until 2.0.0. It says it is deprecated on stderr, then runs `bench provision`.                                  |
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
| `bench-rename-app [--url <U>] [--branch <B>] [--no-pull] <OLD> <NEW>`           | The bench's half of renaming an app. See below.                                                                                                                                 |
| `relock [--uv-only\|--node-locks [<TARGET>…]] [<UV_LOCK_FLAGS>…]`              | Re-lock what the bench commits: `uv.lock`, then the fallback locks in `node-locks/`. The same program as `nix run .#relock`. See [Dependencies and locks](locks.md#relock).    |
| `update-deps`                                                                   | `relock`'s Python half, then `uv sync`, `yarn install` in every app, and the fallback locks in `node-locks/`. Exits 1 if any step failed.                                      |

### bench provision

`bench provision` creates the site named by `FRAPPE_SITE` and installs every app `sites/apps.txt` lists, in that order. The Administrator password is the first argument, or `admin`. Put `--` before a password that starts with a dash. `--help` prints its usage.

- It runs `bench new-site --force`, which drops an existing database for that site. To install an app added after the site exists, run `reconcile-apps` instead.
- When `bench new-site` asks for the MariaDB root password, leave it blank and press Enter.
- It stops at the first `bench new-site` or `install-app` that fails, with that command's exit status. A missing `FRAPPE_SITE` or `FRAPPE_DB_SOCKET` is exit 1, and a second argument is exit 2, before anything runs.
- It ends with the site's URL, on the port the running bench leased, or on `webserver_port` from `sites/common_site_config.json` when the bench is down.

`provision-site` was its name before frappe-nix 1.2. It still works, and says on stderr that it is deprecated. It goes in frappe-nix 2.0.0.

### bench-update

`bench-update` is the submodule-aware replacement for `bench update`:

- `--pull` fetches each submodule's `.gitmodules` branch from the remote that carries its declared URL, because `origin` is often a developer's fork.
- It refuses to discard local commits. A shallow clone whose pin and tip share no history at all, the shape a depth-limited fetch leaves behind and what git shows as a phantom "1 ahead", is deepened back to the pin's date and re-checked first, since that is never a local commit.
- It clears the one kind of local change that is only tool output: an uncommitted `yarn.lock` or `package-lock.json` that the pull would overwrite (its `package.json` unedited), or an untracked one that upstream adds. It says which. Every other edit is carried across.
- When git still refuses, it skips only that app, shows git's reason and lists the apps not updated when it finishes. The rest of the pull, the registry, migrate and build still run.
- It checks out a registered submodule that has no checkout (a fresh clone's, a deinitialized one's) before pulling it with the rest, and skips local apps and reports stray repos.
- It then regenerates `sites/apps.json` for the new pins, the fallback locks in `node-locks/` for the apps without a `yarn.lock` whose `package.json` moved, and re-locks the workspace (`uv lock --upgrade-package frappe-runtime`) when a `pyproject.toml` did. The flag re-reads the runtime's dependency ranges, which plain `uv lock` leaves at the revision first locked.
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

### bench-rename-app

`bench-rename-app` is the bench's half of renaming an app. [Renaming an app](../app-standards/rename.md) has three: the package in the app's repository and the sites that have it installed are `frappe-rename-app`'s, and a `git pull` of the renamed repository does none of this one. The pull leaves the old directory behind as an untracked repository and the new path empty, and the registry skips an app whose package is not named for its directory.

```bash
bench-rename-app esign esign_webforms
```

- It **moves** the checkout, its clone under `.git/modules` and its `.git/config` section, so nothing is downloaded again and `node_modules` and local history come along. Removing the app and adding it again does neither.
- It points `.gitmodules` at the renamed repository (the URL with `esign` replaced by `esign_webforms`, or `--url`) and the branch (kept, or `--branch`), staging only that change next to the gitlink, so other uncommitted `.gitmodules` edits stay as they were. `git mv` refuses outright while `.gitmodules` has an unstaged edit.
- It fetches the branch's tip from the new URL and checks it out, which is what carries the renamed package. `--no-pull` renames the checkout where it is, and keeps the pin the bench recorded.
- It renames the uv workspace member and its source, regenerates `sites/apps.{txt,json}`, drops `node-locks/<OLD>` and `sites/assets/<OLD>`, and runs `uv lock`.
- It changes nothing until the tip has been fetched and shown to hold `<NEW>/hooks.py`, so a failed fetch, a repository whose package is not renamed yet, a checkout with uncommitted changes, or history that does not contain the checkout leaves the bench as it was.

It does not touch a site. The dev site still records the old name, so it ends by printing the `frappe-nix.renamedApps` entry that has `devenv up` rename it before anything installs. A vendored (local) app is moved with `git add`, with no repository to retarget.

### update-deps

`update-deps` re-locks and reinstalls: `uv lock --upgrade-package frappe-runtime` and the managed build-dependency block, `uv sync`, `yarn install` in every app with a `package.json`, then the fallback locks. A failed `uv lock` stops it. A failed `uv sync` or `yarn install` does not stop the other apps, but the command exits 1 and names what failed. To re-lock without reinstalling, run `relock`.

In a bench the Node step is `yarn install --pure-lockfile`. It installs from `package.json` but never rewrites an app's own `yarn.lock`, which belongs to the app's repository. To update one on purpose, run `yarn install` in that app. An app's postinstall can still rewrite a nested frontend's lock here, which `bench-update --pull` discards when the pull would overwrite it. In app mode the lock is yours and is written as before.
