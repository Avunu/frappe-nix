---
title: frappe-nix documentation
description: Reusable Nix infrastructure for Frappe and ERPNext benches, from the development shell to OCI images and a multi-site NixOS module.
nav_title: Overview
order: 0
tags: [frappe, erpnext, nix, overview]
updated: 2026-10-06
---

frappe-nix packages everything needed to develop and ship a [Frappe](https://frappeframework.com/) or ERPNext bench declaratively, so a project that uses it can be a thin wrapper around a `flake.nix` and a few lock files. The same description builds the development shell on a laptop and the production system on a server.

You consume it as a [flake-parts](https://flake.parts/) module, either from a bench repository or from the repository of a single Frappe app, where the bench around the app is generated from flake inputs instead of being committed.

## What you get

From one `uv` workspace and an `apps/` tree, frappe-nix provides:

- A **devenv development shell** with MariaDB, Redis, nginx, the Frappe runtime, an asset watcher and Mailpit. Apps are editable installs, so source edits reload, and **guard rails** stop a bench restored from production from mailing customers, overwriting a production bucket or uploading a backup.
- Reproducible **production Python environments**, built by [uv2nix](https://github.com/pyproject-nix/uv2nix) from your committed `uv.lock`.
- Reproducible **`node_modules`** for every app and every nested frontend, built from each one's own `yarn.lock`.
- A **`builtBench`** package that runs `bench build` at build time, so compiled assets are part of the artifact. The NixOS module and the container images both consume it.
- **OCI container images**: `runtime`, `nginx` and `bench-cli`, or an eight-image split set when you turn the unified runtime off.
- A multi-tenant **NixOS module**, `services.frappe`, with per-site systemd units, secrets handling, snapshot-protected migrations on deploy and journald logging.
- A set of portable **bench scripts** such as `provision-site`, `bench-update` and `bench-get-app`, and a `bench` wrapper that sends the commands that need frappe-nix handling to them.
- **Age-encrypted secrets** committed next to the code, which the dev shell decrypts and the deployment host can read from the same ciphertext.

## How the pieces fit

Lock files carry the contract between development and production. You change dependencies the way you always have, with `uv` and `yarn`, and Nix rebuilds everything else from the results.

| You commit             | Nix builds from it                                                       |
| ---------------------- | ------------------------------------------------------------------------ |
| `uv.lock`              | the production Python environment, through uv2nix                        |
| each app's `yarn.lock` | each app's `node_modules`, with `yarn install --offline`                 |
| `flake.lock`           | the pinned nixpkgs, devenv and uv2nix that everything else is built with |

All of it ends up in `builtBench`, the one package that the OCI images and `services.frappe` both consume.

Read [From development to production](production/README.md) for the full contract.

## Choose your starting point

| You have                              | Do this                                           | Read                                                |
| ------------------------------------- | ------------------------------------------------- | --------------------------------------------------- |
| Nothing yet, and want to try it       | Scaffold a new bench with `nix run`               | [Getting started](getting-started.md)               |
| A classic `bench init` bench          | Migrate it in place; nothing is deleted           | [Migrate an existing bench](scaffolding/migrate.md) |
| The repository of a single Frappe app | Add a small `flake.nix`; the bench is generated   | [Develop a single app](scaffolding/app-mode.md)     |
| A bench already on frappe-nix         | Pull a newer frappe-nix                           | [Upgrading frappe-nix](development/upgrading.md)    |
| A built bench to deploy               | Run it as a NixOS service, or as container images | [Production](production/README.md)                  |

## Where to go next

- **New to Nix, or deciding whether to adopt it?** Start with [Why frappe-nix](why-frappe-nix.md), which includes the trade-offs and the cases where you should not use it.
- **Working in the development shell?** See [The development shell](development/README.md), [Everyday commands](development/commands.md) and [Development guard rails](development/guard-rails.md).
- **Deploying?** See [Build production images](production/images.md) or [Run Frappe as a NixOS service](production/nixos-service.md), then [Operate a deployed site](production/operations.md).
- **Looking something up?** Every option is in the [reference](reference/README.md), and [Troubleshooting](reference/troubleshooting.md) lists the errors you are most likely to meet, with their fixes.

## The project

frappe-nix is open source under the [MIT License](../LICENSE). The source, issues and pull requests are at [github.com/Avunu/frappe-nix](https://github.com/Avunu/frappe-nix), and [Avunu/frappe-devenv](https://github.com/Avunu/frappe-devenv) is the reference bench: a working frappe, erpnext and hrms bench wired up the way [Write the flake by hand](scaffolding/write-the-flake.md) shows. The project is maintained by [Avunu](https://avunu.net/open-source/frappe-nix/).

> [!NOTE]
> These pages describe the `main` branch of frappe-nix. They live in the `docs/` folder of the repository and change with the code, so the version you read is the version in the checkout you have.
