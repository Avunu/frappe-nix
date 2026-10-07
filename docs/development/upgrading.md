---
title: Upgrading frappe-nix
description: Move a bench or an app repository to a newer frappe-nix, what the shell reconciles for you on entry, and what to commit afterwards.
order: 9
tags: [upgrade, flake-update, reconcile]
updated: 2026-10-07
---

To pick up a newer frappe-nix, update the pin and reload the shell:

```bash
nix flake update frappe-nix   # or `nix flake update` for every pin
direnv reload                 # or: nix develop --no-pure-eval
```

That is the whole procedure.

## What the shell reconciles on entry

A frappe-nix bump can ask something new of a bench's **workspace root**. The case so far is `frappe-runtime` becoming a required dependency: a name in `[project].dependencies`, a `[tool.uv.sources]` entry and a build backend named in `[tool.uv.extra-build-dependencies]`. A bench from before the bump has no way to know.

So `enterShell` reconciles `pyproject.toml` on every entry, with the same `ensure-root` step `frappe-init` runs:

- It **adds what is missing** and changes nothing that is already there. Your name, your `requires-python`, your override list and your comments stay, because the file is edited with tomlkit and not rewritten.
- It runs `uv lock` only when that changed the file. On the common path it is one TOML parse and says nothing.

When it does change something, it says so, lists what it added, and tells you to **commit `pyproject.toml` and `uv.lock` and re-enter the shell**, because the shell you are in was built from the previous lock. Until you do, `devenv up` runs whatever shape the old lock supports. A bench whose lock predates the runtime gets the split `web`, `socketio`, `worker` and `scheduler` processes for that session, and the banner says so. Nothing about that shape is degraded: it is `runtime.enable = false`, which `checks.socket` covers.

If `uv lock` fails, because there is no network or because the new requirement genuinely conflicts with an app's pins, both files are put back exactly as they were, mtimes included, and the shell opens anyway. A `pyproject.toml` that declares what `uv.lock` does not carry would fail the _next_ evaluation (see [Dependencies and locks](locks.md#a-stale-uvlock-is-an-evaluation-error)), which is the one outcome this hook exists to avoid: you would be back to fixing the shell from outside it. The next entry retries. To resolve a conflict by hand, make the listed additions in `pyproject.toml`, add the override `uv` asks for, and run `uv lock` in the shell.

## What it deliberately does not do

The shell hook does not register submodules, vendor apps, edit `.gitignore` or `common_site_config.json`, or `git add` anything. That is [the reconciler's](../scaffolding/migrate.md) job, and the reconciler still does all of it for the cases a shell hook should not decide:

```bash
nix run github:Avunu/frappe-nix -- -y
```

A change to the _options_ a bench's `flake.nix` sets cannot be reconciled from inside the shell. `nodeOfflineHashes` was one such. It stays an evaluation error that names the edit.

## In app mode

In an [app repository](../scaffolding/app-mode.md) the pins are flake inputs, so a newer frappe-nix is the same `nix flake update frappe-nix`. Moving the Frappe pin or a sibling is `nix flake update frappe`, then `nix run .#relock`, and you commit `flake.lock` with `nix/`.

The bench root's dev group gained `coverage` and `unittest-xml-reporting`, which [`frappe-test`](../app-standards/testing.md) runs the tests under. An app that has not opted in to the [app standards](../app-standards/README.md) gets them only where its committed `nix/uv.lock` already has them, so no app has to relock after the upgrade:

- **On version-16** the lock has both already (Frappe's `test` extra), so the dev environment gains `coverage` and `xmlrunner`. The next `nix run .#relock` also adds four lines to `nix/uv.lock`: the two names under the root package's `dev` group and their two requirements. Commit them with whatever else that relock changed.
- **On version-15** the lock has neither, so the root and the environment stay exactly as they were. `frappe-test` then has no coverage to run under and says so (exit 10): pass `--no-coverage`, or opt in and run `nix run .#relock`, which adds them.

## The runtime moves with frappe-nix

The unified runtime's source is the `runtime/` directory of this repository, so a bump of frappe-nix is also a bump of the runtime, with no relock in any bench. The one exception is a runtime release that adds a new dependency. That needs one relock:

```bash
nix run .#relock -- --upgrade-package frappe-runtime
```

Note the flag: a plain `uv lock` keeps an already resolved git revision and reports success without changing anything. See [The unified runtime](../production/runtime.md#how-a-bench-gets-it).
