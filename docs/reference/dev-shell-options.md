---
title: Dev shell options
description: Every perSystem.frappe-nix option that configures the development shell, the production package and the container images, with types and defaults.
order: 1
tags: [options, reference, devenv]
updated: 2026-10-07
---

These options sit under `perSystem.frappe-nix` in your flake. The types and defaults were checked against the module by evaluating it. The prose describes what each option does. For a guided tour of the options you will touch most, see [Write the flake by hand](../scaffolding/write-the-flake.md). The options under `frappe-nix.secrets` sit at the top level and have [their own page](secrets-options.md), and the production module's options are under [`services.frappe`](nixos-options.md).

> [!NOTE]
> `devguard.mail.smtpPort`, `devguard.mail.httpPort` and `devguard.mail.pop3.port` default to a base plus `ports.offset`, an offset between 0 and 899 hashed from `benchName`. Nothing moves a port that is taken: `devenv up` stops and names it, and `FRAPPE_NIX_PORT_OFFSET` picks another offset for the shell.

## Core

The options every bench sets.

| Option          | Type         | Default                                          | Notes                                                                                                                                                                                      |
| --------------- | ------------ | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `enable`        | bool         | `false`                                          | Enable the dev shell and the packages.                                                                                                                                                     |
| `benchName`     | str          | required (the normalized `app.name` in app mode) | Identifier for environment names and the container image prefix. It also seeds the per-bench port offsets.                                                                                 |
| `siteName`      | str          | `""`                                             | `FRAPPE_SITE`. Empty means multi-tenancy, with the site set per shell through `.env`.                                                                                                      |
| `workspaceRoot` | path or null | `null`                                           | The bench root, where `pyproject.toml` and `apps/` live, usually `./.`. Required in bench mode. It must stay `null` in app mode, where frappe-nix assembles the workspace itself.          |
| `python`        | package      | `pkgs.python312`                                 | Python interpreter. In app mode, the `app.frappeVersion` preset's.                                                                                                                         |
| `nodejs`        | package      | `pkgs.nodejs_22`                                 | Node.js for frontend builds and the Node realtime server. In app mode, the `app.frappeVersion` preset's.                                                                                   |
| `esbuildTarget` | str          | `"es2022"`                                       | The target Frappe's esbuild pipeline compiles bundles for, exported as `ESBUILD_TARGET` in the dev shell and to `builtBench`. See [Asset builds](../development/assets.md#compile-target). |

## App mode

Set `app.enable = true` in the repository of a single Frappe app. See [Develop a single app](../scaffolding/app-mode.md). `workspaceRoot` must stay `null` there.

