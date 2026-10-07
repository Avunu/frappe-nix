---
title: Profiles
description: "How an app opts in, the built-in minimal and recommended profiles, org profiles, and how [tool.frappe-nix] layers over them."
order: 1
tags: [app-standards, profiles, configuration]
updated: 2026-10-07
---

The app standards are opt-in and profile-driven. A profile decides which modules are on (lint, types, tests, commits, releases, Dependabot, CI, …) and sets their parameters and the organisation's values. An app picks one in `[tool.frappe-nix] profile`, and can change any value for itself in the same table. The [interface spec](spec.md) (§8) is the contract this page summarises.

## Opting in

An app opts in by having a `[tool.frappe-nix]` table in its `pyproject.toml`. The quickest way is to let sync create it:

```sh
nix run github:Avunu/frappe-nix/release-1#frappe-init -- --app --sync --standards recommended --frappe-version version-16
```

or add it by hand and run `frappe-init --sync`:

```toml
[tool.frappe-nix]
schema = 1
profile = "recommended"
frappe-major = 16
```

A table without `profile` means `minimal`. Without the table, nothing changes: `frappe-init --app` writes the same three files as always, the dev shell adds no tools, and `--sync`/`--check` exit 2 with a hint.

## The built-in profiles

| Profile | What it turns on |
|---|---|
| `minimal` | The dev shell only: `flake.nix`, `.envrc` and the `.gitignore` block. Frozen for all of v1. |
| `recommended` | Vendor-neutral defaults: EditorConfig, packaging metadata, ruff, ty, oxlint and oxfmt, TypeScript, stylelint, frappe-test with coverage, semgrep, workflow and shell linting, Nix linting, the basic git hygiene hooks, conventional commits, release-please, Dependabot and the CI callers. Nothing that needs an organisation's values or repository admin: repo policy, marketplace listing, README blocks, icons, screenshots, the demo, test_utils, a docs site and pilot assets are off. |

`frappe-nix profile list` prints them. `recommended` is shipped as frozen snapshots named after the frappe-nix minor that introduced them (`recommended@1.0` first). Plain `recommended` means the newest snapshot of the running frappe-nix; `profile = "recommended@1.0"` stays on that snapshot through every later `v1.x`. When a frappe-nix bump moves plain `recommended` to a newer snapshot, `frappe-init --sync` prints each default that changed and suggests pinning the old snapshot, so the bump PR says in its own log which defaults moved.

## Modules

Every module has `enable`. A module is on when its own `enable` is true and every module it needs is on; a module whose needs are off is exit 2 naming both, never switched off silently. `releases` needs `commits`; `pilot-assets` needs `ci` and `releases`; `listing` needs `metadata`; `readme` needs `listing`; `screenshots` needs `demo`; `docs-site` needs `dependabot`. A module with a `tool` parameter is off with `tool = "none"` (`js.tool = "none"` is the route for teams on eslint, prettier or biome).

The modules and their parameters are in the spec (§8.2), and in the schema `frappe-nix data-path schema/profile.schema.json`. A few that change what sync renders:

