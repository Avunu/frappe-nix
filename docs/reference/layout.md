---
title: Repository layout
description: Where things live in the frappe-nix repository, the flake checks that CI runs, and how to build these documentation pages locally.
order: 5
tags: [reference, contributing, layout, tests]
updated: 2026-10-20
---

This page is for people who read or change frappe-nix itself.

## Layout

```text
frappe-nix/
├── flake.nix                 # flakeModules / nixosModules / lib outputs, and the checks
├── LICENSE                   # MIT
├── lib/
│   ├── python.nix            # mkPythonEnvs: prod and editable-dev virtualenvs (uv2nix)
│   ├── bench.nix             # app discovery, node_modules (yarn install --offline from each yarn.lock), benchRoot
│   ├── app-workspace.nix     # app mode: the bench workspace, assembled in the store
│   ├── bench-patches.nix     # keeps `bench update` past bench's own patch list (frappe_nix_tools.deps)
│   ├── root-sync.nix         # shell entry: pyproject.toml up to date with frappe-nix, then uv lock
│   ├── relock-advice.nix     # how a bench re-locks, for the stale-lock message
│   ├── build-deps.nix        # [tool.uv.extra-build-dependencies] from uv.lock
│   ├── overrides.nix         # mysqlclient (the shared fixes are python-devops-nix's)
│   ├── yarn-lock.nix         # one fetchurl per tarball in a yarn.lock
│   ├── node-locks.nix        # the node-locks/ fallback generator (frappe_nix_tools.deps)
│   ├── secrets.nix           # python-devops-nix's secrets tools, with frappe-nix's names
│   ├── shell-tools.nix       # python-devops-nix's dev-shell pieces (sources, db-nocow, banner, CI mode), configured
│   ├── backup-fetch.nix      # the object-store half of bench restore (python-devops-nix's lib.backups)
│   ├── devguard/             # frappe_devguard: guards against reaching production (dev only)
│   ├── unixsock/             # frappe_unixsock: unix-socket transport fixes (dev and prod)
│   ├── journald/             # frappe_journald: journald priorities, no log files (dev and prod, acts only under systemd)
│   ├── nodebuild/            # frappe_nodebuild: hands Frappe's asset builds the esbuild preload (dev and prod builds)
│   ├── benchcli/             # frappe_benchcli: the virtualenv's `bench update` / `bench build` hand over to bench-update / bench-build (dev only)
│   ├── init.nix              # `nix run` entry point: builds frappe-init from sh/*
│   ├── sh/                   # the scaffolder and migrator, concatenated into one script
│   │   ├── common.sh         #   presets, naming, output helpers
│   │   ├── detect.sh         #   bench shape, Frappe version, per-app classification
│   │   ├── template.sh       #   staged template render, .gitignore and site config merge
│   │   ├── apps.sh           #   submodule registration, vendoring, workspace sync
│   │   ├── pipeline.sh       #   the phases both modes share
│   │   ├── init.sh           #   scaffold mode
│   │   ├── app-init.sh       #   app mode: set up an app's own repository
│   │   ├── migrate.sh        #   migrate mode
│   │   └── main.sh           #   flags and mode dispatch (must be concatenated last)
│   ├── frappe-workspace.py   # apps/ ⇄ pyproject.toml ⇄ sites/apps.{txt,json} reconciler (tomlkit)
│   ├── offline-migrate.py    # alters large tables online ahead of bench migrate (pt-online-schema-change)
│   ├── offline-migrate.nix   # pt-online-schema-change with its perl
│   ├── frappe-presets.json   # Frappe version → python / node / branch matrix
│   └── scripts.nix           # portable bench shell scripts
├── templates/
│   ├── bench/                # what a new bench is laid down from
│   └── app/                  # what an app repository is laid down from
├── modules/
│   ├── flake-module.nix      # imports devenv.flakeModule, secrets.nix, devenv.nix and containers.nix
│   ├── devenv.nix            # perSystem.frappe-nix options, the dev shell and the packages
│   ├── secrets.nix           # top-level frappe-nix.secrets
│   ├── containers.nix        # OCI image builds
│   └── nixos.nix             # services.frappe-nix (the NixOS systemd module)
├── runtime/                  # frappe-runtime: the unified process (a hard fork, MIT)
├── tests/                    # flake checks (see `nix flake check`)
└── docs/                     # these pages
```

`lib/sh/*.sh` are concatenated into a single `writeShellApplication`, so shellcheck sees the whole program at build time. `main.sh` holds the only top-level code and must stay last.

## Checks

`nix flake check` evaluates every output and builds the checks. The checks are small and mostly offline: stub servers, fixture benches and fake `hooks.py` files stand in for Frappe, so each one runs without a real bench.

```bash
nix flake check --no-build                                # evaluate every output
nix build -L .#checks.x86_64-linux.<NAME>                 # one check
```

On `x86_64-linux` the checks are:

