---
title: Upgrading frappe-nix
description: Move a bench or an app repository to a newer frappe-nix, what the shell reconciles for you on entry, what to commit afterwards, and how to move a 1.x bench to 2.0.
order: 9
tags: [upgrade, flake-update, reconcile, release]
updated: 2026-10-28
---

To pick up a newer frappe-nix, update the pin and reload the shell:

```bash
nix flake update frappe-nix   # or `nix flake update` for every pin
direnv reload                 # or: nix develop --no-pure-eval
```

That is the whole procedure.

## Releases

frappe-nix is released with release-please. A `fix:` merged to `main` makes a patch release and a `feat:` a minor one. Within a major release nothing a bench sets is taken away: a renamed option, flag or environment variable keeps its old name, with a warning, through every release of that major, and the old names go in the next major release. A change that alters a running deployment without anyone asking for it ships opt-in, and becomes the default in the next major release. A major release is cut by a `Release-As: <N>.0.0` footer (the pr-policy check refuses `!` and `BREAKING CHANGE`), and this page says what to change: [1.x to 2.0](#1x-to-20).

| `frappe-nix.url`                     | Follows                                       |
| ------------------------------------ | --------------------------------------------- |
| `github:Avunu/frappe-nix/release-2`  | every 2.x release, and nothing that breaks it |
| `github:Avunu/frappe-nix/release-1`  | every 1.x release; it stays at the last one   |
| `github:Avunu/frappe-nix/v2.0.0`     | that one release                              |
| `github:Avunu/frappe-nix`            | `main`, unreleased changes included           |

Each `vN.*` tag moves a `release-N` branch, which only ever fast-forwards: `release-2` is created by the first 2.x release, and `release-1` stays where the last 1.x release left it. The scaffolder writes plain `github:Avunu/frappe-nix` for a bench. An app that opted in to the [app standards](../app-standards/versioning.md) follows `release-<N>` of the frappe-nix that last synced it, so it moves to `release-2` when you run `frappe-init --sync` with a 2.x frappe-nix.

## 1.x to 2.0

frappe-nix 2.0.0 removes every alias 1.x kept, makes `db_name` and `db_type` the NixOS module's own, and turns on two defaults that 1.x shipped opt-in: the systemd sandbox for every unit, and non-root container images. An old name is not ignored where a bench is likely to have set it: the shell, the host or the command fails with a message that names its replacement, so evaluating your flake shows what is left to change.

Do it in this order:

1. [Point the flake at 2.x](#1-point-the-flake-at-2x).
2. [Rename the dev shell options](#2-rename-the-dev-shell-options).
3. [Rename the `services.frappe-nix` options](#3-rename-the-servicesfrappe-nix-options), and drop `db_name` and `db_type` from a site's `settings`.
4. [Rename the environment variables, flags and other names](#4-environment-variables-flags-and-other-names).
5. [Check the dev shell](#5-check-the-dev-shell).
6. [Deploy the host with hardening on](#6-deploy-the-host-with-hardening-on).
7. [Move the container images](#7-move-the-container-images): their package names, and their volume's owner.

### 1. Point the flake at 2.x

```nix
frappe-nix.url = "github:Avunu/frappe-nix/release-2";
```

Then `nix flake update frappe-nix`. A bench on plain `github:Avunu/frappe-nix` gets 2.0 with its next update anyway.

### 2. Rename the dev shell options

In `perSystem.frappe-nix`:

| 1.x | 2.0 |
| --- | --- |
| `benchName` | `name`, which also sets devenv's own `name` |
| `siteName` | `defaultSite` |
| `restore.withFiles` | `restore.files` |
| `runtime.dev` | `watch.python` |
| `assets.reassert.hooks`, `assets.reassert.debounceMs` | `watch.reassert.hooks`, `watch.reassert.debounceMs`; the `assets` tree is gone |
| `devguard.mail.host`, `devguard.mail.port` (`devguard.mail.smtpPort`), `devguard.mail.httpPort` | `mailpit.host`, `mailpit.port`, `mailpit.httpPort` |
| `devguard.mail.pop3.{enable,port,user,password}` | `mailpit.pop3.{enable,port,user,password}` |
| `devguard.scheduler.enable`, `.blockServerScripts`, `.extraBlockedJobs` | `devguard.jobs.enable`, `.blockServerScripts`, `.extraBlocked` |
| `containers.aliases` | nothing: the packages are `container-<role>` only. Remove it (see [step 7](#7-move-the-container-images)). |

`devguard.mail.enable`, `devguard.mail.sender` and `devguard.mail.unmute` stay: they are the guard's own. `containers.nonRoot` stays an option, `true` by default now.

The renames follow the development shell's option tree ([python-devops-nix's conventions](https://github.com/Avunu/python-devops-nix/blob/main/docs/conventions.md#the-development-shell-option-tree)): `watch` is what the shell does when a file changes, `mailpit` is the catcher as a service, `devguard` is guard policy only, and `ports` has one attribute per service. Mailpit runs whether or not the mail guard is on (`mailpit.enable`, default `true`), and `watch.python = false` keeps serving `/assets` and `/files`, as it has since the renames landed in 1.x.

### 3. Rename the `services.frappe-nix` options

| 1.x | 2.0 |
| --- | --- |
| `services.frappe.*` | `services.frappe-nix.*`, the same options |
| `sites.<SITE>.extraConfig` | `sites.<SITE>.settings` |
| `sites.<SITE>.extraConfigFiles` | `sites.<SITE>.settingsFiles` |
| `sites.<SITE>.settings.db_name` | `sites.<SITE>.database.name` |
| `sites.<SITE>.settings.db_type` | nothing: the module runs MariaDB only. Remove it. |

Each old name fails an assertion that names the new one. `db_name` and `db_type` are the module's now, at normal priority: `db_name` is `database.name` and `db_type` is `mariadb`. Through 1.x a different value was a warning; now it is a conflicting definition, and one forced past that with `lib.mkForce` fails an assertion. The migrate snapshot and its rollback, `database.createLocally` and the grants all use `database.name`, so a `db_name` that differed was never covered by them. See [Site settings](../reference/nixos-options.md#site-settings).

### 4. Environment variables, flags and other names

The variables frappe-nix's own tools read are `FRAPPE_NIX_*`. The old names are not read:

| 1.x | 2.0 |
| --- | --- |
| `FRAPPE_OFFLINE_MIGRATE` | `FRAPPE_NIX_OFFLINE_MIGRATE` |
| `FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD` | `FRAPPE_NIX_OFFLINE_MIGRATE_ROW_THRESHOLD` |
| `FRAPPE_OFFLINE_MIGRATE_PT_OSC` | `FRAPPE_NIX_OFFLINE_MIGRATE_PT_OSC` |
| `FRAPPE_BENCH_UPDATE_FORCE` | `FRAPPE_NIX_BENCH_UPDATE_FORCE` |
| `FRAPPE_RESTORE_ALLOW_UNGUARDED` | `FRAPPE_NIX_RESTORE_ALLOW_UNGUARDED`; the old name is ignored, so `bench restore` still refuses an unguarded bench |
| `FRAPPE_BACKUP_SOURCE`, `FRAPPE_BACKUP_CACHE` | `FRAPPE_NIX_BACKUP_SOURCE`, `FRAPPE_NIX_BACKUP_CACHE` |
| `FRAPPE_WEB_SOCKET`, `FRAPPE_REPLICA_DB_SOCKET` | `FRAPPE_NIX_WEB_SOCKET`, `FRAPPE_NIX_REPLICA_DB_SOCKET` |
| `FRAPPE_LOG_LEVEL` | `FRAPPE_NIX_LOG_LEVEL` |
| `FRAPPE_TEST_ADMIN_PASSWORD` | `FRAPPE_NIX_TEST_ADMIN_PASSWORD` |
| `FRAPPE_DEVGUARD_SCHEDULER_ENABLE`, `_BLOCK_SERVER_SCRIPTS`, `_BLOCKED_JOBS`, `_EXTRA_BLOCKED_JOBS` | `FRAPPE_DEVGUARD_JOBS_ENABLE`, `_BLOCK_SERVER_SCRIPTS`, `_BLOCKED_JOBS`, `_EXTRA_BLOCKED`; the old names are ignored, so the guard stays on |
| `FRAPPE_DEVGUARD_DISABLE=scheduler` | `FRAPPE_DEVGUARD_DISABLE=jobs` |

`services.frappe-nix` no longer sets `FRAPPE_WEB_SOCKET` and `FRAPPE_LOG_LEVEL` beside the new names, which 1.x did for a bench package built from a frappe-nix older than 1.2. Build the bench package and the host from the same frappe-nix. Frappe's own variables (`FRAPPE_SITE`, `FRAPPE_DB_SOCKET`, `FRAPPE_REDIS_CACHE` and the rest) keep their names, and so do the grafts' switches (`FRAPPE_DEVGUARD_*`, `FRAPPE_UNIXSOCK_ENABLED`).

Commands and other names:

| 1.x | 2.0 |
| --- | --- |
| `frappe-init --frappe-version <V>` | `--version <V>`; `--frappe-version` is a usage error that names it |
| `frappe-init --site <SITE>` | `--default-site <SITE>`; `--site` is a usage error that names it |
| `provision-site [ADMIN_PASSWORD]` | `bench provision [ADMIN_PASSWORD]` |
| `lib.overrides.pycups`, `python-ldap`, `pyvips`, `cairocffi` in `pythonOverrides` | nothing: every bench gets those fixes. Remove them; each fails evaluation, naming itself. |
| `lib.frappeSecrets` | `lib.secrets` |

`frappe-nix sync` keeps its own `--frappe-version` and `--site`, which `frappe-init --sync` passes `--version` and `--default-site` on as.

When `flake.lock` pins another frappe-nix release, `frappe-nix sync` re-runs the `frappe-init` the lock pins. A 1.x sync passes it the old names, and a 2.x `frappe-init` reads them there, so moving an app's lock to 2.x from 1.x tools works.

### 5. Check the dev shell

```bash
direnv reload        # or: nix develop --no-pure-eval
```

Evaluation stops with every removed name the bench still sets and what replaced each, then points here. Fix them and reload until the shell starts.

### 6. Deploy the host with hardening on

`services.frappe-nix.hardening.enable` is `true` by default. Every unit the module defines then runs under python-devops-nix's sandboxing baseline: `ProtectSystem=strict` with only the site's directory writable, no capabilities, a system call filter, and no writable-and-executable memory except for the units that run Node or a PDF renderer (see [The sandbox](../production/nixos-service.md#the-sandbox)). The VM tests run with it on, but a host may do something they do not:

- **Watch the first start**: `systemctl status 'frappe-*'` and `journalctl -b -u 'frappe-*' -p err`. The services order after `frappe-migrate-<SITE>`, so check it first: a migrate that fails leaves the site in maintenance mode.
- **A NixOS (nspawn) container** may not let a unit set up its mount namespace. The unit then fails with `226/NAMESPACE`.
- **A path outside the site's directory** that a unit writes to, such as a `siteDir` that holds a link to somewhere else, needs `systemd.services.<UNIT>.serviceConfig.ReadWritePaths = [ … ];`.
- Every key is a `mkDefault`, so relax one with `systemd.services.<UNIT>.serviceConfig.<KEY>`. To deploy without the sandbox and turn it on later, set `services.frappe-nix.hardening.enable = false;` for now.

### 7. Move the container images

With `containers.enable`, two things change, and each needs a step where the images are built or run.

**The package names.** The packages are `container-<role>` only: `container-runtime`, `container-nginx`, `container-bench`, and the split set's `container-web`, `container-scheduler`, `container-worker-*` and `container-socketio`. The 1.x aliases (`runtime`, `nginx`, `bench-cli`, `web`, …) are gone, and so is `containers.aliases`. The image names (`<REGISTRY>/<BENCH_NAME>/<ROLE>`) have not changed. Point scripts and CI that build `.#runtime` or `.#bench-cli` at `.#container-runtime` and `.#container-bench`.

**The user.** The images run as `frappe`, uid 1000 and gid 1000 (`containers.nonRoot`, now `true`). A sites volume that a 1.x image wrote is owned by root, and the new image cannot write to it: its entrypoint fails on `site_config.json`. Before the first start of a 2.0 image, change the volume's owner once, with the image's own `chown`:

```bash
docker run --rm --user 0 --entrypoint chown \
  -v <BENCH_NAME>-sites:/bench/sites \
  <BENCH_NAME>/runtime:latest -R 1000:1000 /bench/sites
```

A bind mount needs `chown -R 1000:1000 <DIR>` on the host, and the files mounted at `/secrets` must be readable by uid 1000. Under rootless Podman the nginx image needs `--sysctl net.ipv4.ip_unprivileged_port_start=0` to keep port 80. To keep running as root for now, set `containers.nonRoot = false;`. See [Running as non-root](../production/images.md#running-as-non-root).

## What the shell reconciles on entry

A frappe-nix bump can ask something new of a bench's **workspace root**. The case so far is `frappe-runtime` becoming a required dependency: a name in `[project].dependencies`, a `[tool.uv.sources]` entry and a build backend named in `[tool.uv.extra-build-dependencies]`. A bench from before the bump has no way to know.

So `enterShell` reconciles `pyproject.toml` on every entry, with the same `ensure-root` step `frappe-init` runs:

- It **adds what is missing** and changes nothing that is already there. Your name, your `requires-python`, your override list and your comments stay, because the file is edited with tomlkit and not rewritten.
- It runs `uv lock` only when that changed the file. On the common path it is one TOML parse and says nothing.

When it does change something, it says so, lists what it added, and tells you to **commit `pyproject.toml` and `uv.lock` and re-enter the shell**, because the shell you are in was built from the previous lock. Until you do, `devenv up` runs whatever shape the old lock supports. A bench whose lock predates the runtime gets the split `web`, `socketio`, `worker` and `scheduler` processes for that session, and the banner says so. Nothing about that shape is degraded: it is `runtime.enable = false`, which `checks.socket` covers.

If `uv lock` fails, because there is no network or because the new requirement genuinely conflicts with an app's pins, both files are put back exactly as they were, mtimes included, and the shell opens anyway. A `pyproject.toml` that declares what `uv.lock` does not carry would fail the _next_ evaluation (see [Dependencies and locks](locks.md#a-stale-uvlock-is-an-evaluation-error)), which is the one outcome this hook exists to avoid: you would be back to fixing the shell from outside it. The next entry retries. To resolve a conflict by hand, make the listed additions in `pyproject.toml`, add the override `uv` asks for, and run `uv lock` in the shell.

It does not touch `[tool.uv.extra-build-dependencies]` beyond adding a frappe-nix entry a hand-kept list lacks. A bench scaffolded before frappe-nix derived that table from `uv.lock` keeps its list until you move it; see [Moving a bench to the derived list](locks.md#moving-a-bench-to-the-derived-list). If the bench's `pythonOverrides` lists `lib.overrides.pycups`, `python-ldap`, `pyvips` or `cairocffi`, evaluation fails, naming it: every bench gets those fixes now, so remove them.

## What it deliberately does not do

The shell hook does not register submodules, vendor apps, edit `.gitignore` or `common_site_config.json`, or `git add` anything. That is [the reconciler's](../scaffolding/migrate.md) job, and the reconciler still does all of it for the cases a shell hook should not decide:

```bash
nix run github:Avunu/frappe-nix -- -y
```

A change to the _options_ a bench's `flake.nix` sets cannot be reconciled from inside the shell. `nodeOfflineHashes` was one such. It stays an evaluation error that names the edit.

## In app mode

In an [app repository](../scaffolding/app-mode.md) the pins are flake inputs, so a newer frappe-nix is the same `nix flake update frappe-nix`. Moving the Frappe pin or a sibling is `nix flake update frappe`, then `nix run .#relock`, and you commit `flake.lock` with `nix/`.

The bench root's dev group gained `coverage` and `unittest-xml-reporting`, which [`frappe-test`](../app-standards/testing.md) runs the tests under. An app that has not opted in to the [app standards](../app-standards/README.md) keeps the root it had: each of the two stays out of it unless the root package in the committed `nix/uv.lock` already lists it in its `dev` group. On version-15 and version-16 alike, the generated root, the dev environment and the lock stay exactly as they were, and `nix run .#relock` leaves `nix/uv.lock` as it is. A version-16 lock does have both packages, through Frappe's `test` extra, but that does not put them in the dev environment.

`frappe-test` then has no coverage to run under and says so (exit 10): pass `--no-coverage`, or opt in and run `nix run .#relock`, which adds them.

## Ports are leased at `devenv up`

The ports a bench's shell evaluates are now the ports it *prefers*. `devenv up` leases them before anything starts ([The development shell](README.md#ports-and-sockets)). Nothing changes for a bench whose preferred ports are free, which is the usual case. When one is taken:

- An app-mode bench moves to the next free port instead of failing to bind it, or answering readiness checks from another bench's nginx. Find the ports in force with `frappe-nix-ports`, not with `ports.*` or `mailpit.*` values read from Nix, which are the preferred ones.
- A bench-mode bench stops before starting anything, naming the port and the bench or process that holds it. Before, nginx alone failed to bind and the rest came up.
- To keep an app-mode bench on its exact ports, refusing a taken one as `devenv up` did before for an app that opted in, set `ports.strict = true` in the flake or export `FRAPPE_NIX_STRICT_PORTS=1`.

`FRAPPE_NIX_PORT_OFFSET`, `ports.offset`, `ports.base` and `ports.worktreeSalt` keep their meaning: they choose the preferred ports.

## The runtime moves with frappe-nix

The unified runtime's source is the `runtime/` directory of this repository, so a bump of frappe-nix is also a bump of the runtime, with no relock in any bench. The one exception is a runtime release that changes its dependencies. That needs one relock:

```bash
nix run .#relock
```

Use that and not a plain `uv lock`, which keeps an already resolved git revision and reports success without changing anything. `relock`, `bench update` (when it re-locks) and `update-deps` all run `uv lock --upgrade-package frappe-runtime`. See [The unified runtime](../production/runtime.md#how-a-bench-gets-it).
