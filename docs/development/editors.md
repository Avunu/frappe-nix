---
title: Editors and language servers
description: What frappe-nix.ide links into the workspace so an editor resolves a bench's imports, how to set up VS Code and LSP editors, and where ruff, ty and the TypeScript server fit.
order: 11
tags: [ide, editor, vscode, lsp, pyright, devenv]
updated: 2026-10-21
---

An editor needs two things from a bench to resolve its imports: an interpreter, and the path to every app. The interpreter is the Nix-built dev virtualenv. The path to the apps is the harder part, and `frappe-nix.ide` exists for it.

The dev virtualenv installs each app **editable** (see [Editable installs](README.md#editable-installs)), through a `.pth` file whose one line is

```python
import sys; import os.path; sys.path.append(os.path.expandvars(("$FRAPPE_BENCH_ROOT/apps/frappe")))
```

A running interpreter executes that line, so `bench` and the runtime find every app. A static analyser does not: Pylance, pyright and basedpyright read the lines of a `.pth` file that are plain paths and skip `import` lines. Without help, `import frappe` is unresolved in every app of the bench.

## What the shell writes

With `ide.enable` (the default), every shell entry keeps two symlinks at the workspace root. That is the bench root, or the app's repository in [app mode](../scaffolding/app-mode.md), which is the directory you open in the editor.

| Link | Points to | For |
| --- | --- | --- |
| `./.venv` | the dev virtualenv, the same one `env` links to | the interpreter. `.venv` is the one name every editor and ty look for without configuration. |
| `./pyrightconfig.json` | a generated file in the Nix store | Pylance, pyright and basedpyright: the `.venv` interpreter and every app's root as an `extraPaths` entry. |

The generated `pyrightconfig.json` for a bench with erpnext and hrms looks like this:

```json
{
  "venvPath": ".",
  "venv": ".venv",
  "pythonVersion": "3.14",
  "extraPaths": ["apps/erpnext", "apps/frappe", "apps/hrms"],
  "typeCheckingMode": "off",
  "exclude": ["**/node_modules", "**/__pycache__", "**/.*", "env", "sites", "logs"]
}
```

-   **`extraPaths` is the shell's `PYTHONPATH`**, relative to the workspace root and in the same order: every directory under `apps/`. In app mode the siblings are under `.frappe-nix/bench/apps/` (`app.benchDir`), and the app you are developing is left out, because it is the workspace root, which the analyser searches first.
-   **It is refreshed on every shell entry.** An app added with `bench-get-app`, or a sibling added to `app.siblings`, is in the file the next time the shell loads. That is why it is a symlink into the store rather than a file you edit, and why it is not in `.vscode/settings.json`, which is seeded once and is yours after that.
-   **`typeCheckingMode` is `"off"`**, as in Pylance by default. Frappe's own code is not typed, so the stricter modes report thousands of errors in it. Navigation, completion, hover and unresolved imports still work. Set your own with `ide.pyrightSettings`.
-   `nix build .#pyrightConfig` builds the same file, if you want it somewhere else.

Neither link is meant to be committed: each points at a store path that changes with the bench. A link the repository does not already ignore is added to `.git/info/exclude`, the repository's local ignore list, which is never committed. So the links do not show in `git status`, and no `.gitignore` has to change. The bench template's `.gitignore` already ignores `.venv`.

A real file or directory at either path is yours, and the shell leaves it alone with a one-line note. If you commit your own `pyrightconfig.json`, it replaces the generated one entirely, `extraPaths` included: list the apps in it yourself, or delete it and set what you need with `ide.pyrightSettings`.

## VS Code

With `ide.vscodeSettings` (the default), the first shell entry seeds two files when they do not exist yet, and never overwrites either:

-   `.vscode/settings.json` sets `python.defaultInterpreterPath` to `${workspaceFolder}/.venv/bin/python`, and keeps the file watcher off `.devenv`, `.direnv`, `.frappe-nix` and `node_modules`.
-   `.vscode/extensions.json` recommends the Python extension (`ms-python.python`, which brings Pylance) and ruff's (`charliermarsh.ruff`).

Pylance reads `pyrightconfig.json` itself, and a `pyrightconfig.json` wins over the `python.analysis.*` settings it also covers, such as `extraPaths` and `typeCheckingMode`. Change those through `ide.pyrightSettings`, not in `settings.json`.

Open the workspace root (the bench, or the app's repository), not a single app. If VS Code picked another interpreter before `.venv` existed, run **Python: Select Interpreter** and choose `./.venv/bin/python`. VS Code started outside the dev shell does not have its environment. That matters for terminals and tasks, not for Pylance, which reads only the two files above. The `mkhl.direnv` extension loads the shell's environment into VS Code, if you want it there.

## Neovim, Helix, Zed and other LSP editors

Every pyright-based server reads `pyrightconfig.json` from the workspace root, so a server started there resolves the bench's imports with no further setup. To put one on the dev shell's `PATH`, pinned with the rest of the bench:

```nix
frappe-nix.ide.languageServer.enable = true; # basedpyright
# or another pyright-compatible server:
frappe-nix.ide.languageServer.package = pkgs.pyright;
```

The server is `basedpyright-langserver --stdio` (or `pyright-langserver --stdio`). Start the editor from inside the shell, or through direnv, so it finds that binary. It is off by default, as devenv's TypeScript server is in frappe-nix: an editor's server should not be something that keeps a shell from building.

## ruff and ty

**ruff** needs no interpreter or search paths. An app that has opted in to the [app standards](../app-standards/README.md) pins it in `tools/`, and the ruff extension or `ruff server` reads the app's `pyproject.toml`. For a bench, add `pkgs.ruff` to `extraDevPackages`.

**ty** does not read `pyrightconfig.json`. It finds `.venv` on its own, and it adds `PYTHONPATH` to its search paths, which the dev shell sets to every app's root. So `ty check` run in the shell resolves the bench's imports, and so does an editor's ty server started from the shell or through direnv. Started without the shell's environment, ty finds the interpreter but not the apps. The app standards' `typecheck:py` script passes `--python` explicitly for the same reason.

## TypeScript and JavaScript

VS Code's built-in TypeScript support needs nothing from the shell. To use Frappe's own TypeScript version rather than VS Code's, set `"js/ts.tsdk.path": "${workspaceFolder}/apps/frappe/node_modules/typescript/lib"`, which exists once `node_modules` is installed.

devenv's `typescript-language-server` is off in frappe-nix (`languages.javascript.lsp.enable = lib.mkDefault false`), because a nixpkgs revision that cannot build it fails every uncached shell. Turn it on in your `perSystem` if your editor needs it from `PATH`:

```nix
devenv.shells.default.languages.javascript.lsp.enable = true;
```

## Options

| Option | Default | Effect |
| --- | --- | --- |
| `ide.enable` | `true` | Keep `./.venv` and `./pyrightconfig.json` linked at the workspace root. |
| `ide.pyrightSettings` | `{ }` | Keys merged over the generated `pyrightconfig.json`, for example `{ typeCheckingMode = "basic"; }`. |
| `ide.vscodeSettings` | `true` | Seed `.vscode/settings.json` and `.vscode/extensions.json` once. Needs `ide.enable`. |
| `ide.languageServer.enable` | `false` | Put the language server on the dev shell's `PATH`. Needs `ide.enable`. |
| `ide.languageServer.package` | `pkgs.basedpyright` | The server, a pyright-compatible one. |

The same tree is in odoo-nix, where `ide.languageServer` is odoo-ls.
