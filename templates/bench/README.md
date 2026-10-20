# @BENCH_NAME@

A [Frappe](https://frappeframework.com/) bench scaffolded with
[frappe-nix](https://github.com/Avunu/frappe-nix). Apps live under `apps/` as git
submodules; the whole dev + production environment is declared in [flake.nix](flake.nix).

## Getting started

Prerequisites: [Nix](https://nixos.org/download.html) (flakes enabled) and
[direnv](https://direnv.net/).

```sh
direnv allow                 # checks out a fresh clone's apps, then loads the dev shell
devenv up                    # MariaDB, Redis, web, scheduler, worker, socketio, …
provision-site               # (first run, another shell) create the site + install apps
# → http://localhost:8000
```

Nix reads `apps/*` while it evaluates `flake.nix`, so a fresh clone needs them
checked out first; `.envrc` does that with frappe-nix's own tool, as partial
clones. Without direnv, run `nix run github:Avunu/frappe-nix/release-1#bootstrap`
before `nix develop --no-pure-eval`.

## Day-to-day

`bench` is wrapped so the normal commands Just Work in this environment:

- `bench update` — pull apps, re-lock what moved, migrate, build (an app with local edits git won't overwrite is skipped and named, not fatal; an uncommitted `yarn.lock` is discarded when the pull would overwrite it)
- `bench get-app <url|alias>` — add an app (git submodule + uv workspace)
- `bench new-app <name>` — scaffold a new app
- `bench migrate` / `bench build` / `bench console` — as usual

See the [frappe-nix README](https://github.com/Avunu/frappe-nix) for the full list and for
building production OCI images / the NixOS service module.
