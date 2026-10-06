---
title: Memory and disk
description: How the dev shell keeps a small machine usable, with a memory-limited scope, a selective asset watcher, native Sass and a MariaDB tuned for speed over durability.
order: 5
tags: [performance, watch, mariadb, btrfs]
updated: 2026-10-06
---

A development bench runs MariaDB, Redis, the runtime and asset watchers on one machine, usually next to an editor. The shell makes four choices to keep that workable. Each can be changed with an option.

> [!NOTE]
> The timings on this page are the project's own measurements on the bench they were taken from. They show the order of magnitude and are not a promise for your hardware.

## `devenv up` runs in a scope of its own

Started from an editor's terminal, MariaDB, the runtime and the asset watchers would otherwise all be accounted to the editor's cgroup. When that ran short, systemd-oomd, which kills whole cgroups, took the editor down with them.

So the `devenv up` script re-runs itself under `systemd-run --user --scope` with `MemoryHigh=` set from `processScope.memoryHigh` (default `40%` of physical RAM). Past that, the dev stack is the one slowed and reclaimed from, and if it comes to a kill, it is the stack that is killed and not the editor. `systemctl --user status 'frappe-nix-*'` shows the scope and what it holds.

| Option                    | Default | Meaning                                                                                                                                                                                               |
| ------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `processScope.enable`     | `true`  | Use the scope. Linux with a systemd user manager and the process-compose manager; a no-op elsewhere.                                                                                                  |
| `processScope.memoryHigh` | `"40%"` | The scope's `MemoryHigh=`: past it the stack is slowed and made to give memory back. A percentage is of physical RAM. It is not a kill threshold.                                                     |
| `processScope.memoryMax`  | `null`  | The scope's `MemoryMax=`, or none. Past it the kernel's OOM killer picks off one process inside the scope, often `mariadbd` as the largest, so most machines are better served by `memoryHigh` alone. |

A machine with no user manager, such as a bare SSH login or a container, runs unscoped instead of failing to start.

## The watcher leaves out what nobody edits

`bench watch` holds every app's whole dependency graph in memory for as long as it runs. On one bench, Frappe's own bundles were 1.4 GB of a 2.5 GB watcher. The `watch` process ([`lib/bench-watch.py`](../../lib/bench-watch.py)) therefore skips:

- every app whose `hooks.py` `app_publisher` names a publisher in `watch.excludePublishers`, which by default is Frappe Technologies, so frappe, erpnext, hrms, payments and the rest;
- every right-to-left stylesheet, which esbuild otherwise compiles a second time through rtlcss. Set `watch.rtl = true` to build them.

Skipped apps keep the assets of the last `bench build`. `bench update` builds after every pull, and after editing one yourself, run `bench build --app <APP>`. The first line of the `watch` log names what it left out. Set `watch.apps` to choose the list yourself. `[ ]` turns the watcher off.

## Sass is compiled natively

Frappe compiles stylesheets with Dart Sass compiled to JavaScript, through its legacy `render()`. On Carbon-based stylesheets that is 4 to 10 seconds each, and the watcher runs it for every stylesheet at startup and for every one that imports a partial you save.

With `watch.nativeSass` (on by default where nixpkgs builds `dart-sass`), the same preload hands Frappe's `require("sass")` the `sass-embedded` package backed by nixpkgs' native compiler ([`lib/sass-embedded.nix`](../../lib/sass-embedded.nix)). It exposes the same API, importers and `includedFiles`, and takes 1 to 1.6 seconds per stylesheet.

The compiler is newer than the one Frappe pins, so the CSS is not byte-identical to `bench build`'s:

- properties written after a nested rule are grouped in source order, as native CSS nesting does;
- computed colors print as precise `rgb(%)` rather than rounded hex.

On the stylesheets compared, it renders the same, with every color within 1/255 per channel. Newer deprecation notices (`@import`, chiefly) show in the watch log, and the one about Frappe's own use of the legacy API is silenced. `bench build` keeps Frappe's compiler.

Together, on one bench (frappe, erpnext and two React frontends on Carbon), the watcher went from 2.5 GB and 50 s to start to about 0.75 GB and 8 s. Saving a Sass partial went from about 7 s to about 2.3 s before the rebuilt CSS was on disk, and the whole stack went from about 3 GB to 1.3 GB.

## The database trades durability for speed

With `mariadb.durable = false` (the default), InnoDB flushes its log once a second instead of at every commit, and skips the doublewrite buffer. Frappe commits on every request, job and migrated document, so this is most of `bench migrate`'s and the test suite's disk time. On an NVMe laptop, 2,000 single-row commits took 6.2 s at full durability and 0.2 s without.

The cost is the last second of commits if the _machine_ crashes. A crashed `mariadbd` alone loses nothing. A power cut can also leave a torn page, which `bench restore` repairs, as it does anything else in a development database. Set `mariadb.durable = true` to flush at every commit and keep the doublewrite buffer, as production does. Each of the two settings is a default, so either can still be changed on its own through `services.mysql.settings.mysqld`.

## On btrfs, the data directory stays off copy-on-write

On a copy-on-write filesystem, every InnoDB page write becomes a new extent, compressed first on the compressed mounts desktops usually have. Every fsync commits a filesystem transaction that other programs' saves wait behind, and table files fragment into hundreds of extents.

The `NOCOW` attribute only reaches files created after it is set, so shell entry sets it on a data directory that is missing or empty, and reports one that already holds data. For an existing database, stop `devenv up` and run this once:

```bash
frappe-nix-db-nocow migrate "$MYSQL_HOME"
```

It copies the data directory into a `NOCOW` directory, keeps the original beside it as `mysql.cow-backup-<DATE>`, and tells you to delete that once the site works. It is a no-op on any other filesystem. Set `mariadb.noCow = false` to opt out.
