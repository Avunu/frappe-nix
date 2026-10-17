---
title: Upgrading frappe-nix
description: Move a bench or an app repository to a newer frappe-nix, what the shell reconciles for you on entry, and what to commit afterwards.
order: 9
tags: [upgrade, flake-update, reconcile]
updated: 2026-10-17
---

To pick up a newer frappe-nix, update the pin and reload the shell:

```bash
nix flake update frappe-nix   # or `nix flake update` for every pin
direnv reload                 # or: nix develop --no-pure-eval
```

That is the whole procedure.

## What the shell reconciles on entry

A frappe-nix bump can ask something new of a bench's **workspace root**. The case so far is `frappe-runtime` becoming a required dependency: a name in `[project].dependencies`, a `[tool.uv.sources]` entry and a build backend named in `[tool.uv.extra-build-dependencies]`. A bench from before the bump has no way to know.

So `enterShell` reconciles `pyproject.toml` on every entry, with the same `ensure-root` step `frappe-init` runs:

- It **adds what is missing** and changes nothing that is already there. Your name, your `requires-python`, your override list and your comments stay, because the file is edited with tomlkit and not rewritten.
- It runs `uv lock` only when that changed the file. On the common path it is one TOML parse and says nothing.

When it does change something, it says so, lists what it added, and tells you to **commit `pyproject.toml` and `uv.lock` and re-enter the shell**, because the shell you are in was built from the previous lock. Until you do, `devenv up` runs whatever shape the old lock supports. A bench whose lock predates the runtime gets the split `web`, `socketio`, `worker` and `scheduler` processes for that session, and the banner says so. Nothing about that shape is degraded: it is `runtime.enable = false`, which `checks.socket` covers.

If `uv lock` fails, because there is no network or because the new requirement genuinely conflicts with an app's pins, both files are put back exactly as they were, mtimes included, and the shell opens anyway. A `pyproject.toml` that declares what `uv.lock` does not carry would fail the _next_ evaluation (see [Dependencies and locks](locks.md#a-stale-uvlock-is-an-evaluation-error)), which is the one outcome this hook exists to avoid: you would be back to fixing the shell from outside it. The next entry retries. To resolve a conflict by hand, make the listed additions in `pyproject.toml`, add the override `uv` asks for, and run `uv lock` in the shell.

