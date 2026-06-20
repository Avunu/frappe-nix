# frappe-nix

Reusable Nix infrastructure for [Frappe](https://frappeframework.com/) bench projects.

`frappe-nix` packages everything needed to develop and ship a Frappe/ERPNext bench
declaratively, so a consuming project's flake stays a thin wrapper instead of a
1000-line monolith. From a single `uv` workspace + `apps/` tree it provides:

- a **devenv** development shell (MariaDB, Redis, web/scheduler/worker/socketio/watch,
  Mailpit) with editable Python installs and live asset reloading;
- reproducible **production Python environments** (via [uv2nix](https://github.com/pyproject-nix/uv2nix));
- reproducible **node_modules** from `yarn.lock` (yarn-v1 hooks);
- a `benchRoot` derivation that assembles the whole `/bench` tree;
- eight **OCI container images** (web, scheduler, three workers, socketio, nginx, bench-cli);
- a **NixOS module** (`services.frappe`) with systemd units for production deployment;
- a set of portable **bench scripts** (`provision-site`, `bench-update`, `bench-get-app`, …).

It is consumed as a [flake-parts](https://flake.parts/) module.

## Requirements

`frappe-nix` expects a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/)
laid out the way a Frappe bench is:

```
.
├── flake.nix                 # your thin wrapper (see Quick start)
├── pyproject.toml            # [tool.uv.workspace] members = apps/*, [tool.uv.sources]
├── uv.lock                   # committed lock — drives the Nix Python env
├── apps/                     # Frappe apps (typically git submodules)
│   ├── frappe/
│   ├── erpnext/
│   └── …                     # each with pyproject.toml; yarn.lock if it has assets
└── sites/
    ├── apps.txt              # apps installed into the site
    └── apps.json             # (optional) app metadata for the bench
```

## Create a new bench

`nix run github:Avunu/frappe-nix` scaffolds a fresh bench — the frappe-nix equivalent of
`bench init`. It selects a frappe version (which fixes the python/node versions from a
preset) and an optional set of apps, then writes the wrapper flake, adds `frappe` + the apps
as git submodules pinned to that version's branch, and runs `uv lock`:

```sh
nix run github:Avunu/frappe-nix                 # interactive (gum prompts)
# or fully non-interactive:
nix run github:Avunu/frappe-nix -- \
  --frappe-version version-15 --apps erpnext,hrms --name mybench mybench
cd mybench && direnv allow && devenv up         # then `provision-site` in another shell
```

Presets (curated in `lib/frappe-presets.json`, from frappe's `requires-python` / `engines`):

| Preset | python | node | app branch |
| --- | --- | --- | --- |
| `develop` | python314 | nodejs_24 | `develop` |
| `version-16` | python314 | nodejs_24 | `version-16` |
| `version-15` | python312 | nodejs_20 | `version-15` |

Apps follow the chosen version's branch when it exists (auto-detected via `git ls-remote`),
else the repo default. Flags: `--frappe-version`, `--apps` (names → `frappe/<name>`, or
`owner/repo`, or full git URLs), `--name`, `--site`, and a positional target dir. Bump the
presets file as frappe's requirements move; the python/node defaults are overridable in the
generated `flake.nix`.

## Quick start

A complete consuming flake is just a configured module. Because `frappe-nix.lib.mkFlake`
merges frappe-nix's own inputs (nixpkgs, devenv, uv2nix, …) into yours, you don't
re-declare them:

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
              benchName = "mybench";          # container image prefix: mybench/web, …
              siteName = "mysite.localhost";  # → FRAPPE_SITE (empty for multi-tenancy)
              workspaceRoot = ./.;
              python = pkgs.python312;
              nodejs = pkgs.nodejs_22;
              mariadb.initialDatabases = [ { name = "mysite_db"; } ];
              containers.enable = true;

              # fetchYarnDeps offline-cache hashes, one per app with a yarn.lock.
              # Leave an app out, run `nix build .#benchRoot`, and copy the
              # reported `got: sha256-…` value here.
              nodeOfflineHashes = {
                frappe = "sha256-…";
                erpnext = "sha256-…";
              };
            };
          };
      }
    );
}
```

Then:

```sh
direnv allow            # or: nix develop --no-pure-eval
devenv up               # start MariaDB, Redis, web, worker, scheduler, socketio, …
provision-site          # (first run, in another shell) create the site + install apps
# → http://localhost:8000
```

> [`Avunu/frappe-devenv`](https://github.com/Avunu/frappe-devenv) is the reference
> consumer — a working frappe + erpnext + hrms bench wired up exactly as above.

## Flake outputs

| Output | Purpose |
| --- | --- |
| `flakeModules.default` | The flake-parts module — `imports` it and configure `perSystem.frappe-nix`. |
| `nixosModules.default` | Standalone NixOS module exposing `services.frappe` (production systemd). |
| `lib.mkFlake` | `flake-parts.lib.mkFlake` wrapper that merges frappe-nix's inputs into the consumer's. |
| `lib.overrides` | Composable Python package overrides for native deps (`mysqlclient`, `pycups`, `python-ldap`, `cairocffi`). |

When `frappe-nix.enable` is set, the module adds these **packages** to your flake
(`nix build .#<name>`):

