---
title: CI
description: "The gate commands and the reusable workflows behind ci / lint, typecheck, test, marketplace and pr-policy, and the files that call them."
order: 5
tags: [app-standards, ci, workflows]
updated: 2026-10-19
---

An opted-in app with the `ci` module on gets small caller workflows in `.github/workflows/`, rendered by `frappe-init --sync`. Each one calls a reusable workflow in frappe-nix, pinned to the frappe-nix revision the app's `flake.lock` locks. The gates are commands, not YAML: every step of `lint`, `typecheck` and `marketplace` is `frappe-nix gate <gate>`, and `test` is `frappe-test --ci`, so any CI, or a laptop, runs exactly what GitHub runs.

Nothing here reaches an app that has not opted in, and within an opted-in app a workflow or job exists only while its module is on. Turning a module off and running `frappe-init --sync` deletes what it rendered.

## The gates

Each gate is one required check with a fixed name. The caller job that runs it is named `ci`, and the reusable job is named after the gate, so GitHub reports `ci / <gate>` whatever else the app enables.

| Check | Reusable workflow | Present when | What it runs |
|---|---|---|---|
| `ci / pr-policy` | `app-pr-policy.yml`, from its own caller `pr-policy.yml` | `ci` and `commits` | `frappe-nix policy --pr`: the base branch, the title and every commit |
| `ci / lint` | `app-lint.yml` | `ci` | `frappe-nix gate lint` |
| `ci / typecheck` | `app-typecheck.yml` | `ci` and `typescript` (with a TypeScript project), or `tests.js-unit` with unit tests | `frappe-nix gate typecheck` |
| `ci / test` | `app-test.yml` | `ci` and any of `tests`, `python-types`, `nix-lint` or a non-empty `shell-checks` | `frappe-test --ci` in one Nix dev-shell entry |
| `ci / marketplace` | `app-marketplace.yml` | `ci` and `listing` | `frappe-nix gate marketplace` |

`pr-policy` has a caller of its own so that retitling a PR re-runs only that check. If `ci.yml` re-ran on `edited` and skipped `test`, the skipped run would replace an earlier failure, and GitHub counts a skipped check as passing.

The shipped form is one reusable workflow per gate, each called from a job named `ci`. `selftest-ci.yml` asserts on every run that GitHub reports these jobs as `ci / lint`, `ci / typecheck` and `ci / marketplace`.

The required checks a ruleset lists are computed from the same set of gates (see [GitHub settings](github.md)), so a gate that is off is never required, and turning one on never renames another.

### `frappe-nix gate lint`

Every step runs, and the gate fails after the last one if any failed. A step whose module is off, or whose file the app doesn't have, is reported as `skipped` with the reason.

| Step | Module | What |
|---|---|---|
| sync-check | (always) | `frappe-nix sync --check`: the managed files match what this frappe-nix renders. In CI it also checks the installed tool is the revision `flake.lock` pins (`--expect-rev`). |
| dependabot-cooldown | `dependabot` | Every `.github/dependabot.yml` entry, including those in the app's own `updates` region, has a `cooldown`. |
| prek | (when `.pre-commit-config.yaml` is live) | `uv run --frozen --project tools prek run --all-files --show-diff-on-failure`. A hook that rewrites a file fails. |
| customizations | `test-utils` | `frappe-nix minibench --mode customizations`, then `validate_customizations` and `clean_customized_doctypes` from the mini-bench. |
| semgrep | `semgrep` | frappe's semgrep rules at the revision frappe-nix pins, frappe-nix's test-correctness rules over `test_*.py`, and the app's own `semgrep/` rules. |
| ratchet | (each rule its module) | `frappe-nix ratchet --base <merge-base>`: coverage floor, `ty: ignore` count, semgrep baseline, exemption lists. Skipped on pushes and schedules. |
| ci-lint | | `yarn -s ci:lint`, when `package.json` has that script. |

### `frappe-nix gate typecheck`

| Step | Module | What |
|---|---|---|
| frappe-path | | `FRAPPE_PATH` is the app's locked frappe tree (`frappe-nix pin-path frappe`), fetched and hash-checked. Later steps and the app's scripts read it. |
| frappe-node-modules | `typescript` | `yarn install` in frappe's tree, when `[tool.frappe-nix] frappe-node-modules = true`. |
| typecheck | `typescript` | `yarn -s typecheck`: the managed TypeScript solution, then each SPA's own check. |
| unchecked-js | `typescript` with `check-js` | `frappe-nix unchecked-js --stale`, while `[[tool.frappe-nix.unchecked-js]]` lists anything. |
| js-unit | `tests` with `js-unit` | At least one `test/unit/**/*.test.ts` is tracked, then `yarn -s test:unit`. |
| nested | | `yarn install` and `typecheck` in each nested frontend that has that script. |
| doctypes | `typescript` | When `types/doctypes.d.ts` is tracked: a doctypes mini-bench, then `frappe-types gen-registry … --check`, with the siblings `hooks.required_apps` names typed in full. |
| audit-consumer | `typescript` | `frappe-types audit-consumer`: `--strict` with `typescript.audit-consumer = true`, otherwise an advisory notice. |
| ci-typecheck | | `yarn -s ci:typecheck`, when present. |

