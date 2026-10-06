---
title: Write the flake by hand
description: The shape of a frappe-nix bench flake, the inputs it needs, and the handful of options you will change most often.
order: 4
tags: [flake, flake-parts, configuration, options]
updated: 2026-10-06
---

A complete consuming flake is just a configured module. The scaffolder writes one for you, and this page explains what is in it so you can edit it or write your own.

Because `frappe-nix.lib.mkFlake` merges frappe-nix's own inputs (nixpkgs, devenv, uv2nix and the rest) into yours, you do not re-declare them. You declare `frappe-nix`, and a `nixpkgs` that follows it.

## A bench flake

This is the **bench** shape: a repository that holds `apps/*`. For a single app's own repository see [Develop a single app](app-mode.md). The module is the same, but `workspaceRoot` is replaced by `app.*` and frappe-nix builds the workspace itself.

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

Then enter the shell and start the stack:

```bash
direnv allow            # or: nix develop --no-pure-eval
devenv up               # start MariaDB, Redis, the Frappe runtime, watch, …
provision-site          # (first run, in another shell) create the site + install apps
# → http://localhost:<PORT>
```

The `.envrc` is a single line, `use flake . --no-pure-eval`. [Avunu/frappe-devenv](https://github.com/Avunu/frappe-devenv) is the reference bench: a working frappe, erpnext and hrms bench wired up exactly this way.

> [!NOTE]
> The scaffolder's flake also declares `nixConfig` with the `devenv.cachix.org` substituter and its public key, which is why Nix may ask you to trust the cache the first time.

## Options you will touch most

| Option                         | Default                            | Purpose                                                                                            |
| ------------------------------ | ---------------------------------- | -------------------------------------------------------------------------------------------------- |
| `benchName`                    | required                           | Identifier for environment names and the container image prefix. It also seeds the port offset.    |
| `siteName`                     | `""`                               | Sets `FRAPPE_SITE`. Leave empty for a multi-tenant bench.                                          |
| `workspaceRoot`                | `null`                             | The bench root, where `pyproject.toml` and `apps/` live. Required in bench mode.                   |
| `python`, `nodejs`             | `pkgs.python312`, `pkgs.nodejs_22` | Interpreters. In app mode the `frappeVersion` preset chooses them.                                 |
| `mariadb.initialDatabases`     | `[]`                               | Databases created on the first `devenv up`.                                                        |
| `watch.apps`                   | `null`                             | Apps the watcher rebuilds. `null` skips apps published by Frappe Technologies. `[ ]` turns it off. |
| `mariadb.durable`              | `false`                            | Trades crash durability for much faster commits.                                                   |
| `ports.base`                   | `null`                             | First port to try. Defaults to 8000 plus a hash of `benchName`.                                    |
| `sockets.enable`               | `true`                             | Unix sockets behind one nginx port. Needs Frappe 15.46 or newer.                                   |
| `runtime.enable`               | `true`                             | One `frappe-runtime` process instead of split web, socket.io, worker and scheduler processes.      |
| `containers.enable`            | `false`                            | Build the OCI images. See [Build production images](../production/images.md).                      |
| `extraEnv`, `extraDevPackages` | `{}`, `[]`                         | Extra environment variables and packages in the shell.                                             |

Every option, with its type, is in the [dev shell options reference](../reference/dev-shell-options.md).

## Outputs

When `frappe-nix.enable` is set, the flake gains these `packages`, which you build with `nix build .#<name>`:

| Package                 | What it is                                                                        |
| ----------------------- | --------------------------------------------------------------------------------- |
| `default`, `builtBench` | The production-ready bench: apps, Python environment, Node and compiled assets.   |
| `prodPythonEnv`         | The production virtualenv: workspace apps and runtime dependencies, no dev tools. |
| `devPythonEnv`          | The development virtualenv: adds dev groups and editable installs of `apps/*`.    |
| `benchRoot`             | The unbuilt `/bench` tree. Used by the dev path and as the input to `builtBench`. |

It also gains the `relock` app, run with `nix run .#relock`. See [Flake outputs](../reference/README.md#flake-outputs) for the full list, including the images that `containers.enable` adds.
