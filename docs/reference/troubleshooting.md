---
title: FAQ and troubleshooting
description: Answers to common questions about frappe-nix and the errors you are most likely to meet in the dev shell, in builds and on a deployed host, with their fixes.
order: 6
tags: [faq, troubleshooting, errors]
updated: 2026-10-06
---

Each entry starts with what you see, then what it means and what to do. Where a page explains the cause in depth, the entry links to it.

## The shell will not open

### `attribute '<NAME>' missing`, from uv2nix

```text
error: attribute 'json-repair' missing
at …/uv2nix/build/lib/resolvers.nix:123:23
```

`uv.lock` is stale: an app gained a dependency the lock never recorded. frappe-nix audits the lock first and names the app, the requirement and the fix. Because the shell that carries `uv` is the thing that will not open, run the fix from outside it:

```bash
nix run .#relock
```

Then commit `uv.lock` (`nix/uv.lock` in app mode) and re-enter the shell. See [Dependencies and locks](../development/locks.md#a-stale-uvlock-is-an-evaluation-error).

### `uv.lock is missing`, but the file is on disk

A flake's source tree is exactly its git-tracked files, so an untracked or ignored lock is invisible to evaluation. Stage it with `git add`. In app mode `nix run .#relock` stages `nix/uv.lock` for you. If it is tracked and still reported missing in app mode, check that you did not filter `app.src`: a filter that drops the lock directory takes `uv.lock` with it.

### The scaffolder says a flag is required, or the directory is the wrong shape

Without a terminal the scaffolder asks nothing, so it needs `--frappe-version` (and `-y` to migrate). See [Scaffolder](scaffolder.md#exit-codes) for what each exit code means.

### An app is missing from the build

A nested repository added to `apps/` with `git add` and no `.gitmodules` entry is a gitlink that Nix sees as an empty directory. See [A gitlink with no .gitmodules entry](../development/apps.md#a-gitlink-with-no-gitmodules-entry) for the two ways out.

## Builds fail

### `Couldn't find any versions for "<PACKAGE>" that matches "<RANGE>" in our cache`

An upstream `yarn.lock` does not cover its own `package.json`, so the offline install cannot resolve it. Force a repaired fallback with `bench-update --node-locks <APP>/<SUBDIR>`, or leave the frontend out with `nodeNestedFrontendExcludes`. See [A yarn.lock that cannot resolve offline](../development/locks.md#a-yarnlock-that-cannot-resolve-offline).

### `builtBench` fails on an app with a build script, and evaluation warned about a missing lock

An app that ships a `package.json` but no `yarn.lock` has nothing to build `node_modules` from. Run `bench-update --node-locks` in the dev shell and commit `node-locks/`. See [The fallback](../development/locks.md#the-fallback-node-locks).

### A bundle needs a newer JavaScript syntax than Frappe's default allows

Frappe's own esbuild target, es2017, cannot lower async generators or BigInt literals. frappe-nix compiles for `es2022` by default, and `esbuildTarget` changes it. See [Asset builds](../development/assets.md#compile-target).

### The browser reports a 404 for a JS or CSS bundle after `bench watch` rebuilt it

An app's `hooks.py` names a bundle key that a watch rebuild does not rewrite. Point `assets.reassert.hooks` at your app's fixup. See [Asset-shadow staleness](../development/assets.md#asset-shadow-staleness).

### The nginx image cannot start

The image runs `nginx -c /bench/config/nginx.conf` and frappe-nix does not generate that file. The scaffolded `.gitignore` excludes `config/*.conf`, so add yours with `git add -f config/nginx.conf`. See [The nginx image](../production/images.md#the-nginx-image).

## In the development shell

### A `node_modules` error from a Vite config several apps deep

A dependency was added by a pull and the build ran without reinstalling. Run `bench setup requirements --node`. The shell also scans for damaged installs on every entry.

### `Cannot find module 'sass'`, `Bus error (core dumped)` from `vite build`, or `Rolldown failed to resolve "hast-util-raw"`

An install that was cut short looks finished to yarn. Shell entry detects exactly these three cases and deletes the damaged files so the next install puts them back. Re-enter the shell, or run `bench setup requirements --node`. See [A damaged install looks finished](../development/README.md#a-damaged-install-looks-finished).

### The editor was killed, or the machine runs out of memory, while `devenv up` ran

`devenv up` runs in its own memory-limited systemd scope, and the watcher leaves out apps nobody edits. Skipped apps keep the assets of the last build, so after editing one yourself, run `bench build --app <APP>`. See [Memory and disk](../development/memory-and-disk.md).

### A page of an app I just added returns 404, or throws

The app is importable but not installed on the site. With `siteName` set, `devenv up` installs missing apps for you. In a multi-tenant bench, run `reconcile-apps <SITE_NAME>`. Do not run `provision-site` again: it drops the database. See [Installed-app drift](../development/apps.md#installed-app-drift).

### `bench update --reset` is not accepted

`bench update` is redirected to `bench-update`, which takes `--pull`, `--migrate`, `--build` or `--node-locks`. The real command stays reachable with `_FRAPPE_BENCH_RAW=1 bench update --reset`. See [Everyday commands](../development/commands.md).

### `bench update` dies with `ModuleNotFoundError: No module named 'bench.patches.v3'`

A bench root whose `patches.txt` has no record of the patches that frappe-bench ships fails this way, and stays failed, because the failed run rewrites the file as one empty byte. The shell reconciles `patches.txt` on every entry, so entering the shell repairs it. See the note in [Everyday commands](../development/commands.md#what-the-wrapper-redirects).

### `bench update` prints `frappe_benchcli:` and runs `bench-update`

```text
frappe_benchcli: `bench update` is bench-update here. This shell reached the virtualenv's bench ahead of the devenv's, which activating ./env does. Running bench-update.
```

The virtualenv's own `bench` ran, not the wrapper, because something put `env/bin` ahead of it on `PATH`: `source env/bin/activate`, or an editor that activates `./env` in its terminals. frappe-nix handed the command to `bench-update` (and `bench build` to `bench-build`) for you. Without that, the stock `bench update` exits 1 within a second: it runs `git show upstream/<branch>:<app>/__init__.py`, and the apps here are submodules with an `origin` remote and no `upstream`. The notice stops when the shell no longer activates `./env`. See [Everyday commands](../development/commands.md#what-the-wrapper-redirects).

### `bench build` fails in one app, and the log ends with `✗ N app build(s) failed`

Frappe ends a build by running `yarn build` in every app that has one, and stops at the first that fails, so every app after it is left unbuilt. In the dev shell frappe-nix carries on: each failure is named where it happens, the other apps are built, and `bench build` still exits 1. Fix the error from the app named, then rebuild it with `bench build --app <APP>`. `builtBench` does not carry on; an image build stops at the first failure. On the `develop` preset Frappe's own build no longer runs the apps one after another, and its failure output differs: the builds already running finish, none is started after the failure, and each failure is listed as `yarn build failed for <app>`. See [Resolution the way apps assume](../development/assets.md#resolution-the-way-apps-assume).

### `provision-site` asks for the MariaDB root password

Leave it blank and press Enter. The development MariaDB root has none.

### A database connection from an app lands in the wrong database, or fails

With the default `sockets.enable`, MariaDB listens only on its unix socket, and `FRAPPE_DB_HOST` and `FRAPPE_DB_PORT` name a port nothing listens on, so a client that connects by TCP fails against this bench instead of reaching a neighbor's. See [MariaDB and TCP clients](../development/README.md#mariadb-and-tcp-clients).

### The wiki app's realtime stopped working after the port moved

The wiki frontend bakes `socketio_port` into its bundle at build time. Run `bench build --app wiki` again.

### Mail does not arrive anywhere

That is the mail guard working. Everything a dev site sends goes to Mailpit, whose address is in the shell banner. See [Development guard rails](../development/guard-rails.md).

### Attachments pushed from dev never reach a Backblaze B2 bucket

B2 answers conditional writes with `501`, and `cloud_storage` swallows the failure. Set `devguard.objectstore.conditionalWrites = false`. See [Pushing attachments to production](../development/guard-rails.md#pushing-attachments-to-production).

### `bench restore` refuses to write the encryption key

The restore carries production's `encryption_key` only into a bench with the guard rails on. Re-enable `devguard.enable`, or restore with `--no-site-config`. See [The encryption key](../development/restore-from-production.md#the-encryption-key).

### The migration reported a tracked `site_config.json`

Run the `git rm --cached` command it printed, and rotate every credential the file has held. See [Migrate an existing bench](../scaffolding/migrate.md).

## On a deployed host

### `frappe-migrate-<SITE>` succeeded, but the site is down

On a site whose database has no tables the unit logs that the site is not installed, and exits cleanly so it does not block other sites. Create or restore the site. See [Create or restore the site](../production/operations.md#create-or-restore-the-site).

### A migration failed on deploy

The unit restored the pre-migrate snapshot and left the site in maintenance mode. Read the error with `journalctl -u frappe-migrate-<SITE> -p err`, fix the cause and deploy again, or return to the previous generation with `nixos-rebuild switch --rollback`. See [Safe migrations on deploy](../production/operations.md#safe-migrations-on-deploy).

### Edits to `site_config.json` disappear

The init unit regenerates it on every start. Put non-secret settings in `extraConfig` and secrets in `extraConfigFiles`. See [Secrets](../production/nixos-service.md#secrets).

### The module warns that `socketio.socketPath` or `web.workers` is ignored

Those options belong to the split processes. With the default unified runtime they do nothing. Remove them, or set `runtime.enable = false`.

### The runtime is reachable from the network

With `web.port` the runtime binds `0.0.0.0`. Keep the port closed in the firewall, or use `web.socketPath` so the app has no TCP listener. See [Put nginx in front](../production/nixos-service.md#put-nginx-in-front).

### A container exits immediately

`FRAPPE_SITE` is required, and without it the entrypoint stops. See [Environment variables](../production/images.md#environment-variables).

## Questions

### Which Frappe versions does it support?

`develop`, `version-16` and `version-15`. There is no preset below `version-15`, because older apps ship a `setup.py` with no `pyproject.toml` and cannot be uv workspace members. See [Versions and presets](../scaffolding/README.md#versions-and-presets).

### Can I use it on macOS?

The scaffolded flake lists `aarch64-darwin` and `x86_64-darwin` among its systems, and features such as the memory scope are written to do nothing where they do not apply. Production needs a NixOS host for the module, or any host that runs OCI images.

### Do I have to adopt it for production too?

No. The development shell stands on its own, and nothing about it changes how production runs. See [Why frappe-nix](../why-frappe-nix.md).

### Does it replace `bench`?

It wraps it. The real `bench` still runs for everything the wrapper does not redirect. See [Everyday commands](../development/commands.md).

### Can I run several benches at once?

Yes. Everything that can be is on a unix socket, and the ports that remain are per bench. See [The development shell](../development/README.md#ports-and-sockets).

### Where do I report a problem?

In the [issue tracker](https://github.com/Avunu/frappe-nix/issues). Include the output of the failing command and which mode (bench or app) you are in.
