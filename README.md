# frappe-nix

Reusable Nix infrastructure for [Frappe](https://frappeframework.com/) bench projects.

`frappe-nix` packages everything needed to develop and ship a Frappe/ERPNext bench
declaratively, so a consuming project's flake stays a thin wrapper instead of a
1000-line monolith. From a single `uv` workspace + `apps/` tree it provides:

- a **devenv** development shell (MariaDB, Redis, web/scheduler/worker/socketio/watch,
  Mailpit) with editable Python installs, live asset reloading, and a
  [guard rails](#development-guard-rails) so no bench can mail customers, overwrite a
  production bucket or upload a backup;
- reproducible **production Python environments** (via [uv2nix](https://github.com/pyproject-nix/uv2nix));
- reproducible **node_modules** from `yarn.lock` (yarn-v1 hooks);
- a `benchRoot` derivation that assembles the whole `/bench` tree;
- a **`builtBench`** package that runs `bench build` at build time (immutable assets) —
  the production-ready deployable consumed by both the NixOS module and OCI containers;
- eight **OCI container images** (web, scheduler, three workers, socketio, nginx, bench-cli);
- a multi-tenant **NixOS module** (`services.frappe`) with per-site systemd units;
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
as git submodules pinned to that version's branch, and runs `uv lock`.

Run in an **existing bench** directory, the same command detects it and
[migrates it in place](#migrate-an-existing-bench) instead.

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

## Migrate an existing bench

The same entry point converts a classic `bench init` bench — or a half-converted one — into
a frappe-nix repo. It **detects the mode from the target directory**, so from inside a bench:

```sh
cd ~/frappe-bench
nix run github:Avunu/frappe-nix -- --dry-run    # inspect the plan first
nix run github:Avunu/frappe-nix                 # then migrate
```

It is a **reconciler, not a converter**: it probes what the bench already has, adds only
what is missing, repairs drift, and never deletes. Running it on an already-migrated bench
is a no-op. Concretely it:

- detects the frappe version (branch → `sites/apps.json` → `frappe.__version__`) and pins
  python/node from the matching preset;
- `git init`s the bench root if needed and registers each app under `apps/` as a **git
  submodule pinned at its current commit** — nothing is fast-forwarded — recording the
  app's *actual* branch in `.gitmodules` (without which `bench-update --pull` silently
  skips it) and adding an `origin` alias when the app only has `upstream`;
- **vendors** apps with no usable remote: the nested `.git` moves to `.frappe-nix-backup/`
  (with a provenance JSON) and the source is committed into the bench, because an untracked
  nested repo is invisible to the flake and would vanish from the build;
- writes `flake.nix`, `pyproject.toml`, `.envrc` and the `uv.lock`, merging into an existing
  `pyproject.toml` rather than replacing it, and shimming a `pyproject.toml` for vendored
  apps that only ship `setup.py`;
- reconciles `sites/common_site_config.json` — forcing the per-bench web/socketio port
  the dev shell derives from the bench name, preserving everything else, dropping
  production-only keys (`host_name`, `http_port`, `restart_*`) and keys the socket setup
  supersedes (`db_host`, `db_port`, the `redis_*` URLs, `file_watcher_port`), and blanking
  `mariadb_root_password` since the file is about to be committed;
- extends `.gitignore` with a managed block so `sites/*/site_config.json`, site
  `private/`/`public/` data, `Procfile`, `patches.txt`, `config/*.conf` and `node_modules`
  stay out of git — then **verifies** with `git check-ignore` that nothing the build needs
  got excluded;
- moves a classic `env/` virtualenv to `.frappe-nix-backup/` (a real `env/` directory
  silently defeats the dev shell's `ln -sfn` and leaves `bench` on the stale interpreter).

Nothing is committed — the result is staged, so `git diff --cached` is the review. Add
`--commit` to commit it.

| Flag | Effect |
| --- | --- |
| `--dry-run` | Print the full plan (per-app disposition, warnings) and exit |
| `-y`, `--yes` | Skip the confirmation; **required** to migrate in a non-TTY |
| `--frappe-version <v>` | Override version detection |
| `--migrate` / `--init` | Force the mode instead of detecting it |
| `--vendor <a,b>` | Vendor these apps even though they have a remote |
| `--no-vendor` | Abort instead of vendoring an app with no usable remote |
| `--allow-file-remotes` | Accept filesystem paths as submodule URLs (breaks other clones) |
| `--legacy-apps <policy>` | `shim` \| `skip` \| `abort` for apps with only a `setup.py` |
| `--strict` | Treat dirty / unpushed apps as errors |
| `--keep-db-root-password` | Do not blank `mariadb_root_password` |
| `--commit[=<msg>]` | Commit the migration instead of only staging it |

Afterwards, run `bench-update --node-hashes` inside the dev shell before the first
`nix build` — `node-offline-hashes.json` is not generated by the migration.

**What it cannot fix.** An app pinned at a commit that is on no remote branch builds on your
machine and nowhere else; an app whose remote is unreachable will fail on a fresh clone of
the bench. Both are reported as warnings, and `--strict` turns the first into an error. A
`setup.py`-only app that is a *submodule* cannot be shimmed — a generated `pyproject.toml`
would sit outside the pinned commit — so vendor it (`--vendor <app>`) or fix it upstream.

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
              # Leave an app out, run `nix build .#default`, and copy the
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
| `nixosModules.default` | Standalone NixOS module exposing `services.frappe` (multi-tenant production systemd). |
| `lib.frappeSecrets` | *(in a consuming bench)* The declared `.age` paths and recipients, so a deployment can read the same ciphertext — see [Secrets](#secrets). |
| `lib.mkFlake` | `flake-parts.lib.mkFlake` wrapper that merges frappe-nix's inputs into the consumer's. |
| `lib.overrides` | Composable Python package overrides for native deps (`mysqlclient`, `pycups`, `python-ldap`, `cairocffi`). |

When `frappe-nix.enable` is set, the module adds these **packages** to your flake
(`nix build .#<name>`):

| Package | What it is |
| --- | --- |
| `default` / `builtBench` | Production-ready bench: apps + python env + node + **compiled assets**. The deployable consumed by the NixOS module and OCI containers. |
| `prodPythonEnv` | Production virtualenv — workspace apps + runtime deps, no dev tools. |
| `devPythonEnv` | Development virtualenv — adds dev groups + editable installs of `apps/*`. |
| `benchRoot` | The unbuilt `/bench` tree (apps + node_modules + Python env + site/config). Used by the dev path and as input to `builtBench`. |

and one **app** (`nix run .#<name>`):

| App | What it does |
| --- | --- |
| `relock` | `uv lock` in the bench root, from a uv that does not come from the workspace. See [stale `uv.lock`](#a-stale-uvlock-is-an-evaluation-error) — it exists for the case where the shell that carries `uv` is what refuses to open. |

The `builtBench` package exposes `passthru.{pythonEnv, nodejs, appsPath, appNames}` so
the NixOS module and containers can discover interpreters from the package itself —
no separate `pythonEnv`/`nodejs` options needed.

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
| `extraPackages` | list of package | `[]` | Extra packages installed in *both* the dev shell and any production deployment of this package (read off `builtBench`'s `passthru.extraPackages` by `services.frappe`'s NixOS module — no server-side config needed). |
| `extraLibraryPaths` | list of package | `[]` | Extra `LD_LIBRARY_PATH` entries (dev shell). |
| `extraScripts` | attrs | `{}` | Extra devenv scripts, merged over the standard set. |
| `extraEnv` | attrs of str | `{}` | Extra environment variables (dev shell). |
| `sockets.enable` | bool | `true` | Put MariaDB, Redis, socketio and the web server on unix sockets behind one nginx port, so several benches can run at once. Needs frappe ≥ 15.46. |
| `ports.base` | port or null | `null` | First port this bench tries; defaults to `8000` + a hash of `benchName`. |
| `devguard.enable` | bool | `true` | Master switch for all guard rails — see [Development guard rails](#development-guard-rails). |
| `devguard.mail.enable` | bool | `true` | Route all outgoing mail to Mailpit, refuse IMAP/POP3. |
| `devguard.mail.host` | str | `"127.0.0.1"` | Interface Mailpit binds and Frappe is redirected to. |
| `devguard.mail.smtpPort` | port | `19000` + hash | Catcher SMTP port (per-bench). |
| `devguard.mail.httpPort` | port | `20000` + hash | Mailpit web UI port (per-bench). |
| `devguard.mail.sender` | str | `"notifications@example.com"` | From address used only on sites with no outgoing Email Account at all. |
| `devguard.mail.unmute` | bool | `true` | Ignore `mute_emails` in `site_config.json`. |
| `devguard.mail.pop3.enable` | bool | `false` | Serve incoming mail from Mailpit's POP3 listener instead of blocking it. |
| `devguard.mail.pop3.port` | port | `21000` + hash | Mailpit POP3 port (per-bench). |
| `devguard.mail.pop3.user` / `.password` | str | `"dev"` | Mailpit POP3 credentials (local development only). |
| `devguard.backups.enable` | bool | `true` | Block Dropbox / S3 / Google Drive / Frappe Cloud backup upload. |
| `devguard.objectstore.enable` | bool | `true` | Force `cloud_storage` to local disk instead of the configured bucket. |
| `devguard.integrations.enable` | bool | `true` | Block outbound HTTP via `frappe.integrations.utils.make_request`. |
| `devguard.integrations.allowHosts` | list of str | `[]` | Hosts to permit anyway. Loopback is always allowed. |
| `devguard.google.enable` | bool | `true` | Block Google Calendar / Contacts / Drive access. |
| `devguard.webhooks.enable` | bool | `true` | Drop outbound `Webhook` requests. |
| `devguard.plaid.enable` | bool | `true` | Block Plaid bank synchronisation. |
| `devguard.scheduler.enable` | bool | `true` | Skip scheduled jobs that reach production services. |
| `devguard.scheduler.blockServerScripts` | bool | `true` | Skip `Scheduled Job Type`s backed by a `Server Script`. |
| `devguard.scheduler.extraBlockedJobs` | list of str | `[]` | Extra `Scheduled Job Type.method` values to skip (exact match). |
| `restore.enable` | bool | `secrets.backupAccess.enable` | Let `bench restore` fetch from the object store — see [Restoring from production](#restoring-from-production). |
| `restore.prefix` | str | `""` | Path inside the bucket. Normally carried in the secret as `BACKUPS_PREFIX` instead. |
| `restore.withFiles` | `none`/`private`/`all` | `"none"` | File archives to pull by default; they are routinely tens of GB. |
| `restore.carryConfigKeys` | list of str | `[ "encryption_key" "backup_encryption_key" ]` | Allowlist of keys copied from the backup's site config. |
| `restore.migrate` | bool | `true` | Run `bench migrate` after restoring. |
| `restore.requireDevguard` | bool | `true` | Refuse to write production's encryption key into an unguarded bench. |
| `containers.enable` | bool | `false` | Build the OCI images. |
| `containers.registry` | str | `""` | Registry URL prefix. |

## Options — top-level `frappe-nix.secrets`

These sit at the flake's top level, not under `perSystem`: recipients and `.age`
paths are facts about the bench rather than about a platform, and agenix-shell's
own secret options are top-level for the same reason. See [Secrets](#secrets).

| Option | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `recipients != {}` | Wire agenix + agenix-shell into this bench. |
| `dir` | path | *(required)* | Where the `.age` files live, e.g. `./secrets`. |
| `relDir` | str | `baseNameOf dir` | The same directory relative to the bench root; override only if `dir` is nested. |
| `recipients` | attrs of str | `{}` | SSH public keys of the people who may decrypt. Attribute names become labels in error messages. |
| `hostRecipients` | attrs of str | `{}` | Deployment host keys; added to the per-site secrets only. |
| `identityPaths` | list of str | `[ "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa" ]` | Private keys tried when decrypting. |
| `backupAccess.enable` | bool | `secrets.enable` | Declare `backup-access.age` — the object-store credentials. |
| `sites.<name>.{encryptionKey,databasePassword,extraConfig}` | bool | `true` | Which per-site secrets to declare. |
| `sites.<name>.developers` | bool | `true` | Let `recipients`, not just hosts, read this site's secrets. |
| `extra.<name>.{format,var,hosts}` | — | — | Additional secrets; `format` is `env`, `raw` or `json`. |

## Development shell

`devenv up` runs the full stack via process-compose. **Several benches can run at
once**: everything that can be is on a unix socket under `$DEVENV_RUNTIME`, which
devenv gives each project uniquely, and the ports that remain are per-bench.

| Service / process | Listens on |
| --- | --- |
| `nginx` | **TCP `8000` + a hash of `benchName`** — the only port a browser sees |
| `web` (`bench serve`) | `$DEVENV_RUNTIME/web.sock` |
| `socketio` (Node) | `$DEVENV_RUNTIME/socketio.sock` |
| MariaDB | `$DEVENV_RUNTIME/mysql.sock` — **and loopback TCP `3306` + the same hash** |
| Redis (cache + queue) | `$DEVENV_RUNTIME/redis.sock` |
| Mailpit (SMTP / HTTP / POP3) | TCP `19000` / `20000` / `21000` + the same hash |
| `scheduler`, `worker`, `watch` | — |

nginx routes `/socket.io` to the realtime socket and everything else to the web
socket — the same shape [`services.frappe`](#nixos-module--servicesfrappe) uses
in production. `webserver_port` and `socketio_port` in
`sites/common_site_config.json` are both set to the nginx port, which is what
lets the browser reach both over one origin.

MariaDB is the one service that keeps a TCP listener, on loopback and on its own
per-bench port, which `FRAPPE_DB_HOST`/`FRAPPE_DB_PORT` name. Frappe never uses
it — `db_socket` wins over host/port in `get_connection_settings` — but an app
that opens its own connection to `frappe.conf.db_host:db_port` does, and with no
listener of this bench's there it silently reaches whichever *other* bench holds
3306. Insights' "Site DB" data source is one such app (ibis rewrites host
`localhost` back to `127.0.0.1`, so libmysqlclient's socket shortcut does not
save it), and a `bench update` that lands in a neighbour's database fails
mid-migrate with an access-denied for a user that server has never heard of.

The ports are hashed from `benchName` rather than the project path so that every
*clone* of a bench derives the same number and the committed
`common_site_config.json` never conflicts; devenv's port allocator still walks
forward if something is genuinely in the way, and `devenv up` writes the value it
settled on back into the config. Override the base with `ports.base`, or set
`sockets.enable = false` to put everything back on TCP — ports are still
allocated dynamically in that mode, so benches still do not collide, they just
use more ports and no nginx.

> One caveat if you use the **wiki** app: its frontend does
> `import { socketio_port } from 'sites/common_site_config.json'`, so the port is
> baked into its bundle at build time. If the allocator ever moves your port,
> re-run `bench build --app wiki`. `frappe-ui`'s vendored `socketio.js` similarly
> defaults to a hardcoded 9000 unless the call site passes
> `port: window.frappe?.boot?.socketio_port`.

`apps/*` are installed as **editable** packages (uv2nix editable overlay), so source
edits hot-reload. `uv` and `yarn` write to mutable state dirs (`$DEVENV_STATE`) so
`uv add` / `yarn add` work despite the read-only Nix store; the resulting `uv.lock` /
`yarn.lock` are then consumed declaratively for production builds.

Each app's `node_modules` is a real `yarn install`, not the Nix-built one — nested
vite frontends (`erpnext/banking`, `hrms/frontend`, `helpdesk/desk`, …) get their
deps from a postinstall that needs the network. It is skipped for an app whose
`package.json`/`yarn.lock` — its own and every nested one — are unchanged since the
last successful install, and re-run when any of them moves. `bench build` re-runs it
too, and refuses to build if it fails: pull an app that added a dependency, build
without reinstalling, and what you get is a missing-package error from a vite config
several apps deep, naming nothing that leads back to the install.

### Development guard rails

A bench restored from a production backup carries working production credentials in its
database and `site_config.json`. Left alone, `devenv up` will mail real customers within
minutes, delete production files out of an object store within the hour, and — depending
on what is configured — capture real payments, push its dev-mutated database over the
production backup rotation, and delete real calendar events.

`frappe-nix.devguard` closes those routes. Nothing is installed into any site and no
config is edited; each guard is independently toggleable, and `devguard.enable = false`
turns them all off.

| Guard | What it stops | How |
| --- | --- | --- |
| `mail` | Any mail leaving the machine | Redirects SMTP to Mailpit (<http://127.0.0.1:8025>); refuses IMAP/POP3 |
| `backups` | Dropbox / S3 / Google Drive / Frappe Cloud backup upload | No-ops the scheduler entries, blocks the upload funnels, throws on the desk buttons |
| `objectstore` | `cloud_storage` writing to and deleting from the production bucket | Forces the app's own `use_local` mode, so files go to local disk |
| `integrations` | Outbound HTTP via `frappe.integrations.utils.make_request` | Refuses non-loopback hosts unless listed in `allowHosts` |
| `google` | Calendar / Contacts / Drive access — sync writes back and can delete real events | Blocks `GoogleOAuth`'s service-object and token-refresh calls |
| `webhooks` | `Webhook` rows firing at production endpoints | No-ops `enqueue_webhook` |
| `plaid` | Bank sync against the production Plaid item | Blocks `PlaidConnector`, no-ops the hourly job |
| `scheduler` | Third-party backup jobs and `Server Script` scheduler events | Skips them in `ScheduledJobType.execute` |

Local backups are untouched by all of this: `bench backup`, `bench restore`,
`trim-database`, `drop-site` and the desk Backups page keep working. Only egress is
blocked.

#### How it works

Frappe offers no config-only way to do this — `find_default_outgoing` consults the
database *before* falling back to `frappe.conf`, and the backup integrations are gated
by doctype rows that a production dump restores in the enabled state. The interception
therefore lives below the app layer, in `lib/devguard/frappe_devguard`, grafted into the
development virtualenv by a `.pth` file that Python executes at interpreter startup. It
applies to `bench serve`, `worker`, `schedule`, `console`, and any bare
`./env/bin/python`.

It is deliberately **not** on `PYTHONPATH`: `apps/*` reach `sys.path` through the
editable `.pth` files in the venv, so an interpreter started outside the devenv
environment would still import Frappe and still reach production. And it is
development-only by construction — `prodPythonEnv`, the NixOS module and the containers
never see it.

Each patch is checked as it is applied: if Frappe's internals move, the import fails
loudly rather than leaving a silently inert guard behind.

#### `frappe_unixsock` — grafted the same way, but not a guard rail

`lib/unixsock/frappe_unixsock` uses the same `.pth` mechanism but ships to **both**
virtualenvs, and therefore into `builtBench`, the containers and `services.frappe`. It
carries no policy: its whole job is to make Frappe honour a unix socket in the two places
it only half-does.

| Patch | Where it bites |
| --- | --- |
| `frappe.app.serve` binds `unix://$FRAPPE_WEB_SOCKET` | `bench serve` hardcodes `run_simple("0.0.0.0", int(port))`, so there is no other way off TCP. Inert in production, which runs gunicorn `--bind unix:` natively. |
| `frappe.connect_replica` uses `$FRAPPE_REPLICA_DB_SOCKET` | it hardcodes `socket=None`, twenty lines below the `connect()` that honours `db_socket`. Production-only, and only when a replica is configured. |

Every patch is gated on its socket actually being set, so a bench with no sockets
installs nothing and cannot be broken by a Frappe upgrade moving a target; a bench that
*is* on sockets fails loudly instead, because silently falling back to TCP would mean
connecting to another project's service. `FRAPPE_UNIXSOCK_ENABLED=0` disables it for a
single command.

Shipping it to production does not weaken devguard's dev-only guarantee: the two packages
are separate and share no code. Guarding against *reaching* production is meaningless in
production; correcting a socket transport is not.

#### What this is not

Only the `mail` guard offers **transport-level** containment: it patches
`smtplib`/`imaplib`/`poplib`, which know nothing about Frappe and so hold across
upgrades, third-party apps, and `override_doctype_class` controllers.

Every other guard patches Frappe and app APIs, and is therefore one refactor or one
unknown app away from being bypassed. The `scheduler` denylist covers exactly the dotted
paths in it; egress from a document event in an app nobody has looked at is not covered.
Treat this as a large reduction in blast radius, not an airgap.

Two related notes for a restored bench: Frappe's telemetry is inert here only because
`developer_mode: 1` is set, so re-check it if you ever clear that flag; and
`check_for_update` / `fetch_changelog_feed` still reach github.com and frappe.io, which
is harmless and deliberately left alone.

#### Turning guards off

```sh
FRAPPE_DEVGUARD_DISABLE=backups,google bench console   # named guards, one command
FRAPPE_DEVGUARD_ENABLED=0 bench console                # all of them
```

Nix-baked values are likewise overridable at runtime — `FRAPPE_DEVGUARD_MAIL_HOST`,
`FRAPPE_DEVGUARD_MAIL_PORT`, `FRAPPE_DEVGUARD_INTEGRATIONS_ALLOW_HOSTS`, and so on —
without a rebuild.

**Incoming mail is blocked** by default: a dev bench polling production mailboxes every
10 minutes marks real messages seen and fires auto-replies. Set
`devguard.mail.pop3.enable = true` to serve incoming from Mailpit's POP3 listener
instead, with the caveat that Frappe issues `DELE` after fetching and Mailpit honours it,
so pulled messages disappear from the Mailpit UI.


### `bench` is transparent

The shell ships an umbrella **`bench` wrapper** that shadows the venv's `bench` (devenv
wraps scripts with `lib.hiPrioSet`, so it wins on PATH) and transparently redirects the
subcommands that need frappe-nix handling — so you just run normal `bench` commands:

| You run | Redirected to | Why |
| --- | --- | --- |
| `bench update …` | `bench-update` | vanilla update pip-installs / assumes `upstream` remotes |
| `bench build …` | `bench-build` | brings `node_modules` back in step with the apps first |
| `bench get-app <url\|alias>` | `bench-get-app` | git submodule + uv workspace instead of pip |
| `bench new-app <name>` | `bench-new-app` | scaffold + uv workspace (skips the failing pip step) |
| `bench restore [<sql>]` | `bench-restore` | injects the MariaDB root credentials; with no file, fetches the latest production backup |
| `bench new-site <site>` | real bench + injected `--db-socket`/`--db-root-username root` | non-interactive site creation |
| `bench migrate` / `console` / `clear-cache` | `bench-*` | inject `--site $FRAPPE_SITE` |
| everything else (`serve`, `install-app`, `--help`, …) | the real `bench` | unchanged |

Recursion is avoided with a `_FRAPPE_BENCH_RAW` env guard the specialized scripts export and
the wrapper checks, so a script's own nested `bench …` calls reach the real CLI — whether you
invoke `bench update` or the underlying `bench-update` directly. Two caveats: redirected
commands follow the frappe-nix scripts' flags, not vanilla bench's (e.g. `bench update`
takes `--pull|--migrate|--build|--node-hashes`, not `--reset`); and interception is
subcommand-first, so `bench --site X migrate` (global option before the subcommand) passes
straight through.

Because those two caveats leave the real `bench update` reachable — `_FRAPPE_BENCH_RAW=1
bench update --reset`, or just `env/bin/bench` — the shell also keeps its first step
working. `bench update` starts with `bench.patches.run()`, which executes every entry in the
`patches.txt` frappe-bench ships that the **bench root's** `patches.txt` does not record as
done. bench deleted the v3/v4 patch modules in 2022 but still lists them, so a bench root
with no record dies immediately on `ModuleNotFoundError: No module named 'bench.patches.v3'`
— and stays dead, because the failed run rewrites the root file as one empty byte. `bench
init` avoids this by copying the shipped list in verbatim; frappe-nix never runs `bench
init`, and the file is gitignored, so `enterShell` reconciles it instead — on every shell
entry, non-destructively, and silently unless it changes something. See
[`lib/bench-patches.nix`](lib/bench-patches.nix) for why *every* patch is recorded as done
rather than only the two that cannot import.

### A stale `uv.lock` is an evaluation error

`apps/*` are git submodules and `uv.lock` is a committed, resolved snapshot of what
they all declare. Move an app to a commit whose `pyproject.toml` gained a dependency
and the two disagree — uv2nix then looks up a name the lock never recorded, and the
bench fails to **evaluate**:

```
error: attribute 'json-repair' missing
at …/uv2nix/build/lib/resolvers.nix:123:23
```

Three things keep that from being a puzzle:

- **`bench-update --pull` re-locks.** It already refreshed `node-offline-hashes.json`
  for every app whose `yarn.lock` moved; it now runs `uv lock` when any app's
  `pyproject.toml` moved, so the pull that causes the drift also resolves it. Commit
  `uv.lock` with the submodule bumps.
- **The error says so.** Before uv2nix resolves anything, frappe-nix audits every
  declared requirement against the set the resolver will index
  ([`lib/lock-audit.nix`](lib/lock-audit.nix)) and names the app, the requirement and
  the fix. Marker-gated and direct-URL requirements are left alone — they can sit
  outside a resolution legitimately, and a false alarm would be worse than the raw
  error it replaces.
- **`nix run .#relock` works when nothing else does.** This class of failure blocks
  evaluation, so the dev shell that carries `uv` is exactly what you cannot open. The
  `relock` app is deliberately outside every other output's dependency graph and
  takes its `uv` from nixpkgs, so it still runs.

### Bench scripts

These back the wrapper and are also callable directly:

| Script | Description |
| --- | --- |
| `provision-site [admin-pass]` | Create `$FRAPPE_SITE` and install every app from `sites/apps.txt`. |
| `bench-update [--pull\|--migrate\|--build\|--node-hashes]` | Submodule-aware replacement for `bench update`; also re-locks the workspace (`uv lock`) and refreshes `node-offline-hashes.json` for the apps whose lock files moved. |
| `bench-migrate` / `bench-build` / `bench-clear-cache` / `bench-console` | Thin `bench` wrappers honoring `$FRAPPE_SITE`. |
| `bench-restore [<sql>\|--at <ts>\|--list]` | Restore from a SQL backup, or from the latest one in the object store. See [Restoring from production](#restoring-from-production). |
| `edit-secret <name>` | Decrypt a secret into `$EDITOR` and re-encrypt it to the declared recipients. |
| `rekey-secrets` | Re-encrypt every secret after changing `recipients`. |
| `check-secrets [<name>]` | Verify the `.age` files match the declared recipients; with a name, explain why *you* cannot decrypt one. |
| `bench-get-app <url\|alias>` | Add an app as a git submodule + register it in the uv workspace. `helpdesk` → `frappe/helpdesk`; `owner/repo` and full URLs also work. |
| `bench-new-app <name>` | Scaffold a new app and register it in the workspace. |
| `update-deps` | Re-lock + sync Python (uv) and Node (yarn) across all apps. |

## Secrets

A bench's credentials — the site encryption key, the database password, the
object-store keys — live in `.age` files encrypted with
[age](https://github.com/FiloSottile/age), committed to the repo, and decrypted
into the dev shell by [agenix-shell](https://github.com/aciceri/agenix-shell).
frappe-nix imports agenix-shell itself, so a consuming flake declares only this:

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
| `secrets/backup-access.age` | env-file | `bench restore`'s fetch |
| `secrets/<site>/encryption-key.age` | one line | `services.frappe`'s `encryptionKeyFile` |
| `secrets/<site>/db-password.age` | one line | `database.passwordFile` |
| `secrets/<site>/site-config.age` | JSON object | `extraConfigFiles` |

The shapes are the ones `services.frappe` already consumes, so the same
ciphertext can serve the deployment: `flake.lib.frappeSecrets` exposes the
paths, which beats keeping a second copy in the server repo that has to be
rotated in lockstep.

`recipients` are the people; `hostRecipients` are deployment hosts, and are
added to the per-site secrets only. A site can set `developers = false` to keep
its secrets host-only — a useful tier, since it lets someone restore the
database without being able to read the credentials stored inside it.

**`.age` files are meant to be committed.** They are ciphertext, and a flake's
source tree is exactly its git-tracked files — an untracked secret is invisible
to the build. `edit-secret` stages new ones for you.

### There is no `secrets.nix`

agenix normally reads a committed rules file listing who may decrypt what.
frappe-nix generates that file into the store instead and points agenix's
`RULES` at it, because a hand-maintained one can be edited without re-encrypting
anything and nothing notices. That is not hypothetical: in the bench this was
built for, a rotated key sat in the rules for months while the ciphertext still
named the key it replaced, and the person it was rotated for could not decrypt
anything.

So the recipient list in `flake.nix` is the only place it is written down, and
`check-secrets` proves the ciphertext agrees:

```
$ check-secrets
secrets/backup-access.age: not encrypted to 1 declared recipient(s):
      KATJVw  bob
    Someone who can still decrypt it must run:  rekey-secrets
```

It works offline and needs no private key — an age header names its recipients
in the clear, and an SSH recipient's tag is derivable from the public key alone.
`check-secrets <name>` turns that around and explains why *your* key cannot open
a particular secret.

After changing `recipients`, run `rekey-secrets` and commit the result.

### Restoring from production

```sh
bench restore                       # the newest backup
bench restore --list                # what is available
bench restore --at 20260814_000042  # a specific one
bench restore --files               # also the public files archive
bench restore ./dump.sql.gz         # an explicit file, no object store
```

With no file, `bench restore` reads the `backup-access` secret, finds the newest
backup folder, downloads the database and the site-config backup, and restores
them — **creating the site first if it does not exist**, so a fresh clone needs
nothing but `direnv allow`, `devenv up`, `bench restore`.

It reads Frappe's own layout: `S3 Backup Settings` writes one folder per backup
named `YYYYMMDD_HHMMSS`, holding the database, a verbatim copy of production's
`site_config.json`, and optionally the two file archives. Downloads are cached
under `$DEVENV_STATE`, keyed by folder — the name is a timestamp, so it is also
the version, and a re-run of the same restore re-downloads nothing.

The credentials are decrypted at the moment they are used, not at shell entry.
agenix-shell re-runs `rage` every time its script is sourced and `enterShell`
runs on every direnv reload, so loading them there would prompt for a
passphrase on every file save — and it exports the plaintext itself, not just a
path, which would put credentials in the environment of every process in the
session, `devenv up`'s children included.

#### The encryption key

The backup folder contains production's `site_config.json` verbatim, which is
how `restore.carryConfigKeys` gets `encryption_key` and `backup_encryption_key`.
Without the first, every stored password and API secret in the dump decrypts to
nothing; the second opens the next encrypted backup. Both are written into the
dev site's `site_config.json` at mode 0600.

It is an allowlist rather than a denylist, because a denylist loses to the next
app that invents `foo_api_secret`. Everything else production had — `host_name`,
`db_*`, `mail_*`, `cloud_storage_settings`, `maintenance_mode` — is left behind.

**This is a real capability, not a formality.** A bench holding that key can
decrypt every stored production credential in the dump: mail passwords, payment
secrets, API tokens. It is what makes a restore a clone rather than a shell, and
it is precisely what [the guard rails](#development-guard-rails) exist to
survive. `bench restore` therefore refuses to write it into a bench with
`devguard.enable = false`; `--no-site-config` restores without it, and the site
still works with its stored credentials opaque.

One consequence worth stating plainly: the backup's site-config copy is **never
encrypted**, even when the database beside it is (`backup_encryption()` covers
the dump and the two archives, not the config). Anyone who can read your backup
bucket can read production's encryption key. Keep the bucket private.

## Production containers

```sh
nix build .#web          # → result is a Docker image tarball
docker load < result     # loads <benchName>/web:latest
```

The images are built from `builtBench` (apps + python env + node + compiled assets),
with no imperative `uv sync` / `yarn install` / `bench build` at container start. Each
process container runs a config-synthesis entrypoint that assembles `site_config.json`
from environment variables and mounted secret files (`/secrets/`). `web` runs gunicorn on
`:8000`, `nginx` reverse-proxies on `:80`, `socketio` runs on `:9000`, and `bench-cli`
is for migrations / one-off commands.

## NixOS module — `services.frappe`

`nixosModules.default` is a standalone NixOS module (not flake-parts) for multi-tenant
production deployment. It takes a **bench package** (the `builtBench` / `packages.default`
from a bench repo) and derives all interpreters from its `passthru` — no separate
`pythonEnv`/`nodejs`/`benchRoot` options.

**Important:** The bench repo exposes only the package. The NixOS module is imported
directly from `frappe-nix`, not re-exported by the bench. A deployment server combines
both:

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
| `frappe-init-<site>` | Oneshot: assembles runtime bench tree, symlinks assets from the package, synthesizes `site_config.json` via `jq` (merging base config + secrets). |
| `frappe-migrate-<site>` | Oneshot: runs `bench migrate` when the build changes. Snapshots the DB first and rolls back on failure (see [Safe migrations](#safe-migrations-on-deploy)). |
| `frappe-web-<site>` | Gunicorn bound to `sites.<name>.web.port`. |
| `frappe-scheduler-<site>` | Background scheduler. |
| `frappe-socketio-<site>` | SocketIO (Node). |
| `frappe-worker-{default,short,long}-<site>` | Background workers (one per queue). |

All service units `after`/`requires` their `frappe-init-<site>`.

### Config synthesis (secrets stay out of the store)

`frappe-init-<site>` writes a base `site_config.json` to the store from Nix-declared
values (db host/port, redis URLs, `extraConfig`), then merges in secrets at activation
time via `jq`:
- `database.passwordFile` → `db_password` key
- `encryptionKeyFile` → `encryption_key` key
- `extraConfigFiles` → deep-merged JSON (for cloud storage creds, etc.)

The final `site_config.json` is written to the site's state directory with mode 0600.

### Key options

**Top-level:**

| Option | Type | Default | Notes |
| --- | --- | --- | --- |
| `package` | package | *(required)* | Default bench package (`builtBench`). Sites inherit this unless overridden. |
| `web.workers` | int | `4` | Gunicorn worker count (shared across sites). |
| `workers` | list of str | `["default" "short" "long"]` | Background worker queues per site. |
| `database.createLocally` | bool | `false` | Aggregate: enable MariaDB if this or any site requests it. |
| `redis.createLocally` | bool | `false` | Enable a local Redis instance. |
| `user` / `group` | str | `"frappe"` | Service user/group. |
| `extraEnv` | attrs of str | `{}` | Extra env vars for all Frappe services. |
| `migrate.enable` | bool | `true` | Run `bench migrate` automatically per site when the build changes. |
| `migrate.snapshot` | bool | `true` | Take a `mysqldump` snapshot before migrating (safety net). |
| `migrate.rollbackOnFailure` | bool | `true` | Restore the snapshot if the migration fails. |
| `migrate.maintenanceMode` | bool | `true` | Toggle maintenance mode around migrate; left on if it fails. |
| `migrate.snapshotRetention` | int | `3` | Snapshots to keep per site under `<siteDir>/snapshots`. |

**Per-site (`services.frappe.sites.<name>`):**

| Option | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Enable this site. |
| `package` | package or null | `null` | Per-site bench package override. |
| `siteDir` | str | `/var/lib/frappe/<name>` | State directory for this site. |
| `web.port` | port | `8000` | Gunicorn listen port (ignored when `web.socketPath` is set). |
| `web.socketPath` | str | `""` | Unix socket for gunicorn; nginx reaches it via a generated upstream. |
| `socketio.port` | port | `9000` | SocketIO listen port (ignored when `socketio.socketPath` is set). |
| `socketio.socketPath` | str | `""` | Unix socket for the realtime server (`socketio_uds`); needs frappe ≥ 15.46. Removes the site's last non-loopback TCP listener. |
| `database.{createLocally,host,port,socket,name,user,passwordFile}` | — | — | Per-site database config. |
| `redis.{cacheUrl,queueUrl,socketioUrl}` | str | `redis://127.0.0.1:13000` | Redis URLs. |
| `encryptionKeyFile` | path or null | `null` | File containing the Frappe encryption key. |
| `extraConfig` | attrs | `{}` | Extra keys merged into base `site_config.json` (no secrets). |
| `extraConfigFiles` | list of path | `[]` | JSON files deep-merged at activation (for secrets). |
| `nginx.enable` | bool | `false` | Create an nginx virtualHost for this site. |

Site creation remains an operational step (run `bench new-site` against the deployed host).

### Safe migrations on deploy

Whenever a new build is deployed (`nixos-rebuild switch`), the `frappe-migrate-<site>`
oneshot runs `bench migrate` for the site. It re-runs only when the build actually
changes — the last migrated build's store path is recorded in
`<siteDir>/.frappe-migrate-build` and re-migration is skipped when it is unchanged.

Because Frappe migrations perform DDL (`CREATE`/`ALTER TABLE`), which auto-commits in
MariaDB and cannot be rolled back in a transaction, the unit wraps the migration in a
physical snapshot instead:

1. **Snapshot** — `mysqldump --single-transaction` of the site DB to
   `<siteDir>/snapshots/premigrate-<site>-<timestamp>.sql.gz` (owner-only, 0600). If the
   snapshot cannot be taken, the migration is aborted (never migrate without a safety net).
2. **Migrate** — `bench --site <name> migrate`, with the site in maintenance mode.
3. **On success** — clear maintenance mode, record the build, prune old snapshots.
4. **On failure** — restore the snapshot (drop all current tables, re-import the dump),
   **leave the site in maintenance mode**, log `MIGRATION FAILED` to the journal, and exit
   non-zero (the unit shows `failed`). The database is returned to its pre-migrate state;
   recover with a fixed forward deploy or `nixos-rebuild switch --rollback`.

It runs as the `frappe` user with the site's own DB credentials (no DB-root needed), so it
works for both locally-created and externally-managed databases. Tune or disable it via the
`services.frappe.migrate.*` options above (e.g. `migrate.snapshot = false` for very large
databases where a snapshot per deploy is too costly).

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
| `bench build`                  | `builtBench` runs `bench build` in the sandbox |
| edits `apps/*` source         | `benchRoot` / `builtBench` copies the source tree |

Commit `uv.lock` and each app's `yarn.lock`; the production env, node_modules, compiled
assets, containers, and NixOS deployment are all rebuilt from them.

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
│   ├── bench-patches.nix     # keeps `bench update` past bench's own patch list
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
│   │   ├── migrate.sh        #   migrate mode
│   │   └── main.sh           #   flags + mode dispatch (must be concatenated last)
│   ├── frappe-workspace.py   # apps/ ⇄ pyproject.toml ⇄ apps.txt reconciler (tomlkit)
│   ├── frappe-presets.json   # frappe version → python / node / branch matrix
│   └── scripts.nix           # portable bench shell scripts
├── templates/bench/          # what a new bench is laid down from
├── tests/                    # flake checks (see `nix flake check`)
└── modules/
    ├── flake-module.nix      # imports devenv.flakeModule + devenv.nix + containers.nix
    ├── devenv.nix            # perSystem.frappe-nix options + dev shell + packages
    ├── containers.nix        # OCI image builds
    └── nixos.nix             # services.frappe (NixOS systemd module)
```

`lib/sh/*.sh` are concatenated into a single `writeShellApplication`, so shellcheck sees the
whole program at build time; `main.sh` holds the only top-level code and must stay last.
