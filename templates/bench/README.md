# @BENCH_NAME@

A [Frappe](https://frappeframework.com/) bench scaffolded with
[frappe-nix](https://github.com/Avunu/frappe-nix). Apps live under `apps/` as git
submodules; the whole dev + production environment is declared in [flake.nix](flake.nix).

## Getting started

Prerequisites: [Nix](https://nixos.org/download.html) (flakes enabled) and
[direnv](https://direnv.net/).

```sh
direnv allow                 # or: nix develop --no-pure-eval
devenv up                    # MariaDB, Redis, web, scheduler, worker, socketio, …
provision-site               # (first run, another shell) create the site + install apps
# → http://localhost:8000
```

## Day-to-day

`bench` is wrapped so the normal commands Just Work in this environment:

- `bench update` — pull apps, refresh node hashes, migrate, build
- `bench get-app <url|alias>` — add an app (git submodule + uv workspace)
- `bench new-app <name>` — scaffold a new app
- `bench migrate` / `bench build` / `bench console` — as usual

See the [frappe-nix README](https://github.com/Avunu/frappe-nix) for the full list and for
building production OCI images / the NixOS service module.