### `frappe-nix gate marketplace`

One step: `frappe-nix listing check` (see [Marketplace](marketplace.md)).

### Running a gate anywhere

The gate needs the app's tools installed, which is what the reusable workflow does before it. On any other CI, or locally:

```bash
yarn install --frozen-lockfile                       # when package.json is live
uv sync --frozen --project tools                     # when tools/pyproject.toml is live
rev="$(jq -r '.nodes[.nodes.root.inputs["frappe-nix"]].locked.rev' flake.lock)"
uv tool install --python 3.14 "frappe-nix-tools @ git+https://github.com/Avunu/frappe-nix@${rev}#subdirectory=py/frappe_nix_tools"

frappe-nix gate lint --base "$(git merge-base origin/develop HEAD)"
frappe-nix gate typecheck
frappe-nix gate marketplace
FRAPPE_NIX_CI=1 nix develop --no-pure-eval -c frappe-test --ci   # the test gate
```

Each gate writes `.dev-dist/gate/<gate>.json` with every step's status. `--format github` groups each step's output in the log and annotates failures. Without `--base`, the ratchet runs only on a pull request (`BASE_SHA`) or a dispatch (`origin/<integration branch>`), as in CI.

## The other workflows

| Caller | Reusable workflow | Present when | What it does |
|---|---|---|---|
| `release.yml` | `app-release.yml` | `ci` and `releases` | release-please on the integration branch, CI on its release PR, and the fast-forward of the release branch (see [GitHub settings](github.md)) |
| `deps.yml` | `app-deps.yml` | `ci` and `dependabot` | Relock and sync after a flake bump, auto-merge by the decision table, the sweep, and the weekly Python relock |
| `nightly.yml` | `app-nightly.yml` | `ci` with `ci.nightly` | The integration suites, screenshots, the listing's links, code duplication, and the drift reminders |
| `assets.yml` | pilot's `app-assets.yml` | `pilot-assets` | pilot's release assets, dispatched by the release workflow |

### Dependency PRs (`deps.yml`)

Dependabot opens the PRs; `deps.yml` decides what happens to them.

- **A nix bump** (frappe and the siblings) changes `flake.lock`, and usually needs `nix/uv.lock` and `nix/node-locks/**` regenerated. `relock-plan` runs the PR's code with no write token, makes the relock and sync patch, and refuses one that touches `.github/workflows/`. `relock-push` only applies that patch, commits it to the PR branch and re-runs CI there. A PR that moves `frappe-nix` or the standards profile is closed: those move only with `frappe-nix repo rollout` (see [Versioning](versioning.md)).
- **Auto-merge** follows this table. It is enabled only when the integration branch requires every enabled gate's `ci / <gate>` check, and the repository allows auto-merge. Otherwise the PR is labelled `needs-review` and the log names `frappe-nix repo doctor`.

  | Ecosystem | Merged automatically | Labelled `needs-review` |
  |---|---|---|
  | GitHub Actions | the `actions` group | the docs-site actions group and the `pilot` group |
  | npm | minor and patch | `/docs-site`, and any major |
  | uv (`/tools`) | minor and patch | major |
  | pre-commit | the `hooks` group | the `test-utils` group |
  | nix | after the relock lands, or when there was nothing to relock | a move of `frappe-nix` or `standards-profile` is closed instead |
  | git submodules | always | |

- **The sweep** re-dispatches `ci.yml` and `pr-policy.yml` on bot PRs that have no `ci / *` run on their head, which covers dispatches a Dependabot-triggered job could not make.
- **The weekly relock** runs `nix run .#relock -- --upgrade` and opens `chore(deps): relock python` on `standards/relock-python`.

No workflow ever pushes a change to `.github/workflows/`: GitHub refuses that from `GITHUB_TOKEN`, so every pushing job refuses such a patch first.

### Nightly (`nightly.yml`)

| Job | Runs when | What |
|---|---|---|
| integration | `tests` | `frappe-test --ci --keep-up`, `bench build`, then each `nightly-suites` command against the running site |
| shots | `screenshots` | `frappe-shots --update`; changed screenshots go to a `standards/screenshots` PR that is never merged automatically |
| links | `listing` | The listing's website and documentation answer 200; with `icons`, the raster icon checks |
| duplication | `test-utils` with `check_code_duplication` | Informational |
| registry-refresh | `listing.publish` | Rebuilds the open registry PR on upstream `main` |
| drift | always | Keeps the "frappe-nix vX.Y.Z available" and "standards profile update available" issues, which name the `frappe-nix repo rollout` command |

## Schedules

Every schedule is a profile parameter, `ci.schedules`, and `""` turns one off:

| Key | Default | Why |
|---|---|---|
| `ci` | `0 6 * * *` | An auto-merge made with `GITHUB_TOKEN` raises no push, so the integration branch's checks catch up daily |
| `release` | `30 5 * * *` | The same, for the release PR |
| `deps-sweep` | `0 9 * * *` | The sweep |
| `deps-relock` | `0 8 * * 1` | The weekly Python relock |
| `nightly` | `0 7 * * *` | The nightly jobs |