- `editorconfig`: `indent` (`"tab"` or `"space"`), `line-length`;
- `metadata`: `build-backend` (`"flit"` or `"any"`), `package-type`, `package-manager`, `node-engine` (`""` leaves the key to the app);
- `python-lint`: `select`, `ignore`, `line-length`, `indent-style`, `quote-style`, `typing-modules`;
- `python-types`: `error-on-warning`;
- `js`: `tool`, `locked-rules`, `format-width`, `format-tabs`, `oxlint.categories`;
- `typescript`: `preset` (`"frappe-types"`, the npm package's presets, or `"inline"`, which needs no extra dependency but rules out `check-js`), `strict-extras`, `check-js`;
- `commits`: `allowed-types`, `subject-length`;
- `releases`: `branching` (`"develop+version"` or `"main+tags"`), `version-scheme` (`"semver"` or `"frappe-major"`), `tag-prefix`;
- `tests`: `coverage.initial-floor`, `js-unit`, `js-coverage-min`.

## Resolution

An app's configuration is resolved the same way everywhere (sync, `--check`, `frappe-nix config`, frappe-test, CI):

1. the built-in profile: `profile` itself when it names one, else the org profile's `extends`;
2. the org profile's `profile.toml`, when there is one;
3. the app's `[tool.frappe-nix]`.

Tables merge key by key; a scalar or a list is replaced whole by the higher layer, so an app that sets `python-lint.ignore` gives the complete list. Defaults from the schema fill anything still unset.

```sh
frappe-nix profile show             # the resolved configuration and module switches
frappe-nix profile show --explain   # each value with the layer that set it
frappe-nix config modules.ci        # one value
```

## Org profiles

An organisation keeps its choices in its own profile instead of in every app. A profile is a directory with `profile.toml` and an optional `templates/`:

```toml
schema = 1
name = "example-org"
extends = "recommended@1.0"          # a built-in; a frozen snapshot keeps the org stable
requires-frappe-nix = ">=1.0,<2"     # optional

[org]
publisher = "Example Org"
email = "apps@example.org"
license = "MIT"
website-url = "https://example.org/apps/{app}/"

[ssort]
enable = true

[releases]
version-scheme = "frappe-major"

[[retire]]                           # a name-only retire rule frappe-nix doesn't ship
paths = [".github/workflows/check.yml"]
module = "ci"

[[extra-files]]                      # a file every app of the org gets
path = "SECURITY.md"
template = "SECURITY.md.j2"          # under templates/
strategy = "whole"
module = "hygiene"
header = "none"
```

Besides a built-in name, an app names an org profile in one of two ways:

| `profile` | Where it is read from |
|---|---|
| `"github:<owner>/<repo>[/<ref>]"`, `"gitlab:<group>/<repo>[/<ref>]"`, `"git+https://<host>/<path>?ref=<ref>"` | the flake input `standards-profile`, which sync adds to `flake.nix` and locks, so `flake.lock` pins the profile revision the files came from. No-Nix CI reads it with `frappe-nix pin-path standards-profile`, narHash-verified (`FRAPPE_NIX_FETCH_TOKEN` for a private repository). |
| `"./<dir>"` | the app's own checkout: an in-repo profile, versioned with the app, with no input and no lock. The route for a single app that wants overrides or extra files without a profile repository. |

Dependabot never moves `standards-profile`, because a profile change can rewrite workflow files. It moves with a reviewed `nix flake update standards-profile && nix run .#frappe-init -- --sync` commit. A profile should release like frappe-nix: tags `vX.Y.Z` and a moving branch `v<major>` that apps follow.

**Template overrides.** A file in the profile's `templates/` replaces a built-in template that is marked overridable (the README blocks, the listing seed and the repo-policy JSON), with the same context. Anything else there that isn't an `[[extra-files]]` template is exit 2: tool configs, merged keys and caller workflows change through parameters, never through an override.

**Authoring.** Work against a local checkout with `frappe-init --sync --profile-path ../my-profile` on a test app, and check the file with:

```sh
frappe-nix profile validate profile.toml [--templates templates]
```

which checks the schema, the `extends`, that no app-only key is set, that every module's needs are met under the profile's own switches, `requires-frappe-nix`, that `[[extra-files]]` don't collide with a managed path, and that every template overrides an overridable one (or is an extra file) and renders for a plain app and one with erpnext and hrms. Exit 0, 2 or 3.

## Org values

frappe-nix itself carries no organisation's values. Publisher, e-mail, copyright holder, licence, brand colours, URL templates, GitHub owner, registry fork, README badges and links come from `[org]` in a profile or the app's `[tool.frappe-nix.org]`. The built-in profiles leave them empty, so `package.json` gets no `author` or `license` from sync unless a profile sets them, and a module that needs an empty value is exit 2 naming the key.