It does not touch `[tool.uv.extra-build-dependencies]` beyond adding a frappe-nix entry a hand-kept list lacks. A bench scaffolded before frappe-nix derived that table from `uv.lock` keeps its list until you move it; see [Moving a bench to the derived list](locks.md#moving-a-bench-to-the-derived-list). If the bench's `pythonOverrides` lists `lib.overrides.pycups`, `python-ldap`, `pyvips` or `cairocffi`, evaluation warns: every bench gets those fixes now, so remove them. The aliases are kept for all of 1.x, removed in 2.0.0.

## What it deliberately does not do

The shell hook does not register submodules, vendor apps, edit `.gitignore` or `common_site_config.json`, or `git add` anything. That is [the reconciler's](../scaffolding/migrate.md) job, and the reconciler still does all of it for the cases a shell hook should not decide:

```bash
nix run github:Avunu/frappe-nix -- -y
```

The scaffolder's `--frappe-version` and `--site` are now `--version` and `--default-site`. The old names work, with a warning, through 1.x, until frappe-nix 2.0.0; see [Renamed flags](../reference/scaffolder.md#renamed-flags).

Some options were renamed to the names frappe-nix shares with odoo-nix (python-devops-nix's `CONVENTIONS.md`). The old names still work, with an evaluation warning naming the new one, through 1.x, until frappe-nix 2.0.0:

| Old                                     | New                                |
| --------------------------------------- | ---------------------------------- |
| `benchName`                             | `name`                             |
| `siteName`                              | `defaultSite`                      |
| `restore.withFiles`                     | `restore.files`                    |
| `devguard.mail.smtpPort`                | `mailpit.port`                     |
| `devguard.mail.port`                    | `mailpit.port`                     |
| `devguard.mail.host`                    | `mailpit.host`                     |
| `devguard.mail.httpPort`                | `mailpit.httpPort`                 |
| `devguard.mail.pop3.*`                  | `mailpit.pop3.*`                   |
| `runtime.dev`                           | `watch.python`                     |
| `assets.reassert.hooks`                 | `watch.reassert.hooks`             |
| `assets.reassert.debounceMs`            | `watch.reassert.debounceMs`        |
| `devguard.scheduler.enable`             | `devguard.jobs.enable`             |
| `devguard.scheduler.blockServerScripts` | `devguard.jobs.blockServerScripts` |
| `devguard.scheduler.extraBlockedJobs`   | `devguard.jobs.extraBlocked`       |

The last of those follow the development shell's option tree (python-devops-nix's `CONVENTIONS.md`): `watch` is what the shell does when a file changes, `mailpit` is the catcher as a service, `devguard` is guard policy only, and `ports` has one attribute per service (`ports.http`, `ports.db`, `ports.smtp`, `ports.mailUi`, `ports.pop3`, `ports.socketio`) that the rest of the module reads. Two behaviours changed with them:

- **Mailpit runs whether or not the mail guard is on** (`mailpit.enable`, default `true`). Before, it ran only with `devguard.mail.enable`, and turning the guard off also stopped the catcher. The server, workers and scheduler start after it whenever it runs.
- **`watch.python = false` keeps serving `/assets` and `/files`.** `runtime.dev = false` used to turn both off, because frappe-runtime's `--dev` did both. The runtime now has a separate `--serve-assets`, which the shell always passes. With `runtime.enable = false`, `watch.python = false` runs `bench serve --noreload`.

The scheduler guard's environment variables moved with it: `FRAPPE_DEVGUARD_JOBS_EXTRA_BLOCKED` (was `FRAPPE_DEVGUARD_SCHEDULER_EXTRA_BLOCKED_JOBS`), `FRAPPE_DEVGUARD_JOBS_BLOCK_SERVER_SCRIPTS`, `FRAPPE_DEVGUARD_JOBS_ENABLE` and `FRAPPE_DEVGUARD_DISABLE=jobs`. The old ones are read, with a warning, for the same period. Its log lines still read `frappe_devguard[scheduler]`.

The NixOS module moved the same way: `services.frappe` is now `services.frappe-nix`, and a site's `extraConfig` and `extraConfigFiles` are now `settings` and `settingsFiles`, the nixpkgs name for a block that becomes the framework's config file (`site_config.json`). The old names still work, with a NixOS warning, through 1.x, until frappe-nix 2.0.0.

The environment variables frappe-nix's own tools read are all `FRAPPE_NIX_*` now. The old names are still read, with a warning, through 1.x, until frappe-nix 2.0.0:

| Old                                    | New                                        |
| -------------------------------------- | ------------------------------------------ |
| `FRAPPE_OFFLINE_MIGRATE`               | `FRAPPE_NIX_OFFLINE_MIGRATE`               |
| `FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD` | `FRAPPE_NIX_OFFLINE_MIGRATE_ROW_THRESHOLD` |
| `FRAPPE_OFFLINE_MIGRATE_PT_OSC`        | `FRAPPE_NIX_OFFLINE_MIGRATE_PT_OSC`        |
| `FRAPPE_BENCH_UPDATE_FORCE`            | `FRAPPE_NIX_BENCH_UPDATE_FORCE`            |
| `FRAPPE_WEB_SOCKET`                    | `FRAPPE_NIX_WEB_SOCKET`                    |
| `FRAPPE_REPLICA_DB_SOCKET`             | `FRAPPE_NIX_REPLICA_DB_SOCKET`             |
| `FRAPPE_LOG_LEVEL`                     | `FRAPPE_NIX_LOG_LEVEL`                     |
| `FRAPPE_TEST_ADMIN_PASSWORD`           | `FRAPPE_NIX_TEST_ADMIN_PASSWORD`           |

`services.frappe-nix` sets both `FRAPPE_NIX_WEB_SOCKET` and `FRAPPE_WEB_SOCKET`, and both log-level names, in the same window, so a bench package built from an older frappe-nix than the module still finds them.

Frappe's own variables (`FRAPPE_SITE`, `FRAPPE_DB_SOCKET`, `FRAPPE_REDIS_CACHE` and the rest) keep their names, and so do the grafts' switches (`FRAPPE_DEVGUARD_*`, `FRAPPE_UNIXSOCK_ENABLED`).

A change to the _options_ a bench's `flake.nix` sets cannot be reconciled from inside the shell. `nodeOfflineHashes` was one such. It stays an evaluation error that names the edit.

## In app mode

In an [app repository](../scaffolding/app-mode.md) the pins are flake inputs, so a newer frappe-nix is the same `nix flake update frappe-nix`. Moving the Frappe pin or a sibling is `nix flake update frappe`, then `nix run .#relock`, and you commit `flake.lock` with `nix/`.

The bench root's dev group gained `coverage` and `unittest-xml-reporting`, which [`frappe-test`](../app-standards/testing.md) runs the tests under. An app that has not opted in to the [app standards](../app-standards/README.md) keeps the root it had: each of the two stays out of it unless the root package in the committed `nix/uv.lock` already lists it in its `dev` group. On version-15 and version-16 alike, the generated root, the dev environment and the lock stay exactly as they were, and `nix run .#relock` leaves `nix/uv.lock` as it is. A version-16 lock does have both packages, through Frappe's `test` extra, but that does not put them in the dev environment.

`frappe-test` then has no coverage to run under and says so (exit 10): pass `--no-coverage`, or opt in and run `nix run .#relock`, which adds them.

## The runtime moves with frappe-nix

The unified runtime's source is the `runtime/` directory of this repository, so a bump of frappe-nix is also a bump of the runtime, with no relock in any bench. The one exception is a runtime release that adds a new dependency. That needs one relock:

```bash
nix run .#relock -- --upgrade-package frappe-runtime
```

Note the flag: a plain `uv lock` keeps an already resolved git revision and reports success without changing anything. See [The unified runtime](../production/runtime.md#how-a-bench-gets-it).
