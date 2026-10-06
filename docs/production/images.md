---
title: Build production images
description: How frappe-nix turns a bench into an immutable build, which OCI images come out of it, and how to build, load, push and run them.
order: 1
tags: [oci, containers, builtbench, docker]
updated: 2026-10-06
---

This page explains how frappe-nix turns a Frappe bench into an immutable build, which container images come out of it, and how to build and run them. Use it when you want a Frappe deployment where nothing is installed or compiled at container start.

If you would rather run Frappe directly on a NixOS host, without containers, see [Run Frappe as a NixOS service](nixos-service.md). It consumes the same `builtBench` package.

## How the immutable bench is built

frappe-nix builds one package, `builtBench`, and every image is assembled from it. The package contains the apps, the production Python environment, Node and the compiled assets. `nix build .#default` builds the same thing.

### Python

The Python environment comes from `uv.lock` through uv2nix. Commit `uv.lock` and the build reads it. Nothing runs `uv sync` at build or start time.

### node_modules from each yarn.lock

Every app with a `package.json` is a node target. So is each immediate subdirectory of an app that has its own `package.json`, such as a nested frontend. Each target gets its own `node_modules`, reproduced from its `yarn.lock`:

1. frappe-nix reads the lockfile at evaluation time and creates one `fetchurl` per tarball, using the URL and `integrity` the lock already records, or the `#sha1` suffix in older locks. Git dependencies are fetched by commit.
2. The tarballs are linked into an offline mirror.
3. nixpkgs' `yarnConfigHook` runs `yarn install --offline --frozen-lockfile --ignore-scripts --ignore-engines --ignore-platform` against that mirror.

You commit no hashes. The derivation sees only `package.json`, `yarn.lock`, `.yarnrc` and `.npmrc`, so editing other files in an app does not rebuild its `node_modules`.

Lifecycle scripts do not run in the sandbox. The frontends frappe-nix was built against need only prebuilt or platform-specific packages, so this works for them. If an app does need extra build steps, `nodeOverrides.<TARGET>` lets you add attributes such as `postPatch` or `nativeBuildInputs` to that target's derivation.

An app's own `yarn.lock` always wins. If an app ships a `package.json` but no `yarn.lock`, generate a fallback from the bench's dev shell and commit it:

```bash
bench-update --node-locks
```

That writes `node-locks/<APP>/yarn.lock` in a bench, or `nix/node-locks/` in app mode through `nix run .#relock`. Evaluation warns when a fallback is older than its `package.json`, and when one is unused because the app now ships its own lock. It also warns about a target with no lock at all. The package then has no `node_modules` for it, and `builtBench` fails on any such app that has a build script.

