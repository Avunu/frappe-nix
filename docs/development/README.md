---
title: The development shell
description: What devenv up runs, how several benches share one machine over unix sockets and per-bench ports, and how editable installs and node_modules are kept healthy.
nav_title: Development
order: 4
tags: [devenv, development, ports, sockets]
updated: 2026-10-06
---

`devenv up` runs the full stack through process-compose. **Several benches can run at once**: everything that can be is on a unix socket under `$DEVENV_RUNTIME`, which devenv gives each project uniquely, and the ports that remain are per bench.

You enter the shell with `direnv allow` (or `nix develop --no-pure-eval`) and start the stack with `devenv up`. [Getting started](../getting-started.md) shows the first run.

## What runs

| Service or process                                  | Listens on                                                                                             |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| nginx                                               | `127.0.0.1`, TCP 8000 plus a hash of `benchName`. The only port a browser needs.                       |
| runtime (web, realtime, background jobs, scheduler) | `$DEVENV_RUNTIME/web.sock`                                                                             |
| MariaDB                                             | `$DEVENV_RUNTIME/mysql.sock`                                                                           |
| Redis (cache and queue)                             | `$DEVENV_RUNTIME/redis.sock`                                                                           |
| Mailpit (SMTP, web UI, POP3)                        | TCP 19000, 20000 and 21000, each plus the same hash. POP3 only when `devguard.mail.pop3.enable` is on. |
| watch                                               | none                                                                                                   |
| assetsWatch                                         | none. Runs only when `assets.reassert.hooks` is set.                                                   |

nginx routes `/socket.io` and everything else to the same socket, because the unified runtime answers both. That is the same shape [`services.frappe`](../production/nixos-service.md) uses in production. `webserver_port` and `socketio_port` in `sites/common_site_config.json` are both set to the nginx port, which lets the browser reach both over one origin.

`http://localhost:<PORT>`, and `127.0.0.1`, serve the site named by `siteName`. The runtime sends a request whose `Host` names no site on the bench to `FRAPPE_SITE` or `default_site`, on the web and the socket.io path alike. A `Host` that does name a site, such as `http://other.localhost:<PORT>` on a bench with several, still reaches that one.

With `runtime.enable = false` the dev shell runs the split `web`, `socketio`, `worker` and `scheduler` processes instead, and nginx sends `/socket.io` to the socketio socket. See [The unified runtime](../production/runtime.md).

## Ports and sockets

Because sockets and hashed ports are per bench, you can run several benches at the same time without collisions.

The ports are hashed from `benchName` and not from the project path, so every clone of a bench derives the same number and the committed `common_site_config.json` never conflicts. The offset is between 0 and 899. devenv's port allocator still walks forward if something is genuinely in the way, and `devenv up` writes the value it settled on back into the config.

