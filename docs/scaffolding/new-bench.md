---
title: Create a new bench
description: Scaffold a fresh Frappe bench with frappe-nix, the equivalent of bench init, choosing a Frappe version and a set of apps.
order: 1
tags: [scaffolding, bench, uv]
updated: 2026-10-06
---

`nix run github:Avunu/frappe-nix` scaffolds a fresh bench: the frappe-nix equivalent of `bench init`. You choose a Frappe version, which fixes the Python and Node versions from a [preset](README.md#versions-and-presets), and an optional set of apps. The scaffolder then writes the wrapper flake, adds `frappe` and the apps as git submodules pinned to that version's branch, and runs `uv lock`.

## Run it

Interactively, from the directory where the bench should live:

```bash
nix run github:Avunu/frappe-nix
```

With a terminal and no flags, the scaffolder prompts for the Frappe version, the apps (a picker with erpnext, hrms, payments, helpdesk, crm, lms, builder, insights, wiki, print_designer, webshop and drive) and the directory, which defaults to `frappe-bench`.

Non-interactively, pass everything as flags:

```bash
nix run github:Avunu/frappe-nix -- \
  --frappe-version version-15 --apps erpnext,hrms --name mybench mybench
```

Then enter the directory and start the stack:

```bash
cd mybench
direnv allow
devenv up
```

In another terminal, run `provision-site` to create the site. [Getting started](../getting-started.md) walks through those steps.

## Flags

| Flag                   | Meaning                                                                                   |
| ---------------------- | ----------------------------------------------------------------------------------------- |
| `--frappe-version <V>` | `develop`, `version-16` or `version-15`. Required without a terminal.                     |
| `--apps <A,B,C>`       | Apps to add: bare names (resolved to `frappe/<name>`), `owner/repo`, or full git URLs.    |
| `--name <NAME>`        | The bench name: the container image prefix and the source of the per-bench port offset.   |
| `--site <SITE>`        | The default site. Defaults to `frappe.localhost`.                                         |
| a positional directory | Where to create the bench. Prompted for when omitted, with `frappe-bench` as the default. |
| `--skip-lock`          | Do not run `uv lock`.                                                                     |
| `--dry-run`            | Print the plan and exit without changing anything.                                        |

The full list, including the migration-only flags, is in [Scaffolder reference](../reference/scaffolder.md).

## How apps are chosen

Each app follows the chosen version's branch when the remote has one. The scaffolder checks with `git ls-remote` and falls back to the repository's default branch otherwise. Apps are cloned shallow first, then registered as git submodules by the same pipeline [migration](migrate.md) uses, so a new bench and a migrated one cannot drift apart.

> [!NOTE]
> The presets live in `lib/frappe-presets.json` and are curated from Frappe's own `requires-python` and `engines` fields, so they move as Frappe's requirements move. The Python and Node versions in the generated `flake.nix` are plain options, and you can change them there.

## What it writes

| File                                | Purpose                                                                                                       |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `flake.nix`                         | The thin wrapper. See [Write the flake by hand](write-the-flake.md).                                          |
| `.envrc`                            | One line, `use flake . --no-pure-eval`, so direnv loads the shell.                                            |
| `pyproject.toml` and `uv.lock`      | The uv workspace, with `members = apps/*`, and its lock.                                                      |
| `apps/*`                            | `frappe` and your apps, as git submodules.                                                                    |
| `sites/apps.txt`, `sites/apps.json` | The registered apps and their pins. See [Apps in a bench](../development/apps.md#the-app-registry).           |
| `.gitignore`                        | A managed block that keeps site data, logs, `node_modules` and similar out of git.                            |
| `README.md`                         | A short readme for the bench, with the day-to-day commands.                                                   |
| `secrets/README.md`                 | Explains the layout of the age-encrypted secrets you can add later. See [Secrets](../development/secrets.md). |
| `logs/`, `config/pids/`             | Empty directories kept with `.gitkeep` files.                                                                 |

The generated `flake.nix` also carries a commented-out `frappe-nix.secrets` block. Uncomment it and add your SSH public key to enable [secrets](../development/secrets.md) and [restoring from production](../development/restore-from-production.md).

The scaffolder never overwrites a file that already exists, and the `.gitignore` block is spliced in between marker lines, so a later run can upgrade it in place.

> [!TIP]
> The scaffolder stages its work but does not commit it. Review it with `git diff --cached --stat`.