A lock that does not resolve offline, and the two ways around it, are covered in [Dependencies and locks](../development/locks.md#a-yarnlock-that-cannot-resolve-offline).

### Compiling assets

`builtBench` copies the unbuilt bench tree into the build directory and runs `bench build --production` inside the Nix sandbox. It sets `ESBUILD_TARGET` from the `esbuildTarget` option, which defaults to `es2022`, and `NODE_OPTIONS=--max-old-space-size=4096`. The finished tree, including nested frontend output and rewritten asset links, becomes the package.

In a bench, the build also copies the bench root's `config/` directory into the package when it exists. That is how an nginx configuration reaches the nginx image, see below. [Asset builds](../development/assets.md) explains the esbuild preload that applies here too.

Because the environment, `node_modules` and assets are all fixed at build time, a given set of locks and app commits produces the same bench every time.

## The OCI images

Enable the images in your bench flake:

```nix
frappe-nix = {
  enable = true;
  benchName = "<BENCH_NAME>";
  containers.enable = true;
};
```

Each image is built with `dockerTools.buildLayeredImage`, tagged `latest`, and named `<benchName>/<name>`. The [unified runtime](runtime.md) is on by default, which gives you three images.

| Package     | Image                         | Contents                                                                                     |
| ----------- | ----------------------------- | -------------------------------------------------------------------------------------------- |
| `runtime`   | `<BENCH_NAME>/runtime:latest` | One `frappe-runtime` process: web, realtime, background jobs and scheduler. Listens on 8000. |
| `nginx`     | `<BENCH_NAME>/nginx:latest`   | nginx plus the built bench. Listens on 80.                                                   |
| `bench-cli` | `<BENCH_NAME>/bench:latest`   | The `bench` command for migrations and one-off tasks.                                        |

Note that the package is `bench-cli` but the image is named `bench`.

### The split image set

Set `runtime.enable = false` and frappe-nix builds the classic split instead. You get eight packages instead of three:

| Package          | Image                                | Process                           |
| ---------------- | ------------------------------------ | --------------------------------- |
| `web`            | `<BENCH_NAME>/web:latest`            | gunicorn on port 8000             |
| `scheduler`      | `<BENCH_NAME>/scheduler:latest`      | `bench schedule`                  |
| `worker-default` | `<BENCH_NAME>/worker-default:latest` | `bench worker --queue default`    |
| `worker-short`   | `<BENCH_NAME>/worker-short:latest`   | `bench worker --queue short`      |
| `worker-long`    | `<BENCH_NAME>/worker-long:latest`    | `bench worker --queue long`       |
| `socketio`       | `<BENCH_NAME>/socketio:latest`       | Node realtime server on port 9000 |
| `nginx`          | `<BENCH_NAME>/nginx:latest`          | nginx on port 80                  |
| `bench-cli`      | `<BENCH_NAME>/bench:latest`          | `bench`                           |

> [!NOTE]
> With the default `runtime.enable = true`, the `web` package does not exist. Build `runtime` instead. The scaffolded `flake.nix` carries a commented hint that mentions `nix build .#web`, which applies to the split set only.

## Building, loading and pushing

Build an image from your bench repository:

```bash
nix build .#runtime
docker load < result
```

`result` is an image tarball, and `docker load` registers it as `<BENCH_NAME>/runtime:latest`. Repeat with `.#nginx` and `.#bench-cli` for the other two.

frappe-nix declares a `containers.registry` option but does not use it to push anything. Pushing is up to you, with ordinary container tooling:

```bash
docker tag <BENCH_NAME>/runtime:latest registry.example.com/<BENCH_NAME>/runtime:latest
docker push registry.example.com/<BENCH_NAME>/runtime:latest
```

## Running the images

Every image except `nginx` and `socketio` starts through the same entrypoint. It builds the site's configuration from environment variables and mounted secrets, then runs the image's command.

### Volumes

Mount two paths:

- `/bench/sites` is a persistent volume for site state.
- `/secrets` holds secret files, mounted read-only.

At every start the entrypoint links `apps.txt`, `apps.json` and the compiled `assets` from the image into `/bench/sites`, so a new image always brings its own app registry and assets. `common_site_config.json` is copied in once, and from then on it belongs to you.

### Environment variables

`FRAPPE_SITE` is required. Without it the container exits immediately. The rest have defaults you should usually override:

| Variable                | Default                                          | Becomes                  |
| ----------------------- | ------------------------------------------------ | ------------------------ |
| `FRAPPE_DB_HOST`        | `127.0.0.1`                                      | `db_host`                |
| `FRAPPE_DB_PORT`        | `3306`                                           | `db_port`                |
| `FRAPPE_DB_TYPE`        | `mariadb`                                        | `db_type`                |
| `FRAPPE_DB_NAME`        | the site name, with dots replaced by underscores | `db_name`                |
| `FRAPPE_DB_USER`        | the same default as `FRAPPE_DB_NAME`             | `db_user`                |
| `FRAPPE_REDIS_CACHE`    | `redis://127.0.0.1:13000`                        | `redis_cache`            |
| `FRAPPE_REDIS_QUEUE`    | `redis://127.0.0.1:13000`                        | `redis_queue`            |
| `FRAPPE_REDIS_SOCKETIO` | `redis://127.0.0.1:13000`                        | `redis_socketio`         |
| `FRAPPE_DB_SOCKET`      | unset                                            | `db_socket`, when set    |
| `FRAPPE_SOCKETIO_UDS`   | unset                                            | `socketio_uds`, when set |

The images also set `FRAPPE_BENCH_ROOT=/bench`, `SITES_PATH=/bench/sites`, `FRAPPE_ENV_TYPE=production`, `FRAPPE_STREAM_LOGGING=1` and `FRAPPE_TUNE_GC=1`.

### Secrets

The entrypoint merges these files into `site_config.json`, in this order:

1. `/secrets/db_password` becomes `db_password`.
2. `/secrets/encryption_key` becomes `encryption_key`.
3. Every `/secrets/*.json` is deep-merged over what is already there, which is the place for object-storage credentials and similar settings.

`FRAPPE_DB_SOCKET` and `FRAPPE_SOCKETIO_UDS` are merged after that, when set. The entrypoint writes the result with mode `0600`. Secrets never enter the image or the Nix store.

### A single-host example

This starts the runtime image against a MariaDB and Redis you already have:

```bash
docker run -d --name <BENCH_NAME>-runtime \
  -p 8000:8000 \
  -e FRAPPE_SITE=site1.example.com \
  -e FRAPPE_DB_HOST=<DB_HOST> \
  -e FRAPPE_REDIS_CACHE=redis://<REDIS_HOST>:6379 \
  -e FRAPPE_REDIS_QUEUE=redis://<REDIS_HOST>:6379 \
  -e FRAPPE_REDIS_SOCKETIO=redis://<REDIS_HOST>:6379 \
  -v <BENCH_NAME>-sites:/bench/sites \
  -v <SECRETS_DIR>:/secrets:ro \
  <BENCH_NAME>/runtime:latest
```

The default command is `frappe-runtime --host 0.0.0.0 --port 8000 --job-threads 4`, with `/bench` as the working directory. The runtime is not started with `--dev`, the flag that makes it serve `/assets` and `/files` itself, so put nginx in front of it for `/assets`.

### One-off commands

Run `bench` commands from the `bench-cli` image with the same variables and volumes:

```bash
docker run --rm \
  -e FRAPPE_SITE=site1.example.com \
  -e FRAPPE_DB_HOST=<DB_HOST> \
  -v <BENCH_NAME>-sites:/bench/sites \
  -v <SECRETS_DIR>:/secrets:ro \
  <BENCH_NAME>/bench:latest bench --site site1.example.com migrate
```

### The nginx image

The nginx image runs `nginx -c /bench/config/nginx.conf -g "daemon off;"`. frappe-nix does not generate that file. The build copies your bench's `config/` directory into the image, so you supply `config/nginx.conf` yourself.

> [!WARNING]
> The `.gitignore` that frappe-nix scaffolds excludes `config/*.conf`. A flake only sees tracked files, so add your file with `git add -f config/nginx.conf`, or it never reaches the image.

## What the images do not do

- The entrypoint writes `site_config.json` only. It does not create the site's database, so create or restore the site yourself.
- frappe-nix ships no compose file or orchestration for these images.
- They do not carry a database or Redis. The process images include common runtime dependencies, such as `wkhtmltopdf`, `chromium`, fonts and the MariaDB client, and `extraContainerRuntimeDeps` adds more.