| Package | What it is |
| --- | --- |
| `prodPythonEnv` | Production virtualenv — workspace apps + runtime deps, no dev tools. |
| `devPythonEnv` | Development virtualenv — adds dev groups + editable installs of `apps/*`. |
| `benchRoot` | The assembled `/bench` tree (apps + node_modules + Python env + site/config). |

With `containers.enable = true` it additionally builds (named `<benchName>/<name>:latest`):
`web`, `scheduler`, `worker-default`, `worker-short`, `worker-long`, `socketio`,
`nginx`, `bench-cli`.

## Options — `perSystem.frappe-nix`

| Option | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Enable the dev shell + packages. |
| `benchName` | str | *(required)* | Identifier for env names and container image prefix. |
| `siteName` | str | `""` | `FRAPPE_SITE`. Empty = multi-tenancy (set per-shell via `.env`). |
| `workspaceRoot` | path | *(required)* | Bench root (where `pyproject.toml` + `apps/` live). Usually `./.`. |
| `python` | package | `pkgs.python312` | Python interpreter. |
| `nodejs` | package | `pkgs.nodejs_22` | Node.js for frontend builds + socketio. |
| `mariadb.package` | package | `pkgs.mariadb` | MariaDB package. |
| `mariadb.initialDatabases` | list of `{ name }` | `[]` | Databases created on first `devenv up`. |
| `nodeOfflineHashes` | attrs of str | `{}` | Per-app `fetchYarnDeps` hash overrides (see [Node offline hashes](#node-offline-hashes); normally generated into `node-offline-hashes.json`). |
| `nodeOverrides` | attrs of attrs | `{}` | Per-app attrs merged into the node_modules `stdenv.mkDerivation`. |
| `pythonOverrides` | overlay | no-op | Extra Python package set overlay (compose with `lib.overrides`). |
| `extraDevPackages` | list of package | `[]` | Extra packages on the dev shell. |
| `extraContainerRuntimeDeps` | list of package | `[]` | Extra runtime packages in production containers. |
| `extraLibraryPaths` | list of package | `[]` | Extra `LD_LIBRARY_PATH` entries (dev shell). |
| `extraScripts` | attrs | `{}` | Extra devenv scripts, merged over the standard set. |
| `extraEnv` | attrs of str | `{}` | Extra environment variables (dev shell). |
| `containers.enable` | bool | `false` | Build the OCI images. |
| `containers.registry` | str | `""` | Registry URL prefix. |

## Development shell

`devenv up` runs the full stack via process-compose:

| Service / process | Port |
| --- | --- |
| MariaDB | 3306 |
| Redis (cache / queue / socketio) | 13000 |
| `web` (`bench serve`) | 8000 |
| `socketio` (Node) | 9000 |
| `watch` (asset file watcher) | 6787 |
| Mailpit (SMTP / HTTP) | 1025 / 8025 |
| `scheduler`, `worker` | — |

`apps/*` are installed as **editable** packages (uv2nix editable overlay), so source
edits hot-reload. `uv` and `yarn` write to mutable state dirs (`$DEVENV_STATE`) so
`uv add` / `yarn add` work despite the read-only Nix store; the resulting `uv.lock` /
`yarn.lock` are then consumed declaratively for production builds.

### `bench` is transparent

The shell ships an umbrella **`bench` wrapper** that shadows the venv's `bench` (devenv
wraps scripts with `lib.hiPrioSet`, so it wins on PATH) and transparently redirects the
subcommands that need frappe-nix handling — so you just run normal `bench` commands:

| You run | Redirected to | Why |
| --- | --- | --- |
| `bench update …` | `bench-update` | vanilla update pip-installs / assumes `upstream` remotes |
| `bench get-app <url\|alias>` | `bench-get-app` | git submodule + uv workspace instead of pip |
| `bench new-app <name>` | `bench-new-app` | scaffold + uv workspace (skips the failing pip step) |
| `bench restore <sql>` | `bench-restore` | injects the MariaDB root credentials |
| `bench new-site <site>` | real bench + injected `--db-socket`/`--db-root-username root` | non-interactive site creation |
| `bench migrate` / `console` / `clear-cache` | `bench-*` | inject `--site $FRAPPE_SITE` |
| everything else (`build`, `serve`, `install-app`, `--help`, …) | the real `bench` | unchanged |

Recursion is avoided with a `_FRAPPE_BENCH_RAW` env guard the specialized scripts export and
the wrapper checks, so a script's own nested `bench …` calls reach the real CLI — whether you
invoke `bench update` or the underlying `bench-update` directly. Two caveats: redirected
commands follow the frappe-nix scripts' flags, not vanilla bench's (e.g. `bench update`
takes `--pull|--migrate|--build|--node-hashes`, not `--reset`); and interception is
subcommand-first, so `bench --site X migrate` (global option before the subcommand) passes
straight through.

### Bench scripts

These back the wrapper and are also callable directly:

| Script | Description |
| --- | --- |
| `provision-site [admin-pass]` | Create `$FRAPPE_SITE` and install every app from `sites/apps.txt`. |
| `bench-update [--pull\|--migrate\|--build\|--node-hashes]` | Submodule-aware replacement for `bench update`; also refreshes `node-offline-hashes.json` for changed apps. |
| `bench-migrate` / `bench-build` / `bench-clear-cache` / `bench-console` | Thin `bench` wrappers honoring `$FRAPPE_SITE`. |
| `bench-restore <sql> [opts]` | Restore the site from a SQL backup. |
| `bench-get-app <url\|alias>` | Add an app as a git submodule + register it in the uv workspace. `helpdesk` → `frappe/helpdesk`; `owner/repo` and full URLs also work. |
| `bench-new-app <name>` | Scaffold a new app and register it in the workspace. |
| `update-deps` | Re-lock + sync Python (uv) and Node (yarn) across all apps. |

## Production containers

```sh
nix build .#web          # → result is a Docker image tarball
docker load < result     # loads <benchName>/web:latest
```

The images are built from `benchRoot` (declarative apps + node_modules + `prodPythonEnv`),
with no imperative `uv sync` / `yarn install` at container start. `web` runs gunicorn on
`:8000`, `nginx` reverse-proxies on `:80` reading `/bench/config/nginx.conf`, `socketio`
runs on `:9000`, and `bench-cli` is for migrations / one-off commands.

## NixOS module — `services.frappe`

`nixosModules.default` is a standalone NixOS module (not flake-parts). It takes the
`benchRoot` and `prodPythonEnv` packages your flake already builds and runs them as
systemd units, so it stays pure-nixpkgs with no uv2nix dependency.

```nix
# In a nixosConfiguration:
{
  imports = [ frappe-nix.nixosModules.default ];

  services.frappe = {
    enable = true;
    benchRoot = self.packages.x86_64-linux.benchRoot;
    pythonEnv = self.packages.x86_64-linux.prodPythonEnv;
    defaultSite = "mysite.localhost";
    nginx.enable = true;
    redis.createLocally = true;
    database.createLocally = true;
  };
}
```

It creates a `frappe` user, a `frappe-init` oneshot that seeds a writable
`/var/lib/frappe/sites`, and units for `frappe-web` (gunicorn), `frappe-scheduler`,
`frappe-worker-{default,short,long}`, and `frappe-socketio`. Optional toggles bring up a
local MariaDB / Redis and an nginx reverse-proxy vhost.

Key options: `web.{port,workers}`, `socketio.port`, `workers` (queue list),
`database.{createLocally,host,port,socket}`, `redis.{createLocally,port,cacheUrl,queueUrl,socketioUrl}`,
`nginx.enable`, `user`/`group`, `extraEnv`. Site creation/migration and asset builds
remain operational steps (run `bench` against the deployed bench).

## Library

### `lib.mkFlake`

```nix
frappe-nix.lib.mkFlake { inherit inputs; } flakeConfig
```

Calls `flake-parts.lib.mkFlake` with `inputs = frappe-nix.inputs // yourInputs`, so the
modules resolve `nixpkgs`, `devenv`, `pyproject-nix`, `uv2nix`,
`pyproject-build-systems` and `nix2container` from frappe-nix's pins. Your wrapper only
needs to declare `frappe-nix` (and `nixpkgs.follows` for the perSystem `pkgs`).

### `lib.overrides`

Composable overlays for Python packages needing native libraries. `mysqlclient` is wired
in automatically from `mariadb.package`; add others via `pythonOverrides`:

```nix
pythonOverrides = lib.composeManyExtensions [
  (frappe-nix.lib.overrides.pycups { inherit pkgs; })
  (frappe-nix.lib.overrides.python-ldap { inherit pkgs; })
];
```

Pure-Python build deps (setuptools, etc.) belong in `pyproject.toml`
`[tool.uv.extra-build-dependencies]` so uv2nix handles them — these overlays are only for
packages that need C headers/system libraries.

## The dev → prod contract

| Developer (imperative)        | Nix build (declarative)            |
| ----------------------------- | ---------------------------------- |
| `uv add` / `uv sync`          | uv2nix reads `uv.lock`             |
| `yarn add` / `yarn install`   | `fetchYarnDeps` reads `yarn.lock`  |
| edits `apps/*` source         | `benchRoot` copies the source tree |

Commit `uv.lock` and each app's `yarn.lock`; the production env, node_modules, containers,
and NixOS deployment are all rebuilt from them.

### Node offline hashes

Because the node_modules build went off the (removed) `mkYarnPackage` to the yarn-v1 hooks,
each app's offline cache is a fixed-output derivation whose hash depends on its `yarn.lock`.
These hashes live in a committed **`node-offline-hashes.json`** at the workspace root
(`{ "<app>": "sha256-…" }`), which `bench.nix` reads automatically.

You don't manage that file by hand — **`bench-update` keeps it current**:

- `bench-update` / `bench-update --pull` regenerates the hash for any app whose `yarn.lock`
  changed during the pull (or is missing from the file);
- `bench-update --node-hashes` force-regenerates every app's hash.

(`bench-get-app` also adds new apps; run `bench-update --node-hashes` afterwards, or it will
be picked up on the next pull.) The `nodeOfflineHashes` option still exists as a manual
override for individual apps and takes precedence over the file.

## Layout

```
frappe-nix/
├── flake.nix                 # flakeModules / nixosModules / lib outputs
├── lib/
│   ├── python.nix            # mkPythonEnvs — prod + editable-dev virtualenvs (uv2nix)
│   ├── bench.nix             # app discovery, node_modules (yarn hooks), benchRoot
│   ├── overrides.nix         # mysqlclient / pycups / python-ldap / cairocffi
│   └── scripts.nix           # portable bench shell scripts
└── modules/
    ├── flake-module.nix      # imports devenv.flakeModule + devenv.nix + containers.nix
    ├── devenv.nix            # perSystem.frappe-nix options + dev shell + packages
    ├── containers.nix        # OCI image builds
    └── nixos.nix             # services.frappe (NixOS systemd module)
```
