---
title: Apps in a bench
description: How frappe-nix registers apps in sites/apps.txt and apps.json, the two shapes an app can have in a bench, and how it keeps a site's installed apps in step with them.
order: 7
tags: [apps, submodules, registry, apps-reconcile]
updated: 2026-10-06
---

This page covers three things that are easy to confuse: which apps the bench knows about (the registry), where their source lives (submodule or local), and which apps a site has actually installed.

## The app registry

`sites/apps.txt` is what `frappe.get_all_apps()` returns. It is the list every process consults, and the one `install-app` checks a name against. `sites/apps.json` is bench's record of each app's version and pin (`is_repo`, `resolution.{commit_hash,branch}`, `required`, `idx`, `version`), in bench's own shape.

frappe-nix generates both from one rule: **the registered apps are the `[tool.uv.workspace].members`**, in declared order, with `frappe` first. They are the members because that is what the virtualenv actually installs. A directory under `apps/` that is not a member is on `PYTHONPATH` and nothing more, and evaluation warns about it.

One tool writes them, `frappe-nix-workspace sync-registry`, and it runs wherever the members or the pins change:

- `frappe-init`, `bench-get-app`, `bench-new-app`, `bench-remove-app` and `bench-update --pull`;
- every dev-shell entry, which is the one hook that also sees a pin moved by hand inside `apps/<APP>`;
- `benchRoot`, when the package is built.

The dev shell's regeneration and the build's are byte-identical when the committed record is current. A dirty `sites/apps.json` after a pin moves therefore means exactly one thing: commit it with the bump. The build reads the committed file for the one fact the flake's source tree cannot carry, a submodule's commit, and recomputes everything else from the sources. A stale record costs a stale `commit_hash` and nothing more.

- `version` comes from `[project].version` or the app's `__version__`.
- `required` comes from `hooks.py`'s `required_apps`.
- The branch comes from `.gitmodules`, or from the flake input's ref in app mode.

At runtime the two files are symlinks into the package, like `sites/assets`. `frappe-init-<SITE>` and the container entrypoint relink them on every start, so a deploy that adds an app registers it, and nothing on the host can drift them. An upstream `bench get-app` run by hand fails on the read-only store, as it should. Only `common_site_config.json` is the operator's: it is seeded once and never touched again.

The one thing this does not change is that `bench build` still compiles assets for every directory under `apps/`, registered or not, because esbuild scans the directory and not the registry.

## Local apps

An app lives in a bench in one of two shapes, and everything above works with either.

- A **git submodule**. `.gitmodules` names it, `bench-update --pull` moves it and `nix build` fetches it. `bench-get-app` makes these.
- A **local app**: source committed with the bench, with no `.git` of its own. `bench-new-app` makes these, scaffolding with `--no-git`. `frappe-init --migrate` turns an app with no usable remote into one by _vendoring_ it. The nested `.git` moves to `.frappe-nix-backup/<APP>.git`, the source is `git add`ed, and the app becomes a workspace member like any other. `bench-update --pull` reports it as having nothing to pull, and `sites/apps.json` records it with `is_repo: false`.

### Fresh clones

Nothing moves a submodule except `bench-update --pull`, bar a fresh clone's first shell entry, which checks out every app submodule that clone has never had.

That first entry checks each one out as a _partial_ clone and not the shallow one `shallow = true` asks for:

- The branch `.gitmodules` pairs the app with comes with every commit and folder but no file contents, so `git log` and merge-bases work offline and a checkout downloads only the files it needs.
- Every other branch comes as commits only, so `git switch <BRANCH>` works and downloads that branch's folders and files as it switches.
- An app already checked out shallow, or following a single branch, is brought to the same shape in place, once, without moving its checkout.

Past that, entering the shell only reports a registered submodule with no checkout, one removed by hand or deinitialized, and the command that checks it out. Doing it for you on every `nix develop` is how an app removed by hand came straight back, re-cloned.

### A gitlink with no .gitmodules entry

There is a third shape that git will happily produce and nothing can use: a nested repository, from `bench new-app` without `--no-git` or a `git clone` into `apps/`, that was `git add`ed as it was. The index records a **gitlink with no `.gitmodules` entry**.

- `git submodule update --init` and `git submodule foreach` die on it with `No url found for submodule path 'apps/<APP>' in .gitmodules`.
- The flake's source tree carries an empty directory in its place, so `nix build` silently produces a bench without the app.

frappe-nix never iterates `git submodule …` itself for that reason. Every dev-shell hook goes through `frappe-nix-workspace apps`, which names what each `apps/<APP>` is, and it tells you on shell entry and on `bench-update --pull` when it finds one. There are two ways out:

```bash
frappe-init --migrate          # vendor it: source committed, history kept in .frappe-nix-backup/
# or, once it has a remote to live at:
git rm --cached apps/<APP> && rm -rf apps/<APP> && bench-get-app <OWNER>/<APP>
```

## Installed-app drift

`sites/apps.txt` is bench-level, and frappe-nix regenerates it from the workspace members in `app.siblings` order. A site's installed apps are per site and database-backed (`frappe.get_installed_apps()`), and they only ever grow through an explicit `bench install-app`.

`apps.txt` is the candidate list that `install_app()` validates a name against. It is not a queue anything drains, and `bench migrate` walks the database list and never `apps.txt`. So if you pin a new sibling into an already provisioned bench, nothing installs it. The app is importable, on `PYTHONPATH` and even visible in the desk's app switcher, and every page it owns returns 404 or throws, with no signal pointing at "app not installed". [Issue 32](https://github.com/Avunu/frappe-nix/issues/32) describes how this presented: a report page that quietly ran on stock `frappe-datatable` because the app that was supposed to replace it had never been installed.

`appsReconcile.enable` closes this. A `frappe:apps-reconcile` task diffs `sites/apps.txt` against `siteName`'s installed apps on every `devenv up` and installs whatever is missing. `install-app` is idempotent, a no-op unless `--force`, which this never passes, so on an already reconciled bench the task costs one `bench list-apps`.

It defaults to `true` whenever `siteName` names one site, which is the common case for every app-mode bench. It defaults to `false` in multi-tenant mode (`siteName = ""`), where the task has no single site to target. There, run it by hand:

```bash
reconcile-apps <SITE_NAME>
```

> [!WARNING]
> `provision-site` is not a substitute for this. It installs into a site it just created, and running it again to pick up a later-pinned sibling would drop the site's database, because it runs `bench new-site --force`.