| Option              | Type                                    | Default                       | Notes                                                                                                                                                                                                                                                                      |
| ------------------- | --------------------------------------- | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.enable`        | bool                                    | `false`                       | App mode: this flake is one Frappe app's repository, not a bench.                                                                                                                                                                                                          |
| `app.frappe`        | path                                    | (required in app mode)        | The Frappe source, as a `flake = false` input.                                                                                                                                                                                                                             |
| `app.siblings`      | list of `{ name; src; }`                | `[ ]`                         | The other apps the bench should carry, in install order. It is a list and not an attribute set, because that order is the members' order and so the order of `sites/apps.txt`. `name` is the directory name under `apps/`, and `src` is the source, usually a flake input. |
| `app.frappeVersion` | `develop`, `version-15` or `version-16` | `"version-16"`                | The row of [`lib/frappe-presets.json`](../../lib/frappe-presets.json) that drives `python`, `nodejs`, `requires-python` and `override-dependencies`. It does not pin Frappe: `app.frappe` does.                                                                            |
| `app.src`           | path                                    | `inputs.self`                 | The app's own source, which becomes `apps/<NAME>`. Do not filter it: `app.lockDir` is read out of it.                                                                                                                                                                      |
| `app.name`          | str                                     | `[project].name` of `app.src` | The app's directory name under `apps/`, which is Frappe's own app name.                                                                                                                                                                                                    |
| `app.lockDir`       | str                                     | `"nix"`                       | Where the generated-but-committed `uv.lock` (and `node-locks/`, for pins without a `yarn.lock`) live, relative to the repo root.                                                                                                                                           |
| `app.benchDir`      | str                                     | `".frappe-nix/bench"`         | Where the dev shell materializes the writable bench, relative to the repository root. Gitignore it.                                                                                                                                                                        |

## The watcher

How the `watch` process chooses what to rebuild. See [Memory and disk](../development/memory-and-disk.md).

| Option                    | Type                | Default                                 | Notes                                                                                                                                                                                                                                   |
| ------------------------- | ------------------- | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `watch.apps`              | list of str or null | `null`                                  | Apps the `watch` process rebuilds on save. `null` chooses by publisher (`watch.excludePublishers`). See [Memory and disk](../development/memory-and-disk.md).                                                                           |
| `watch.excludePublishers` | list of str         | `[ "Frappe Technologies" ]`             | With `watch.apps` `null`, leave out every app whose `hooks.py` `app_publisher` contains one of these, ignoring case. `[ ]` watches everything, as `bench watch` does.                                                                   |
| `watch.rtl`               | bool                | `false`                                 | Also rebuild right-to-left stylesheets on save. `bench build` always builds them.                                                                                                                                                       |
| `watch.nativeSass`        | bool                | `true` where nixpkgs builds `dart-sass` | Compile the watcher's stylesheets with native Dart Sass ([`lib/sass-embedded.nix`](../../lib/sass-embedded.nix)) instead of Frappe's JavaScript build of it, 3 to 6 times faster per stylesheet. `bench build` keeps Frappe's compiler. |

## MariaDB

| Option                     | Type               | Default        | Notes                                                                                                                                                                                                                                                                                                     |
| -------------------------- | ------------------ | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mariadb.package`          | package            | `pkgs.mariadb` | MariaDB package.                                                                                                                                                                                                                                                                                          |
| `mariadb.initialDatabases` | list of `{ name }` | `[ ]`          | Databases created on the first `devenv up`.                                                                                                                                                                                                                                                               |
| `mariadb.durable`          | bool               | `false`        | Flush InnoDB to disk at every commit and keep the doublewrite buffer, as production does. Off, the log is flushed once a second, which makes commits roughly 30 times faster, at the risk of the last second of commits if the machine crashes. See [Memory and disk](../development/memory-and-disk.md). |
| `mariadb.noCow`            | bool               | `true`         | On btrfs, keep the data directory off copy-on-write (`chattr +C`). Set at shell entry on a directory that is missing or empty. One already holding data is reported, and `frappe-nix-db-nocow migrate "$MYSQL_HOME"` moves it once.                                                                       |

## Process scope

