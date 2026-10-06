---
title: From development to production
description: The immutable builtBench package that the development shell, the container images and the NixOS module all share, and how to choose between images and the module.
nav_title: Production
order: 5
tags: [production, builtbench, deployment]
updated: 2026-10-06
---

frappe-nix builds one production artifact, `builtBench`, and everything you deploy is assembled from it. It contains the apps, the production Python environment, Node and the compiled assets. `nix build .#default` builds it, and so does `nix build` in an [app repository](../scaffolding/app-mode.md).

Nothing is installed or compiled at deploy time or at container start. The environment, `node_modules` and assets are all fixed at build time, so a given set of locks and app commits produces the same bench every time.

## What goes into it

| Part               | Comes from                                                                                                                                      |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Python environment | `uv.lock`, through uv2nix. Nothing runs `uv sync` at build or start time.                                                                       |
| `node_modules`     | Each node target's own `yarn.lock`, or a `node-locks/` fallback. See [Dependencies and locks](../development/locks.md#node-each-apps-yarnlock). |
| Compiled assets    | `bench build --production`, run inside the Nix sandbox. See [Build production images](images.md#how-the-immutable-bench-is-built).              |
| App registry       | `sites/apps.txt` and `sites/apps.json`, regenerated from the workspace members. See [Apps in a bench](../development/apps.md#the-app-registry). |

The package exposes `passthru.{pythonEnv, nodejs, appsPath, appNames}`, so the NixOS module and the containers discover interpreters from the package itself. There is no separate `pythonEnv` or `nodejs` option on the server side. The `extraPackages` option installs extra packages in both the dev shell and any production deployment of this package: the NixOS module reads them off the package's `passthru.extraPackages`, so no server-side configuration is needed.

## What is the same in development and production

- The Python and Node interpreters, and every locked dependency.
- The unified [runtime](runtime.md), and the single-origin shape of nginx in front of unix sockets.
- `frappe_unixsock`, which makes Frappe honor unix sockets, and `frappe_journald`, which gives log lines journald priorities. Both are grafted into both virtualenvs.

## What is development-only

- The [guard rails](../development/guard-rails.md). `prodPythonEnv`, the NixOS module and the containers never see `frappe_devguard`.
- The editable installs of `apps/*`, the watcher and Mailpit.

## Two ways to run it

| You want                                                                                                        | Use                                                    |
| --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Declarative, multi-tenant Frappe on a NixOS host, with systemd units, automatic migrations and journald logging | [The `services.frappe` NixOS module](nixos-service.md) |
| To run Frappe on a host that is not NixOS, or in an orchestrator you already have                               | [OCI container images](images.md)                      |

> [!IMPORTANT]
> The bench repository exposes only the package. The NixOS module is imported directly from frappe-nix, not re-exported by the bench, so a deployment server combines both.

Both read the same `builtBench`. Read [The unified runtime](runtime.md) for the process they run, [Operate a deployed site](operations.md) for migrations, tuning and day-two tasks, and [Logging](logging.md) for the journal contract.

## Production checklist

1. Commit `uv.lock`, every `yarn.lock` that is yours, `node-locks/` and `sites/apps.json`. A flake sees only tracked files.
2. Build the package: `nix build .#default`. It must succeed from a clean checkout.
3. Decide how secrets reach the host. See [Secrets](../development/secrets.md#use-the-same-secrets-on-the-server).
4. Deploy with the module or the images.
5. Create or restore the site. Neither route installs one for you. See [Operate a deployed site](operations.md#create-or-restore-the-site).
