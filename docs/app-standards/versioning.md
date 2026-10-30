---
title: Versioning
description: "How an app pins frappe-nix and its profile, how a release reaches every app, and what each version number promises."
order: 10
tags: [app-standards, versioning, releases]
updated: 2026-10-30
---

frappe-nix stays on `main` and releases with tags `vX.Y.Z`. A branch per major, `release-1`, is fast-forwarded to each `v1.*` tag and never moves otherwise. Apps follow that branch, so updating their lock only ever proposes a release, never an arbitrary `main` commit.

## How an app pins frappe-nix

An app that has opted in pins one frappe-nix release in four places, and sync keeps them in step:

- `flake.nix` names `github:Avunu/frappe-nix/release-1`, unless the app sets `[tool.frappe-nix.dev-shell] frappe-nix-url` (a tag, a commit, a fork or a mirror).
- `flake.lock` records the commit. For the default URL that is always a `v1.*` tag commit, because `release-1` only fast-forwards to tags.
- The caller workflows use `uses: …@<that commit> # vX.Y.Z`. `frappe-init --sync` rewrites them from `flake.lock`, and `--check` fails when they disagree.
- The jobs that run without Nix install `frappe-nix-tools` from the same commit.

An app with an org profile also locks the `standards-profile` input, so its managed files come from exactly one profile revision too. The result is one frappe-nix version and one profile revision per app commit, everywhere.

## How a release reaches every app