The defaults suit a public repository, where Actions minutes are free. A private one that pays for minutes turns off what it doesn't need; with `ci` or `release` off and auto-merge on, the branch status and the release PR catch up only on the next human push, and `frappe-nix repo doctor` says so.

## Runners

Every Linux job in frappe-nix's reusable workflows runs on `ubuntu-latest` unless the app's `ci.runs-on` names another runner: a runner label, or a list of labels a runner must all have. A profile sets it in `[ci]`, an app in `[tool.frappe-nix.ci]`:

```toml
[ci]
runs-on = ["self-hosted", "nixos"]
```

`frappe-init --sync` then passes it, as JSON, to every job that calls a frappe-nix workflow (`ci.yml`, `pr-policy.yml`, `release.yml`, `deps.yml` and `nightly.yml`):

```yaml
    with:
      app: my_app
      integration-branch: develop
      runs-on: '["self-hosted","nixos"]'
```

Unset, the callers carry no `runs-on` line and are byte for byte what they were before the key existed. `assets.yml` calls pilot's own workflow, which takes no runner, so it stays on GitHub's runners.

A pull request from a fork always runs on `ubuntu-latest`, whatever `ci.runs-on` says: each job's `runs-on` is `${{ github.event.pull_request.head.repo.fork && 'ubuntu-latest' || fromJSON(inputs.runs-on) }}`, so code from outside the repository never runs on your machine.

### A self-hosted NixOS runner

The jobs expect a plain Linux machine with Nix, not GitHub's Ubuntu image. On such a runner (`runner.environment` is `self-hosted`) they skip the hosted image's disk cleanup, the Nix installer, magic-nix-cache and `apt-get`, and get what those provided from Nix instead. The runner needs:

- **Nix** with `nix-command` and `flakes`, the daemon running, and the runner's user in `trusted-users`: the jobs pass `access-tokens` through `NIX_CONFIG` and a binary cache's substituters through the user's configuration, where a hosted runner would edit `/etc/nix/nix.conf` with `sudo`. The local `/nix/store` persists between jobs and is the cache.
- **Tools on `PATH`**: bash, coreutils, git, gnutar, gzip and zstd (checkout and `actions/cache`), and gh, jq, curl, unzip, xz, findutils, gnugrep, gnused, gawk, diffutils, which and nodejs for the job scripts and the setup actions.
- **nix-ld** (`programs.nix-ld.enable = true` on NixOS): `setup-node`, `setup-uv` and the Python uv downloads are binaries built for a standard Linux, which run only through it.
- **No `sudo` and no `apt`**: the `marketplace` job builds mysqlclient with gcc, pkg-config and the MariaDB client headers from nixpkgs, and `lint` and `typecheck` install yarn classic with npm when the runner has none.

A runner takes one job at a time, so one runner per repository runs that repository's jobs in turn. Its home directory outlives the job and is shared by every runner of the same user, so the jobs keep uv's tools (`tool-dir`, `tool-bin-dir`) and a binary cache's credentials (`XDG_CONFIG_HOME`) in the job's `$RUNNER_TEMP`. The runner's own cleanup of `$RUNNER_TEMP` skips read-only files (copies out of the Nix store), so each job's first step, on a self-hosted runner only, clears what an earlier job left there.

## The binary cache

The Nix jobs (`test`, the relock and the nightly jobs) use a binary cache when the organisation sets one, with no change to any app:

- the Actions variable `FRAPPE_NIX_CACHE`: empty for none, `cachix:<name>`, or `attic:<endpoint>/<cache>`;
- the secret `FRAPPE_NIX_CACHE_TOKEN`, which can write. It is an Actions secret only, and only a push or schedule on the integration branch ever sees it;
- the secret `FRAPPE_NIX_CACHE_READ_TOKEN`, which can only read. It is an Actions and a Dependabot secret; leave it unset for a public cache.

Pull requests, Dependabot runs and every job that resolves new third-party code get the read token or none, so nothing they run can poison the cache.

## Private sources

An org profile, a frappe fork or a sibling app in a private repository needs a token to fetch: set the secret `FRAPPE_NIX_FETCH_TOKEN` (Contents read on those repositories; also a Dependabot secret when `dependabot` is on). The callers pass it to every reusable workflow, which gives it to `pin-path` and `minibench`, and to Nix as an access token.

## Security notes

- Every third-party action is pinned to a commit SHA with a version comment, and `.github/zizmor.yml` makes zizmor enforce it. Dependabot moves third-party pins; frappe-nix's own pin moves only with `frappe-init --sync`.
- Every checkout uses `persist-credentials: false`. The three jobs that push (relock-push, relock-upgrade and nightly shots) push to an explicit token URL from that step alone.
- Every workflow starts from `permissions: {}`, and each job asks for exactly what it uses.
- The jobs that hold a write token never install frappe-nix-tools from the PR's `flake.lock`: they read the integration branch's.
