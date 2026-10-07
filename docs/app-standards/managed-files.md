---
title: Managed files
description: "The files frappe-nix keeps in an opted-in app, the [tool.frappe-nix] parameters, and frappe-init --sync and --check."
order: 2
tags: [app-standards, sync, managed-files]
updated: 2026-10-07
---

An app that has opted in to the app standards carries a set of tool configs that frappe-nix writes and keeps in step: the flake, the git hooks, the formatter and linter settings, the TypeScript projects, the release-please files and the tool locks. Which of them an app gets is decided by its [profile](profiles.md): every file belongs to a module, and exists only while that module is on. The [interface spec](spec.md) (§2, §3) is the contract this page summarises.

An app without a `[tool.frappe-nix]` table is never touched: `frappe-init --app` writes the same `flake.nix`, `.envrc` and `.gitignore` it always has, and `--sync` and `--check` refuse with exit 2 and the opt-in hint.

## The commands

```sh
nix run .#frappe-init -- --sync --standards recommended   # opt in, then write the managed files
nix run .#frappe-init -- --sync     # write the managed files (frappe-nix sync --write)
nix run .#frappe-init -- --check    # report drift and change nothing (frappe-nix sync --check)
```

Both run in the app's repository: a git work tree whose `pyproject.toml` `[project].name` names a package with a `hooks.py`. `frappe-init` hands both to `frappe-nix sync`, which is on `PATH` in an opted-in app's dev shell too.

