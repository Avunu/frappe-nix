# frappe-nix

Reusable Nix infrastructure for [Frappe](https://frappeframework.com/) bench projects.

**Documentation:** [frappe-nix.avunu.net](https://frappe-nix.avunu.net). The same pages are in [`docs/`](docs/README.md), so they also read on GitHub.

`frappe-nix` packages everything needed to develop and ship a Frappe/ERPNext bench declaratively, so a consuming project can be a thin wrapper. From a single `uv` workspace + `apps/` tree it provides:

-   a **devenv** development shell (MariaDB, Redis, the Frappe runtime, watch, Mailpit) with editable Python installs, live asset reloading, and [guard rails](docs/development/guard-rails.md) so no bench can mail customers, overwrite a production bucket or upload a backup
-   reproducible **production Python environments** (via [uv2nix](https://github.com/pyproject-nix/uv2nix))
-   reproducible **node\_modules** for every app and nested frontend, straight from each one's own `yarn.lock`
-   a `benchRoot` derivation that assembles the whole `/bench` tree
-   a **`builtBench`** package that runs `bench build` at build time (immutable assets) — the production-ready deployable consumed by both the NixOS module and OCI containers
-   **OCI container images** — `runtime`, `nginx`, `bench-cli`, or the eight-image split set (web, scheduler, three workers, socketio, nginx, bench-cli) with `runtime.enable = false`
-   a multi-tenant **NixOS module** (`services.frappe`) with per-site systemd units
-   a set of portable **bench scripts** (`provision-site`, `bench-update`, `bench-get-app`, …)

It is consumed as a [flake-parts](https://flake.parts/) module, from a bench repository or — see [Develop a single app](docs/scaffolding/app-mode.md) — from one Frappe app's own repository, where the bench around it is generated from flake inputs instead of committed.

## Where to read more

| I want to… | Read |
| --- | --- |
| try it | [Getting started](docs/getting-started.md) |
| decide whether to adopt it | [Why frappe-nix](docs/why-frappe-nix.md) |
| scaffold a bench, set up an app repository, or convert an existing bench | [Choose a mode](docs/scaffolding/README.md) |
| work in the dev shell | [The development shell](docs/development/README.md), [Everyday commands](docs/development/commands.md), [Development guard rails](docs/development/guard-rails.md) |
| build images or deploy | [From development to production](docs/production/README.md), [Build production images](docs/production/images.md), [Run Frappe as a NixOS service](docs/production/nixos-service.md) |
| manage secrets and restore production data | [Secrets](docs/development/secrets.md), [Restore a production backup](docs/development/restore-from-production.md) |
| look up an option | [Dev shell options](docs/reference/dev-shell-options.md), [Secrets options](docs/reference/secrets-options.md), [`services.frappe` options](docs/reference/nixos-options.md) |
| fix an error | [FAQ and troubleshooting](docs/reference/troubleshooting.md) |

## Requirements

`frappe-nix` expects a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/) laid out the way a Frappe bench is:

```
.
├── flake.nix                 # your thin wrapper (see Quick start)
├── pyproject.toml            # [tool.uv.workspace] members = apps/*, [tool.uv.sources]
├── uv.lock                   # committed lock — drives the Nix Python env
├── apps/                     # Frappe apps (typically git submodules)
│   ├── frappe/
│   ├── erpnext/
│   └── …                     # each with pyproject.toml; yarn.lock if it has assets
├── node-locks/               # only for apps that ship no yarn.lock: a generated fallback — commit it
└── sites/
    ├── apps.txt              # generated: the registered apps (the workspace members)
    └── apps.json             # generated: their versions and pins — commit it
```

In [app mode](docs/scaffolding/app-mode.md) frappe-nix builds that layout itself, and the repository is a Frappe app instead:

```
.
├── flake.nix                 # your thin wrapper
├── pyproject.toml            # the APP's own — frappe-nix never touches it
├── <app_name>/hooks.py       # what makes this directory a Frappe app
└── nix/
    ├── uv.lock                     # committed lock — drives the Nix Python env
    └── node-locks/                 # fallback yarn.lock for pins that ship none — commit it
```

## Create a new bench, migrate one, or develop a single app

One command does all three. It detects the mode from the directory it runs in.

```sh
nix run github:Avunu/frappe-nix                 # interactive (gum prompts)
# or fully non-interactive:
nix run github:Avunu/frappe-nix -- \
  --frappe-version version-15 --apps erpnext,hrms --name mybench mybench
cd mybench && direnv allow && devenv up         # then `provision-site` in another shell
```

-   In an **empty directory** it scaffolds a fresh bench — the frappe-nix equivalent of `bench init`. See [Create a new bench](docs/scaffolding/new-bench.md).
-   In an **existing `bench init` bench** it [migrates it in place](docs/scaffolding/migrate.md). It is a reconciler: it adds what is missing, repairs drift, never deletes, and stages the result for you to review (`--dry-run` shows the plan first).
-   In **one Frappe app's repository** it sets up [app mode](docs/scaffolding/app-mode.md): a small `flake.nix` and `nix/uv.lock`, with the bench generated into `.frappe-nix/` and gitignored.

## Quick start

A complete consuming flake is just a configured module. Because `frappe-nix.lib.mkFlake` merges frappe-nix's own inputs (nixpkgs, devenv, uv2nix, …) into yours, you don't re-declare them.

This is the **bench** shape — a repository that holds `apps/*`. For a single app's own repository, see [Develop a single app](docs/scaffolding/app-mode.md); the module is the same, but `workspaceRoot` is replaced by `app.*` and frappe-nix builds the workspace itself.

```nix
{
  inputs = {
    # apps/* are git submodules; expose their contents to the flake source tree.
    self.submodules = true;
    frappe-nix.url = "github:Avunu/frappe-nix";
    # flake-parts resolves perSystem `pkgs` from an input literally named `nixpkgs`.
    nixpkgs.follows = "frappe-nix/nixpkgs";
  };

  outputs =
    { self, frappe-nix, ... }@inputs:
    frappe-nix.lib.mkFlake { inherit inputs; } (
      { inputs, self, ... }:
      {
        imports = [ frappe-nix.flakeModules.default ];
        systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];

        perSystem =
          { pkgs, ... }:
          {
            frappe-nix = {
              enable = true;
              benchName = "mybench";          # container image prefix: mybench/runtime, …
              siteName = "mysite.localhost";  # → FRAPPE_SITE (empty for multi-tenancy)
              workspaceRoot = ./.;
              python = pkgs.python312;
              nodejs = pkgs.nodejs_22;
              mariadb.initialDatabases = [ { name = "mysite_db"; } ];
              containers.enable = true;
            };
          };
      }
    );
}
```

Then:

```sh
direnv allow            # or: nix develop --no-pure-eval
devenv up               # start MariaDB, Redis, the Frappe runtime, watch, …
provision-site          # (first run, in another shell) create the site + install apps
# → http://localhost:<port>  (8000 plus a per-bench offset; the shell banner prints it)
```

> [`Avunu/frappe-devenv`](https://github.com/Avunu/frappe-devenv) is the reference consumer — a working frappe + erpnext + hrms bench wired up exactly as above.

## Flake outputs

| Output | Purpose |
| --- | --- |
| flakeModules.default | The flake-parts module — imports it and configure perSystem.frappe-nix. |
| nixosModules.default | Standalone NixOS module exposing services.frappe (multi-tenant production systemd). |
| lib.frappeSecrets | (in a consuming bench) The declared .age paths and recipients, so a deployment can read the same ciphertext — see [Secrets](#secrets). |
| lib.mkFlake | flake-parts.lib.mkFlake wrapper that merges frappe-nix's inputs into the consumer's. |
| lib.overrides | Composable Python package overrides for native deps (mysqlclient, pycups, python-ldap, pyvips, cairocffi). |

When `frappe-nix.enable` is set, the module adds these **packages** to your flake (`nix build .#<name>`):

| Package | What it is |
| --- | --- |
| default / builtBench | Production-ready bench: apps + python env + node + compiled assets. The deployable consumed by the NixOS module and OCI containers. |
| prodPythonEnv | Production virtualenv — workspace apps + runtime deps, no dev tools. |
| devPythonEnv | Development virtualenv — adds dev groups + editable installs of apps/*. |
| benchRoot | The unbuilt /bench tree (apps + node_modules + Python env + site/config). Used by the dev path and as input to builtBench. |

and one **app** (`nix run .#<name>`): `relock`, which runs `uv lock` from a `uv` that does not come from the workspace, so it still works when a stale `uv.lock` stops the dev shell from opening. In app mode it also writes `nix/uv.lock` (and `nix/node-locks/`) back into the repo. See [Dependencies and locks](docs/development/locks.md).

With `containers.enable = true` it additionally builds (named `<benchName>/<name>:latest`): `runtime`, `nginx`, `bench-cli` — or, with `runtime.enable = false`, `web`, `scheduler`, `worker-default`, `worker-short`, `worker-long`, `socketio`, `nginx`, `bench-cli`. See [Build production images](docs/production/images.md).

## The unified runtime

By default each bench runs a single [`frappe-runtime`](runtime) process — a hard fork maintained in this repository under `runtime/` — serving the web app, realtime, the background jobs and the scheduler together, in place of gunicorn (or `bench serve`), the Node `socket.io` server, one worker per queue, and `bench schedule`. See [The unified runtime](docs/production/runtime.md).

### Adding it to a bench

Nothing to do for a new bench: `frappe-init` writes the dependency into `pyproject.toml` from the template and locks it. Nothing to do for an existing bench either: the dev shell adds it on entry, and `enterShell` reconciles the workspace root and re-locks (see [Upgrading frappe-nix](docs/development/upgrading.md)). Commit `pyproject.toml` and `uv.lock`, re-enter the shell, and `devenv up` runs the runtime.

What lands is three things: `frappe-runtime` in `[project].dependencies`; a `[tool.uv.sources]` entry pointing at this repository's `runtime/` subdirectory; and `hatchling` in `[tool.uv.extra-build-dependencies]`. The declaration exists for uv's resolver and is a placeholder, not a version pin: frappe-nix points uv2nix's `srcOverrides` at its own `runtime/` directory, so a bump is `nix flake update frappe-nix` — no relock in any bench. A frappe-runtime release that adds a new dependency does need `nix run .#relock -- --upgrade-package frappe-runtime` once.

`runtime.enable = false` goes back to the split processes and the Node realtime server, in both the dev shell and `services.frappe`.

## Development shell

`devenv up` runs the full stack via process-compose. Several benches can run at once: everything that can be is on a unix socket, and the ports that remain are per-bench. See [The development shell](docs/development/README.md) for the processes, ports and `node_modules` handling, [Everyday commands](docs/development/commands.md) for the `bench` wrapper and the scripts, and [Memory and disk](docs/development/memory-and-disk.md) for how the stack stays light.

### Development guard rails

A bench restored from a production backup carries working production credentials. `frappe-nix.devguard` stops it from mailing customers, deleting objects from a production bucket, uploading backups and the like. Treat it as a large reduction in blast radius, not an airgap: only the mail guard is transport-level, and every other guard patches Frappe and app APIs. See [Development guard rails](docs/development/guard-rails.md).

## Secrets

A bench's credentials — the site encryption key, the database password, the object-store keys — live in `.age` files encrypted with [age](https://github.com/FiloSottile/age), committed to the repo, and decrypted into the dev shell by [agenix-shell](https://github.com/aciceri/agenix-shell). A consuming flake declares only the recipients and the sites:

```nix
frappe-nix.secrets = {
  dir = ./secrets;
  recipients = {
    alice = "ssh-ed25519 AAAAC3Nza…";
    bob   = "ssh-ed25519 AAAAC3Nza…";
  };
  hostRecipients.myserver = "ssh-ed25519 AAAAC3Nza…";
  sites."erp.example.com" = { };
};
```

There is no `secrets.nix`: the recipient list in `flake.nix` is the only place it is written down, and `check-secrets` proves the ciphertext agrees. With secrets declared, `setup-backup-access` and `bench restore` clone production into the dev shell, and `services.frappe` can read the same ciphertext through `lib.frappeSecrets`. See [Secrets](docs/development/secrets.md) and [Restore a production backup](docs/development/restore-from-production.md).

## Local apps

An app lives in a bench in one of two shapes: a **git submodule** (`bench-get-app` makes these, `bench-update --pull` moves them) or a **local app** with its source committed to the bench and no `.git` of its own (`bench-new-app`, or `frappe-init --migrate` vendoring an app with no usable remote). Both are workspace members and are registered in `sites/apps.txt` and `sites/apps.json` the same way. A nested repository that was `git add`ed with no `.gitmodules` entry is a third shape that nothing can use; `frappe-init --migrate` vendors it. See [Apps in a bench](docs/development/apps.md).

## Production

-   [Build production images](docs/production/images.md) — the immutable `builtBench` and the OCI images made from it.
-   [Run Frappe as a NixOS service](docs/production/nixos-service.md) — `services.frappe`: per-site units, secrets, MariaDB, Redis, nginx.
-   [Operate a deployed site](docs/production/operations.md) — creating or restoring a site, snapshot-protected migrations on deploy, runtime tuning.
-   [Logging](docs/production/logging.md) — everything goes to the journal, with `APP_SERVICE` and `APP_SITE` fields.

## Options

Every option, with its type and default, is in the reference: [`perSystem.frappe-nix`](docs/reference/dev-shell-options.md), [`frappe-nix.secrets`](docs/reference/secrets-options.md) and [`services.frappe`](docs/reference/nixos-options.md).

## Layout

The repository layout and the checks CI runs are described in [Repository layout](docs/reference/layout.md). In short: `lib/` holds the Nix and shell building blocks (`lib/sh/*.sh` is concatenated into the `frappe-init` scaffolder), `modules/` the flake-parts and NixOS modules, `templates/` what a new bench or app repository is laid down from, `runtime/` the unified runtime, `tests/` the flake checks, `docs/` the documentation pages, and `docs-site/` the project that builds them into the documentation site.

## License

frappe-nix is released under the [MIT License](LICENSE). Copyright (c) 2026 Avunu LLC.
