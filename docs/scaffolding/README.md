---
title: Choose a mode
description: The three ways to put a Frappe project on frappe-nix, how the scaffolder picks one from the directory you run it in, and what each leaves in your repository.
nav_title: Scaffolding
order: 3
tags: [scaffolding, bench, app-mode, migrate]
updated: 2026-10-06
---

frappe-nix works in two shapes, and one command sets up all three starting points. `nix run github:Avunu/frappe-nix` looks at the directory you run it in and does the matching thing.

| You have                         | Mode             | What it does                                                               | What you commit                                                                                       |
| -------------------------------- | ---------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| An empty directory               | New bench        | Creates a bench, adds apps as submodules, locks Python                     | `flake.nix`, `pyproject.toml`, `uv.lock`, `sites/apps.txt`, `sites/apps.json`, `apps/*` as submodules |
| An existing `bench init` bench   | Migrate in place | Reconciles it into a frappe-nix bench; adds what is missing, never deletes | The same files as a new bench, added by the migrator                                                  |
| A single Frappe app's repository | App mode         | Writes a small `flake.nix`; the bench is generated from flake inputs       | `flake.nix`, `nix/uv.lock`, and `nix/node-locks/` only if a pin ships no `yarn.lock`                  |

In a bench, the repository is the uv workspace. In app mode there is no bench to commit: frappe-nix assembles one from flake inputs into `.frappe-nix/bench/`, which is gitignored and safe to delete.

- [Create a new bench](new-bench.md)
- [Migrate an existing bench](migrate.md)
- [Develop a single app](app-mode.md)
- [Write the flake by hand](write-the-flake.md), for the module's own shape and the options you will touch most

## How the mode is picked

The scaffolder inspects the target directory, which is the directory you run it in unless you pass one as an argument.

1. A directory that is empty or does not exist gets a **new bench**.
2. A directory that is already a frappe-nix bench (a `flake.nix` that mentions `frappe-nix` and a `pyproject.toml` with a `[tool.uv.workspace]` table) is **reconciled**, which is the migrate mode: it repairs drift and is a no-op when nothing has drifted.
3. A directory with `apps/` and `sites/` and at least one app that has a `hooks.py` is a classic bench, so it is **migrated**. If it already has a `flake.nix` that is not a frappe-nix wrapper, the scaffolder stops instead: `--migrate --force` then writes frappe-nix's wrapper to `flake.nix.frappe-nix` for you to merge by hand.
4. A directory with a `pyproject.toml` whose `[project].name` names a package that holds a `hooks.py` is a Frappe app, so it gets **app mode**.
5. Anything else stops with an explanation. A bench skeleton with no app in it has nothing to migrate, and a directory that is not empty and is neither a bench nor an app tells you which flag forces the mode you meant.

Use `--init`, `--migrate` or `--app` to force a mode, and `--force` to relax the guard that stops you running a mode in a directory of the wrong shape. `--force` never overwrites an existing file. `--dry-run` prints the plan and changes nothing.

## What frappe-nix expects

A bench is a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/) laid out the way a Frappe bench is:

```text
.
├── flake.nix                 # your thin wrapper (see Write the flake by hand)
├── pyproject.toml            # [tool.uv.workspace] members = apps/*, [tool.uv.sources]
├── uv.lock                   # committed lock; drives the Nix Python env
├── apps/                     # Frappe apps (typically git submodules)
│   ├── frappe/
│   ├── erpnext/
│   └── …                     # each with pyproject.toml; yarn.lock if it has assets
├── node-locks/               # only for apps that ship no yarn.lock: a generated fallback; commit it
└── sites/
    ├── apps.txt              # generated: the registered apps (the workspace members)
    └── apps.json             # generated: their versions and pins; commit it
```

In [app mode](app-mode.md) frappe-nix builds that layout itself, and the repository is a Frappe app instead:

```text
.
├── flake.nix                 # your thin wrapper
├── pyproject.toml            # the APP's own; frappe-nix never touches it
├── <app_name>/hooks.py       # what makes this directory a Frappe app
└── nix/
    ├── uv.lock               # committed lock; drives the Nix Python env
    └── node-locks/           # fallback yarn.lock for pins that ship none; commit it
```

> [!IMPORTANT]
> A flake's source tree is exactly its git-tracked files. Whatever the build needs, including `uv.lock`, `node-locks/` and the `.age` secret files, must be committed, or at least staged with `git add`. An untracked file is invisible to Nix and reads as missing.

## Versions and presets

The Frappe version fixes the interpreters. The presets live in [`lib/frappe-presets.json`](../../lib/frappe-presets.json), taken from Frappe's `requires-python` and `engines`.

| Preset       | Python      | Node        | Frappe branch |
| ------------ | ----------- | ----------- | ------------- |
| `develop`    | `python314` | `nodejs_24` | `develop`     |
| `version-16` | `python314` | `nodejs_24` | `version-16`  |
| `version-15` | `python312` | `nodejs_20` | `version-15`  |

The migrator detects the version from the bench (the branch, then `sites/apps.json`, then `frappe.__version__`). frappe-nix has no preset below `version-15`: older apps ship a `setup.py` with no `pyproject.toml` and cannot be uv workspace members. The python and node defaults are overridable in the generated `flake.nix`.

## Scaffolder flags

The complete flag list is in [Scaffolder reference](../reference/scaffolder.md).