`--standards <profile>` opts the app in: it creates `[tool.frappe-nix]` with that profile (`minimal`, `recommended`, a snapshot such as `recommended@1.0`, an org profile's flake URL, or an in-repo `./<dir>`). Given again on an app whose table names the same profile it changes nothing; naming another one is exit 2 (edit `profile` in the table instead).

`--sync` stages what it writes with `git add` and commits nothing. It also takes `--force` (replace an unmanaged `flake.nix` or `.envrc` on first opt-in), `--dry-run` (print the diff and the commands), `--skip-lock` (don't relock the bench), `--only <path>[,<path>…]`, `--init-listing`, `--frappe-version version-<N>` and `--profile-path <dir>` (read an org profile from a local checkout, for profile authors; it prints a warning on every run and is never recorded). `frappe-nix sync --write --offline` (or `FRAPPE_NIX_OFFLINE=1`) runs no `nix`, `uv` or `yarn` command and reports the locks it left alone.

`--check` runs nothing at all, so it works in a CI job without Nix. It takes `--format text|json|github`, `--only`, `--expect-rev <sha>` and `--profile-path` (refused with exit 3 when `CI` is set).

| Exit | Meaning |
|---|---|
| 0 | Clean: nothing to write, or everything written. |
| 1 | Drift: a managed file is missing, differs or should not exist; a retired file is tracked; a lock is below its floor. Also a rule only the app can fix, such as code in `__init__.py` outside the version block. |
| 2 | Invalid configuration sync can't fix: not opted in; a schema error; an unknown or unresolvable profile; a module whose needs are off; an org value a live file needs that is empty; an override of a template that isn't overridable; an unknown sibling; a forbidden key; a malformed local region; content in the local region of a file whose module turned off; an unmanaged `flake.nix` or `.envrc` that first opt-in would replace without `--force`. |
| 3 | Environment: not an app, not a git repository, an unreadable lock, a profile that requires another frappe-nix, version skew, or a crash. |

### A first sync (bootstrap)

An app with no flake, or one whose flake still follows frappe-nix `main`, opts in with:

```sh
nix run github:Avunu/frappe-nix/release-1#frappe-init -- --app --sync --standards recommended --frappe-version version-16
```

It needs only `nix` and `git`. Sync runs in two phases. **Phase A** creates `[tool.frappe-nix]` (the profile from `--standards`; the major from `--frappe-version` or the existing flake's `frappeVersion`; the siblings from `hooks.required_apps` plus the flake's, an existing sibling pinned off the generic rule written in the object form so its branch is kept; `integration-branch` when `origin/HEAD` names another branch than the branching model's default), writes `flake.nix` and `.envrc`, and locks the flake. When the lock now pins a different frappe-nix release than the one running, sync hands over to `nix run .#frappe-init -- --sync` once, so **phase B** always renders with the tools the lock pins. Phase B renders every other file, retracts what a module that turned off left behind, deletes the retired files, and brings the locks up: `uv lock --project tools`, `yarn install`, the node-lock seeds, `nix run .#relock`, and the README blocks.

**First opt-in.** An existing `flake.nix` or `.envrc` with no managed header that differs from what `frappe-init --app` writes is the app's own: sync stops with exit 2, prints what it would replace it with, and names where custom Nix goes instead (below). `--force` replaces it after you have reviewed the diff.

`frappe-init --app --standards <profile>` on a new app copies frappe-nix's app template as it always has and then runs `frappe-nix sync --write`, so a new app starts in sync.

## What is managed

| Path | Strategy | Module (when) |
|---|---|---|
| `flake.nix` | whole, local region `inputs` | `dev-shell` |
| `.envrc` | whole, local region `envrc` | `dev-shell` |
| `.gitignore` | block (`# >>> frappe-nix >>>`) | `dev-shell` |
| `.editorconfig` | whole, local region `editorconfig` | `editorconfig` |
| `.pre-commit-config.yaml` | whole, local region `repos`; third-party `rev:`s are floors | any hook module |
| `committed.toml` | whole | `commits` |
| `tools/pyproject.toml` | whole: the tools of the enabled modules | any module with a tool there |
| `tools/uv.lock` | seed (`uv lock --project tools`); versions are floors | as `tools/pyproject.toml` |
| `pyproject.toml` | toml-merge, one key group per module | `metadata`, `python-lint`, `python-types`, `tests`, `test-utils` |
| `<app>/__init__.py` | block (`x-release-please`) | `releases` |
| `package.json` | json-merge, one key group per module; `devDependencies` are floors | `metadata`, `js`, `stylelint`, `typescript`, `tests`, `python-lint`, `python-types`, `vite-register` |
| `yarn.lock` | seed (`yarn install`; drift when it lacks a `package.json` dependency's `name@range`) | as `package.json` |
| `.oxlintrc.json`, `.oxfmtrc.jsonc` | whole | `js` (`tool = "oxc"`) |
| `.stylelintrc.json` | json-merge | `stylelint` (the app has SCSS) |
| `tsconfig.json`, or `tsconfig.solution.json` when an SPA owns `tsconfig.json` | whole | `typescript` (any project) |
| `tsconfig.base.json` | whole | `typescript` (browser, scripts or test project) |
| `tsconfig.browser.json`, `.scripts.json`, `.test.json` | whole | `typescript` (that project has files) |
| `tsconfig.desk.json`, `.web.json` | whole | `typescript` with `check-js` (that project has files) |
| `release-please-config.json` | whole, no header (JSON) | `releases` |
| `.release-please-manifest.json` | seed `{".": "<__version__>"}` | `releases` |
| `.git-blame-ignore-revs` | seed, then validated | `hygiene` |
| `nix/node-locks/<key>/` | seeded from frappe-nix for frappe, erpnext and hrms | `dev-shell`, when the app has siblings and no lock |
| each `[[extra-files]]` entry of an org profile | whole or seed | the module the entry names |

The CI caller workflows, `.github/dependabot.yml` and `.github/zizmor.yml` ([CI](ci.md)), `scripts/vite-register.mjs` ([App assets](assets.md)), and the README blocks, `marketplace/shots.d.ts` and the semgrep baseline ([Marketplace](marketplace.md)) are managed the same way, by the same engine.

### Strategies

- **whole**: the file is the rendered template, compared byte for byte. It starts with a header such as ``# frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`); do not edit.``; plain JSON files carry none. Edit it only inside its local regions, or through `[tool.frappe-nix]`. While `js.tool` isn't `"oxc"`, a YAML, JSON, JSONC or TOML file is compared as data plus its header line instead, so a formatter you chose (prettier, biome) may restyle it, and sync leaves a restyled file alone.
- **toml-merge** and **json-merge**: only the managed keys are sync's; every other key, comment and order is the app's. `--check` compares parsed values, so a formatter's reflow is never drift.
- **block**: only the text between the markers is sync's.
- **seed**: written once when missing, then the app's (and validated).

**Local regions** sit between `# frappe-nix:local-begin <name>` and `# frappe-nix:local-end <name>`. Sync keeps their content as it is. A region may add, never redefine: a hook id, a dependabot `updates` entry, an EditorConfig section or a flake input that sync already manages is exit 2, as is an unknown, duplicate or unclosed region.

**Floors** are versions dependabot moves. Sync never lowers one: a pre-commit `rev:` at or above the floor is kept, a `devDependencies` caret or exact range whose minimum is at or above the floor is kept, and a `tools/uv.lock` version below its floor is drift that `uv lock --upgrade-package` fixes. A `devDependencies` entry that names a tarball, a git ref or a path has no minimum to compare and is left as the app wrote it.

### Turning a module off

Every file and key above belongs to a module, so turning a module off in `[tool.frappe-nix]` (or in the profile) removes what it rendered:

- a whole file that still opens with its managed header is deleted; if one of its local regions holds content, sync stops with exit 2 naming the region instead, so nothing you wrote is lost silently;
- a seed (a lock, the release-please manifest) is left: it is the app's once written;
- the version block in `<app>/__init__.py` loses its markers and keeps `__version__`;
- in `pyproject.toml` and `package.json`, each key of a module that is off now is removed when it still holds what sync wrote, and left when you changed it (a toolchain `devDependencies` entry counts as unchanged at `^<floor>` or higher). A module that was never on owns nothing yet, so opting in with `minimal` never removes the ruff or coverage settings an app already has. What `bench new-app` writes (the flit `[build-system]`, `requires-python`, its ruff settings) and a `[tool.bench.frappe-dependencies]` that matches your bench apps stay in any case: they are the app's baseline.

"Was on" means on in any configuration the app has committed since it opted in: each `[tool.frappe-nix]` table read against the profile committed beside it (an in-repo profile's `profile.toml` at that commit, a flake-URL profile's tree as that commit's `flake.lock` locks it). So it makes no difference whether the module was turned off in the table, by editing the in-repo profile or by bumping the locked one (`nix flake update standards-profile`, `frappe-nix repo rollout --profile-to`), nor whether you sync before committing the change or after: `--check` on the pull request reports what is left, and the next `--sync` removes it. What sync leaves to you (a key or file you changed, a seed) is reported once, on the run that turns the module off.

That answer comes from the git history of `pyproject.toml`, `flake.lock` and the in-repo profile. A shallow clone that holds a file or key a module that is off now could have written, without the commits that would say whether it was ever on, exits 3 ("fetch-depth: 0") rather than give a verdict a full clone would contradict; so does an old locked profile tree that can't be fetched (`FRAPPE_NIX_FETCH_TOKEN` for a private one). A clone with nothing of the kind left to ask about checks as usual.

Where the repository is hosted is not in git. When `origin` is off GitHub and no `repo` names the host, the GitHub-only modules resolve off; if the repository has a `.github/` directory (at `HEAD` or in the commit), each committed configuration is also read as hosted on GitHub, so what `releases` left behind (its `release-please-config.json`, the version block's markers) is retracted after a move.

Turning the module back on restores the files, byte for byte where sync laid them out.

### The app's own Nix

`flake.nix` is managed whole, so an app's own Nix goes in three places:

- **extra inputs** in the `inputs` local region (an input named like a managed one, such as `frappe` or `frappe-nix`, is exit 2);
- **outputs** (packages, overlays, NixOS modules, checks, extra dev-shell settings) in `nix/local.nix`, a flake-parts module the flake imports when it exists;
- **systems, binary caches and the frappe-nix source** in `[tool.frappe-nix.dev-shell]`: `systems`, `extra-substituters`, `extra-trusted-public-keys`, and `frappe-nix-url` (a tag, a rev, a fork or a mirror in place of `release-<N>`). Phase A relocks frappe-nix only when the lock's `original` differs from that URL, so a pin is never undone.

`.envrc` has the `envrc` local region for `dotenv`, exports and other direnv lines.

### `pyproject.toml`

By module: `metadata` owns `requires-python`, `dynamic` (it must contain `version`), `[build-system]` (only with `metadata.build-backend = "flit"`; with `"any"` it is the app's) and `[tool.bench.frappe-dependencies]` (exactly frappe plus `required_apps`, with each sibling's resolved range); `python-lint` the ruff profile (`[tool.ruff]`, `.lint`, `.format`) from its parameters; `python-types` `[tool.ty.environment]`, `.src` and `.terminal`; `tests` `[tool.coverage.run] omit` (plus each `coverage-omit` glob) and the `[tool.coverage.report]` settings, seeding a missing `fail_under` as `tests.coverage.initial-floor`; `test-utils` `[tool.vulture] exclude` and the entries `[tool.test_utils.static-analysis] whitelist` must hold. A table sync adds goes after the last `[tool.*]` table.

Refused (exit 2), each only while its module is on: extra codes in the top-level `[tool.ruff] ignore`, `extend-select`, `extend-ignore`, `unfixable`, `isort`; F401 or E402 in a `per-file-ignores` glob ruff applies to the whole package; `[tool.ty.rules]` other than `"error"`, `[tool.ty.overrides]`; `[tool.coverage.run] source` or `relative_files`; `[tool.poetry]` under flit. `[project].dependencies` naming frappe, erpnext, hrms or payments is exit 1, and so is a `[project].license` that differs from the profile's `org.license`.

### `package.json`

By module: `metadata` owns `private`, `type` (`metadata.package-type`), `license` and `author` (only when the profile sets `org.license` and `org.publisher`), `engines.node`, `packageManager` and `frappe` (`{major, branch}`), each left to the app when its parameter is `""`; `js` the `format`, `format:check`, `lint` and `check` scripts and the oxlint and oxfmt floors; `stylelint` the `lint:css` script and its floors; `typescript` the `typecheck` script and the typescript and `@types/node` floors (plus frappe-types with `typescript.preset = "frappe-types"`); `tests` `test:unit`; `python-lint` `lint:py`; `python-types` `typecheck:py`; `vite-register` the `node scripts/vite-register.mjs` step a Vite app's `build` must end with. `check` chains `format:check`, `lint`, `lint:css`, `typecheck` and `test:unit`, each when that script exists, whoever owns it. With `js.tool = "none"`, `lint` and `check` are the app's, so an eslint or biome setup keeps its own scripts.

Refused: a `build` script with nothing to build (no Vite config, nested frontend, SPA entry or `build = true`), eslint or prettier packages while `js` is on, and `frappe-nix:` script names.

## `[tool.frappe-nix]`

The app's parameters live in its `pyproject.toml`. Sync creates the table once and never rewrites it; the schema is `frappe-nix data-path schema/tool-frappe-nix.schema.json`, and an unknown key is exit 2. It holds app keys (`profile`, `frappe-major`, `siblings`, `repo`, `integration-branch`, `site`, `generated`, `build`, `retire-keep`, the `[[…]]` exemption lists) and module tables that layer over the profile ([Profiles](profiles.md)). The spec (§2.1) documents every key. The ones that change what sync renders:

- `frappe-major` and `siblings`: the flake inputs, the dependency ranges, the node-lock seeds and the desk globals. A sibling is a known app (`"erpnext"`), `"<owner>/<repo>"`, or an object with `repo`, `flake-url`, `branch`, `range` and `desk_global` for a sibling off the Frappe convention;
- `site`, `build`, `integration-branch`, `generated` (globs of tracked generated files, kept out of prek, `validate_copyright`, oxfmt and oxlint);
- `[tool.frappe-nix.typescript]`: `preset` (`"frappe-types"` or `"inline"`), `strict-extras`, `check-js`, and the app-only `browser`, `browser-include`, `web-include`, `paths`, `exclude` (never a source file under the package), and `[[…spa]]` for a Vue/Vite SPA without a `package.json` of its own;
- `[[tool.frappe-nix.unchecked-js]]`: desk and web scripts left out of strict checkJs while they are typed. `frappe-nix unchecked-js --stale` fails on an entry whose file now type-checks;
- `[tool.frappe-nix.js]` (`tool`, `locked-rules`, `format-width`, `format-tabs`, `oxlint`, `oxfmt`), `[tool.frappe-nix.stylelint]`, `[tool.frappe-nix.dev-shell]`.

What can be read from the tracked tree is never declared: the desk and web script sets, the TypeScript projects, Vite, SCSS, nested frontends, `docs-site/`, submodules and the marketplace listing are discovered from `git ls-files`, so a first `.ts` file under `scripts/` is drift until sync renders `tsconfig.scripts.json`.

## Retired files

Sync deletes, and `--check` reports as `legacy file`, the files a module replaces, and only while that module is on:

| Module | Retired |
|---|---|
| `releases` (with `ci`) | a workflow that runs `release-please-action` beside the managed callers (a docs tool's `docs*.yml` excepted) |
| `dependabot` (with `ci`) | a workflow that runs `gh pr merge --auto` or `dependabot/fetch-metadata` |
| `js` | `.oxfmtrc.json`, eslint and prettier configs |
| `python-lint` | `.flake8`, a flake8-only `setup.cfg` |
| `metadata` (flit) | `MANIFEST.in`, `requirements.txt` |
| `dev-shell` | `nix/node-offline-hashes.json` |
| `vite-register` | `update-assets.mjs` |

A file name says nothing about what a workflow does, so name-only rules belong to an org profile (`[[retire]]`). `[tool.frappe-nix] retire-keep` exempts any path from every rule.

Two of them need something first. A `requirements.txt` (anywhere but `docs/` and `docs-site/`, which belong to your docs tool) that lists a package `[project].dependencies` doesn't name is exit 2, naming the missing ones: move them into `pyproject.toml`, then sync deletes the file. Sync drops `node update-assets.mjs` steps from the `package.json` scripts when it deletes the script, and any other script that still names it is exit 2. In an app with a Vite config, `update-assets.mjs` is retired only once frappe-nix renders `scripts/vite-register.mjs`, which replaces it.

A managed path with a symlink anywhere on it (the file or a directory above it) is exit 2: sync never reads or writes through a link. A retired link is deleted as a link. The same holds for what sync reads from an org profile: an in-repo profile directory reached through a link, a linked `profile.toml`, or a link anywhere under the profile's `templates/` is exit 2, unread.

## `frappe-nix compat`

The prek hook checks that versions, ranges and majors agree, each rule while its module is on: under `releases.version-scheme = "frappe-major"`, the `package.json` major against the Frappe major (equal once a `<tag-prefix><major>.*` tag exists or on a release branch); the `frappe` stanza; the flake's `version-<major>` and each sibling's resolved branch; the dependency ranges; `__version__` = `package.json` = the release-please manifest; `required_apps` ⊆ siblings; no hand-written frappe globals (augmentations only in `types/<app>.augment.d.ts`, each member marked `// app-owned: <reason>`); a valid `.git-blame-ignore-revs`; the Vite registration; and that every `unchecked-js` and `coverage-omit` entry still matches a tracked file. `frappe-nix compat --add-blame-ignore <sha>` appends a mass-reformat commit to `.git-blame-ignore-revs`.

The two hooks that call `frappe-nix` (compat and the commit-message rule) skip with a warning when a commit is made outside the dev shell, where `frappe-nix` is not on `PATH`; CI's `lint` and `pr-policy` jobs still run them.

## Version skew

`flake.lock` pins one frappe-nix, and the caller workflows and the no-Nix CI install of frappe-nix-tools must name the same one. `--check` exits 3 when a caller's `<owner>/<repo>/…@<sha> # v<version>` (the repository the app locks frappe-nix from: frappe-nix itself, or a fork) or `--expect-rev` disagrees with the lock. `FRAPPE_NIX_ALLOW_SKEW=1` turns that off; only frappe-nix's own self-tests and the nightly drift report set it.

## Rendering is deterministic

Discovery reads tracked files only, iteration is sorted, the Jinja configuration is fixed, and nothing reads the clock: the only date is the year of the first commit. A second `--sync` changes no byte, and `--check` is clean after any `--sync` that exited 0. The JSON configs are printed in oxfmt's own form, so oxfmt never rewrites a managed file.
