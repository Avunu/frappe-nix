---
title: GitHub settings
description: "The optional rulesets and repository settings, the release flow, and how they are applied and audited."
order: 6
tags: [app-standards, github, rulesets, releases]
updated: 2026-10-31
---

The CI gates and the release flow need a few repository settings to work, and rulesets to be worth anything: auto-merge is only as safe as the checks the integration branch requires. frappe-nix renders the settings and rulesets from an app's resolved configuration, and `frappe-nix repo apply` applies them, but only for an app whose profile turns on `repo-policy` (it is off in `recommended`). Without it nobody applies anything, and `frappe-nix repo doctor` tells you what the enabled modules rely on.

## The release flow

`release.yml` runs releases with no GitHub App, or with the one `ci.github-app` names (see [CI](ci.md#a-github-app)):

1. **release-please** runs on every push to the integration branch, and on a daily schedule, because a `GITHUB_TOKEN` merge raises no push. It keeps the release PR open, and cuts the tag and the GitHub release once that PR is merged. It uses `GITHUB_TOKEN`, or the App's token when there is one, and then the release PR starts `ci.yml` and `pr-policy.yml` by itself.
2. **dispatch-ci**, without an App only, runs `ci.yml` and `pr-policy.yml` on the release PR, because a push made with `GITHUB_TOKEN` starts no workflow. It checks each required check on the PR's head separately, so a run that failed or was cancelled is retried by the next one.
3. **fast-forward** (`develop+version` only) moves the release branch, `version-<major>`, to the commit of the new tag with a REST `PATCH` that GitHub refuses unless it is a fast-forward (or creates the branch on the first release). It then reads the branch back and fails unless it equals the tag. Under `frappe-major` the tag must also start with `<tag-prefix><major>.`.
4. **assets** dispatches `assets.yml` on the release branch (or on the tag under `main+tags`) when `pilot-assets` is on.
5. **publish** opens the Frappe marketplace registry PR when `listing.publish` is on and the repository variable `MARKETPLACE_PUBLISH` is `true`, using the `REGISTRY_TOKEN` secret, and appends the PR's link to the release notes.

Merging the release PR is the decision to ship. Nothing merges it automatically.

| Setting | Needed by |
|---|---|
| Actions may create pull requests (Settings → Actions → General → Workflow permissions) | release-please, the weekly relock, the screenshots PR, unless `ci.github-app` names an App |
| The `ci.github-app` App installed on the repository, its ID in an Actions variable and its private key in an Actions secret (both also Dependabot secrets with `dependabot` on) | bot PRs that start CI without approval or dispatch (optional; see [CI](ci.md#a-github-app)) |
| GitHub Actions may update `version-*` (the release-branch ruleset's bypass, or no ruleset there) | the fast-forward |
| Auto-merge allowed, and a ruleset or branch protection requiring every enabled `ci / <gate>` | Dependabot auto-merge, and with `dependabot.auto-merge-policy = "all"` the screenshots PR |
| Workflows write for the `ci.github-app` App (optional) | `dependabot.update-branches` updating a bot PR across a change to `.github/workflows/` |
| `REGISTRY_TOKEN` secret and `listing.registry-fork` | publishing to the registry |
| `FRAPPE_NIX_FETCH_TOKEN` secret (Actions, and Dependabot) | a private org profile, frappe fork or sibling |

## `frappe-nix repo doctor`

```bash
frappe-nix repo doctor                       # the app in the current directory
frappe-nix repo doctor example-org/my_app --format json
```

Read-only, and it needs no admin. For each setting the enabled modules rely on it prints `ok`, `missing`, or `unknown` (not readable without admin), with where to change it. It exits 1 when anything is `missing`. `frappe-init --sync` suggests it whenever it turns on a module that needs one of these settings.

## Rulesets and settings

The templates live in frappe-nix's `repo-policy/` (shipped in frappe-nix-tools, so an installed tool can read them). They are JSON with `{placeholders}` filled from the app's configuration:

| File | Applied | What |
|---|---|---|
| `rulesets/integration.json` | `--phase full` | The integration branch: no deletion or force-push, PRs only with `repo-policy.merge-methods`, `repo-policy.required-approvals`, thread resolution, and the required checks |
| `rulesets/integration-provisional.json` | `--phase provisional` | The same without required checks, for a repository that has not opted in yet. The full ruleset has the same name and replaces it in place. |
| `rulesets/release-branch.json` | `--phase full`, `develop+version` only | `version-*` moves only by the release automation: GitHub Actions is the only bypass actor (`repo-policy.release-bypass`) |
| `rulesets/tags.json` | `--phase full`, with `releases` | Release tags are never moved or deleted |
| `repo-settings.json` | always | Merge methods, auto-merge, delete-branch-on-merge, squash commit title and message, secret scanning, Actions' default token permissions, vulnerability alerts, and the labels the enabled modules use |

The required checks are one `ci / <gate>` per enabled gate, in the order `pr-policy`, `lint`, `typecheck`, `test`, `marketplace`, so a gate that is off is never required. `repo-policy.required-checks` may instead name a subset of the gates.

An org profile may replace any of these files with `templates/repo-policy/<same path>` (see [Profiles](profiles.md)).

## `frappe-nix repo apply`

```bash
frappe-nix repo apply example-org/my_app --dry-run           # the JSON diff, nothing changed
frappe-nix repo apply example-org/my_app                     # settings, labels and the full rulesets
frappe-nix repo apply --all --fleet ../my-profile/fleet.json --phase provisional --rename-default-branch
frappe-nix repo apply example-org/my-profile --policy-dir repo-policy   # a directory of its own
```

It needs `gh` authenticated with `repo` scope and admin on the target (never `admin:org`). For each repository it reads the default branch's `pyproject.toml` and resolves the configuration. A repository that has not opted in yet is resolved with the profile `--profile`, its fleet entry or the fleet file names, and gets only `--phase provisional`. A repository whose resolved `repo-policy.enable` is false is skipped with a notice, so the tool never applies policy nobody asked for.

- Rulesets are matched by name: a missing one is created, a different one is updated, and rulesets it doesn't know are listed, never deleted.
- `--rename-default-branch` renames the default branch to the integration branch first when the latter doesn't exist (GitHub retargets open PRs).
- Rulesets on a private repository need a paid plan; a 403 fails that repository with that message. A fleet entry with `private: true` is refused for `--phase full`.
- If GitHub refuses GitHub Actions as a bypass actor, it stops for that repository and prints the two fallbacks: a machine user (`repo-policy.release-bypass = "user:<id>"`), or a deploy-key bypass through an org override of `release-branch.json`.

Exit status: 0 applied or already matching, 1 some repository failed (the rest continue), 2 a configuration error.

## The fleet list

`--all` reads a fleet file, which belongs to the organisation (usually its profile repository), not to frappe-nix:

```json
{ "schema": 1, "profile": "github:example-org/standards-profile/v1", "apps": [
  { "repo": "example-org/my_app", "app": "my_app", "private": false, "list": true,
    "documentation": "https://docs.example.org/my-app/" }
] }
```

An entry's `profile` and `integration_branch` override the file's for that repository; `target_app` and `rename_mode` describe a planned rename.

## `frappe-nix repo audit`

```bash
frappe-nix repo audit --repo example-org/my_app --format json
frappe-nix repo audit --all --fleet fleet.json --issue --issue-repo example-org/standards-profile
```

The scorecard: for each repository, a shallow clone of the integration branch and rows A1 to A17 (the default branch, the release branch and tags, settings and rulesets, `sync --check`, CI and nightly status, ty, TypeScript, coverage and testmap, legacy tooling, Dependabot and drift, releases, README, icons, the listing, screenshots, the registry and the docs site). Each row is `green`, `amber`, `red`, `n/a` (its module is off) or `unknown` (unreadable, usually for lack of admin). It exits 1 on any amber or red.

frappe-nix ships `.github/workflows/fleet-audit.yml` as a reusable workflow only. An organisation calls it on a schedule from its own repository, with the fleet file and a read-only token:

```yaml
jobs:
  audit:
    permissions:
      contents: read
      issues: write
    uses: Avunu/frappe-nix/.github/workflows/fleet-audit.yml@<sha> # v1.x.y
    with:
      fleet: fleet.json
      issue-repo: ${{ github.repository }}
      frappe-nix-version: v1.x.y
    secrets:
      FRAPPE_NIX_AUDIT_TOKEN: ${{ secrets.FRAPPE_NIX_AUDIT_TOKEN }}
```

`FRAPPE_NIX_AUDIT_TOKEN` is a fine-grained token with Administration and Contents read on the fleet's repositories. The scorecard issues are written with the calling repository's own token.

## `frappe-nix repo rollout`

A new frappe-nix release, or a new release of the org profile, rewrites the caller workflows' pinned SHAs. GitHub refuses a workflow change pushed with `GITHUB_TOKEN`, so Dependabot never moves either; rollout does, with your own token:

```bash
gh auth refresh -s workflow
frappe-nix repo rollout --to v1.2.0 --repo example-org/my_app --dry-run
frappe-nix repo rollout --to v1.2.0 --all --fleet fleet.json
frappe-nix repo rollout --profile-to latest --all --fleet fleet.json
```

For each repository it clones the integration branch, runs `nix flake update frappe-nix` (and/or `standards-profile`), `nix run .#relock` and `frappe-init --sync`, commits `chore(deps): frappe-nix v1.2.0`, pushes `standards/frappe-nix-v1.2.0`, opens the PR and, unless `--no-auto-merge`, enables auto-merge with the first merge method. The nightly drift issues and audit row A10 remind you when an app is behind. An app without CI callers needs none of this: `nix flake update frappe-nix && frappe-init --sync` in any commit is enough.

## Landing a workflow change without a PAT

Whether `GITHUB_TOKEN` can commit a change to `.github/workflows/` through the Git Data API (blobs, a tree, a commit, then a ref `PATCH`), which would let a later release automate the frappe-nix bump, has not been tested yet. The test needs a scratch repository: create a blob, a tree and a commit that change a workflow file, then `PATCH git/refs/heads/<branch>` with `force=false` from a workflow with `contents: write`. Until it is shown to work, `frappe-nix repo rollout` remains the only path.
