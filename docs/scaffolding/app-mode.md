---
title: Develop a single app
description: Use frappe-nix from one Frappe app's own repository, where a small flake.nix is all you commit and the bench around the app is generated from flake inputs.
order: 3
tags: [scaffolding, app-mode, flake-inputs]
updated: 2026-10-06
---

A bench repository is the uv workspace: a committed `pyproject.toml`, a committed `uv.lock` and `apps/*` as git submodules. An app repository has none of that. It is one app, and the bench around it is an implementation detail of developing it.

**App mode** inverts the relationship. The app repository commits a small `flake.nix`, and frappe-nix assembles the bench from flake inputs.

## Set it up

From inside the app's repository, which must be a git repository:

```bash
cd <APP_REPO>
nix run github:Avunu/frappe-nix       # detects an app repo and sets up app mode
direnv allow                          # or: nix develop --no-pure-eval
devenv up                             # then `provision-site` in another shell
```

The scaffolder recognizes an app by a `pyproject.toml` whose `[project].name` names a package that holds a `hooks.py`. It writes three files, `flake.nix`, `.envrc` and a managed `.gitignore` block, stages them with `git add`, and runs `nix run .#relock` to produce `nix/uv.lock`. Nothing is committed.

It never touches the app's own `pyproject.toml`. That file is the app's packaging metadata, and the workspace root frappe-nix generates is a different file that lives in the Nix store.

| Detail         | What the scaffolder does                                                                                              |
| -------------- | --------------------------------------------------------------------------------------------------------------------- |
| Frappe version | Prompts for it, or takes `--frappe-version`, which is required without a terminal.                                    |
| Bench name     | Derived from the app's `[project].name`, normalized. An app repository has exactly one bench, so it is not asked for. |
| Default site   | `<BENCH_NAME>.localhost`, or `--site`.                                                                                |
| Preview        | `--dry-run` prints the plan and changes nothing.                                                                      |
| First lock     | `--skip-lock` skips `nix run .#relock`; run it yourself before you enter the shell.                                   |

> [!IMPORTANT]
> Commit `flake.nix`, `.envrc` and the `nix/` directory. A flake's source tree is only its tracked files, so an uncommitted `nix/uv.lock` is invisible to the build and reads as missing.

## The flake it writes

The flake pins Frappe as a non-flake input, so it is a source tree and not a flake, and enables app mode:

```nix
inputs = {
  frappe-nix.url = "github:Avunu/frappe-nix";
  nixpkgs.follows = "frappe-nix/nixpkgs";
  frappe = { url = "github:frappe/frappe/version-16"; flake = false; };
  # erpnext = { url = "github:frappe/erpnext/version-16"; flake = false; };
};
# …
perSystem = _: {
  frappe-nix = {
    enable = true;
    siteName = "carbon.localhost";
    app = {
      enable = true;
      frappeVersion = "version-16";
      frappe = inputs.frappe;
      # siblings = [ { name = "erpnext"; src = inputs.erpnext; } ];
    };
  };
};
```

Everything else is inferred:

- `app.src` comes from `self`.
- `app.name` comes from the repository's `[project].name`.
- `benchName` is that name, normalized.
- `python` and `nodejs` come from the `frappeVersion` [preset](README.md#versions-and-presets).
- `app.lockDir` defaults to `nix`.

Declare other apps your app needs, such as the apps named in its `required_apps` hook, as flake inputs and list them under `app.siblings`, in install order. It is a list and not an attribute set because that order is the members' order and so the order of `sites/apps.txt`. Re-run `nix run .#relock` after you change the list.

## What you get, and where it lives

| Aspect                    | Bench mode                                                                                | App mode                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| the uv workspace          | the repository                                                                            | a derivation assembled from the flake inputs                |
| `apps/*`                  | git submodules                                                                            | flake inputs, pinned by `flake.lock`                        |
| the app under development | one of the submodules                                                                     | this repository, symlinked into the bench so edits are live |
| the bench you run         | the repository                                                                            | `.frappe-nix/bench/`, generated on shell entry, gitignored  |
| what is committed         | `pyproject.toml`, `uv.lock`, `sites/` (`node-locks/` only for apps without a `yarn.lock`) | `flake.nix`, `nix/uv.lock` (`nix/node-locks/` likewise)     |

`FRAPPE_BENCH_ROOT`, `SITES_PATH` and `PYTHONPATH` name the generated bench. `REPO_ROOT` stays the git worktree, which is where `secrets/*.age` live. devenv's own root is deliberately not moved, so `$DEVENV_STATE`, and with it the MariaDB data directory, stays outside the generated tree. Running `rm -rf .frappe-nix` costs a re-copy of the apps, not the database.

`FRAPPE_PATH` is exported as `$FRAPPE_BENCH_ROOT/apps/frappe`. A Frappe app's build scripts conventionally look for the framework at `<app>/../frappe`, and in app mode that sibling lookup does not work, because `apps/<app>` is a symlink and Node resolves `__dirname` through it. An app that wants a different sibling by path needs `extraEnv`:

```nix
frappe-nix.extraEnv.ERPNEXT_PATH = "…";
```

## Moving the pins

There are no submodules to pull, so `bench-update --pull` and `bench-get-app` are replaced by the flake-input equivalents:

```bash
nix flake update frappe      # or `nix flake update` for all pins
nix run .#relock             # re-resolve, rewriting nix/uv.lock (+ nix/node-locks/ for pins without a yarn.lock)
```

`relock` is deliberately reachable without a dev shell. A missing or stale `uv.lock` fails at evaluation, and the shell that carries `uv` is exactly what refuses to open. It stages both files for you, because a flake's source tree is only its tracked files.

Re-entering the shell after a pin moves re-copies `apps/*` out of the store and tells you the compiled assets are stale. Each app's `node_modules` is carried across, so a bump does not cost a full `yarn install` per app.

> [!WARNING]
> Edits made inside `.frappe-nix/bench/apps/frappe` are in a copy, and the next pin bump discards them. The app you are developing is the exception, because it is your repository and is symlinked in.

## Commands that behave differently

Four bench commands edit the bench as if it were a checkout, and in app mode there is no checkout to edit. They refuse and name the flake-input equivalent instead:

| Command                                  | Use instead                                                       |
| ---------------------------------------- | ----------------------------------------------------------------- |
| `bench-update --pull` and `--node-locks` | `nix flake update`, then `nix run .#relock`                       |
| `bench-get-app` and `bench-new-app`      | Declare the app as a flake input and list it under `app.siblings` |
| `bench-remove-app`                       | Drop it from `frappe-nix.app.siblings`                            |

`bench-update --migrate` and `--build` are unaffected, and everything else works exactly as it does in a bench. See [Everyday commands](../development/commands.md).

## Production parity

App mode is not development-only. `nix build` produces the same `builtBench` a bench does, with frappe, the siblings and this app all compiled together, so it is a real check that the app builds in a clean bench:

```bash
nix build                    # result/bench, assets compiled
```

`containers.enable` and the [`services.frappe` module](../production/nixos-service.md) work unchanged.
