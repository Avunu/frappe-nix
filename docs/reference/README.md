---
title: Reference
description: The flake outputs, library functions and option references of frappe-nix, with links to the scaffolder, the scripts and the repository layout.
nav_title: Reference
order: 6
tags: [reference, flake, outputs, library]
updated: 2026-10-06
---

This section lists what frappe-nix exposes. For guides, start from the [overview](../README.md).

- [Dev shell options](dev-shell-options.md): `perSystem.frappe-nix`.
- [Secrets options](secrets-options.md): the top-level `frappe-nix.secrets`.
- [`services.frappe` options](nixos-options.md): the NixOS module.
- [Scaffolder](scaffolder.md): the flags of `nix run github:Avunu/frappe-nix`.
- [Repository layout](layout.md), and the checks that run in CI.
- [FAQ and troubleshooting](troubleshooting.md): the errors you are most likely to meet, with their fixes.
- The scripts in the dev shell are listed in [Everyday commands](../development/commands.md).

## Flake outputs

The `frappe-nix` flake itself provides:

| Output                                     | Purpose                                                                                                                                           |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `flakeModules.default`                     | The flake-parts module. Import it and configure `perSystem.frappe-nix`.                                                                           |
| `nixosModules.default`                     | The standalone NixOS module that exposes `services.frappe`, for multi-tenant production with systemd.                                             |
| `lib.mkFlake`                              | A `flake-parts.lib.mkFlake` wrapper that merges frappe-nix's inputs into the consumer's.                                                          |
| `lib.overrides`                            | Composable Python package overrides for native dependencies.                                                                                      |
| `packages.<system>.default`, `frappe-init` | The scaffolder, run by `nix run github:Avunu/frappe-nix`.                                                                                         |
| `packages.<system>.backup-fetch`           | The object-store half of `bench restore`, standalone, for a production host that restores too. It prints a JSON manifest and touches no database. |
| `apps.<system>.default`, `frappe-init`     | The scaffolder as a flake app.                                                                                                                    |

In a consuming bench that declares [secrets](../development/secrets.md), the bench flake also exposes `lib.frappeSecrets`: the declared `.age` paths and recipients, so a deployment can read the same ciphertext.

### What the module adds to your flake

When `frappe-nix.enable` is set, the module adds these **packages** to your flake, which you build with `nix build .#<name>`:

| Package                 | What it is                                                                                                                                          |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default`, `builtBench` | The production-ready bench: apps, Python environment, Node and compiled assets. The deployable consumed by the NixOS module and the OCI containers. |
| `prodPythonEnv`         | The production virtualenv: workspace apps and runtime dependencies, no dev tools.                                                                   |
| `devPythonEnv`          | The development virtualenv: adds dev groups and editable installs of `apps/*`.                                                                      |
| `benchRoot`             | The unbuilt `/bench` tree (apps, `node_modules`, Python environment, site and config). Used by the dev path and as input to `builtBench`.           |

and one **app**, run with `nix run .#<name>`:

| App      | What it does                                                                                                                                                                                                                                                                                                           |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `relock` | In a bench, `uv lock` in the bench root, from a `uv` that does not come from the workspace. In app mode it assembles the workspace itself and writes `nix/uv.lock`, and `nix/node-locks/` for pins without a `yarn.lock`. See [Dependencies and locks](../development/locks.md#a-stale-uvlock-is-an-evaluation-error). |

The `builtBench` package exposes `passthru.{pythonEnv, nodejs, appsPath, appNames}`, so the NixOS module and the containers can discover interpreters from the package itself.

With `containers.enable = true` it additionally builds, named `<benchName>/<name>:latest`:

- `runtime`, `nginx` and `bench-cli` (image name `bench`), or
- with `runtime.enable = false`: `web`, `scheduler`, `worker-default`, `worker-short`, `worker-long`, `socketio`, `nginx` and `bench-cli`.

See [Build production images](../production/images.md).

## Library

### `lib.mkFlake`

```nix
frappe-nix.lib.mkFlake { inherit inputs; } flakeConfig
```

`lib.mkFlake` calls `flake-parts.lib.mkFlake` with `inputs = frappe-nix.inputs // yourInputs`, so the modules resolve `nixpkgs`, `devenv`, `pyproject-nix`, `uv2nix`, `pyproject-build-systems` and `nix2container` from frappe-nix's pins. Your wrapper only needs to declare `frappe-nix`, and `nixpkgs.follows` for the `perSystem` `pkgs`.

### `lib.overrides`

Composable overlays for Python packages that need native libraries: `mysqlclient`, `pycups`, `python-ldap`, `pyvips` and `cairocffi`. `mysqlclient` is wired in automatically from `mariadb.package`. Add the others through `pythonOverrides`:

```nix
pythonOverrides = lib.composeManyExtensions [
  (frappe-nix.lib.overrides.pycups { inherit pkgs; })
  (frappe-nix.lib.overrides.python-ldap { inherit pkgs; })
];
```

Pure-Python build dependencies (setuptools and the like) belong in `pyproject.toml` under `[tool.uv.extra-build-dependencies]`, so uv2nix handles them. These overlays are only for packages that need C headers or system libraries.