Runs `devenv up` in a systemd scope of its own. See [Memory and disk](../development/memory-and-disk.md#devenv-up-runs-in-a-scope-of-its-own).

| Option                    | Type        | Default | Notes                                                                                                                                                                                                                         |
| ------------------------- | ----------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `processScope.enable`     | bool        | `true`  | Run `devenv up` in a systemd scope of its own, so the dev stack is not accounted to, and killed along with, the editor that started it. Linux with a systemd user manager and the process-compose manager; a no-op elsewhere. |
| `processScope.memoryHigh` | str         | `"40%"` | The scope's `MemoryHigh=`: past it the dev stack is throttled and reclaimed from first. A percentage is of physical RAM.                                                                                                      |
| `processScope.memoryMax`  | str or null | `null`  | The scope's `MemoryMax=`, or none.                                                                                                                                                                                            |

## Node builds

| Option                       | Type           | Default | Notes                                                                                                                                                                                                                                                       |
| ---------------------------- | -------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nodeOverrides`              | attrs of attrs | `{ }`   | Per node target (an app, or `app/subdir`): extra attributes for the derivation that runs its `yarn install --offline`, such as `postPatch`, `nativeBuildInputs` or a `yarnOfflineCache` of your own. See [Dependencies and locks](../development/locks.md). |
| `nodeNestedFrontendExcludes` | list of str    | `[ ]`   | Nested frontends (`app/subdir`) to leave out: no `node_modules`, no assets, and the parent's build script that drives them is dropped. See [Dependencies and locks](../development/locks.md#a-yarnlock-that-cannot-resolve-offline).                        |

## Packages, scripts and environment

| Option                      | Type            | Default       | Notes                                                                                                                                                                                                             |
| --------------------------- | --------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pythonOverrides`           | overlay         | no-op overlay | Extra Python package set overlay. Compose it with `lib.overrides`.                                                                                                                                                |
| `extraDevPackages`          | list of package | `[ ]`         | Extra packages on the dev shell.                                                                                                                                                                                  |
| `extraContainerRuntimeDeps` | list of package | `[ ]`         | Extra runtime packages in production containers.                                                                                                                                                                  |
| `extraPackages`             | list of package | `[ ]`         | Extra packages installed in both the dev shell and any production deployment of this package. The NixOS module reads them off `builtBench`'s `passthru.extraPackages`, so no server-side configuration is needed. |
| `extraLibraryPaths`         | list of package | `[ ]`         | Extra `LD_LIBRARY_PATH` entries (dev shell).                                                                                                                                                                      |
| `extraScripts`              | attrs           | `{ }`         | Extra devenv scripts, merged over the standard set.                                                                                                                                                               |
| `extraEnv`                  | attrs of str    | `{ }`         | Extra environment variables (dev shell).                                                                                                                                                                          |

## Runtime

The unified `frappe-runtime` process. See [The unified runtime](../production/runtime.md). The production counterparts are under [`services.frappe.runtime`](nixos-options.md#top-level-options).

| Option               | Type         | Default                       | Notes                                                                                                                                    |
| -------------------- | ------------ | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime.enable`     | bool         | `true`                        | Run one `frappe-runtime` process (web, realtime, jobs and scheduler) instead of the split web, socketio, worker and scheduler processes. |
| `runtime.jobThreads` | int          | `2`                           | Concurrent background jobs inside the runtime process.                                                                                   |
| `runtime.dev`        | bool         | `true`                        | Pass `--dev`: reload on a Python source change, and serve `/assets` and `/files` from the runtime.                                       |
| `runtime.src`        | null or path | `runtime/` in this repository | The source `frappe-runtime` is built from, overriding the revision `uv.lock` resolved. `null` hands control back to `uv.lock`.           |

## Ports and sockets

See [The development shell](../development/README.md#ports-and-sockets).

| Option           | Type         | Default | Notes                                                                                                                                                              |
| ---------------- | ------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `sockets.enable` | bool         | `true`  | Put MariaDB, Redis, the realtime server and the web server on unix sockets behind one nginx port, so several benches can run at once. Needs Frappe 15.46 or newer. |
| `ports.offset`   | int, 0–899   | a hash of `benchName` | The offset every TCP port derives from: web `8000 +`, MariaDB `3306 +`, Mailpit `19000`, `20000` and `21000 +`. In app mode a linked worktree hashes `benchName@<path>`. `FRAPPE_NIX_PORT_OFFSET` wins over it. |
| `ports.base`     | port or null | `null`  | The web port, overriding `8000 + ports.offset`. Mailpit and MariaDB keep theirs.                                                                                 |

## Apps and assets

| Option                       | Type        | Default          | Notes                                                                                                                                                                                                           |
| ---------------------------- | ----------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `appsReconcile.enable`       | bool        | `siteName != ""` | Install whatever `sites/apps.txt` names that `siteName`'s site does not have installed yet, on every `devenv up`. See [Apps in a bench](../development/apps.md#installed-app-drift).                            |
| `renamedApps`                | attrs of str | `{ }`           | Apps renamed in place, `OLD = NEW`: `reconcile-apps` runs `frappe-rename-app --site` before it installs anything. See [Renaming an app](../ironclad/rename.md).                                                    |
| `replacedApps`               | attrs of str | `{ }`           | Apps replaced by a new app, `OLD = NEW`: `reconcile-apps` installs NEW and uninstalls OLD on a site that has OLD. See [Renaming an app](../ironclad/rename.md#replacing-an-app-instead).                       |
| `assets.reassert.hooks`      | list of str | `[ ]`            | `bench execute` targets run when `sites/assets/assets.json` names a bundle file that does not exist on disk. Empty by default, so it names no app. See [Asset builds](../development/assets.md#assetsreassert). |
| `assets.reassert.debounceMs` | int         | `750`            | How long `assets.json` must sit unmodified before the bench-watch-driven check re-reads it.                                                                                                                     |

## Development guard rails

Each guard is independently switchable, and `devguard.enable = false` turns them all off. See [Development guard rails](../development/guard-rails.md).

| Option                                   | Type              | Default                          | Notes                                                                                                        |
| ---------------------------------------- | ----------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `devguard.enable`                        | bool              | `true`                           | Master switch for all guard rails. See [Development guard rails](../development/guard-rails.md).             |
| `devguard.mail.enable`                   | bool              | `true`                           | Route all outgoing mail to Mailpit, and refuse IMAP and POP3.                                                |
| `devguard.mail.host`                     | str               | `"127.0.0.1"`                    | Interface Mailpit binds and Frappe is redirected to.                                                         |
| `devguard.mail.smtpPort`                 | port              | 19000 plus a hash of `benchName` | Catcher SMTP port (per bench).                                                                               |
| `devguard.mail.httpPort`                 | port              | 20000 plus a hash of `benchName` | Mailpit web UI port (per bench).                                                                             |
| `devguard.mail.sender`                   | str               | `"notifications@example.com"`    | From address used only on sites with no outgoing Email Account at all.                                       |
| `devguard.mail.unmute`                   | bool              | `true`                           | Ignore `mute_emails` in `site_config.json`.                                                                  |
| `devguard.mail.pop3.enable`              | bool              | `false`                          | Serve incoming mail from Mailpit's POP3 listener instead of blocking it.                                     |
| `devguard.mail.pop3.port`                | port              | 21000 plus a hash of `benchName` | Mailpit POP3 port (per bench).                                                                               |
| `devguard.mail.pop3.user`                | str               | `"dev"`                          | Mailpit POP3 username (local development only).                                                              |
| `devguard.mail.pop3.password`            | str               | `"dev"`                          | Mailpit POP3 password (local development only).                                                              |
| `devguard.backups.enable`                | bool              | `true`                           | Block Dropbox, S3, Google Drive and Frappe Cloud backup upload.                                              |
| `devguard.objectstore.enable`            | bool              | `true`                           | Never delete from, or overwrite in, the configured S3 bucket.                                                |
| `devguard.objectstore.mode`              | `local` or `push` | `"local"`                        | `local`: `cloud_storage` writes to local disk. `push`: new files are uploaded to the bucket (additive only). |
| `devguard.objectstore.conditionalWrites` | bool              | `true`                           | Send `If-None-Match: *` on push-mode writes. Set `false` for Backblaze B2, which answers it with 501.        |
| `devguard.integrations.enable`           | bool              | `true`                           | Block outbound HTTP via `frappe.integrations.utils.make_request`.                                            |
| `devguard.integrations.allowHosts`       | list of str       | `[ ]`                            | Hosts to permit anyway. Loopback is always allowed.                                                          |
| `devguard.google.enable`                 | bool              | `true`                           | Block Google Calendar, Contacts and Drive access.                                                            |
| `devguard.webhooks.enable`               | bool              | `true`                           | Drop outbound Webhook requests.                                                                              |
| `devguard.plaid.enable`                  | bool              | `true`                           | Block Plaid bank synchronization.                                                                            |
| `devguard.scheduler.enable`              | bool              | `true`                           | Skip scheduled jobs that reach production services.                                                          |
| `devguard.scheduler.blockServerScripts`  | bool              | `true`                           | Skip Scheduled Job Types backed by a Server Script.                                                          |
| `devguard.scheduler.extraBlockedJobs`    | list of str       | `[ ]`                            | Extra `Scheduled Job Type.method` values to skip (exact match).                                              |

## Restore

How `bench restore` fetches a production backup. See [Restore a production backup](../development/restore-from-production.md).

| Option                    | Type                       | Default                                        | Notes                                                                                                                          |
| ------------------------- | -------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `restore.enable`          | bool                       | `frappe-nix.secrets.backupAccess.enable`       | Let `bench restore` fetch from the object store. See [Restore a production backup](../development/restore-from-production.md). |
| `restore.prefix`          | str                        | `""`                                           | Path inside the bucket. Normally carried in the secret as `BACKUPS_PREFIX` instead.                                            |
| `restore.withFiles`       | `none`, `private` or `all` | `"none"`                                       | File archives to pull by default. They are routinely tens of GB.                                                               |
| `restore.carryConfigKeys` | list of str                | `[ "encryption_key" "backup_encryption_key" ]` | Allowlist of keys copied from the backup's site config.                                                                        |
| `restore.migrate`         | bool                       | `true`                                         | Run `bench migrate` after restoring.                                                                                           |
| `restore.requireDevguard` | bool                       | `true`                                         | Refuse to write production's encryption key into an unguarded bench.                                                           |

## Offline migrate

Alter large tables online in front of `bench migrate`. See [Migrate large tables online](../development/online-migrations.md).

| Option                        | Type | Default  | Notes                                                                                                                                                                                           |
| ----------------------------- | ---- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `offlineMigrate.enable`       | bool | `false`  | Run `bench-offline-migrate` in front of every `bench migrate`, so the ALTERs of large tables cannot lock them. Adds percona-toolkit to the closure.                                             |
| `offlineMigrate.rowThreshold` | int  | `100000` | Rows at which a table is altered online. `0` sends every table with a pending change through pt-online-schema-change. Overridable with `FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD` or `--threshold`. |

## Containers

See [Build production images](../production/images.md).

| Option                | Type | Default | Notes                                                                                                                                                                       |
| --------------------- | ---- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `containers.enable`   | bool | `false` | Build the OCI images.                                                                                                                                                       |
| `containers.registry` | str  | `""`    | Container registry URL prefix. Declared but not used: frappe-nix does not push images. See [Build production images](../production/images.md#building-loading-and-pushing). |
