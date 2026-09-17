# frappe-nix

Reusable Nix infrastructure for [Frappe](https://frappeframework.com/) bench projects.

`frappe-nix` packages everything needed to develop and ship a Frappe/ERPNext bench declaratively, so a consuming project's flake stays a thin wrapper instead of a 1000-line monolith. From a single `uv` workspace + `apps/` tree it provides:

-   a **devenv** development shell (MariaDB, Redis, the Frappe runtime, watch, Mailpit) with editable Python installs, live asset reloading, and a [guard rails](#development-guard-rails) so no bench can mail customers, overwrite a production bucket or upload a backup;
-   reproducible **production Python environments** (via [uv2nix](https://github.com/pyproject-nix/uv2nix));
-   reproducible **node\_modules** for every app and nested frontend, straight from each one's own `yarn.lock` — nothing committed to the bench, no hash ever computed;
-   a `benchRoot` derivation that assembles the whole `/bench` tree;
-   a **`builtBench`** package that runs `bench build` at build time (immutable assets) — the production-ready deployable consumed by both the NixOS module and OCI containers;
-   **OCI container images** — `runtime`, `nginx`, `bench-cli`, or the eight-image split set (web, scheduler, three workers, socketio, nginx, bench-cli) with `runtime.enable = false`;
-   a multi-tenant **NixOS module** (`services.frappe`) with per-site systemd units;
-   a set of portable **bench scripts** (`provision-site`, `bench-update`, `bench-get-app`, …).

It is consumed as a [flake-parts](https://flake.parts/) module, from a bench repository or — see [Develop a single app](#develop-a-single-app) — from one Frappe app's own repository, where the bench around it is generated from flake inputs instead of committed.

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

In [app mode](#develop-a-single-app) frappe-nix builds that layout itself, and the repository is a Frappe app instead:

```
.
├── flake.nix                 # your thin wrapper
├── pyproject.toml            # the APP's own — frappe-nix never touches it
├── <app_name>/hooks.py       # what makes this directory a Frappe app
└── nix/
    ├── uv.lock                     # committed lock — drives the Nix Python env
    └── node-locks/                 # fallback yarn.lock for pins that ship none — commit it
```

## Create a new bench

`nix run github:Avunu/frappe-nix` scaffolds a fresh bench — the frappe-nix equivalent of `bench init`. It selects a frappe version (which fixes the python/node versions from a preset) and an optional set of apps, then writes the wrapper flake, adds `frappe` + the apps as git submodules pinned to that version's branch, and runs `uv lock`.

Run in an **existing bench** directory, the same command detects it and [migrates it in place](#migrate-an-existing-bench) instead.

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
| develop | python314 | nodejs_24 | develop |
| version-16 | python314 | nodejs_24 | version-16 |
| version-15 | python312 | nodejs_20 | version-15 |

Apps follow the chosen version's branch when it exists (auto-detected via `git ls-remote`), else the repo default. Flags: `--frappe-version`, `--apps` (names → `frappe/<name>`, or `owner/repo`, or full git URLs), `--name`, `--site`, and a positional target dir. Bump the presets file as frappe's requirements move; the python/node defaults are overridable in the generated `flake.nix`.

## Develop a single app

A bench repository _is_ the uv workspace: a committed `pyproject.toml`, a committed `uv.lock`, `apps/*` as git submodules. An app repository has none of that — it is one app, and the bench around it is an implementation detail of developing it. **App mode** inverts the relationship: the app repo commits a small `flake.nix`, and frappe-nix assembles the bench from flake inputs.

```sh
cd ~/Development/carbon_frappe          # an existing Frappe app, in git
nix run github:Avunu/frappe-nix         # detects an app repo and sets up app mode
direnv allow                            # or: nix develop --no-pure-eval
devenv up                               # then `provision-site` in another shell
```

The scaffolder writes three files — `flake.nix`, `.envrc` and a managed `.gitignore` block — then runs `nix run .#relock` to produce `nix/uv.lock`. It never touches the app's own `pyproject.toml`: that file is the app's packaging metadata, and the workspace root frappe-nix generates is a different file that lives in the Nix store.

The flake it writes:

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

Everything else is inferred: `app.src` from `self`, `app.name` from the repo's `[project].name`, `benchName` from that normalized, `python`/`nodejs` from the `frappeVersion` preset.

### What you get, and where it lives

|  | Bench mode | App mode |
| --- | --- | --- |
| the uv workspace | the repo | a derivation assembled from the flake inputs |
| apps/* | git submodules | flake inputs, pinned by flake.lock |
| the app under development | one of the submodules | this repo, symlinked into the bench so edits are live |
| the bench you run | the repo | .frappe-nix/bench/, generated on shell entry, gitignored |
| what is committed | pyproject.toml, uv.lock, sites/ (node-locks/ only for apps without a yarn.lock) | flake.nix, nix/uv.lock (nix/node-locks/ likewise) |

`FRAPPE_BENCH_ROOT`, `SITES_PATH` and `PYTHONPATH` name the generated bench; `REPO_ROOT` stays the git worktree, which is where `secrets/*.age` live. devenv's own root is deliberately not moved, so `$DEVENV_STATE` — and with it the MariaDB datadir — stays outside the generated tree: `rm -rf .frappe-nix` costs a re-copy of the apps, not the database.

`FRAPPE_PATH` is exported (`$FRAPPE_BENCH_ROOT/apps/frappe`) because a Frappe app's build scripts conventionally look for the framework at `<app>/../frappe`, and in app mode that sibling lookup does not work: `apps/<app>` is a symlink and Node realpaths `__dirname`. An app that wants a _different_ sibling by path needs `extraEnv`:

```nix
frappe-nix.extraEnv.ERPNEXT_PATH = "…";   # see `extraEnv` in the options table
```

### Moving the pins

There are no submodules to pull, so `bench-update --pull` and `bench-get-app` are replaced by the flake-input equivalents:

```sh
nix flake update frappe      # or `nix flake update` for all pins
nix run .#relock             # re-resolve, rewriting nix/uv.lock (+ nix/node-locks/ for pins without a yarn.lock)
```

`relock` is deliberately reachable without a dev shell, because a missing or stale `uv.lock` fails at _evaluation_ — the shell that carries `uv` is exactly what refuses to open. It stages both files for you: a flake's source tree is only its tracked files, so an untracked lock is invisible to the build and reads as still missing.

Re-entering the shell after a pin moves re-copies `apps/*` out of the store and tells you the compiled assets are stale. Each app's `node_modules` is carried across, so a bump does not cost a full `yarn install` per app. Edits made _inside_ `.frappe-nix/bench/apps/frappe` are in a copy, and the next bump discards them.

### Production parity

App mode is not dev-only. `nix build` produces the same `builtBench` a bench does — frappe, the siblings and this app, with `bench build` run over all of them — so it is a real check that the app compiles in a clean bench:

```sh
nix build                    # → result/bench, assets compiled
```

`containers.enable` and the [NixOS module](#nixos-module--servicesfrappe) work unchanged.

## Migrate an existing bench

The same entry point converts a classic `bench init` bench — or a half-converted one — into a frappe-nix repo. It **detects the mode from the target directory**, so from inside a bench:

```sh
cd ~/frappe-bench
nix run github:Avunu/frappe-nix -- --dry-run    # inspect the plan first
nix run github:Avunu/frappe-nix                 # then migrate
```

It is a **reconciler, not a converter**: it probes what the bench already has, adds only what is missing, repairs drift, and never deletes. Running it on an already-migrated bench is a no-op. Concretely it:

-   detects the frappe version (branch → the bench's `sites/apps.json` → `frappe.__version__`) and pins python/node from the matching preset;
-   `git init`s the bench root if needed and registers each app under `apps/` as a **git submodule pinned at its current commit** — nothing is fast-forwarded — recording the app's _actual_ branch in `.gitmodules` (without which `bench-update --pull` silently skips it) and adding an `origin` alias when the app only has `upstream`;
-   **vendors** apps with no usable remote: the nested `.git` moves to `.frappe-nix-backup/` (with a provenance JSON) and the source is committed into the bench, because an untracked nested repo is invisible to the flake and would vanish from the build;
-   writes `flake.nix`, `pyproject.toml`, `.envrc` and the `uv.lock`, merging into an existing `pyproject.toml` rather than replacing it, and shimming a `pyproject.toml` for vendored apps that only ship `setup.py`;
-   regenerates `sites/apps.txt` and `sites/apps.json` from the workspace members it just registered (see [App registry](#app-registry)) — an app that could not become a member is on PYTHONPATH but not registered, and the report says so;
-   reconciles `sites/common_site_config.json` — forcing the per-bench web/socketio port the dev shell derives from the bench name, preserving everything else, dropping production-only keys (`host_name`, `http_port`, `restart_*`) and keys the socket setup supersedes (`db_host`, `db_port`, the `redis_*` URLs, `file_watcher_port`), and blanking `mariadb_root_password` since the file is about to be committed;
-   extends `.gitignore` with a managed block so `sites/*/site_config.json`, site `private/`/`public/` data, `Procfile`, `patches.txt`, `config/*.conf` and `node_modules` stay out of git — then **verifies** with `git check-ignore` that nothing the build needs got excluded;
-   moves a classic `env/` virtualenv to `.frappe-nix-backup/` (a real `env/` directory silently defeats the dev shell's `ln -sfn` and leaves `bench` on the stale interpreter).

It also **reports a tracked `sites/*/site_config.json`** — the file holding the site's encryption key, database password and object-storage credentials. The managed `.gitignore` block excludes that path, but git keeps honouring an index entry regardless, so adding the rule changes nothing until the file is untracked. The migrator never deletes, so it prints the `git rm --cached` and leaves the decision (and the rotation the disclosure implies) to you.

Nothing is committed — the result is staged, so `git diff --cached` is the review. Add `--commit` to commit it.

| Flag | Effect |
| --- | --- |
| --dry-run | Print the full plan (per-app disposition, warnings) and exit |
| -y, --yes | Skip the confirmation; required to migrate in a non-TTY |
| --frappe-version <v> | Override version detection |
| --migrate / --init | Force the mode instead of detecting it |
| --vendor <a,b> | Vendor these apps even though they have a remote |
| --no-vendor | Abort instead of vendoring an app with no usable remote |
| --allow-file-remotes | Accept filesystem paths as submodule URLs (breaks other clones) |
| --legacy-apps <policy> | shim \| skip \| abort for apps with only a setup.py. A skipped app is not a workspace member and so not in sites/apps.txt |
| --strict | Treat dirty / unpushed apps as errors |
| --keep-db-root-password | Do not blank mariadb_root_password |
| --commit[=<msg>] | Commit the migration instead of only staging it |

Afterwards, if any app ships a `package.json` without a `yarn.lock`, run `bench-update --node-locks` inside the dev shell before the first `nix build` — the fallback lock is not generated by the migration (it needs the network and yarn). Evaluation tells you which apps, if any.

**What it cannot fix.** An app pinned at a commit that is on no remote branch builds on your machine and nowhere else; an app whose remote is unreachable will fail on a fresh clone of the bench. Both are reported as warnings, and `--strict` turns the first into an error. A `setup.py`\-only app that is a _submodule_ cannot be shimmed — a generated `pyproject.toml` would sit outside the pinned commit — so vendor it (`--vendor <app>`) or fix it upstream.

## Quick start

A complete consuming flake is just a configured module. Because `frappe-nix.lib.mkFlake` merges frappe-nix's own inputs (nixpkgs, devenv, uv2nix, …) into yours, you don't re-declare them.

This is the **bench** shape — a repository that holds `apps/*`. For a single app's own repository, see [Develop a single app](#develop-a-single-app); the module is the same, but `workspaceRoot` is replaced by `app.*` and frappe-nix builds the workspace itself.

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
# → http://localhost:8000
```

> [`Avunu/frappe-devenv`](https://github.com/Avunu/frappe-devenv) is the reference consumer — a working frappe + erpnext + hrms bench wired up exactly as above.

## Flake outputs

| Output | Purpose |
| --- | --- |
| flakeModules.default | The flake-parts module — imports it and configure perSystem.frappe-nix. |
| nixosModules.default | Standalone NixOS module exposing services.frappe (multi-tenant production systemd). |
| lib.frappeSecrets | (in a consuming bench) The declared .age paths and recipients, so a deployment can read the same ciphertext — see Secrets. |
| lib.mkFlake | flake-parts.lib.mkFlake wrapper that merges frappe-nix's inputs into the consumer's. |
| lib.overrides | Composable Python package overrides for native deps (mysqlclient, pycups, python-ldap, cairocffi). |

When `frappe-nix.enable` is set, the module adds these **packages** to your flake (`nix build .#<name>`):

| Package | What it is |
| --- | --- |
| default / builtBench | Production-ready bench: apps + python env + node + compiled assets. The deployable consumed by the NixOS module and OCI containers. |
| prodPythonEnv | Production virtualenv — workspace apps + runtime deps, no dev tools. |
| devPythonEnv | Development virtualenv — adds dev groups + editable installs of apps/*. |
| benchRoot | The unbuilt /bench tree (apps + node_modules + Python env + site/config). Used by the dev path and as input to builtBench. |

and one **app** (`nix run .#<name>`):

| App | What it does |
| --- | --- |
| relock | uv lock in the bench root, from a uv that does not come from the workspace. See stale uv.lock — it exists for the case where the shell that carries uv is what refuses to open. In app mode it assembles the workspace itself and writes nix/uv.lock (and nix/node-locks/, for pins without a yarn.lock) back into the repo; --uv-only and --node-locks [target…] do one half each. |

The `builtBench` package exposes `passthru.{pythonEnv, nodejs, appsPath, appNames}` so the NixOS module and containers can discover interpreters from the package itself — no separate `pythonEnv`/`nodejs` options needed.

With `containers.enable = true` it additionally builds (named `<benchName>/<name>:latest`): `runtime`, `nginx`, `bench-cli` — or, with `runtime.enable = false`, `web`, `scheduler`, `worker-default`, `worker-short`, `worker-long`, `socketio`, `nginx`, `bench-cli`.

## The unified runtime

By default each bench runs a single [`frappe-runtime`](runtime/) process — a hard
fork maintained in this repository under `runtime/` — serving the web app, realtime,
the background jobs and the scheduler together, in place of gunicorn (or
`bench serve`), the Node `socket.io` server, one worker per queue, and
`bench schedule`. It began as upstream Frappe's own asyncio/uvicorn port, made to
run against a released Frappe and fixed where the upstream runner did not work
(see [`runtime/docs/upstream-issues/`](runtime/docs/upstream-issues/)).

Two things follow from it beyond the process count. Node leaves the runtime
closure entirely — it stays a build-time dependency for `bench build`. And nginx
loses its loopback `:80` listener along with the `networking.hosts` pin that
resolved each site's FQDN to `127.0.0.1`: those existed only because the Node
realtime server validated sessions by making an HTTP request back to the site's
own name, and node's `fetch` cannot speak a unix socket. The Python runtime
validates in-process against the WSGI app.

### Adding it to a bench

Nothing to do for a new bench: `frappe-init` writes the dependency into
`pyproject.toml` from the template and locks it.

Nothing to do for an existing bench either: the dev shell adds it on entry. A
bench from before the runtime evaluates and opens as it always did — running the
split processes for that one session — and `enterShell` reconciles the
workspace root and re-locks (see [Upgrading frappe-nix](#upgrading-frappe-nix)).
Commit `pyproject.toml` and `uv.lock`, re-enter the shell, and `devenv up` runs
the runtime.

What lands is three things, the same way `frappe-init` lands `frappe-bench` and
`setuptools`: `frappe-runtime` in `[project].dependencies`; a
`[tool.uv.sources]` entry pointing at this repository's `runtime/` subdirectory;
and `hatchling` in `[tool.uv.extra-build-dependencies]`, because uv builds
without isolation here and the package's own `build-system.requires` is not
enough. The reconciler (`nix run github:Avunu/frappe-nix -- -y`) does the same
and remains the way to do it from outside a shell.

The declaration exists for uv's resolver and is a placeholder, not a version pin.
frappe-nix points uv2nix's `srcOverrides` at its own `runtime/` directory, so the
code actually built is whatever this repository ships and a bump is
`nix flake update frappe-nix` — no relock in any bench. The entry has to exist
because uv2nix indexes its package set by `uv.lock`, and `srcOverrides` can only
swap the source of a package already in that set.

The seam: only the *source* is overridden, and dependency metadata still comes from
`uv.lock`. A frappe-runtime release that adds a new dependency does need
`nix run .#relock -- --upgrade-package frappe-runtime` once. Note the flag — a plain
`uv lock` keeps an already-resolved git revision and will report success without
changing anything.

Set `runtime.src = null` to hand version control back to `uv.lock`.

### Going back

`runtime.enable = false` restores the split processes and the Node realtime
server, in both the dev shell and `services.frappe`. Everything that shape needs —
`socketio.socketPath`, `socketio.port`, `web.workers`, the loopback listener, the
per-image container set — is still there and still tested
(`checks.socket` covers it; `checks.socket-runtime` covers the unified one).

## Options — `perSystem.frappe-nix`

| Option | Type | Default | Notes |
| --- | --- | --- | --- |
| enable | bool | false | Enable the dev shell + packages. |
| benchName | str | (required; the normalized app.name in app mode) | Identifier for env names and container image prefix. |
| siteName | str | "" | FRAPPE_SITE. Empty = multi-tenancy (set per-shell via .env). |
| workspaceRoot | path or null | null | Bench root (where pyproject.toml + apps/ live). Usually ./.. Required in bench mode; must stay null in app mode, where frappe-nix assembles the workspace itself. |
| app.enable | bool | false | App mode: this flake is one Frappe app's repository, not a bench. |
| app.frappe | path | (required in app mode) | The Frappe source, as a flake = false input. |
| app.siblings | list of { name; src; } | [] | The other apps the bench should carry, in install order — a list, not an attrset, because that order is the members' order and so sites/apps.txt's. |
| app.frappeVersion | preset name | "version-16" | Row of lib/frappe-presets.json driving python, nodejs, requires-python and override-dependencies. Does not pin frappe; app.frappe does. |
| app.src | path | inputs.self | The app's own source, which becomes apps/<name>. Do not filter it — app.lockDir is read out of it. |
| app.name | str | [project].name of app.src | The app's directory name under apps/, i.e. Frappe's own app name. |
| app.lockDir | str | "nix" | Where the generated-but-committed uv.lock (and node-locks/, for pins without a yarn.lock) live, relative to the repo root. |
| app.benchDir | str | ".frappe-nix/bench" | Where the dev shell materialises the writable bench, relative to the repo root. Gitignore it. |
| python | package | pkgs.python312 | Python interpreter. In app mode, the app.frappeVersion preset's. |
| nodejs | package | pkgs.nodejs_22 | Node.js for frontend builds + socketio. In app mode, the app.frappeVersion preset's. |
| mariadb.package | package | pkgs.mariadb | MariaDB package. |
| mariadb.initialDatabases | list of { name } | [] | Databases created on first devenv up. |
| nodeOverrides | attrs of attrs | {} | Per node target (app, or "app/subdir"): extra attributes for the derivation that runs its yarn install --offline — postPatch, nativeBuildInputs, a yarnOfflineCache of your own. See Node lockfiles. |
| nodeNestedFrontendExcludes | list of str | [] | Nested frontends ("app/subdir") to leave out: no node_modules, no assets, and the parent's build script that drives them is dropped. See Node lockfiles. |
| pythonOverrides | overlay | no-op | Extra Python package set overlay (compose with lib.overrides). |
| extraDevPackages | list of package | [] | Extra packages on the dev shell. |
| extraContainerRuntimeDeps | list of package | [] | Extra runtime packages in production containers. |
| extraPackages | list of package | [] | Extra packages installed in both the dev shell and any production deployment of this package (read off builtBench's passthru.extraPackages by services.frappe's NixOS module — no server-side config needed). |
| extraLibraryPaths | list of package | [] | Extra LD_LIBRARY_PATH entries (dev shell). |
| extraScripts | attrs | {} | Extra devenv scripts, merged over the standard set. |
| extraEnv | attrs of str | {} | Extra environment variables (dev shell). |
| runtime.enable | bool | true | Run one `frappe-runtime` process (web + realtime + jobs + scheduler) instead of the split web/socketio/worker/scheduler processes. |
| runtime.jobThreads | int | 2 | Concurrent background jobs inside the runtime process. |
| runtime.dev | bool | true | Pass `--dev`: reload on a Python source change, and serve /assets and /files from the runtime. |
| runtime.src | null or path | the `frappe-runtime` flake input | Source frappe-runtime is built from, overriding the revision uv.lock resolved. `null` defers to uv.lock. |
| sockets.enable | bool | true | Put MariaDB, Redis, socketio and the web server on unix sockets behind one nginx port, so several benches can run at once. Needs frappe ≥ 15.46. |
| ports.base | port or null | null | First port this bench tries; defaults to 8000 + a hash of benchName. |
| appsReconcile.enable | bool | siteName != "" | Install whatever sites/apps.txt names that siteName's site doesn't have installed yet, on every devenv up. See Installed-app drift. |
| assets.reassert.hooks | list of str | [] | bench execute targets run when sites/assets/assets.json names a bundle file that doesn't exist on disk. Empty by default — names no app. See Asset-shadow staleness. |
| assets.reassert.debounceMs | int | 750 | How long assets.json must sit unmodified before the bench-watch-driven check re-reads it. |
| devguard.enable | bool | true | Master switch for all guard rails — see Development guard rails. |
| devguard.mail.enable | bool | true | Route all outgoing mail to Mailpit, refuse IMAP/POP3. |
| devguard.mail.host | str | "127.0.0.1" | Interface Mailpit binds and Frappe is redirected to. |
| devguard.mail.smtpPort | port | 19000 + hash | Catcher SMTP port (per-bench). |
| devguard.mail.httpPort | port | 20000 + hash | Mailpit web UI port (per-bench). |
| devguard.mail.sender | str | "notifications@example.com" | From address used only on sites with no outgoing Email Account at all. |
| devguard.mail.unmute | bool | true | Ignore mute_emails in site_config.json. |
| devguard.mail.pop3.enable | bool | false | Serve incoming mail from Mailpit's POP3 listener instead of blocking it. |
| devguard.mail.pop3.port | port | 21000 + hash | Mailpit POP3 port (per-bench). |
| devguard.mail.pop3.user / .password | str | "dev" | Mailpit POP3 credentials (local development only). |
| devguard.backups.enable | bool | true | Block Dropbox / S3 / Google Drive / Frappe Cloud backup upload. |
| devguard.objectstore.enable | bool | true | Force cloud_storage to local disk instead of the configured bucket. |
| devguard.integrations.enable | bool | true | Block outbound HTTP via frappe.integrations.utils.make_request. |
| devguard.integrations.allowHosts | list of str | [] | Hosts to permit anyway. Loopback is always allowed. |
| devguard.google.enable | bool | true | Block Google Calendar / Contacts / Drive access. |
| devguard.webhooks.enable | bool | true | Drop outbound Webhook requests. |
| devguard.plaid.enable | bool | true | Block Plaid bank synchronisation. |
| devguard.scheduler.enable | bool | true | Skip scheduled jobs that reach production services. |
| devguard.scheduler.blockServerScripts | bool | true | Skip Scheduled Job Types backed by a Server Script. |
| devguard.scheduler.extraBlockedJobs | list of str | [] | Extra Scheduled Job Type.method values to skip (exact match). |
| restore.enable | bool | secrets.backupAccess.enable | Let bench restore fetch from the object store — see Restoring from production. |
| restore.prefix | str | "" | Path inside the bucket. Normally carried in the secret as BACKUPS_PREFIX instead. |
| restore.withFiles | none/private/all | "none" | File archives to pull by default; they are routinely tens of GB. |
| restore.carryConfigKeys | list of str | [ "encryption_key" "backup_encryption_key" ] | Allowlist of keys copied from the backup's site config. |
| restore.migrate | bool | true | Run bench migrate after restoring. |
| restore.requireDevguard | bool | true | Refuse to write production's encryption key into an unguarded bench. |
| containers.enable | bool | false | Build the OCI images. |
| containers.registry | str | "" | Registry URL prefix. |

## Options — top-level `frappe-nix.secrets`

These sit at the flake's top level, not under `perSystem`: recipients and `.age` paths are facts about the bench rather than about a platform, and agenix-shell's own secret options are top-level for the same reason. See [Secrets](#secrets).

| Option | Type | Default | Notes |
| --- | --- | --- | --- |
| enable | bool | recipients != {} | Wire agenix + agenix-shell into this bench. |
| dir | path | (required) | Where the .age files live, e.g. ./secrets. |
| relDir | str | baseNameOf dir | The same directory relative to the bench root; override only if dir is nested. |
| recipients | attrs of str | {} | SSH public keys of the people who may decrypt. Attribute names become labels in error messages. |
| hostRecipients | attrs of str | {} | Deployment host keys; added to the per-site secrets only. |
| identityPaths | list of str | [ "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa" ] | Private keys tried when decrypting. |
| backupAccess.enable | bool | secrets.enable | Declare backup-access.age — the object-store credentials. |
| sites.<name>.{encryptionKey,databasePassword,extraConfig} | bool | true | Which per-site secrets to declare. |
| sites.<name>.developers | bool | true | Let recipients, not just hosts, read this site's secrets. |
| extra.<name>.{format,var,hosts} | — | — | Additional secrets; format is env, raw or json. |

## Development shell

`devenv up` runs the full stack via process-compose. **Several benches can run at once**: everything that can be is on a unix socket under `$DEVENV_RUNTIME`, which devenv gives each project uniquely, and the ports that remain are per-bench.

| Service / process | Listens on |
| --- | --- |
| nginx | TCP 8000 + a hash of benchName — the only port a browser sees |
| runtime (web + realtime + jobs + scheduler) | $DEVENV_RUNTIME/web.sock |
| MariaDB | $DEVENV_RUNTIME/mysql.sock — and loopback TCP 3306 + the same hash |
| Redis (cache + queue) | $DEVENV_RUNTIME/redis.sock |
| Mailpit (SMTP / HTTP / POP3) | TCP 19000 / 20000 / 21000 + the same hash |
| watch | — |
| assetsWatch (only with `assets.reassert.hooks` set) | — |

nginx routes `/socket.io` and everything else to the same socket — one process answers both — the same shape [`services.frappe`](#nixos-module--servicesfrappe) uses in production. `webserver_port` and `socketio_port` in `sites/common_site_config.json` are both set to the nginx port, which is what lets the browser reach both over one origin.

MariaDB is the one service that keeps a TCP listener, on loopback and on its own per-bench port, which `FRAPPE_DB_HOST`/`FRAPPE_DB_PORT` name. Frappe never uses it — `db_socket` wins over host/port in `get_connection_settings` — but an app that opens its own connection to `frappe.conf.db_host:db_port` does, and with no listener of this bench's there it silently reaches whichever _other_ bench holds 3306. Insights' "Site DB" data source is one such app (ibis rewrites host `localhost` back to `127.0.0.1`, so libmysqlclient's socket shortcut does not save it), and a `bench update` that lands in a neighbour's database fails mid-migrate with an access-denied for a user that server has never heard of.

The ports are hashed from `benchName` rather than the project path so that every _clone_ of a bench derives the same number and the committed `common_site_config.json` never conflicts; devenv's port allocator still walks forward if something is genuinely in the way, and `devenv up` writes the value it settled on back into the config. Override the base with `ports.base`, or set `sockets.enable = false` to put everything back on TCP — ports are still allocated dynamically in that mode, so benches still do not collide, they just use more ports and no nginx.

> One caveat if you use the **wiki** app: its frontend does `import { socketio_port } from 'sites/common_site_config.json'`, so the port is baked into its bundle at build time. If the allocator ever moves your port, re-run `bench build --app wiki`. `frappe-ui`'s vendored `socketio.js` similarly defaults to a hardcoded 9000 unless the call site passes `port: window.frappe?.boot?.socketio_port`.

`apps/*` are installed as **editable** packages (uv2nix editable overlay), so source edits hot-reload. `uv` and `yarn` write to mutable state dirs (`$DEVENV_STATE`) so `uv add` / `yarn add` work despite the read-only Nix store; the resulting `uv.lock` / `yarn.lock` are then consumed declaratively for production builds.

`http://localhost:<port>` (and `127.0.0.1`) serves the site named by `siteName`: the unified runtime sends a request whose `Host` names no site on the bench to `FRAPPE_SITE` / `default_site`, on the web and the socket.io path alike, while a `Host` that does name a site — `http://other.localhost:<port>` on a bench with several — still reaches that one. (`bench serve` used to pin every request to `FRAPPE_SITE`; the runtime, which imports the WSGI app directly, had lost that.)

Each app's `node_modules` is a real `yarn install`, not the Nix-built one — nested vite frontends (`erpnext/banking`, `hrms/frontend`, `helpdesk/desk`, …) get their deps from a postinstall that needs the network. It is skipped for an app whose `package.json`/`yarn.lock` — its own and every nested one — are unchanged since the last successful install, and re-run when any of them moves. `bench build` re-runs it too, and refuses to build if it fails: pull an app that added a dependency, build without reinstalling, and what you get is a missing-package error from a vite config several apps deep, naming nothing that leads back to the install.

### Upgrading frappe-nix

```sh
nix flake update frappe-nix   # or `nix flake update` for every pin
direnv reload                 # or: nix develop --no-pure-eval
```

That is the whole procedure. A frappe-nix bump can ask something new of a bench's **workspace root** — `frappe-runtime` becoming a required dependency is the case so far: a name in `[project].dependencies`, a `[tool.uv.sources]` entry, a build backend named in `[tool.uv.extra-build-dependencies]` — and a bench from before the bump has no way to know. So `enterShell` reconciles `pyproject.toml` on every entry with the same `ensure-root` step `frappe-init` runs: it adds what is missing, changes nothing that is already there (your name, your `requires-python`, your override list, your comments — the file is edited with tomlkit, not rewritten), and runs `uv lock` only when that changed the file. On the common path it is one TOML parse and says nothing.

When it does change something it says so, lists what it added, and tells you to **commit `pyproject.toml` and `uv.lock` and re-enter the shell** — the shell you are in was built from the previous lock. Until you do, `devenv up` runs whatever shape the old lock supports: a bench whose lock predates the runtime gets the split `web`/`socketio`/`worker`/`scheduler` processes for that session, and the banner says so. Nothing about that shape is degraded — it is `runtime.enable = false`, which `checks.socket` covers.

If `uv lock` fails — no network, or a genuine conflict between the new requirement and an app's pins — both files are put back exactly as they were, mtimes included, and the shell opens anyway. A `pyproject.toml` that declares what `uv.lock` does not carry would fail the *next* evaluation (see [A stale `uv.lock` is an evaluation error](#a-stale-uvlock-is-an-evaluation-error)), which is the one outcome this hook exists to avoid: you would be back to fixing the shell from outside it. The next entry retries; to resolve a conflict by hand, make the listed additions in `pyproject.toml`, add the override `uv` asks for, and run `uv lock` in the shell.

What it deliberately does not do: register submodules, vendor apps, edit `.gitignore` or `common_site_config.json`, or `git add` anything — that is [the reconciler's](#migrate-an-existing-bench) job, and the reconciler still does all of it (`nix run github:Avunu/frappe-nix -- -y`) for the cases a shell hook should not decide. A change to the *options* a bench's `flake.nix` sets (`nodeOfflineHashes` was one) cannot be reconciled from inside and stays an evaluation error that names the edit.

### Installed-app drift

`sites/apps.txt` is bench-level — frappe-nix regenerates it from the workspace members, in `app.siblings` order — but a site's installed apps are per-site, DB-backed (`frappe.get_installed_apps()`), and only ever grow through an explicit `bench install-app`. `apps.txt` is the candidate list `install_app()` validates a name against, not a queue anything drains, and `bench migrate` walks the DB list, never `apps.txt`. Pin a new sibling into an already-provisioned bench and nothing installs it: the app is importable, on `PYTHONPATH`, even visible in the desk's app switcher — and every page it owns 404s or throws, forever, with no signal pointing at "app not installed." (See [issue #32](https://github.com/Avunu/frappe-nix/issues/32) for how this actually presented — a report page that quietly ran on stock `frappe-datatable` because the app that was supposed to replace it had never been installed.)

`appsReconcile.enable` closes this: a `frappe:apps-reconcile` task diffs `sites/apps.txt` against `siteName`'s installed apps on every `devenv up` and installs whatever is missing. `install-app` is idempotent (a no-op, unless `--force`, which this never passes), so on an already-reconciled bench it costs one `bench list-apps`. It defaults to `true` whenever `siteName` names one site — the common case for every app-mode bench — and `false` in multi-tenant mode (`siteName = ""`), where the task has no single site to target; run `reconcile-apps <site>` by hand there instead. `provision-site` is not a substitute for this: it installs into a site it just created, and re-running it to pick up a later-pinned sibling would drop the site's database (`bench new-site --force`).

### Asset-shadow staleness

Some Frappe apps ship their own JS/CSS build tooling that shadows Frappe's own bundle keys in `sites/assets/assets.json` — carbon-themed desk skins are the case this was found from, though frappe-nix has no knowledge of any specific one. Frappe's esbuild pipeline writes that file two different ways for the same logical bundle: one keyed by the *source* entry file's basename, which runs on every `bench build` **and every `bench watch` rebuild**; one keyed by the *built* file's basename with its content hash stripped, which runs only on `bench build --using-cached`. An app whose `hooks.py` names the second key can have a `bench watch` rebuild touch only the first, while esbuild's own dist cleanup deletes the file the second key still points at — and Frappe's own resolution (`bundled_asset()`) is a bare dict lookup with no existence check, so the browser 404s with no server-side signal. `bench watch` alone is enough to trigger this — no devenv restart, no second app involved.

`assets.reassert.hooks` is the fix, and it ships with **zero built-in hooks and names no app**: it is a list of `bench execute`-able dotted paths that you point at your own app's asset-shadow fixup. Whenever `sites/assets/assets.json` names a bundle file that doesn't exist on disk, every configured hook runs, in order, against `siteName`. Two triggers cover the two ways this goes stale: a `frappe:assets-reassert` task runs the check once at `devenv up` (a bench that sat idle with a stale `assets.json` heals before the first page load), and an `assetsWatch` process — using [fswatch](https://github.com/emcrisostomo/fswatch) for the file-change detection, portable across Linux and Darwin — watches `assets.json` for the writes `bench watch`'s own rebuilds make, debounced by `assets.reassert.debounceMs`. esbuild's writer truncates-and-writes with no temp-file-and-rename, so a read can land mid-write; the check retries a failed JSON parse a few times before giving up, and a parse failure is never treated as a missing-file invariant failure — it would otherwise fire the hooks on every rebuild instead of only when something is actually missing. `assets-reassert` (a plain devenv script, present only when hooks are configured) runs the same check on demand.

### Development guard rails

A bench restored from a production backup carries working production credentials in its database and `site_config.json`. Left alone, `devenv up` will mail real customers within minutes, delete production files out of an object store within the hour, and — depending on what is configured — capture real payments, push its dev-mutated database over the production backup rotation, and delete real calendar events.

`frappe-nix.devguard` closes those routes. Nothing is installed into any site and no config is edited; each guard is independently toggleable, and `devguard.enable = false` turns them all off.

| Guard | What it stops | How |
| --- | --- | --- |
| mail | Any mail leaving the machine | Redirects SMTP to Mailpit (http://127.0.0.1:8025); refuses IMAP/POP3 |
| backups | Dropbox / S3 / Google Drive / Frappe Cloud backup upload | No-ops the scheduler entries, blocks the upload funnels, throws on the desk buttons |
| objectstore | cloud_storage writing to and deleting from the production bucket | Forces the app's own use_local mode, so files go to local disk |
| integrations | Outbound HTTP via frappe.integrations.utils.make_request | Refuses non-loopback hosts unless listed in allowHosts |
| google | Calendar / Contacts / Drive access — sync writes back and can delete real events | Blocks GoogleOAuth's service-object and token-refresh calls |
| webhooks | Webhook rows firing at production endpoints | No-ops enqueue_webhook |
| plaid | Bank sync against the production Plaid item | Blocks PlaidConnector, no-ops the hourly job |
| scheduler | Third-party backup jobs and Server Script scheduler events | Skips them in ScheduledJobType.execute |

Local backups are untouched by all of this: `bench backup`, `bench restore`, `trim-database`, `drop-site` and the desk Backups page keep working. Only egress is blocked.

#### How it works

Frappe offers no config-only way to do this — `find_default_outgoing` consults the database _before_ falling back to `frappe.conf`, and the backup integrations are gated by doctype rows that a production dump restores in the enabled state. The interception therefore lives below the app layer, in `lib/devguard/frappe_devguard`, grafted into the development virtualenv by a `.pth` file that Python executes at interpreter startup. It applies to `bench serve`, `worker`, `schedule`, `console`, and any bare `./env/bin/python`.

It is deliberately **not** on `PYTHONPATH`: `apps/*` reach `sys.path` through the editable `.pth` files in the venv, so an interpreter started outside the devenv environment would still import Frappe and still reach production. And it is development-only by construction — `prodPythonEnv`, the NixOS module and the containers never see it.

Each patch is checked as it is applied: if Frappe's internals move, the import fails loudly rather than leaving a silently inert guard behind.

#### `frappe_unixsock` — grafted the same way, but not a guard rail

`lib/unixsock/frappe_unixsock` uses the same `.pth` mechanism but ships to **both** virtualenvs, and therefore into `builtBench`, the containers and `services.frappe`. It carries no policy: its whole job is to make Frappe honour a unix socket in the two places it only half-does.

| Patch | Where it bites |
| --- | --- |
| frappe.app.serve binds unix://$FRAPPE_WEB_SOCKET | bench serve hardcodes run_simple("0.0.0.0", int(port)), so there is no other way off TCP. Inert in production, which runs gunicorn --bind unix: natively. |
| frappe.connect_replica uses $FRAPPE_REPLICA_DB_SOCKET | it hardcodes socket=None, twenty lines below the connect() that honours db_socket. Production-only, and only when a replica is configured. |

Every patch is gated on its socket actually being set, so a bench with no sockets installs nothing and cannot be broken by a Frappe upgrade moving a target; a bench that _is_ on sockets fails loudly instead, because silently falling back to TCP would mean connecting to another project's service. `FRAPPE_UNIXSOCK_ENABLED=0` disables it for a single command.

Shipping it to production does not weaken devguard's dev-only guarantee: the two packages are separate and share no code. Guarding against _reaching_ production is meaningless in production; correcting a socket transport is not.

#### What this is not

Only the `mail` guard offers **transport-level** containment: it patches `smtplib`/`imaplib`/`poplib`, which know nothing about Frappe and so hold across upgrades, third-party apps, and `override_doctype_class` controllers.

Every other guard patches Frappe and app APIs, and is therefore one refactor or one unknown app away from being bypassed. The `scheduler` denylist covers exactly the dotted paths in it; egress from a document event in an app nobody has looked at is not covered. Treat this as a large reduction in blast radius, not an airgap.

Two related notes for a restored bench: Frappe's telemetry is inert here only because `developer_mode: 1` is set, so re-check it if you ever clear that flag; and `check_for_update` / `fetch_changelog_feed` still reach github.com and frappe.io, which is harmless and deliberately left alone.

#### Turning guards off

```sh
FRAPPE_DEVGUARD_DISABLE=backups,google bench console   # named guards, one command
FRAPPE_DEVGUARD_ENABLED=0 bench console                # all of them
```

Nix-baked values are likewise overridable at runtime — `FRAPPE_DEVGUARD_MAIL_HOST`, `FRAPPE_DEVGUARD_MAIL_PORT`, `FRAPPE_DEVGUARD_INTEGRATIONS_ALLOW_HOSTS`, and so on — without a rebuild.

**Incoming mail is blocked** by default: a dev bench polling production mailboxes every 10 minutes marks real messages seen and fires auto-replies. Set `devguard.mail.pop3.enable = true` to serve incoming from Mailpit's POP3 listener instead, with the caveat that Frappe issues `DELE` after fetching and Mailpit honours it, so pulled messages disappear from the Mailpit UI.

### `bench` is transparent

The shell ships an umbrella **`bench` wrapper** that shadows the venv's `bench` (devenv wraps scripts with `lib.hiPrioSet`, so it wins on PATH) and transparently redirects the subcommands that need frappe-nix handling — so you just run normal `bench` commands:

| You run | Redirected to | Why |
| --- | --- | --- |
| bench update … | bench-update | vanilla update pip-installs / assumes upstream remotes |
| bench build … | bench-build | brings node_modules back in step with the apps first |
| bench get-app [--branch <b>] <url\|alias> | bench-get-app | git submodule + uv workspace instead of pip |
| bench new-app <name> | bench-new-app | scaffold + uv workspace (skips the failing pip step) |
| bench restore [<sql>] | bench-restore | injects the MariaDB root credentials; with no file, fetches the latest production backup |
| bench new-site <site> | real bench + injected --db-socket/--db-root-username root | non-interactive site creation |
| bench migrate / console / clear-cache | bench-* | inject --site $FRAPPE_SITE |
| everything else (serve, install-app, --help, …) | the real bench | unchanged |

Recursion is avoided with a `_FRAPPE_BENCH_RAW` env guard the specialized scripts export and the wrapper checks, so a script's own nested `bench …` calls reach the real CLI — whether you invoke `bench update` or the underlying `bench-update` directly. Two caveats: redirected commands follow the frappe-nix scripts' flags, not vanilla bench's (e.g. `bench update` takes `--pull|--migrate|--build|--node-locks`, not `--reset`); and interception is subcommand-first, so `bench --site X migrate` (global option before the subcommand) passes straight through.

Because those two caveats leave the real `bench update` reachable — `_FRAPPE_BENCH_RAW=1 bench update --reset`, or just `env/bin/bench` — the shell also keeps its first step working. `bench update` starts with `bench.patches.run()`, which executes every entry in the `patches.txt` frappe-bench ships that the **bench root's** `patches.txt` does not record as done. bench deleted the v3/v4 patch modules in 2022 but still lists them, so a bench root with no record dies immediately on `ModuleNotFoundError: No module named 'bench.patches.v3'` — and stays dead, because the failed run rewrites the root file as one empty byte. `bench init` avoids this by copying the shipped list in verbatim; frappe-nix never runs `bench init`, and the file is gitignored, so `enterShell` reconciles it instead — on every shell entry, non-destructively, and silently unless it changes something. See [`lib/bench-patches.nix`](lib/bench-patches.nix) for why _every_ patch is recorded as done rather than only the two that cannot import.

### A stale `uv.lock` is an evaluation error

`apps/*` are git submodules and `uv.lock` is a committed, resolved snapshot of what they all declare. Move an app to a commit whose `pyproject.toml` gained a dependency and the two disagree — uv2nix then looks up a name the lock never recorded, and the bench fails to **evaluate**:

```
error: attribute 'json-repair' missing
at …/uv2nix/build/lib/resolvers.nix:123:23
```

Three things keep that from being a puzzle:

-   **`bench-update --pull` re-locks.** It runs `uv lock` when any app's `pyproject.toml` moved, and regenerates the fallback lock of any app without a `yarn.lock` whose `package.json` moved, so the pull that causes the drift also resolves it. Commit `uv.lock` (and `node-locks/`, if it changed) with the submodule bumps.
-   **The error says so.** Before uv2nix resolves anything, frappe-nix audits every declared requirement against the set the resolver will index ([`lib/lock-audit.nix`](lib/lock-audit.nix)) and names the app, the requirement and the fix. Marker-gated and direct-URL requirements are left alone — they can sit outside a resolution legitimately, and a false alarm would be worse than the raw error it replaces.
-   **`nix run .#relock` works when nothing else does.** This class of failure blocks evaluation, so the dev shell that carries `uv` is exactly what you cannot open. The `relock` app is deliberately outside every other output's dependency graph and takes its `uv` from nixpkgs, so it still runs.

In [app mode](#develop-a-single-app) the cause is `nix flake update` rather than a submodule bump, and the fix is `nix run .#relock` rather than `uv lock` — the workspace root there is generated into the store, so there is no bench root of yours to run `uv` in. The audit says that instead. `relock` is also how the _first_ lock is produced: a repository with no `nix/uv.lock` cannot evaluate the dev shell either, and the error names the command.

### Bench scripts

These back the wrapper and are also callable directly:

| Script | Description |
| --- | --- |
| provision-site [admin-pass] | Create $FRAPPE_SITE and install every app from sites/apps.txt. |
| reconcile-apps [site] | Install whatever sites/apps.txt names that $SITE (or the given site) doesn't have installed yet. Idempotent; also runs automatically — see appsReconcile.enable. |
| bench-update [--pull\|--migrate\|--build\|--node-locks] | Submodule-aware replacement for bench update. --pull fetches each submodule's .gitmodules branch from the remote that carries its declared URL (origin is often a developer's fork), refuses to discard local commits (a shallow clone whose pin and tip share no history at all — the shape a depth-limited fetch leaves behind, and what git shows as a phantom "1 ahead" — is deepened back to the pin's date and re-checked first, since that is never a local commit), skips local apps and reports stray repos; then regenerates sites/apps.json for the new pins, the fallback locks in node-locks/ for the apps without a yarn.lock whose package.json moved, and re-locks the workspace (uv lock) when a pyproject.toml did. --node-locks [target…] regenerates node-locks/ for every app and nested frontend without a yarn.lock of its own; a named target gets a lock forced over the yarn.lock it ships. In app mode, --migrate and --build only. |
| bench-migrate / bench-build / bench-clear-cache / bench-console | Thin bench wrappers honoring $FRAPPE_SITE. |
| bench-restore [<sql>\|--at <ts>\|--list] | Restore from a SQL backup, or from the latest one in the object store. See Restoring from production. |
| setup-backup-access | Prompt for the object-store credentials, test them against the bucket, and write backup-access.age. |
| edit-secret <name> | Decrypt a secret into $EDITOR and re-encrypt it to the declared recipients. Reads stdin when it is not a terminal, so a secret can be piped in. |
| rekey-secrets | Re-encrypt every secret after changing recipients. |
| check-secrets [<name>] | Verify the .age files match the declared recipients; with a name, explain why you cannot decrypt one. |
| bench-get-app [--branch <b>] <url\|alias> | Add an app as a git submodule, register it in the uv workspace and in sites/apps.{txt,json}. helpdesk → frappe/helpdesk; owner/repo and full URLs also work. The branch — --branch, else the remote's default — is recorded in .gitmodules, which is what bench-update --pull follows. |
| bench-new-app <name> | Scaffold a new app as a local app (committed source, no nested git) and register it the same way. |
| update-deps | Re-lock + sync Python (uv) and Node (yarn) across all apps, then refresh the fallback locks in node-locks/. |

In [app mode](#develop-a-single-app) the three that edit the bench as if it were a checkout have no checkout to edit — there are no submodules, and anything written into the generated bench is discarded on the next pin bump. They refuse with the flake-input equivalent instead: `bench-update --pull` and `--node-locks` point at `nix flake update`

-   `nix run .#relock`, and `bench-get-app` / `bench-new-app` at declaring the app as an input. `--migrate` and `--build` are unaffected, and everything else in the table works exactly as it does in a bench.

## Secrets

A bench's credentials — the site encryption key, the database password, the object-store keys — live in `.age` files encrypted with [age](https://github.com/FiloSottile/age), committed to the repo, and decrypted into the dev shell by [agenix-shell](https://github.com/aciceri/agenix-shell). frappe-nix imports agenix-shell itself, so a consuming flake declares only this:

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

That declares five secrets, on a fixed layout:

| File | Shape | What consumes it |
| --- | --- | --- |
| secrets/backup-access.age | env-file | bench restore's fetch |
| secrets/<site>/encryption-key.age | one line | services.frappe's encryptionKeyFile |
| secrets/<site>/db-password.age | one line | database.passwordFile |
| secrets/<site>/site-config.age | JSON object | extraConfigFiles |

The shapes are the ones `services.frappe` already consumes, so the same ciphertext can serve the deployment: `flake.lib.frappeSecrets` exposes the paths, which beats keeping a second copy in the server repo that has to be rotated in lockstep.

`recipients` are the people; `hostRecipients` are deployment hosts, and are added to the per-site secrets only. A site can set `developers = false` to keep its secrets host-only — a useful tier, since it lets someone restore the database without being able to read the credentials stored inside it.

**`.age` files are meant to be committed.** They are ciphertext, and a flake's source tree is exactly its git-tracked files — an untracked secret is invisible to the build. `edit-secret` stages new ones for you.

### There is no `secrets.nix`

agenix normally reads a committed rules file listing who may decrypt what. frappe-nix generates that file into the store instead and points agenix's `RULES` at it, because a hand-maintained one can be edited without re-encrypting anything and nothing notices. That is not hypothetical: in the bench this was built for, a rotated key sat in the rules for months while the ciphertext still named the key it replaced, and the person it was rotated for could not decrypt anything.

So the recipient list in `flake.nix` is the only place it is written down, and `check-secrets` proves the ciphertext agrees:

```
$ check-secrets
secrets/backup-access.age: not encrypted to 1 declared recipient(s):
      KATJVw  bob
    Someone who can still decrypt it must run:  rekey-secrets
```

It works offline and needs no private key — an age header names its recipients in the clear, and an SSH recipient's tag is derivable from the public key alone. `check-secrets <name>` turns that around and explains why _your_ key cannot open a particular secret.

After changing `recipients`, run `rekey-secrets` and commit the result.

### Setting up backup access

`setup-backup-access` asks five questions instead of making you remember five variable names and the quoting rules for a file the shell will source:

```
┌────────────────────────────────────────────────────────┐
│ Backup access for mybench                              │
│                                                        │
│ These are the object-store credentials `bench restore` │
│ uses to find and download production backups.          │
└────────────────────────────────────────────────────────┘

Endpoint URL
> https://s3.us-east-005.backblazeb2.com
…
Test these against the bucket now? [Yes]
✓ 34 backup(s) found; newest is 20260814_000042
```

It offers to list the bucket before encrypting anything, because a typo in an access key is otherwise a mystery several minutes into the first restore. Run it again to edit: existing values are pre-filled, and a blank secret key keeps the stored one, so rotating an access key does not mean re-typing a secret that has not changed.

For anything scripted, pipe the env-file in instead:

```sh
edit-secret backup-access <<'ENV'
BACKUPS_URL=https://s3.us-east-005.backblazeb2.com
BACKUPS_ACCESS_KEY=…
BACKUPS_SECRET_KEY=…
BACKUPS_BUCKET=MyERPBackups
BACKUPS_PREFIX=
ENV
```

### Restoring from production

```sh
bench restore                       # the newest backup
bench restore --list                # what is available
bench restore --at 20260814_000042  # a specific one
bench restore --files               # also the public files archive
bench restore ./dump.sql.gz         # an explicit file, no object store
```

With no file, `bench restore` reads the `backup-access` secret, finds the newest backup folder, downloads the database and the site-config backup, and restores them — **creating the site first if it does not exist**, so a fresh clone needs nothing but `direnv allow`, `devenv up`, `bench restore`.

It reads Frappe's own layout: `S3 Backup Settings` writes one folder per backup named `YYYYMMDD_HHMMSS`, holding the database, a verbatim copy of production's `site_config.json`, and optionally the two file archives. Downloads are cached under `$DEVENV_STATE`, keyed by folder — the name is a timestamp, so it is also the version, and a re-run of the same restore re-downloads nothing.

The credentials are decrypted at the moment they are used, not at shell entry. agenix-shell re-runs `rage` every time its script is sourced and `enterShell` runs on every direnv reload, so loading them there would prompt for a passphrase on every file save — and it exports the plaintext itself, not just a path, which would put credentials in the environment of every process in the session, `devenv up`'s children included.

#### The encryption key

The backup folder contains production's `site_config.json` verbatim, which is how `restore.carryConfigKeys` gets `encryption_key` and `backup_encryption_key`. Without the first, every stored password and API secret in the dump decrypts to nothing; the second opens the next encrypted backup. Both are written into the dev site's `site_config.json` at mode 0600.

It is an allowlist rather than a denylist, because a denylist loses to the next app that invents `foo_api_secret`. Everything else production had — `host_name`, `db_*`, `mail_*`, `cloud_storage_settings`, `maintenance_mode` — is left behind.

**This is a real capability, not a formality.** A bench holding that key can decrypt every stored production credential in the dump: mail passwords, payment secrets, API tokens. It is what makes a restore a clone rather than a shell, and it is precisely what [the guard rails](#development-guard-rails) exist to survive. `bench restore` therefore refuses to write it into a bench with `devguard.enable = false`; `--no-site-config` restores without it, and the site still works with its stored credentials opaque.

One consequence worth stating plainly: the backup's site-config copy is **never encrypted**, even when the database beside it is (`backup_encryption()` covers the dump and the two archives, not the config). Anyone who can read your backup bucket can read production's encryption key. Keep the bucket private.

## Production containers

```sh
nix build .#web          # → result is a Docker image tarball
docker load < result     # loads <benchName>/web:latest
```

The images are built from `builtBench` (apps + python env + node + compiled assets), with no imperative `uv sync` / `yarn install` / `bench build` at container start. Each process container runs a config-synthesis entrypoint that assembles `site_config.json` from environment variables and mounted secret files (`/secrets/`). `web` runs gunicorn on `:8000`, `nginx` reverse-proxies on `:80`, `socketio` runs on `:9000`, and `bench-cli` is for migrations / one-off commands.

## NixOS module — `services.frappe`

`nixosModules.default` is a standalone NixOS module (not flake-parts) for multi-tenant production deployment. It takes a **bench package** (the `builtBench` / `packages.default` from a bench repo) and derives all interpreters from its `passthru` — no separate `pythonEnv`/`nodejs`/`benchRoot` options.

**Important:** The bench repo exposes only the package. The NixOS module is imported directly from `frappe-nix`, not re-exported by the bench. A deployment server combines both:

```nix
# In a nixosConfiguration — the two-import pattern:
{
  imports = [ frappe-nix.nixosModules.default ];

  services.frappe = {
    enable = true;
    package = benchFlake.packages.x86_64-linux.default;  # builtBench from a bench repo

    database.createLocally = true;
    redis.createLocally = true;

    sites."mysite.example.com" = {
      enable = true;
      database.createLocally = true;
      database.passwordFile = config.age.secrets.db-pass.path;
      encryptionKeyFile = config.age.secrets.enc-key.path;
      extraConfigFiles = [ config.age.secrets.cloud-storage.path ];
      nginx.enable = true;
    };

    # Multiple sites on one host, optionally with different bench packages:
    sites."staging.example.com" = {
      enable = true;
      package = stagingBench.packages.x86_64-linux.default;  # per-site override
      web.port = 8001;
      socketio.port = 9001;
      nginx.enable = true;
    };
  };
}
```

### Per-site systemd services

For each enabled site, the module generates:

| Unit | Role |
| --- | --- |
| frappe-init-<site> | Oneshot: assembles runtime bench tree, links the app registry (sites/apps.txt, sites/apps.json) and assets from the package, synthesizes site_config.json via jq (merging base config + secrets). |
| frappe-migrate-<site> | Oneshot: runs bench migrate when the build changes. Snapshots the DB first and rolls back on failure (see Safe migrations). |
| frappe-web-<site> | Gunicorn bound to sites.<name>.web.port. |
| frappe-scheduler-<site> | Background scheduler. |
| frappe-socketio-<site> | SocketIO (Node). |
| frappe-worker-{default,short,long}-<site> | Background workers (one per queue). |

All service units `after`/`requires` their `frappe-init-<site>`.

### Config synthesis (secrets stay out of the store)

`frappe-init-<site>` writes a base `site_config.json` to the store from Nix-declared values (db host/port, redis URLs, `extraConfig`), then merges in secrets at activation time via `jq`:

-   `database.passwordFile` → `db_password` key
-   `encryptionKeyFile` → `encryption_key` key
-   `extraConfigFiles` → deep-merged JSON (for cloud storage creds, etc.)

The final `site_config.json` is written to the site's state directory with mode 0600.

### Key options

**Top-level:**

| Option | Type | Default | Notes |
| --- | --- | --- | --- |
| package | package | (required) | Default bench package (builtBench). Sites inherit this unless overridden. |
| runtime.enable | bool | true | One `frappe-runtime` unit per site (web + realtime + jobs + scheduler) instead of separate web/socketio/worker/scheduler units. |
| runtime.jobThreads | int | 4 | Concurrent background jobs inside the runtime process. |
| runtime.webThreads | int | 0 | Concurrent web requests; 0 keeps `frappe_runtime.asgi`'s own default. Size the DB pool against it. |
| runtime.restartAfterRequests | int | 5000 | Web requests before a graceful restart (0 = never). |
| runtime.restartAfterJobs | int | 500 | Background jobs before a graceful restart (0 = never). |
| runtime.restartIdleSeconds | int | 300 | Idle seconds before a graceful restart (0 = never). |
| runtime.requestDrainSeconds | int | 60 | Graceful-stop wait for in-flight web requests. |
| runtime.jobDrainSeconds | int | 600 | Graceful-stop wait for a job in progress. `TimeoutStopSec` is derived from this plus requestDrainSeconds, so systemd outlasts the drain instead of SIGKILLing partway through. |
| runtime.extraArgs | list of str | [] | Extra arguments appended to the `frappe-runtime` command line. |
| web.workers | int | 4 | Gunicorn worker count (shared across sites). Ignored when runtime.enable. |
| workers | list of str | ["default" "short" "long"] | Background worker queues per site. Passed to the runtime as `--queue` when runtime.enable. |
| database.createLocally | bool | false | Aggregate: enable MariaDB if this or any site requests it. |
| redis.createLocally | bool | false | Enable a local Redis instance. |
| user / group | str | "frappe" | Service user/group. |
| extraEnv | attrs of str | {} | Extra env vars for all Frappe services. |
| migrate.enable | bool | true | Run bench migrate automatically per site when the build changes. |
| migrate.snapshot | bool | true | Take a mysqldump snapshot before migrating (safety net). |
| migrate.rollbackOnFailure | bool | true | Restore the snapshot if the migration fails. |
| migrate.maintenanceMode | bool | true | Toggle maintenance mode around migrate; left on if it fails. |
| migrate.snapshotRetention | int | 3 | Snapshots to keep per site under <siteDir>/snapshots. |

**Per-site (`services.frappe.sites.<name>`):**

| Option | Type | Default | Notes |
| --- | --- | --- | --- |
| enable | bool | false | Enable this site. |
| package | package or null | null | Per-site bench package override. |
| siteDir | str | /var/lib/frappe/<name> | State directory for this site. |
| web.port | port | 8000 | Gunicorn listen port (ignored when web.socketPath is set). |
| web.socketPath | str | "" | Unix socket for gunicorn; nginx reaches it via a generated upstream. |
| socketio.port | port | 9000 | SocketIO listen port (ignored when socketio.socketPath is set). |
| socketio.socketPath | str | "" | Unix socket for the realtime server (socketio_uds); needs frappe ≥ 15.46. Removes the site's last non-loopback TCP listener. |
| database.{createLocally,host,port,socket,name,user,passwordFile} | — | — | Per-site database config. |
| redis.{cacheUrl,queueUrl,socketioUrl} | str | redis://127.0.0.1:13000 | Redis URLs. |
| encryptionKeyFile | path or null | null | File containing the Frappe encryption key. |
| extraConfig | attrs | {} | Extra keys merged into base site_config.json (no secrets). |
| extraConfigFiles | list of path | [] | JSON files deep-merged at activation (for secrets). |
| nginx.enable | bool | false | Create an nginx virtualHost for this site. |

Site creation remains an operational step (run `bench new-site` against the deployed host).

### Safe migrations on deploy

Whenever a new build is deployed (`nixos-rebuild switch`), the `frappe-migrate-<site>` oneshot runs `bench migrate` for the site. It re-runs only when the build actually changes — the last migrated build's store path is recorded in `<siteDir>/.frappe-migrate-build` and re-migration is skipped when it is unchanged.

Because Frappe migrations perform DDL (`CREATE`/`ALTER TABLE`), which auto-commits in MariaDB and cannot be rolled back in a transaction, the unit wraps the migration in a physical snapshot instead:

1.  **Snapshot** — `mysqldump --single-transaction` of the site DB to `<siteDir>/snapshots/premigrate-<site>-<timestamp>.sql.gz` (owner-only, 0600). If the snapshot cannot be taken, the migration is aborted (never migrate without a safety net).
2.  **Migrate** — `bench --site <name> migrate`, with the site in maintenance mode.
3.  **On success** — clear maintenance mode, record the build, prune old snapshots.
4.  **On failure** — restore the snapshot (drop all current tables, re-import the dump), **leave the site in maintenance mode**, log `MIGRATION FAILED` to the journal, and exit non-zero (the unit shows `failed`). The database is returned to its pre-migrate state; recover with a fixed forward deploy or `nixos-rebuild switch --rollback`.

It runs as the `frappe` user with the site's own DB credentials (no DB-root needed), so it works for both locally-created and externally-managed databases. Tune or disable it via the `services.frappe.migrate.*` options above (e.g. `migrate.snapshot = false` for very large databases where a snapshot per deploy is too costly).

## Library

### `lib.mkFlake`

```nix
frappe-nix.lib.mkFlake { inherit inputs; } flakeConfig
```

Calls `flake-parts.lib.mkFlake` with `inputs = frappe-nix.inputs // yourInputs`, so the modules resolve `nixpkgs`, `devenv`, `pyproject-nix`, `uv2nix`, `pyproject-build-systems` and `nix2container` from frappe-nix's pins. Your wrapper only needs to declare `frappe-nix` (and `nixpkgs.follows` for the perSystem `pkgs`).

### `lib.overrides`

Composable overlays for Python packages needing native libraries. `mysqlclient` is wired in automatically from `mariadb.package`; add others via `pythonOverrides`:

```nix
pythonOverrides = lib.composeManyExtensions [
  (frappe-nix.lib.overrides.pycups { inherit pkgs; })
  (frappe-nix.lib.overrides.python-ldap { inherit pkgs; })
];
```

Pure-Python build deps (setuptools, etc.) belong in `pyproject.toml` `[tool.uv.extra-build-dependencies]` so uv2nix handles them — these overlays are only for packages that need C headers/system libraries.

## The dev → prod contract

| Developer (imperative) | Nix build (declarative) |
| --- | --- |
| uv add / uv sync | uv2nix reads uv.lock |
| yarn add / yarn install | the app's yarn.lock → one fetchurl per tarball → yarn install --offline (node-locks/<app>/yarn.lock, from bench-update --node-locks, for an app that ships none) |
| bench build | builtBench runs bench build in the sandbox |
| edits apps/* source | benchRoot / builtBench copies the source tree |
| bench-get-app / bench-update --pull | benchRoot regenerates sites/apps.{txt,json} from the members |

Commit `uv.lock`, each app's `yarn.lock` (in the app), `sites/apps.json`, and `node-locks/` for the apps that have no `yarn.lock` to commit; the production env, node\_modules, compiled assets, app registry, containers, and NixOS deployment are all rebuilt from them.

In [app mode](#develop-a-single-app) the left column is the same but the right one reads the _inputs_ rather than the checkout: `nix/uv.lock` (and `nix/node-locks/`, for pins without a `yarn.lock`) are what you commit, `nix run .#relock` is what writes them, and the source `builtBench` copies is the pinned flake input — not the writable copy the dev shell put in `.frappe-nix/bench/apps/`. So an edit made inside that copy is a dev-only edit by construction; the app you are developing is the one exception, because it is your repository and `nix build` reads it the same way the shell does.

### App registry

`sites/apps.txt` is what `frappe.get_all_apps()` returns — the list every process consults, and the one `install-app` checks a name against. `sites/apps.json` is bench's record of each app's version and pin (`is_repo`, `resolution.{commit_hash,branch}`, `required`, `idx`, `version`), in bench's own shape. frappe-nix generates both, from one rule: **the registered apps are the `[tool.uv.workspace].members`**, in declared order, `frappe` first. Members, because that is what the virtualenv actually installs. A directory under `apps/` that is not a member is on PYTHONPATH and nothing more, and evaluation warns about it.

One tool writes them — `frappe-nix-workspace sync-registry` — and it runs wherever the members or the pins change: `frappe-init`, `bench-get-app`, `bench-new-app`, `bench-update --pull`, on every dev-shell entry (the one hook that also sees a pin moved by hand inside `apps/<x>`), and in `benchRoot` when the package is built. The dev shell's regeneration and the build's are byte-identical when the committed record is current, so a dirty `sites/apps.json` after a pin moves means exactly one thing: commit it with the bump. The build reads the committed file for the one fact the flake's source tree cannot carry — a submodule's commit — and recomputes everything else from the sources; a stale record costs a stale `commit_hash`, nothing more. `version` comes from `[project].version` or the app's `__version__`, `required` from `hooks.py`'s `required_apps`, the branch from `.gitmodules` (or the flake input's ref, in app mode).

At runtime the two files are symlinks into the package, like `sites/assets`: `frappe-init-<site>` and the container entrypoint relink them on every start, so a deploy that adds an app registers it, and nothing on the host can drift them (an upstream `bench get-app` run by hand fails on the read-only store, as it should). Only `common_site_config.json` is the operator's — seeded once, never touched again.

The one thing this does not change: `bench build` still compiles assets for every directory under `apps/`, registered or not — esbuild scans the directory, not the registry.

### Local apps

An app lives in a bench in one of two shapes, and everything above works with either:

-   a **git submodule** — `.gitmodules` names it, `bench-update --pull` moves it, `nix build` fetches it. `bench-get-app` makes these.
-   a **local app** — source committed with the bench, no `.git` of its own. `bench-new-app` makes these (it scaffolds with `--no-git`), and `frappe-init --migrate` turns an app with no usable remote into one by *vendoring* it: the nested `.git` moves to `.frappe-nix-backup/<app>.git`, the source is `git add`ed, and the app becomes a workspace member like any other. `bench-update --pull` reports it as having nothing to pull; `sites/apps.json` records it with `is_repo: false`.

There is a third shape git will happily produce and nothing can use: a nested repository — `bench new-app` without `--no-git`, or a `git clone` into `apps/` — that was `git add`ed as-is. The index records a **gitlink with no `.gitmodules` entry**. `git submodule update --init` and `git submodule foreach` die on it (`No url found for submodule path 'apps/<x>' in .gitmodules`), and the flake's source tree carries an empty directory in its place, so `nix build` silently produces a bench without the app. frappe-nix never iterates `git submodule …` itself for that reason — every dev-shell hook goes through `frappe-nix-workspace apps`, which names what each `apps/<x>` is — and it tells you on shell entry and on `bench-update --pull` when it finds one. Two ways out:

```sh
frappe-init --migrate          # vendor it: source committed, history kept in .frappe-nix-backup/
# or, once it has a remote to live at:
git rm --cached apps/<x> && rm -rf apps/<x> && bench-get-app <owner>/<x>
```

### Node lockfiles

Each app with a `package.json`, and each nested frontend under it (`erpnext/banking`, `hrms/frontend`, `commit/dashboard` — an immediate subdirectory with its own `package.json`), is a **node target**, and gets a `node_modules` in the bench package. It is built from the target's own **`yarn.lock`**, and nothing about it is committed to the bench or computed by hand:

-   `lib/yarn-lock.nix` reads the lock at evaluation time and turns every tarball in it into one `fetchurl`, by the URL and the `integrity` the lock already states (or the `#sha1` on the URL in an older lock). A git dependency is fetched by its commit (`builtins.fetchGit`, at evaluation time — a cold cache needs the repository reachable the first time anything forces the package, `nix flake check --no-build` included) and packed the way nixpkgs' `prefetch-yarn-deps` packs it.
-   The fetched tarballs are linked into an offline mirror under the names nixpkgs' `fixup-yarn-lock` rewrites the lock to, and nixpkgs' own `yarnConfigHook` installs from it: `yarn install --offline --frozen-lockfile --ignore-scripts --ignore-engines --ignore-platform`. This is exactly what nixpkgs does for a yarn 1 project — minus `fetchYarnDeps`, the one fixed-output derivation over the whole mirror whose hash had to be mined out of a failing build (the `node-offline-hashes.json` scheme frappe-nix used to have).
-   The derivation sees only the target's `package.json`, `yarn.lock`, `.yarnrc` and `.npmrc`, so a change to anything else in the app does not rebuild its `node_modules`. An upstream `yarn.lock` bump refetches only what moved.

Lifecycle scripts are not run in the sandbox (every native piece a Frappe frontend needs is a platform package or a prebuilt binary); all platforms' optional binaries are fetched, since they are in the lock. `nodeOverrides.<target>` is merged into that target's derivation — `postPatch`, `nativeBuildInputs`, `preInstall`, or a `yarnOfflineCache` of your own; the install flags are the hook's. In the bench tree each target's `node_modules` is a real directory of links into the store, not a link to the store's directory: Vite creates `node_modules/.vite-temp` while bundling an ESM `vite.config`, and only a permission error is tolerated — the sandbox's read-only store answers `EROFS`. `bench build` is unchanged (`yarn run production`, then `yarn build` in every app with a build script); the dev shell is unchanged too — its `node_modules` is a plain online `yarn install`, as upstream tooling expects.

**The fallback: `node-locks/`.** An app that ships a `package.json` but no `yarn.lock` has nothing to build from, and evaluation says so. `bench-update --node-locks` resolves one for it — `yarn install` in a scratch directory holding nothing but the manifests, on the developer's machine, with the network — and writes it to **`node-locks/<app>/yarn.lock`** (`nix/node-locks/` in app mode, from `nix run .#relock`) with a stamp of the `package.json` it came from. Commit it; the target builds from it exactly as it would from its own. `bench-update` / `bench-update --pull` regenerate the fallbacks whose `package.json` moved during the pull (the stamp makes the others free), `update-deps` refreshes them after its `yarn install`s, and evaluation warns about one that is older than its manifest. An app that ships a `yarn.lock` gets no fallback, and one it once needed is reported as unused when the app grows a lock upstream (`git rm -r` it).

**A `yarn.lock` that cannot resolve offline.** Occasionally an upstream lock does not cover its own `package.json` — a dependency bump that never regenerated the transitive entries — and the offline install fails with `Couldn't find any versions for "<pkg>" that matches "<range>" in our cache`; the build log names the remedies next to that line. One is to leave the frontend out with `nodeNestedFrontendExcludes = [ "app/subdir" ]`: no `node_modules`, no assets, and because a nested frontend is usually built by its *parent* app's `build` script (`cd banking && yarn build`) and frappe's esbuild runs every app's `build` script with no opt-out, the parent's `build`/`postinstall` scripts that name it are dropped from the bench tree's `package.json` too (the build log says so per script). The other is to **force** a fallback: `bench-update --node-locks app/subdir` resolves a lock seeded from upstream's own — its pins stay, only the gap is filled from the registry — stamps it `forced`, and that target builds from `node-locks/app/subdir/yarn.lock` instead of the upstream file. A forced lock follows the upstream `yarn.lock` on `--pull` like the others follow `package.json`. Nested frontends are discovered one level deep; a subdirectory the app tracks as a git submodule (`hrms/frappe-ui`) is a project of its own and skipped.

**Migrating a bench** from the earlier schemes: `nodeOfflineHashes` and `node-offline-hashes.json` are gone (the option is an error, so it cannot linger silently), and so are the `node-locks/<app>/package-lock.json` directories of the interim npm-based scheme — `bench-update --node-locks` removes those for every app that ships a `yarn.lock`, and generates the yarn.lock fallback for any that does not. `nodeOverrides` entries carrying `yarnFlags` or npm attributes should go too (`yarnFlags` was never read; a warning says so).

## Layout

```
frappe-nix/
├── flake.nix                 # flakeModules / nixosModules / lib outputs
├── lib/
│   ├── python.nix            # mkPythonEnvs — prod + editable-dev virtualenvs (uv2nix)
│   ├── bench.nix             # app discovery, node_modules (yarn install --offline from each yarn.lock), benchRoot
│   ├── app-workspace.nix     # app mode: the bench workspace, assembled in the store
│   ├── bench-patches.nix     # keeps `bench update` past bench's own patch list
│   ├── root-sync.nix         # shell entry: pyproject.toml up to date with frappe-nix, then uv lock
│   ├── lock-audit.nix        # names a stale uv.lock before uv2nix trips over it
│   ├── overrides.nix         # mysqlclient / pycups / python-ldap / cairocffi
│   ├── secrets-schema.nix    # the one derivation of a bench's secret set
│   ├── secrets-tools.nix     # generated agenix rules + the recipient checker
│   ├── agecheck.py           # are the .age files encrypted to who we think?
│   ├── backup-fetch.nix      # → sh/backup-fetch.sh, shellchecked
│   ├── devguard/             # frappe_devguard — guards against reaching production
│   ├── unixsock/             # frappe_unixsock — unix-socket transport fixes (dev + prod)
│   ├── init.nix              # `nix run` entry point: builds frappe-init from sh/*
│   ├── sh/                   # the scaffolder/migrator, concatenated into one script
│   │   ├── common.sh         #   presets, naming, output helpers
│   │   ├── detect.sh         #   bench shape, frappe version, per-app classification
│   │   ├── template.sh       #   staged template render, .gitignore + site config merge
│   │   ├── apps.sh           #   submodule registration / vendoring / workspace sync
│   │   ├── pipeline.sh       #   the phases both modes share
│   │   ├── init.sh           #   scaffold mode
│   │   ├── app-init.sh       #   app mode — set up an app's own repository
│   │   ├── migrate.sh        #   migrate mode
│   │   └── main.sh           #   flags + mode dispatch (must be concatenated last)
│   ├── frappe-workspace.py   # apps/ ⇄ pyproject.toml ⇄ sites/apps.{txt,json} reconciler (tomlkit)
│   ├── frappe-presets.json   # frappe version → python / node / branch matrix
│   └── scripts.nix           # portable bench shell scripts
├── templates/
│   ├── bench/                # what a new bench is laid down from
│   └── app/                  # what an app repository is laid down from
├── tests/                    # flake checks (see `nix flake check`)
└── modules/
    ├── flake-module.nix      # imports devenv.flakeModule + devenv.nix + containers.nix
    ├── devenv.nix            # perSystem.frappe-nix options + dev shell + packages
    ├── containers.nix        # OCI image builds
    └── nixos.nix             # services.frappe (NixOS systemd module)
```

`lib/sh/*.sh` are concatenated into a single `writeShellApplication`, so shellcheck sees the whole program at build time; `main.sh` holds the only top-level code and must stay last.
