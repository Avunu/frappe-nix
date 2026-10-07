---
title: Scaffolder
description: The flags and exit codes of frappe-init, the program behind nix run github:Avunu/frappe-nix that creates, migrates and sets up benches and app repositories.
order: 4
tags: [reference, frappe-init, cli, scaffolding]
updated: 2026-10-07
---

`nix run github:Avunu/frappe-nix` runs `frappe-init`, the scaffolder. The flags below follow `frappe-init --help`. Pass them after `--`:

```bash
nix run github:Avunu/frappe-nix -- --dry-run
```

With a terminal and no flags it prompts for what it needs, using [gum](https://github.com/charmbracelet/gum). It never prompts when stdin or stdout is not a terminal, so scripted runs must pass what they need.

```text
Usage: frappe-init [options] [target-dir]
```

## Mode

| Flag          | Meaning                                                                                                    |
| ------------- | ---------------------------------------------------------------------------------------------------------- |
| `--init`      | Force scaffold mode. Needs an empty directory unless you add `--force`.                                    |
| `--migrate`   | Force migration mode. Also re-syncs a frappe-nix bench.                                                    |
| `--app`       | Force app mode: this repository is one Frappe app, and the bench around it is generated from flake inputs. |
| `--sync`      | App mode: write the app's [managed files](../app-standards/managed-files.md) (`frappe-nix sync --write`). The app must have opted in, or pass `--standards`. |
| `--check`     | App mode: report drift in the managed files and change nothing (`frappe-nix sync --check`).                |
| `--force`     | Relax the mode guard. It never overwrites an existing file.                                                |
| `--dry-run`   | Print the plan and exit without changing anything.                                                         |
| `-y`, `--yes` | Assume yes. Required to migrate without a terminal.                                                        |

Without a mode flag, the mode is detected from the target directory, which is the current directory when it is a bench or a Frappe app. See [Choose a mode](../scaffolding/README.md#how-the-mode-is-picked).

## Common

| Flag                   | Meaning                                                                        |
| ---------------------- | ------------------------------------------------------------------------------ |
| `--frappe-version <V>` | `develop`, `version-16` or `version-15`. Overrides detection.                  |
| `--name <NAME>`        | The bench name. Defaults to the existing name, or the target directory's name. |
| `--site <SITE>`        | The default site. Defaults to the existing `default_site`.                     |
| `--skip-lock`          | Do not run `uv lock`.                                                          |
| `-h`, `--help`         | Show the help.                                                                 |

New benches default to the site `frappe.localhost`. App mode defaults to `<BENCH_NAME>.localhost`.

## App standards

These apply in app mode, to an app that has opted in to the [app standards](../app-standards/README.md) or opts in now.

| Flag                     | Meaning                                                                                                   |
| ------------------------ | --------------------------------------------------------------------------------------------------------- |
| `--standards <PROFILE>`  | Opt in with `--app` or `--sync`: `minimal`, `recommended`, `recommended@<minor>`, an org profile's flake URL or `./<dir>`. |
| `--profile-path <DIR>`   | Read the org profile from a local checkout instead of its locked input (profile authors).                 |
| `--only <PATH,…>`        | Limit `--sync`, `--check` or an opted-in `--app` to these managed files.                                  |
| `--init-listing`         | `--sync`: also seed `marketplace/listing.toml`.                                                           |
| `--format <F>`           | `--check`: `text`, `json` or `github`.                                                                    |
| `--expect-rev <SHA>`     | `--check`: the frappe-nix revision frappe-nix-tools was installed from.                                   |

With `--sync`, `--force` also replaces an unmanaged `flake.nix` or `.envrc` on first opt-in, after you have reviewed the diff sync prints. The exit codes of `--sync` and `--check` are `frappe-nix sync`'s: 0 clean, 1 drift, 2 invalid configuration (including an app that has not opted in), 3 environment. A usage error under them is 2, never 1. Without `--sync` or `--check`, `--format` and `--expect-rev` are unknown flags, and the others are refused (exit 1, nothing written) outside app mode or, except `--standards`, on an app that has not opted in. See [Managed files](../app-standards/managed-files.md).

## Scaffold only

| Flag             | Meaning                                        |
| ---------------- | ---------------------------------------------- |
| `--apps <A,B,C>` | Apps to add: names, `owner/repo`, or git URLs. |

## Migration only

| Flag                      | Meaning                                                                    |
| ------------------------- | -------------------------------------------------------------------------- |
| `--vendor <A,B>`          | Force these apps to be vendored, with their source committed to the bench. |
| `--no-vendor`             | Abort instead of vendoring an app with no usable remote.                   |
| `--allow-file-remotes`    | Accept filesystem paths as submodule URLs.                                 |
| `--legacy-apps <POLICY>`  | `shim`, `skip` or `abort`, for apps with only a `setup.py`.                |
| `--strict`                | Treat dirty or unpushed apps as errors.                                    |
| `--absorb-gitdirs`        | Run `git submodule absorbgitdirs` after registering the apps.              |
| `--keep-db-root-password` | Do not blank `mariadb_root_password` before committing.                    |
| `--commit[=<MESSAGE>]`    | Commit the result instead of only staging it.                              |

See [Migrate an existing bench](../scaffolding/migrate.md) for what each does.

## Exit codes

A scripted run can tell why it stopped from the exit code.

| Code | Meaning                                                                                                                                                |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `0`  | Done, or `--dry-run` finished.                                                                                                                         |
| `1`  | A forced `--migrate` or `--app` found an empty or missing directory, or another error with no more specific code.                                      |
| `2`  | `--init` into a directory that is not empty, without `--force`.                                                                                        |
| `4`  | A `flake.nix` that is not a frappe-nix wrapper is in the way. `--migrate --force` writes the wrapper to `flake.nix.frappe-nix` to merge.               |
| `5`  | The Frappe version is missing, unknown, undetectable or unsupported (below `version-15`), or the bench has several sites and no `default_site`.        |
| `6`  | The directory is not the shape the mode needs (not a bench, not a Frappe app, or not a git repository for app mode), or you declined the confirmation. |
| `7`  | A bench skeleton has no app in it, so there is nothing to migrate.                                                                                     |
| `8`  | The bench is inside another git repository. `--force` creates a nested one anyway.                                                                     |