1. A pull request merges to frappe-nix's `main`. frappe-nix's `release.yml` runs release-please, which opens or updates the release PR. The version bump changes the `# v<version>` comment that the caller workflows of `tests/fixtures/standards-app` carry, so the same run re-renders the fixture offline with the PR's frappe-nix-tools and pushes it to the PR branch; `main` never holds a fixture that `frappe-nix sync --check` rejects. When the repository can read the org's GitHub App (the variable and secret the org profile's `ci.github-app` names), release-please and the fixture push use its token, and the PR starts `check.yml`, `pr-policy.yml` and `docs.yml` by itself. Without it, because a push made with `GITHUB_TOKEN` starts no workflow, the same run then dispatches `check.yml` (its `lint` and `flake` jobs, without the VM tests), `pr-policy.yml` and `docs.yml` (the required `build / Check`) on that PR.
2. Merging the release PR is the decision to ship. release-please tags `vX.Y.Z` and writes the GitHub release; `release.yml` then moves `release-<major>` to the tag with a REST `PATCH` that GitHub refuses unless it is a fast-forward (it creates the branch on a major's first release), reads it back and fails unless the branch equals the tag.
3. Each app is moved deliberately, with a token that may change workflow files:

   ```bash
   frappe-nix repo rollout --to vX.Y.Z --repo example-org/my_app
   frappe-nix repo rollout --to vX.Y.Z --all --fleet fleet.json
   ```

   For each app it updates the `frappe-nix` input, relocks, runs `frappe-init --sync` (which rewrites the caller SHAs and every changed managed file) and opens `chore(deps): frappe-nix vX.Y.Z` with auto-merge on. Until that happens, the app's nightly `drift` job keeps an issue open.
4. The bump PR merges once its `ci / *` checks pass. If a stricter check in the new release fails the app, the PR waits for a fix pushed to its branch.

Dependabot never moves `frappe-nix` or `standards-profile`: the bump has to rewrite workflow files, which `GITHUB_TOKEN` cannot push. An app without the `ci` or `dependabot` modules simply runs `nix flake update frappe-nix && frappe-init --sync` when it wants a new release.

## What each version number promises

frappe-nix follows semver for everything an app consumes: reusable workflows, the `[tool.frappe-nix]` table, profiles, modules, tools and their flags.

- **Major**: a removed or renamed reusable-workflow input, secret, output or job name; changed required-check contexts; a new `schema` for the table or for profiles; a removed or renamed module or parameter, or a changed meaning of an existing value; a change `--sync` cannot apply on its own; a removed tool or flag; a module turned **on** in `minimal`; a change to a released `recommended@<minor>` snapshot other than one that accepts strictly more code; or a new schema default for `releases.branching`, `releases.version-scheme`, `releases.tag-prefix` or `integration-branch`. A new major gets a new `release-<N>` branch, and apps move to it by editing the flake URL in a deliberate PR.
- **Minor**: new managed content or checks that `--sync` applies, even when they fail code that passed before (the bump PR then waits); a new module, off in both built-in profiles and in every existing snapshot; a new `recommended@<minor>` snapshot, which plain `recommended` then means; new parameters, inputs, tools or flags, each with a default that keeps the old behaviour.
- **Patch**: fixes, and pin bumps such as nixpkgs, devenv, the action SHAs inside the reusable workflows and the pre-commit hook revisions.

Within `v1`:

- a reusable workflow at any `v1.y` accepts a caller rendered by any `v1.x`, because inputs are only ever added, with defaults;
- `schema = 1` keys of the table and of profiles, and module names, are never removed;
- a deprecated key keeps working for one minor, and `--check` warns about it with exit 0;
- `minimal` renders the same file set in every `v1.x`, and a released `recommended@<minor>` resolves to the same values in every later `v1.x`;
- an app that has not opted in is never touched.

An app that pins a snapshot (`profile = "recommended@1.0"`), or an org profile that extends one, therefore sees only new checks for modules it already has on, never new modules or new defaults.

## Org profiles

An org profile repository should release the same way: tags `vX.Y.Z` and a branch `v<major>` fast-forwarded to each tag, so apps write `profile = "github:<owner>/<repo>/v1"` and lock a tag commit. Its own semver is about what apps see: major for a change that makes `frappe-nix profile validate` or an app's `--check` fail without an app edit, minor for modules turned on or parameters that `--sync` applies, patch for org-value fixes such as a URL. `requires-frappe-nix` names the frappe-nix versions a profile is written for; sync refuses a profile whose range excludes the running frappe-nix (exit 3), so an app that moves frappe-nix past that range moves the profile in the same PR. A profile moves into apps with `frappe-nix repo rollout --profile-to <ref>|latest`.

## frappe-nix's own releases

- **release-please** (`release-please-config.json`, `.release-please-manifest.json`) uses the `simple` release type, so `version.txt` holds the version. It moves `py/frappe_nix_tools/pyproject.toml` (`project.version`) and the `__version__` block of `frappe_nix_tools/__init__.py` with it, so `frappe-nix --version` always names the release. Tags carry the `v`.
- **The first release is `v1.0.0`**, set by a `Release-As: 1.0.0` footer on the commit that introduced the release flow. A later major is made the same way, deliberately: `pr-policy` refuses `!` and `BREAKING CHANGE` in frappe-nix's titles and commits (`frappe-nix policy --pr --major-free`), so no ordinary merge can bump the major by accident.
- **Commit rules**: `committed.toml` (conventional commits, frappe's types, a subject of at most 100 characters). `pr-policy.yml` checks the PR's base (`main`), its title and each commit. A Dependabot PR's title and Dependabot's own commits are exempt from the subject length alone, since a SHA-pinned bump's generated subject runs far past 100 characters (spec §5.9). It is its own workflow so that a retitle re-runs it without rebuilding the `flake` job.
- **Dependabot** writes `fix(deps)` for the bumps apps run (nix inputs, the action SHAs, pre-commit hook revisions), so each of those ships as a patch release, and `chore(deps)` for frappe-nix's own dev environment (`/dev`), which releases nothing.
- **`check.yml`** builds the NixOS VM tests on pull requests and pushes. A `workflow_dispatch` runs them only with the input `vm-tests: true`; the release flow's dispatch leaves it off.
- **Repository policy**: `repo-policy/self/` holds frappe-nix's own settings and rulesets: `main` requires a pull request and the checks `lint`, `flake` and `pr-policy`; `release-*` may be created and moved only by GitHub Actions, with no deletion or force-push; `v*` tags may not be deleted or moved. Apply them with a token that has admin on the repository:

  ```bash
  frappe-nix repo apply --self --dry-run   # the JSON diff, no writes
  frappe-nix repo apply --self
  ```