`app-workspace`, `apps-registry`, `assets-reassert`, `bench-get-app`, `bench-new-app`, `bench-patches`, `bench-remove-app`, `bench-rename-app`, `bench-restore`, `bench-update`, `bench-watch`, `benchcli`, `bootstrap`, `bootstrap-lock`, `build-deps`, `check-names`, `ci-pins`, `command-flags`, `config-blocks`, `containers-registry`, `db-nocow`, `dependabot`, `dev-build-deps`, `devguard`, `devguard-core`, `devguard-production`, `devguard-renames`, `devguard-test-relax`, `editable-src`, `env-vars`, `frappe-upstream-config`, `grafts`, `ide-config`, `ide-resolve`, `journald`, `lock-audit`, `logging-fields`, `mariadbd-wrapper`, `migrate-classic`, `migrate-rollback`, `migrate-versions`, `nested-frontend-scripts`, `nix-lint`, `node-locks`, `node-locks-precedence`, `node-modules`, `node-targets`, `node-verify`, `nodebuild`, `offline-migrate`, `offline-migrate-scripts`, `offline-migrate-unit`, `option-paths`, `pdf-renderers`, `pdf-renderers-unit`, `port-lease`, `production-unit`, `provision`, `reconcile-apps`, `relock`, `renames-devenv`, `renames-nixos`, `renames-option-tree`, `requirements-check`, `root-sync`, `runtime`, `sass-embedded`, `scaffold-modes`, `secrets-module`, `setup-requirements`, `shared-config`, `socket`, `socket-runtime`, `sources`, `ty`, `unixsock`, `update-deps` and `yarn-lock`, plus the 34 app standards checks, `standards-<name>` (tests/standards/; `standards-all` builds them all).

Each is registered through python-devops-nix's `lib.checks.mk`, so a build log names it `frappe-nix-<NAME>-check`, or `vm-test-run-frappe-nix-<NAME>` for a NixOS VM test. A new check takes `checks` (the helper) as an argument and is `checks.script "<NAME>" { tools = [ … ]; } ''…''`, `checks.eval`, or `checks.nixosTest`.

Seven of them are python-devops-nix's conformance checks, from its `lib.checks.conventions` (tests/conventions.nix): `ci-pins`, `dependabot`, `check-names`, `env-vars`, `option-paths`, `config-blocks` and `command-flags`. They check frappe-nix against the conventions it shares with odoo-nix: the pins in `.github`, the Dependabot entries, the check names, the `FRAPPE_*` variables, the option paths, the `settings` block and the shared flags of `bench restore` and `frappe-init`. `env-vars` takes a list of the `FRAPPE_*` names that may stay outside `FRAPPE_NIX_*`: Frappe's own, frappe-runtime's own, the old names until 2.0.0, frappe-init's template keys and, until they are renamed or kept with a reason, seven names of frappe-nix's own. `option-paths` and `config-blocks` evaluate a NixOS system, so they are Linux-only.

Four of them, `migrate-rollback`, `pdf-renderers`, `socket` and `socket-runtime`, run a NixOS virtual machine against a stub bench. They are Linux-only.

The unit suite of the runtime needs a real bench, because it imports `frappe`, so it runs from `runtime/scripts/run-tests.sh <BENCH>` and not from `nix flake check`.

### What CI runs

The `check` workflow runs on every pull request and every push to `main`. Its `flake` job is the cheap, always-on gate:

1. `nix flake check --no-build --show-trace` evaluates every flake output on `x86_64-linux`. That is what breaks when nixpkgs or uv2nix move under the project: renamed attributes, changed function signatures, dropped packages.
2. `nix build .#default` builds the scaffolder outright, which is also where shellcheck runs over `lib/sh/*`.
3. Every check that is not a VM test is built. The list is read from the flake, so a new check is on the pull request path as soon as it is in `checks`.

The `vm-tests` job then builds each NixOS VM test (a check with a `driver`) as its own matrix entry, on every pull request and push. A manual **Run workflow** runs them only with `vm-tests` ticked.

When a job fails, the `report` job comments every job's result on the pull request, with each failed check and the end of its build log, and edits that comment once the checks pass.

The `autofix` workflow runs `nix fmt` and the prek hooks over a pull request's branch and pushes what they rewrite as a `style:` commit, so a clone without `prek install` still gets a passing `lint`. A push made with the workflow's token starts no workflow, so it then dispatches `check` (with the VM tests), `pr-policy` and `Docs` on the branch. Pull before you push again. A fork's branch is not the workflow's to push to, so there the `lint` log has the diff.

Dependabot keeps the flake inputs and the GitHub Actions current, and its pull requests merge automatically once the required checks pass. See `.github/dependabot.yml`.

## Building these pages

The documentation site is built from the shared workspace [Avunu/site-docs](https://github.com/Avunu/site-docs), where frappe-nix is a submodule that tracks its latest release; there is no site project in this repository. The pages are plain Markdown that reads the same on GitHub: callouts are GitHub alerts, and links between pages are relative file links.

Every pull request runs the `Docs` workflow (its check, `build / Check`, is required by the `main` ruleset, so it cannot be limited to the pull requests that touch `docs/`), which builds the workspace with the pull request's pages in place of the submodule and checks them strictly, links to the other projects included. To preview or check locally, follow the site-docs README with `--source frappe-nix=<this checkout>`.
