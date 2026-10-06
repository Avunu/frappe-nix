---
title: Repository layout
description: Where things live in the frappe-nix repository, the flake checks that CI runs, and how to build these documentation pages locally.
order: 5
tags: [reference, contributing, layout, tests]
updated: 2026-10-06
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
│   ├── bench-patches.nix     # keeps `bench update` past bench's own patch list
│   ├── root-sync.nix         # shell entry: pyproject.toml up to date with frappe-nix, then uv lock
│   ├── lock-audit.nix        # names a stale uv.lock before uv2nix trips over it
│   ├── overrides.nix         # mysqlclient / pycups / python-ldap / pyvips / cairocffi
│   ├── yarn-lock.nix         # one fetchurl per tarball in a yarn.lock
│   ├── node-locks.nix        # the node-locks/ fallback generator
│   ├── secrets-schema.nix    # the one derivation of a bench's secret set
│   ├── secrets-tools.nix     # generated agenix rules and the recipient checker
│   ├── agecheck.py           # are the .age files encrypted to who we think?
│   ├── backup-fetch.nix      # the object-store half of bench restore, shellchecked
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
│   └── nixos.nix             # services.frappe (the NixOS systemd module)
├── runtime/                  # frappe-runtime: the unified process (a hard fork, MIT)
├── tests/                    # flake checks (see `nix flake check`)
├── docs/                     # these pages
└── docs-site/                # the Jx project that builds docs/ into the documentation site
```

`lib/sh/*.sh` are concatenated into a single `writeShellApplication`, so shellcheck sees the whole program at build time. `main.sh` holds the only top-level code and must stay last.

## Checks

`nix flake check` evaluates every output and builds the checks. The checks are small and mostly offline: stub servers, fixture benches and fake `hooks.py` files stand in for Frappe, so each one runs without a real bench.

```bash
nix flake check --no-build                                # evaluate every output
nix build -L .#checks.x86_64-linux.<NAME>                 # one check
```

On `x86_64-linux` the checks are:

`agecheck`, `app-workspace`, `apps-registry`, `apps-report`, `assets-reassert`, `backup-fetch`, `bench-get-app`, `bench-patches`, `bench-remove-app`, `bench-restore`, `bench-update`, `bench-watch`, `benchcli`, `db-nocow`, `devguard`, `editable-src`, `journald`, `lock-audit`, `logging-fields`, `mariadbd-wrapper`, `migrate-classic`, `migrate-rollback`, `migrate-versions`, `nested-frontend-scripts`, `node-locks`, `node-locks-precedence`, `node-modules`, `node-targets`, `node-verify`, `nodebuild`, `offline-migrate`, `offline-migrate-scripts`, `offline-migrate-unit`, `reconcile-apps`, `requirements-check`, `root-sync`, `runtime`, `sass-embedded`, `secrets-cli`, `setup-requirements`, `socket`, `socket-runtime`, `unixsock`, `update-deps` and `yarn-lock`.

Two of them, `migrate-rollback` and `socket`, run a NixOS virtual machine that builds a full Frappe bench inside the guest. `socket-runtime` does the same for the unified runtime. They are Linux-only and heavy.

The unit suite of the runtime needs a real bench, because it imports `frappe`, so it runs from `runtime/scripts/run-tests.sh <BENCH>` and not from `nix flake check`.

### What CI runs

The `check` workflow runs on every pull request and every push to `main`. Its `flake` job is the cheap, always-on gate:

1. `nix flake check --no-build --show-trace` evaluates every flake output on `x86_64-linux`. That is what breaks when nixpkgs or uv2nix move under the project: renamed attributes, changed function signatures, dropped packages.
2. `nix build .#default` builds the scaffolder outright, which is also where shellcheck runs over `lib/sh/*`.
3. The offline checks `devguard`, `migrate-classic` and `migrate-versions` are built.

The VM tests are not on the pull request path. They build a full Frappe bench inside a NixOS guest, which is far past what a hosted runner does in a reasonable time without a binary cache. Run them from the Actions tab with **Run workflow**.

Dependabot keeps the flake inputs and the GitHub Actions current, and its pull requests merge automatically once the required checks pass. The documentation site's own packages are updated by Dependabot too, but those pull requests are left for a person to review. See `.github/dependabot.yml`.

## Building these pages

The documentation site is a [Jx](https://jxsuite.com) project in `docs-site/` that reads the Markdown in `docs/`. You need [Bun](https://bun.sh) 1.4 or newer.

```bash
cd docs-site
bun install
bun run dev --port 3417      # live reload while you edit docs/
bun run check                # what CI runs: tests, contrast, a strict build and a link check
```

`docs-site/README.md` describes the site, its commands and how it publishes. The pages themselves are plain Markdown that reads the same on GitHub: callouts are GitHub alerts, and links between pages are relative file links.