- Override the base with [`ports.base`](../reference/dev-shell-options.md#ports-and-sockets).
- Set `sockets.enable = false` to put everything back on TCP. Ports are still allocated dynamically in that mode, so benches still do not collide, but they use more ports and there is no nginx. Socket mode needs Frappe 15.46 or newer.

### MariaDB and TCP clients

With `sockets.enable` (the default) MariaDB runs with `skip-networking`: it listens on its unix socket and nothing else. `FRAPPE_DB_HOST` and `FRAPPE_DB_PORT` still name its allocated but unbound port. Frappe never uses them, because `db_socket` wins over host and port in `get_connection_settings`. But an app that opens its own connection to `frappe.conf.db_host:db_port` fails against this bench instead of silently landing in whichever neighboring bench holds port 3306.

That second case is real: Insights' "Site DB" data source is one such app, because ibis rewrites host `localhost` back to `127.0.0.1`, so libmysqlclient's socket shortcut does not save it. With `sockets.enable = false`, MariaDB binds `127.0.0.1` on its own per-bench port instead.

> [!NOTE]
> If you use the **wiki** app: its frontend does `import { socketio_port } from 'sites/common_site_config.json'`, so the port is baked into its bundle at build time. If the allocator ever moves your port, run `bench build --app wiki` again. `frappe-ui`'s vendored `socketio.js` similarly defaults to a hardcoded 9000 unless the call site passes `port: window.frappe?.boot?.socketio_port`.

## Editable installs

`apps/*` are installed as **editable** packages (the uv2nix editable overlay), so source edits hot-reload. Each one is built from a trimmed copy of its app, containing only `pyproject.toml`, the readme and license files and the module's `__init__.py` ([`lib/editable-src.nix`](../../lib/editable-src.nix)). An editable install carries none of the app's code. Built from the whole directory, every edit would copy the app into the Nix store again (erpnext alone is about 150 MB) and rebuild the virtualenv on the next evaluation, for an identical result.

`uv` and `yarn` write to mutable state directories under `$DEVENV_STATE`, so `uv add` and `yarn add` work despite the read-only Nix store. The resulting `uv.lock` and `yarn.lock` are then consumed declaratively for production builds. See [Dependencies and locks](locks.md).

## node_modules in the dev shell

Each app's `node_modules` in the shell is a real `yarn install`, not the Nix-built one used for production. Nested Vite frontends (`erpnext/banking`, `hrms/frontend`, `helpdesk/desk` and others) get their dependencies from a postinstall that needs the network.

- The install is skipped for an app whose `package.json` and `yarn.lock`, its own and every nested one, are unchanged since the last successful install. It re-runs when any of them moves.
- `bench build` runs it too, and refuses to build if it fails. Without that, pulling an app that added a dependency and building without reinstalling gives a missing-package error from a Vite config several apps deep, naming nothing that leads back to the install.
- The install changes `node_modules` and nothing else. Those postinstalls run a non-frozen `yarn install` in each nested frontend, which rewrites the frontend's committed `yarn.lock` (or writes one where upstream ships none), and a modified lock is a file the app's next `git checkout` refuses over. So the lockfiles are copied before the install, and any it changed are put back, with a line saying which. A lock you had already edited comes back edited.

### A damaged install looks finished

`node_modules/.yarn-integrity` hashes the lockfile and the install's settings, never the files. An install that was cut short by a killed process, a full disk or a network that gave up leaves a tree that yarn calls up to date for as long as the lockfile stands. It shows up as:

- `Cannot find module 'sass'`, from an empty package directory;
- `Bus error (core dumped)` from `vite build`, from a truncated native binary whose ELF headers point past the end of the file;
- `Rolldown failed to resolve "hast-util-raw"`, from a cache record that lists no dependencies for a package that has them.

No re-run repairs any of these. Shell entry therefore runs `frappe-nix-node-verify` first. It checks the yarn cache and every app's `node_modules`, nested frontends too, for exactly those three, and deletes what is damaged: the cache entry, every installed copy of that name and version, and the markers that say "installed". The install that follows puts it back. Only regenerable files are touched, and it says what it removed.

A clean scan leaves a fingerprint of the cache and the `.yarn-integrity` markers. While an install has not moved them the scan is skipped, which takes about half a second. Damage happens while an install is writing, and an install moves them.

### Check on demand

`bench setup requirements` is the on-demand version. It replaces upstream's `pip install -e` and `yarn install` of every app, which in this bench would fight a read-only environment or skip the checks that matter.

```bash
bench setup requirements            # scan, install what is missing or stale, check the Python side
bench setup requirements --check    # report only; exit 1 if anything is wrong
bench setup requirements --node     # only the Node half
bench setup requirements --python   # only the Python half
```

It always does the full scan, and lists lockfiles that differ from their commit: yours, or left over from before the install learned to put them back. Then it checks the Python side: every requirement an app declares is in `uv.lock`, and every app in `sites/apps.txt` imports. The environment is built by Nix from the lock, so Python findings are reported with their fix (`uv lock`, then re-enter the shell), not repaired. The rest of `bench setup` (nginx, supervisor, production) still goes to upstream.

## In this section

- [Everyday commands](commands.md): the `bench` wrapper and the scripts behind it.
- [Development guard rails](guard-rails.md): what keeps a restored production database from reaching the outside world.
- [Secrets](secrets.md) and [Restore a production backup](restore-from-production.md).
- [Memory and disk](memory-and-disk.md): how the stack stays light on a small machine.
- [Dependencies and locks](locks.md), [Apps in a bench](apps.md) and [Asset builds](assets.md).
- [Upgrading frappe-nix](upgrading.md).
