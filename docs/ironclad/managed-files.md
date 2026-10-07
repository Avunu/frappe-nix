---
title: Managed files
description: "The files frappe-nix keeps in every app, the [tool.ironclad] parameters, and frappe-init --sync and --check."
order: 1
tags: [ironclad, sync, managed-files, tool-ironclad]
updated: 2026-10-07
---

Every Avunu Frappe app carries the same tool configs: the flake, the git hooks, the formatter and linter settings, the TypeScript projects, the release-please files and the tool locks. frappe-nix writes them and keeps them in step, so an app never hand-maintains a copy that drifts from the others. The [interface spec](spec.md) (§2, §3) is the contract this page summarises.

## The two commands

```sh
nix run .#frappe-init -- --sync     # write the managed files (ironclad sync --write)
nix run .#frappe-init -- --check    # report drift and change nothing (ironclad sync --check)
```

Both run in the app's repository: a git work tree whose `pyproject.toml` `[project].name` names a package with a `hooks.py`. `frappe-init` hands both to `ironclad sync`, which is on `PATH` in the app's dev shell too.

`--sync` stages what it writes with `git add` and commits nothing. It takes `--dry-run` (print the diff and the commands), `--skip-lock` (don't relock the bench), `--only <path>[,<path>…]`, `--init-listing` and `--frappe-version version-<N>`. `ironclad sync --write --offline` (or `IRONCLAD_OFFLINE=1`) runs no `nix`, `uv` or `yarn` command and reports the locks it left alone.

`--check` runs nothing at all, so it works in a CI job without Nix. It takes `--format text|json|github`, `--only` and `--expect-rev <sha>`.

| Exit | Meaning |
|---|---|
| 0 | Clean: nothing to write, or everything written. |
| 1 | Drift: a managed file is missing, differs or should not exist; a retired file is tracked; a lock is below its floor. Also a rule only the app can fix, such as code in `__init__.py` outside the version block. |
| 2 | Invalid configuration sync can't fix: a `[tool.ironclad]` schema error, an unknown sibling, a forbidden key, a malformed local region. |
| 3 | Environment: not an app, not a git repository, an unreadable lock, version skew, or a crash. |

### A first sync (bootstrap)

An app with no flake, or one whose flake still follows frappe-nix `main`, is brought in with:

```sh
nix run github:Avunu/frappe-nix/release-1#frappe-init -- --app --sync --frappe-version version-16
```

It needs only `nix` and `git`. Sync runs in two phases. **Phase A** creates `[tool.ironclad]` when it's missing (the major from `--frappe-version` or the existing flake's `frappeVersion`, the siblings from `hooks.required_apps` plus the flake's), writes `flake.nix` and `.envrc`, and locks the flake on `release-1`. When the lock now pins a different frappe-nix release than the one running, sync hands over to `nix run .#frappe-init -- --sync` once, so **phase B** always renders with the tools the lock pins. Phase B renders every other file, deletes the retired ones, and brings the locks up: `uv lock --project tools`, `yarn install`, the node-lock seeds, `nix run .#relock`, and the README blocks.

`frappe-init --app` on a new app copies `.envrc` and the `.gitignore` block from frappe-nix's app template and then runs `ironclad sync --write`, so a new app starts in sync.

## What is managed

| Path | Strategy | When |
|---|---|---|
| `flake.nix` | whole | always |
| `.envrc` | whole | always |
| `.gitignore` | block (`# >>> frappe-nix >>>`) | always |
| `.editorconfig` | whole, local region `editorconfig` | always |
| `.pre-commit-config.yaml` | whole, local region `repos`; third-party `rev:`s are floors | always |
| `committed.toml` | whole | always |
| `tools/pyproject.toml` | whole | always |
| `tools/uv.lock` | seed (`uv lock --project tools`); versions are floors | always |
| `pyproject.toml` | toml-merge | always |
| `<app>/__init__.py` | block (`x-release-please`) | always |
| `package.json` | json-merge; `devDependencies` are floors | always |
| `yarn.lock` | seed (`yarn install`) | always |
| `.oxlintrc.json` | whole, no header (JSON) | always |
| `.oxfmtrc.jsonc` | whole | always |
| `.stylelintrc.json` | json-merge | the app has SCSS |
| `tsconfig.json`, or `tsconfig.ironclad.json` when an SPA owns `tsconfig.json` | whole | any TypeScript project |
| `tsconfig.base.json` | whole | browser, scripts or test project |
| `tsconfig.browser.json`, `.scripts.json`, `.test.json`, `.desk.json`, `.web.json` | whole | that project has files |
| `release-please-config.json` | whole, no header (JSON) | always |
| `.release-please-manifest.json` | seed `{".": "<__version__>"}` | always |
| `.git-blame-ignore-revs` | seed, then validated | always |
| `nix/node-locks/<key>/` | seeded from frappe-nix for frappe, erpnext and hrms | that app is on the bench and has no lock |

The CI caller workflows, `.github/dependabot.yml` and `.github/zizmor.yml` ([CI](ci.md)), `scripts/ironclad-vite-register.mjs` ([App assets](assets.md)), and the README blocks, `marketplace/shots.d.ts` and the semgrep baseline ([Marketplace](marketplace.md)) are managed the same way, by the same engine.

### Strategies

- **whole**: the file is the rendered template, compared byte for byte. It starts with a header such as ``# ironclad:managed — generated by frappe-nix (`frappe-init --sync`); do not edit.``; plain JSON files carry none. Edit it only inside its local regions, or through `[tool.ironclad]`.
- **toml-merge** and **json-merge**: only the managed keys are sync's; every other key, comment and order is the app's. `--check` compares parsed values, so a formatter's reflow is never drift.
- **block**: only the text between the markers is sync's.
- **seed**: written once when missing, then the app's (and validated).

**Local regions** sit between `# ironclad:local-begin <name>` and `# ironclad:local-end <name>`. Sync keeps their content as it is. A region may add, never redefine: a hook id, a dependabot `updates` entry or an EditorConfig section that sync already manages is exit 2, as is an unknown, duplicate or unclosed region.

**Floors** are versions dependabot moves. Sync never lowers one: a pre-commit `rev:` at or above the floor is kept, a `devDependencies` caret or exact range whose minimum is at or above the floor is kept, and a `tools/uv.lock` version below its floor is drift that `uv lock --upgrade-package` fixes. A `devDependencies` entry that names a tarball, a git ref or a path has no minimum to compare and is left as the app wrote it.

### `pyproject.toml`

Sync owns `requires-python`, `dynamic` (it must contain `version`), `[build-system]`, `[tool.bench.frappe-dependencies]` (exactly frappe plus `required_apps`, with the known ranges), the ruff profile (`[tool.ruff]`, `.lint`, `.format`), `[tool.ty.environment]`, `.src` and `.terminal`, `[tool.coverage.run] omit` (plus each `coverage-omit` glob), the `[tool.coverage.report]` settings, and `[tool.vulture] exclude`. A missing `fail_under` is seeded as 0; from then on it is the app's. A table sync adds goes after the last `[tool.*]` table.

Refused (exit 2): extra `[tool.ruff.lint] ignore` codes, `extend-select`, `extend-ignore`, `unfixable`, `isort`; F401 or E402 in a package-wide `per-file-ignores`; `[tool.ty.rules]` other than `"error"`, `[tool.ty.overrides]`; `[tool.coverage.run] source` or `relative_files`; `[tool.poetry]`. `[project].dependencies` naming frappe, erpnext, hrms or payments is exit 1.

### `package.json`

Sync owns `private`, `type`, `license`, `engines.node`, `packageManager`, `frappe` (`{major, branch}`), the scripts `format`, `format:check`, `lint`, `typecheck`, `test:unit`, `lint:py`, `typecheck:py` and `check`, and the toolchain floors: oxlint and oxfmt always; typescript, frappe-types and `@types/node` with any TypeScript or desk JavaScript; stylelint with SCSS. A Vite app's `build` must end with `node scripts/ironclad-vite-register.mjs`, which sync appends. Refused: a `build` script with nothing to build (no Vite config, nested frontend, SPA entry or `build = true`), eslint or prettier packages, and `ironclad:` script names.

## `[tool.ironclad]`

The app's parameters live in its `pyproject.toml`. Sync creates the table once and never rewrites it; the schema is `ironclad data-path schema/tool-ironclad.schema.json`, and an unknown key is exit 2. The spec (§2.1) documents every key. The ones that change what sync renders:

- `frappe-major` and `siblings`: the flake inputs, the dependency ranges, the node-lock seeds and the desk globals;
- `site`, `pilot-assets`, `track-overrides`, `js-coverage-min`, `build`;
- `generated`: globs of tracked generated files, kept out of prek, `validate_copyright`, oxfmt and oxlint;
- `[tool.ironclad.typescript]`: `browser`, `browser-include`, `web-include`, `paths`, `exclude` (never a source file under the package), and `[[…spa]]` for a Vue/Vite SPA that has no `package.json` of its own;
- `[[tool.ironclad.unchecked-js]]`: desk and web scripts left out of strict checkJs while they are typed. `ironclad unchecked-js --stale` fails on an entry whose file now type-checks;
- `[tool.ironclad.oxlint]`, `[tool.ironclad.oxfmt]`, `[tool.ironclad.stylelint]`.

What can be read from the tracked tree is never declared: the desk and web script sets, the TypeScript projects, Vite, SCSS, nested frontends, `docs-site/`, submodules and the marketplace listing are discovered from `git ls-files`, so a first `.ts` file under `scripts/` is drift until sync renders `tsconfig.scripts.json`.

## Retired files

Sync deletes, and `--check` reports as `legacy file`: a second release, auto-merge or dependabot workflow beside the managed callers (`release-please-action`, `gh pr merge --auto`, `dependabot/fetch-metadata`; docusystem's `docs*.yml` excepted), `.github/workflows/check.yml` and `version-branch-guard.yml`, `.oxfmtrc.json`, eslint and prettier configs, `.flake8`, a flake8-only `setup.cfg`, `MANIFEST.in`, `requirements.txt` anywhere, `nix/node-offline-hashes.json` and `update-assets.mjs`.

## `ironclad compat`

The prek hook every app runs checks that versions, ranges and majors agree: the `package.json` major against the Frappe major (equal once a `v<major>.*` tag exists or on a release branch), the `frappe` stanza, the flake's `version-<major>` refs, the dependency ranges, `__version__` = `package.json` = the release-please manifest, `required_apps` ⊆ siblings, no hand-written frappe globals (augmentations only in `types/<app>.augment.d.ts`, each member marked `// app-owned: <reason>`), a valid `.git-blame-ignore-revs`, the Vite registration, and that every `unchecked-js` and `coverage-omit` entry still matches a tracked file. `ironclad compat --add-blame-ignore <sha>` appends a mass-reformat commit to `.git-blame-ignore-revs`.

## Version skew

`flake.lock` pins one frappe-nix, and the caller workflows and the no-Nix CI install of `ironclad` must name the same one. `--check` exits 3 when a caller's `Avunu/frappe-nix/…@<sha> # v<version>` or `--expect-rev` disagrees with the lock. `IRONCLAD_ALLOW_SKEW=1` turns that off; only frappe-nix's own self-tests and the nightly drift report set it.

## Rendering is deterministic

Discovery reads tracked files only, iteration is sorted, the Jinja configuration is fixed, and nothing reads the clock: the only date is the year of the first commit. A second `--sync` changes no byte, and `--check` is clean after any `--sync` that exited 0. The JSON configs are printed in oxfmt's own form, so oxfmt never rewrites a managed file.
