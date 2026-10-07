# Ironclad Platform Interface Spec (frappe-nix v1)

- **Spec version:** 1.1, 2026-10-06 (1.0 plus the review fixes; see Appendix R, "Review log"), with the deviations each implementing PR made recorded in Appendix I. It covers frappe-nix `v1.x` and `[tool.ironclad] schema = 1`.
- **Inputs:**
  - the approved plan (`project-ironclad-coffee.md`: D1–D16, Phases 1 and 1.5, §6, and §7 including the 2026-10-07 defaults);
  - the Phase 0 track results (`p0-results.json`);
  - carbon_frappe on PR #53;
  - frappe-nix `main` at 9e63c96;
  - docusystem v0.1.0;
  - the in-progress frappe-types worktrees (`feat/presets-and-jsdoc`, `feat/gen-doctypes-and-drift-ci`).
- **Audience:**
  - the agents implementing frappe-nix PRs N1–N6 in parallel;
  - the 12 app migrations that consume the result.
- **Normative language:** MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119. "Render" means "produce with the template engine in §3". "The app" means a Frappe app repository in frappe-nix app mode. `<app>` is its Python package name, `<App-hyphen>` is that name with `_` replaced by `-`, and `<major>` is the Frappe major (16).

---

## 0. Decisions this spec makes

These either settle points the plan leaves open or resolve contradictions the Phase 0 tracks found. Every later section assumes them.

| # | Decision | Why |
|---|---|---|
| S1 | **ruff is pinned once, in `tools/uv.lock`.** It runs as `repo: local` hooks through `uv run --frozen --project tools ruff …`. The remote `astral-sh/ruff-pre-commit` hook is forbidden in apps. | The ssort-prek track found D3 and A.4 contradict each other. The uv lock is what carbon_frappe already uses, and one pin then serves the hook, CI, `yarn lint:py` and editors. dependabot `uv` (/tools) is the single mover, so a remote rev and a lock can never disagree. All write-mode hooks give the ssort → isort → lint → format order D4 needs. To keep it the only ruff, N1 removes `ruff`, `pre-commit` and `semgrep` from the generated bench root's dev group (`lib/frappe-workspace.py` `ensure_root`, which adds `ruff>=0.15.0` today), and `ensure-root` drops them from existing roots. |
| S2 | **ssort stays a remote hook.** It is `bwhmather/ssort` with rev floor `0.17.0`, `language_version: python3.14`, and the narrow exclude `'/doctype/([^/]+)/\1\.py$'`. dependabot `pre-commit` moves it. | This is D3 and A.4 as written. prek 0.5.5 accepts the backreference (ssort-prek track). |
| S3 | **The required check contexts are** `ci / pr-policy`, `ci / lint`, `ci / typecheck`, `ci / test` and `ci / marketplace`, each with `integration_id: 15368`. | A job of a reusable workflow reports as `<caller job id> / <job name>` (github-capabilities track). Every caller job that calls an `app-*.yml` gate is named `ci`, and every reusable job name equals the plan's name. |
| S4 | **`pr-policy` has its own caller file** (`.github/workflows/pr-policy.yml`), separate from `ci.yml`. | It must re-run on `edited`, when a PR is retitled. If `ci.yml` itself re-ran on `edited` and skipped `test`, the skipped `ci / test` would replace an earlier failure, and GitHub treats skipped as passing. |
| S5 | **frappe-nix reaches an app only through `ironclad-rollout`** (§5.8), which runs with the user's `gh` token (scope `workflow`). The app's flake input is `github:Avunu/frappe-nix/release-1`. `frappe-init --sync` rewrites the caller workflows' `uses: …@<sha> # vX.Y.Z` from `flake.lock`. **Both** dependabot entries ignore frappe-nix: `github-actions` ignores `Avunu/frappe-nix`, and `nix` ignores the input `frappe-nix`. Dependabot `nix` still moves `frappe` and the siblings, which never touch a workflow file. No workflow ever pushes a commit that changes `.github/workflows/*` (S29). | This changes D9 on purpose. A frappe-nix bump has to rewrite the caller SHAs, and GitHub refuses a `GITHUB_TOKEN` push that creates or changes a workflow file ("refusing to allow a GitHub App to create or update workflow … without `workflows` permission"). `permissions:` can't grant that scope, so relock-push could never finish a frappe-nix bump. One pin plus sync still keeps the workflow SHA and the tool versions identical by construction. The nightly `drift` job and audit row A10 report when an app is behind. A later MINOR can automate the bump if the N4 spike in §4.5 proves a path that works without a PAT. |
| S6 | **frappe-nix stays on `main`.** Its releases are tags `vX.Y.Z`, and a moving branch `release-<major>` (`release-1`) is fast-forwarded to each new tag. | Apps need a ref that only moves on a release, so dependabot nix proposes releases rather than every `main` commit. frappe-nix has no Frappe major to track, so `version-*` branches have no meaning for it. |
| S7 | **App parameters live in `[tool.ironclad]` in the app's `pyproject.toml`.** Facts that can be read from the tracked tree are discovered at sync time, never declared (§2.2). | This keeps one file, and A.3 already puts the override allow-list in pyproject. The registry and pilot ignore unknown `tool.*` tables. |
| S8 | **Managed files use one of five strategies** (§3.2): `whole`, `whole` with `local` regions, `toml-merge`, `json-merge`, `seed` and `blocks`. JSON and JSONC tool configs are customised **only** through `[tool.ironclad.<tool>]` parameters, never through text regions. | Text regions inside JSON need trailing-comma tolerance that oxlint's parser doesn't promise. Parameters can be validated. |
| S9 | **Versions moved by dependabot are floors, not values.** These are pre-commit `rev:`s of third-party hooks, the toolchain `devDependencies` ranges, the versions in `tools/uv.lock`, and third-party `uses:` SHAs in caller files. Sync never lowers one and `--check` accepts anything at or above the floor. | Otherwise every dependabot bump is drift, and sync reverts it. |
| S10 | **One self-contained Python package, `ironclad`, lives at `frappe-nix/py/ironclad`.** It serves both sides. Every data file it reads ships inside the package, under `py/ironclad/ironclad/data/`: the templates, manifest fragments, README templates, node-lock seeds, `known-apps.json`, the schema and the semgrep rules. It reads them through `importlib.resources`. In the Nix dev shell it is built by `lib/ironclad/package.nix`. In no-Nix CI jobs it is installed with `uv tool install "ironclad @ git+https://github.com/Avunu/frappe-nix@<rev>#subdirectory=py/ironclad"`, where `<rev>` is read from the app's `flake.lock`. | `lint`, `typecheck`, `pr-policy` and `marketplace` stay Nix-free (D3), yet they run exactly the tool version the app pins (§6 DoD row 3). A wheel built from the subdirectory contains nothing outside it, so any data kept elsewhere in frappe-nix would be missing in CI. |
| S11 | **The first release uses a `Release-As: <major>.0.0` footer on PR B's squash commit.** PR B is merged with `gh pr merge --squash --body-file <f>`, where the last paragraph of `<f>` is exactly `Release-As: <major>.0.0`. Before merging, `npx release-please release-pr --dry-run` on PR B's head must propose `<major>.0.0`. The repo setting `squash_merge_commit_message` stays `COMMIT_MESSAGES`. No `release-as` key goes in the config. | carbon_frappe cut 16.0.0 with this footer (3c16b00, a direct commit). In a squash of several commits, `COMMIT_MESSAGES` produces a body of `* subject` blocks, and release-please reads only a trailing footer, so the merge body is set explicitly. A `release-as` key in the config would have to be removed after the release, and until then it is drift. |
| S12 | **There is no separate guard-major commit status.** The major rule is part of `ironclad compat` (a prek hook in `lint`) and of `pr-policy`, and both run on the dispatched release PR. | Fewer moving parts. The rule then blocks merging through a required check instead of an advisory status. |
| S13 | **The release flow uses no App.** Its parts: <ul><li>release-please runs with `GITHUB_TOKEN`;</li><li>`dispatch-ci` runs `ci.yml` and `pr-policy.yml` on the release branch;</li><li>a REST fast-forward moves `version-<major>`, with GitHub Actions (integration 15368) as the only bypass actor on `version-*`;</li><li>release runs on a daily cron;</li><li>a nightly `ci.yml` cron runs on develop;</li><li>a daily `sweep` re-dispatches CI for bot PRs whose head has no `ci / *` runs.</li></ul> | This is §7 decision 3 as the github-capabilities track refined it. The org has deploy keys disabled, so a DeployKey bypass isn't available. |
| S14 | **pilot assets are built by `gh workflow run assets.yml --ref version-<major>`,** dispatched after the fast-forward. | pilot's `app-assets.yml` names its release `assets-$GITHUB_REF_NAME`. Called from the release run, which is on `develop`, it would publish `assets-develop`. |
| S15 | **The binary cache is a drop-in, and only trusted runs can write to it.** The caller passes `vars.IRONCLAD_NIX_CACHE` (empty, `cachix:<name>` or `attic:<endpoint>/<cache>`) and two secrets: `IRONCLAD_NIX_CACHE_TOKEN` (write; org **Actions** secret only) and `IRONCLAD_NIX_CACHE_READ_TOKEN` (pull-only; org Actions **and** Dependabot secret; leave it unset for a public cache). The write token reaches a step only when `github.ref == 'refs/heads/develop' && (github.event_name == 'push' \|\| github.event_name == 'schedule')`. Every other run (PRs, Dependabot, dispatched release, relock and sweep runs) gets the read token or none. When `IRONCLAD_NIX_CACHE` is empty, the app's Nix jobs use **no** cache action at all; magic-nix-cache is kept only for frappe-nix's own small self-test closures. | Today there is no cache (ci-timing track). Adding one later is an org variable plus secrets, with no app PR. Dependency bumps run third-party code in the same job, so they must never hold a credential that could poison the cache. magic-nix-cache was throttled in 48 of 48 carbon bench and integration jobs, and it competes with the node_modules caches for the repo's 10 GB Actions cache (9.09 GB already used). |
| S16 | **CI mode is `FRAPPE_NIX_CI=1`, with exactly one `nix develop --no-pure-eval -c frappe-test --ci` per job.** CI mode skips the shell hook's node verify and yarn installs and its banner. The clean `nix build .#default` (a second evaluation) runs on PRs only when the diff touches build inputs (§4.2), and always on develop pushes, schedules and dispatches. | The ci-timing track found 2.1–2.2 minutes of yarn per shell entry, about 1.8 minutes of evaluation per extra `nix develop`, and a 7.6-minute p50 for the bench build. |
| S17 | **frappe-test measures coverage itself.** It runs `coverage run --source=<repo>/<app> -m frappe.utils.bench_helper frappe … run-tests` and never uses frappe's `--coverage`. `coverage` goes into the bench root's dev group. | In app mode, frappe's `--coverage` counts `.frappe-nix/bench/apps/{frappe,erpnext,hrms}` (frappe-nix-runtime track). |
| S18 | **The whitelist and hook test check is dynamic.** It reads the evaluated hooks from `frappe.get_hooks(app_name=…)`, and a target counts as tested when at least one line of its body ran during the same coverage run. | Static parsing misses conditional hooks such as esign's `if frappe_version >= 16:` (rename-mechanics track). Coverage data is already there. |
| S19 | **test_utils provides selected hooks only, at rev floor `v1.30.1`.** The ones that work standalone are in the default stage. `validate_customizations` and `clean_customized_doctypes` are `stages: [manual]`, run in `lint` inside a fabricated sparse mini-bench (§5.9), with a guard that fails when the bench layout is missing. The fixtures are not used. | This follows the test-utils track: outside a bench those two hooks are silent no-ops, `sql_registry` aborts the run, and the repo has no license. |
| S20 | **Registry checks are pinned through frappe-nix flake inputs:** `marketplace` (`github:frappe/marketplace`, locked at 2dd4be42eec1bbedc16a91c95a2c7e7e8728df7e), `pilot` (`github:frappe/pilot/develop`, locked at e2364936eb0a4f89a5309d22fb198056f14ff86a) and `frappe-semgrep-rules` (`github:frappe/semgrep-rules`, default branch `develop`, locked at 81a6e3d47a328249e8ddf04c586e493dd5553002), all `flake = false`. They reach apps through the app's `flake.lock`. The get-app validator runs with the declared dependency apps on its bench, which mirrors `frappe/marketplace#29`. | The plan wants pinned copies, and frappe-nix's dependabot nix moves them. Without the dependency apps on the bench, 5 apps fail ImportCheck for reasons that aren't theirs (registry-dryrun track). |
| S21 | **The semgrep baseline is a multiset.** An entry is keyed by `(rule, path, sha1(normalised matched line))` and carries a `count`. A key found more often than its count fails, and so does a count that is now too high. Baselines only shrink, and publishing requires an empty one. | Line numbers move. OSS semgrep returns `"requires login"` as the fingerprint. Identical matched lines share a key: erpnext_taskview's `api.py` has 12 identical `frappe.db.commit()` lines, and a set would let one entry cover any number of new ones. |
| S22 | **The `<app>/desktop_icon/<app>.json` fixture is committed**, for application-type apps only. It is generated by `frappe-icon` and checked for freshness. The PNGs are not committed. | This deviates from D14 because frappe v16 imports `desktop_icon/*.json` from the installed package (`import_desktop_icon_fixtures`). A file that isn't committed never ships. |
| S23 | **Screenshots are made repeatable** by running the bench under libfaketime with a fixed start time (`FAKETIME="@<demo date> 09:00:00"`, so the clock advances from there), plus CDP timezone, locale and media overrides, no animations, masks, and lossless WebP. The refresh PR is never merged automatically. | ERPNext demo data is relative to "today". Freezing the clock outright would hang timeouts. |
| S24 | **Ratchets** are coverage `fail_under`, the `ty: ignore` count, semgrep baseline counts, and the `[[tool.ironclad.untested]]`, `[[tool.ironclad.unchecked-js]]` and `[[tool.ironclad.coverage-omit]]` lists. They are compared against the PR's merge-base (`git merge-base "$BASE_SHA" HEAD`), never the base tip. They apply only once the base already has `[tool.ironclad]`, which is from PR B onward, and are skipped on develop pushes and schedules. Coverage also ratchets upward: frappe-test fails when coverage has climbed 2 points past a `fail_under` below 80 (§5.1 stage 4). | PR B is the PR that sets each starting value. The base tip would fail a PR that branched before develop raised `fail_under`. Decision 5 wants a floor that only rises towards 80, and a notice alone never raises it. |
| S25 | **Release PRs get screenshots from the nightly run, not a dispatched run.** | A release branch differs from develop only in the version and changelog, so a separate run would show nothing new. This adjusts D16. |
| S26 | **The README "Development" block links to `https://frappe-nix.avunu.net/docs/ironclad/`.** | That site exists and returns 200. avunu-docs `Frappe/App Standards/` has no public URL yet. The link changes in one frappe-nix release when it does. |
| S27 | **`ruff` profile.** Every app gets carbon_frappe's profile: <ul><li>`select = F,E,W,I,UP,B,RUF,SIM,C4,PIE,PERF,T20`;</li><li>`ignore` is exactly `E501`, `W191`;</li><li>tabs, 110 columns, `typing-modules = ["frappe.types.DF"]`.</li></ul> frappe-nix's own Frappe-tracking ruff config (v0.14.10) is unrelated and unchanged. | This is D4. The completeness critic confirmed it isn't a user decision. |
| S28 | **`[tool.bench.frappe-dependencies]` ranges always use the comma form** `">=16.0.0,<17.0.0"`. `payments` is `">=0.0.1,<1.0.0"`. | `packaging.SpecifierSet`, which pilot uses, rejects the `-dev` space form (completeness critic). payments' `version-16` `__version__` is 0.0.1 (registry-dryrun track). |
| S29 | **No workflow pushes a commit that changes `.github/workflows/*`.** relock-push, relock-upgrade and nightly `shots` refuse a patch that touches that path (exit 1, naming the file). Moving `version-*` and `release-*` with a REST ref PATCH to a commit that already exists on develop or main is allowed. | GitHub refuses such pushes from `GITHUB_TOKEN`. The REST fast-forward is proven: carbon_frappe's `version-16` was moved by github-actions[bot] from 1ed9df5 to 30a4be4 across workflow changes that were already on develop. |
| S30 | **Vite outputs are registered by the app's own build, not only by Nix.** Every app with a tracked Vite config gets the managed `scripts/ironclad-vite-register.mjs`, and its `build` script must end with `node scripts/ironclad-vite-register.mjs`. frappe-nix's esbuild-preload runs the same module (§5.11), and running both is idempotent. | frappe's esbuild.js writes `assets.json` and then runs each app's `yarn build`, so a post-build step in the app works on a stock bench: Frappe Cloud, pilot's get-app bench and registry installs. Without it, `taskview.bundle.js`, `timerdock.bundle.js` and timeclock's bundle 404 everywhere except Nix. |
| S31 | **Legacy files are retired by sync.** A `retire` list in the manifest names files that sync deletes and that `--check` reports as drift (§2.4.1). | Otherwise carbon's and taskview's old `check.yml`, `release-please.yml` and `dependabot-auto-merge.yml` keep running beside the new callers: two release flows race for tags, and an auto-merger ignores `needs-review`. |
| S32 | **Sync bootstraps in two phases** (§3.3): phase A writes `flake.nix` and `.envrc`, locks, and re-reads the frappe-nix rev; phase B renders everything else. App migrations start only after frappe-nix `v1.0.0` is tagged, because `release-1` doesn't exist before that. | postgrid_integration and jwt_auth have no flake; carbon, taskview and timeclock lock frappe-nix `main`. A single pass would render the callers with a rev that the same run then changes. |

---

## 1. Repository layout in frappe-nix, and PR ownership

### 1.1 Landing order

1. **N3a "hook points"** lands first. It is a small PR, owned by the N3 agent, that only creates seams. It MUST merge before any other N-PR is rebased for merge. Every other PR MAY start before it merges, by branching from N3a's branch.
2. **Development** of N1, N2, N3 (the rest), N4, N5 and N6 proceeds in parallel after that.
3. **Merge order** follows what each PR's acceptance tests exercise:

   | Step | PRs | Why |
   |---|---|---|
   | 1 | N3a | Seams, the data directory, the schema. |
   | 2 | N1 and N3, in either order | N1 needs only N3a's loaders. N3 is the sync engine everything renders through. |
   | 3 | N2 and N5, after N3 | N2's managed `scripts/ironclad-vite-register.mjs` and N5's README blocks and `marketplace.json` fragment render through N3's engine. |
   | 4 | N4, after N1, N3 and N5 | `selftest-ci` runs `ironclad sync --check` (N3), `frappe-test --ci` (N1) and `ironclad listing check` (N5). |
   | 5 | N6 | frappe-nix's own `pr-policy.yml` runs `ironclad policy` (N4). N6's PR may merge earlier if its policy job is temporarily non-required. |
   | 6 | tag `v1.0.0` | N6's first release PR is merged only after N1–N5 are in, so `v1.0.0` is the first complete platform and `release-1` exists. |

   - The **frappe-nix `main` ruleset** (`ironclad/self/rulesets/main.json`) is applied with `ironclad-apply` only after N4 merges, because it uses N4's tool.
   - **App migrations** (PR A, PR B) start only after `v1.0.0` is tagged (S32).

**Overlap rule.** Only the files listed below as **shared** are touched by more than one PR. For each shared file, N3a creates the seam and the later PR edits only its own hunk. Everything else has exactly one owner, and a PR MUST NOT edit a file it doesn't own. If a PR needs a change in another PR's file, it states that in its PR description and the owner makes the change.

### 1.2 The tree

`[N…]` marks the owner. `(shared)` files list each PR's hunk.

```
frappe-nix/
├── version.txt                                [N3a creates "0.0.0"; N6 makes release-please own it]
├── release-please-config.json                 [N6]
├── .release-please-manifest.json              [N6]
├── committed.toml                             [N6]
├── CHANGELOG.md                               [release-please]
├── flake.nix  (shared)
│     N3a: three new flake=false inputs (marketplace, pilot, frappe-semgrep-rules);
│          `packages`/`apps` merged with `import ./lib/ironclad/outputs.nix`;
│          `checks` merged with `import ./tests/ironclad { … }`
│     nobody else edits flake.nix
├── flake.lock                                 [N3a for the new inputs; afterwards dependabot]
├── py/ironclad/                               Python package `ironclad` (S10); self-contained
│   ├── pyproject.toml                         [N3a]  deps: jinja2>=3.1,<4; tomlkit>=0.13 (no upper cap: the
│   │                                                  locked nixpkgs ships 0.15.0); packaging>=24.
│   │                                                  [tool.setuptools.package-data] or flit's include ships ironclad/data/**
│   ├── ironclad/__init__.py                   [N3a]  release-please version block (N6 adds it to extra-files)
│   ├── ironclad/cli.py                        [N3a]  `ironclad <command>`; imports every module in ironclad/commands/
│   ├── ironclad/common/                       [N3a]  repo.py, flakelock.py, pyproject.py (tool.ironclad loader),
│   │                                                  known_apps.py, gh.py (thin `gh api` wrapper), report.py,
│   │                                                  pins.py (fetch a flake-locked GitHub tree into .dev-dist/pins/
│   │                                                  and verify its narHash), nar.py
│   ├── ironclad/commands/paths.py             [N3a]  `ironclad data-path <rel>` (importlib.resources over
│   │                                                  ironclad/data), `ironclad pin-path <input>` for any locked
│   │                                                  GitHub input, frappe-nix's (frappe-semgrep-rules, marketplace,
│   │                                                  pilot) or the app's own (frappe, erpnext, …)
│   ├── ironclad/data/                         everything sync, listing and the hooks read at run time (S10):
│   │   ├── schema/tool-ironclad.schema.json   [N3a; N3 extends it, others ask N3]
│   │   ├── known-apps.json                    [N3a]
│   │   ├── manifest.d/core.json               [N3]
│   │   ├── manifest.d/assets.json             [N2]   (scripts/ironclad-vite-register.mjs)
│   │   ├── manifest.d/ci.json                 [N4]
│   │   ├── manifest.d/marketplace.json        [N5]
│   │   ├── templates/**                       [N3]   except templates/.github/** [N4], templates/marketplace/** [N5],
│   │   │                                              templates/scripts/ironclad-vite-register.mjs [N2]
│   │   ├── readme/*.md.j2                     [N5]
│   │   ├── node-locks/version-16/{frappe/ui,erpnext/banking,hrms/frontend,hrms/roster}/{yarn.lock,source.json} [N3]
│   │   ├── semgrep/test-correctness.yml       [N4]   (carbon_frappe's semgrep/test-correctness.yml)
│   │   ├── ci/nightly.sh                      [N4]   (§4.6)
│   │   └── shots/shots.d.ts                   [N5]   (copy of lib/shots/shots.d.ts; a nix check asserts equality)
│   ├── ironclad/commands/sync.py              [N3]   `ironclad sync --write|--check`
│   ├── ironclad/commands/compat.py            [N3]
│   ├── ironclad/commands/unchecked_js.py      [N3]   `ironclad unchecked-js --stale` (§2.9)
│   ├── ironclad/commands/config.py            [N3a]  `ironclad config <key>` (prints a [tool.ironclad] value)
│   ├── ironclad/commands/testmap.py           [N1]   plus ironclad/bench/{testmap_probe,composition}.py [N1]
│   ├── ironclad/commands/policy.py            [N4]
│   ├── ironclad/commands/ratchet.py           [N4]
│   ├── ironclad/commands/minibench.py         [N4]
│   ├── ironclad/commands/{apply,audit,rollout}.py [N4]
│   ├── ironclad/commands/listing.py           [N5]   plus ironclad/listing/** [N5]
│   ├── ironclad/commands/icon.py              [N5]   plus ironclad/icon/** [N5]
│   └── tests/test_<module>.py                 [owner of <module>]
├── lib/ironclad/
│   ├── package.nix                            [N3a]  python314 build of py/ironclad against the locked nixpkgs
│   │                                                  (pythonRuntimeDepsCheck stays on; a nix check builds it)
│   ├── outputs.nix                            [N3a]  reads lib/ironclad/tools/*.nix → {packages, apps}
│   ├── shell.nix                              [N3a]  app-mode dev-shell packages and enterShell snippet
│   └── tools/
│       ├── ironclad.nix                       [N3a]
│       ├── ironclad-apply.nix, ironclad-audit.nix, ironclad-rollout.nix   [N4]
│       └── frappe-listing.nix, frappe-icon.nix, frappe-shots.nix          [N5]
├── lib/scripts.nix  (shared)                  N3a: one line merging lib/scripts.d/*.nix; nobody else
├── lib/scripts.d/
│   ├── frappe-test.nix, frappe-rename-app.nix [N1]
│   └── frappe-demo.nix, frappe-shots.nix      [N5]
├── lib/sh/frappe-test.sh                      [N1]
├── lib/sh/app-sync.sh                         [N3]
├── lib/sh/{main.sh,app-init.sh}               [N3]   (new flags: --sync, --check, --format, --only)
├── lib/init.nix                               [N3]   (adds app-sync.sh and the ironclad runtime input)
├── lib/rename/frappe_rename_app.py            [N1]   (ported from the Phase 0 rename-mechanics prototype, `run/frappe_rename_app.py`)
├── lib/demo/frappe_demo.py                    [N5]
├── lib/shots/{cdp.ts,runner.ts,diff.ts,shots.d.ts,package.json,yarn.lock} [N5]
├── lib/js/esbuild-preload.js                  [N2]   (requires lib/js/vite-register.cjs)
├── lib/js/vite-register.cjs                   [N2]   the one registration implementation (S30); the managed
│                                                      scripts/ironclad-vite-register.mjs is a build-time copy of it
├── lib/node-targets.nix, lib/node-locks.nix   [N2]
├── lib/frappe-workspace.py                    [N1]   (`coverage` in the generated root's dev group; `ruff`,
│                                                      `pre-commit` and `semgrep` removed from it, also from existing roots)
├── templates/bench/pyproject.toml             [N1]   (same)
├── modules/devenv.nix  (shared)
│     N3a: app-mode `apps.frappe-init`; import of lib/ironclad/shell.nix
│     N1:  port offset salt and FRAPPE_NIX_PORT_OFFSET; FRAPPE_NIX_CI in enterShell;
│          nixfmt/statix/deadnix in the app-mode shell; frappe-nix.renamedApps
├── modules/nixos.nix                          [N1]   (services.frappe.sites.<site>.renamedApps)
├── templates/app/
│   └── .envrc, .gitignore                     [N3]   the only files left here (.gitignore body updated, §2.6).
│                                                      flake.nix moves to py/ironclad/ironclad/data/templates/flake.nix.j2.
│                                                      `frappe-init --app` copies this tree with install_template, so
│                                                      nothing else may live here; cmd_app_init then calls
│                                                      `ironclad sync --write` for every other file (§3.3)
├── ironclad/
│   ├── repo-settings.json                     [N4]
│   ├── rulesets/{develop,develop-provisional,version,tags}.json   [N4]
│   ├── apps.json                              [N4]
│   └── self/{repo-settings.json,rulesets/{main,release,tags}.json} [N6]
├── .github/
│   ├── workflows/app-ci.yml, app-pr-policy.yml, app-release.yml, app-deps.yml, app-nightly.yml [N4]
│   ├── workflows/ironclad-audit.yml           [N4]   (org-wide nightly scorecard)
│   ├── workflows/selftest-runtime.yml         [N1]
│   ├── workflows/selftest-assets.yml          [N2]   (stock-bench Vite registration, §7)
│   ├── workflows/selftest-scaffold.yml        [N3]
│   ├── workflows/selftest-ci.yml              [N4]
│   ├── workflows/selftest-product.yml         [N5]
│   ├── workflows/check.yml  (shared)
│   │     N3a: the "offline checks" step builds `.#checks.x86_64-linux.ironclad-all` in addition to its list
│   │     N6:  SHA-pin actions/checkout; add a `vm-tests` boolean dispatch input (default false) and
│   │          gate the vm-tests job on `inputs.vm-tests == true` instead of on any dispatch
│   ├── workflows/pr-policy.yml                [N6]   frappe-nix's own, non-reusable `pr-policy` job (§4.10)
│   ├── workflows/release.yml                  [N6]
│   ├── workflows/dependabot-auto-merge.yml    [N6]   (SHA pin; skip rules unchanged)
│   └── dependabot.yml                         [N6]
├── tests/ironclad/default.nix                 [N3a]  reads tests/ironclad/*.nix; exposes `ironclad-all` (linkFarm)
├── tests/ironclad/hookpoints.nix              [N3a]  ironclad-cli, ironclad-loaders, ironclad-app-flake (§7)
├── tests/ironclad/fixtures/                   [N3a]  a test scripts.d file, a clashing one, and a test tools file
├── ty.toml, dev/env.nix                       [N3a]  py/ironclad on ty's extra-paths and the dev env's sourceRoots
├── tests/ironclad/<area>.nix                  [owner of <area>] (sync, compat, ports, preload-vite, node-targets-docs-site,
│                                                       policy, ratchet, listing, icon, …)
├── tests/fixtures/ironclad-app/               [N3a: app sources and a hand-rendered flake.nix (§2.5); N3: the managed
│                                                      files sync renders, committed, flake.nix included]
├── tests/fixtures/spa-app/                    [N2]   (root-level Vite config, a `portal/` SPA with no package.json,
│                                                      `[[tool.ironclad.typescript.spa]]` entries)
├── tests/fixtures/rename-esign/               [N1]   (hooks naming `esign.desk.bundle.js`, the matching files)
└── docs/ironclad/
    ├── README.md (index, links to all pages)  [N3a]
    └── <page>.md                              [N3a creates one-line stubs; each owner fills its page:
                                                managed-files.md N3, testing.md N1, assets.md N2, ci.md N4,
                                                github.md N4, marketplace.md N5, icons.md N5, screenshots.md N5,
                                                versioning.md N6, rename.md N1]
```

### 1.3 What each PR delivers

- **N3a, hook points.** It delivers:
  - the seams listed above;
  - `ironclad --version` and the command dispatcher;
  - the `py/ironclad/ironclad/data/` layout, `ironclad data-path`, and `data/known-apps.json`;
  - the fixture app sources in `tests/fixtures/ironclad-app/`: an app `ironclad_fixture` with one DocType, one whitelisted function with a test, one deliberately untested whitelisted function listed in `[[tool.ironclad.untested]]`, `doc_events` on ToDo, one scheduler job, `extend_doctype_class` on ToDo, `demo.py`, `marketplace/listing.toml`, the icon pair and `marketplace/screenshots.ts`;
  - the new flake inputs;
  - the docs stubs.

  It has no behaviour of its own.
- **N1, runtime.** It delivers:
  - `frappe-test`;
  - coverage scoping;
  - `coverage` in the bench root dev group;
  - the testmap and composition checks;
  - `FRAPPE_NIX_CI`;
  - nixfmt, statix and deadnix in the app-mode shell;
  - the worktree port salt and `FRAPPE_NIX_PORT_OFFSET`, with the documentation corrections;
  - `frappe-rename-app` (code and site halves) and the `renamedApps` and `replacedApps` NixOS and devenv options;
  - the `shell-checks` stage of `frappe-test --ci`;
  - ruff, pre-commit and semgrep removed from the generated bench root's dev group;
  - `selftest-runtime.yml`.

  N1 MAY split the rename work into a follow-up PR, N1b.
- **N2, assets.** It delivers:
  - Vite manifest registration as one module, `lib/js/vite-register.cjs`, run by `esbuild-preload.js` (with an unconditional `child_process` wrap) and shipped to apps as the managed `scripts/ironclad-vite-register.mjs` (S30), plus its `manifest.d/assets.json` fragment and the `ironclad compat` rule C8;
  - the default `docs-site` exclusion in `node-targets.nix` and `node-locks.nix`;
  - the `tests/fixtures/spa-app/` builtBench check;
  - `docs/ironclad/assets.md`, which covers the `<name>.bundle.[hash]` naming convention, `bundled_asset()` in www templates and `[tool.bench.assets]` for pilot.
- **N3, scaffold.** It delivers:
  - the sync engine, including the two-phase bootstrap (S32) and the `retire` list (S31);
  - every template in `manifest.d/core.json`;
  - `frappe-init --sync` and `--check`; `templates/app/` reduced to `.envrc` and `.gitignore`, and `cmd_app_init` delegating everything else to `ironclad sync --write`;
  - `ironclad compat`;
  - node-lock seeding;
  - the committed managed files of the fixture app;
  - `selftest-scaffold.yml`.
- **N4, CI.** It delivers:
  - the five reusable workflows;
  - the caller templates (`manifest.d/ci.json`): `ci.yml`, `pr-policy.yml`, `release.yml`, `deps.yml`, `nightly.yml`, `assets.yml`, `dependabot.yml` and `zizmor.yml`;
  - `ironclad policy`, `ironclad ratchet`, `ironclad minibench`, `ironclad-apply`, `ironclad-audit` and `ironclad-rollout`;
  - `ironclad/*.json`;
  - `selftest-ci.yml`;
  - `ironclad-audit.yml`.
- **N5, product tools.** It delivers:
  - `frappe-listing check|registry|readme`;
  - `frappe-icon`, `frappe-demo` and `frappe-shots`;
  - the README block templates;
  - `marketplace/shots.d.ts`;
  - `selftest-product.yml`.
- **N6, frappe-nix self-release.** It delivers:
  - release-please for frappe-nix;
  - `release.yml`, which runs release-please, dispatch-ci, the `release-1` fast-forward and the `v*` tags;
  - `committed.toml`;
  - SHA pins across frappe-nix's own workflows;
  - frappe-nix's own `pr-policy.yml` and the `vm-tests` dispatch input on `check.yml`;
  - `ironclad/self/*.json`;
  - frappe-nix's dependabot commit prefixes (§6.3).

### 1.4 Seam interfaces (N3a)

What N3a's seams accept, so each later PR adds files without editing the seam.

| Seam | A contribution is | Signature |
|---|---|---|
| `py/ironclad/ironclad/commands/<name>.py` | a module; `cli.py` imports every module there, in name order | `register(subparsers)`, adding one or more subcommands with `set_defaults(func=run)`, where `run(args) -> int` is the exit code. Raise `ironclad.common.report.IroncladError` (`ConfigError` → 2, `EnvError` → 3) for a one-line failure. |
| `py/ironclad/ironclad/data/**` | a file | Read with `ironclad.common.data_path(rel)` (a `Path`), or printed by `ironclad data-path <rel>`. |
| `lib/ironclad/tools/<name>.nix` | a file; becomes `packages.<name>`, `apps.<name>`, a dev-shell package in app mode, and `.#<name>` in an app's flake. A name the flakes already use (`default`, `frappe-init`, `backup-fetch`, `relock`) fails evaluation. | `{ pkgs, lib, ironclad, ... }: <derivation with meta.mainProgram>`. It must propagate nothing (no `propagatedBuildInputs`, no Python package such as `ironclad` itself): the dev shell would put a propagated Python's site-packages on `PYTHONPATH`, ahead of the bench venv's. Wrap a Python program's `bin/` instead, as `tools/ironclad.nix` does; `ironclad-app-flake` fails otherwise. |
| `lib/scripts.d/<name>.nix` | a file; merged over `lib/scripts.nix`'s scripts in name order. Redefining any script fails evaluation. | `{ lib, pkgs, appMode, lockDir, pythonBin, benchBin, atBench, atRepo, siteFlag, … }: { <script> = { exec; description; }; }` (every `lib/scripts.nix` argument and snippet: also `offlineMigrateEnv`, `workspaceBin`, `registerWorkspaceMember`, `refreshNodeModules[Soft]`, `regenNodeLocks[Soft]`, `syncRegistry`; take `...`; see `lib/scripts.d/README.md`) |
| `tests/ironclad/<area>.nix` | a file; its checks join the flake's `checks` and `ironclad-all`. Every check is named `ironclad-<name>` (no frappe-nix check is), so none can replace another. Two areas defining the same check, a name without the prefix, or `ironclad-all` fail evaluation. | `{ pkgs, lib, self, inputs, ironclad, ... }: { ironclad-<check> = <derivation>; }` |
| `lib/ironclad/shell.nix` | (N3a only) | `{ pkgs }: { packages, apps, enterShell }`; `modules/devenv.nix` adds all three in app mode. |

`ironclad pin-path <input>` reads `--lock`, by default the nearest `flake.lock` at or above the current directory without leaving the git work tree (so an app in a subdirectory, like frappe-nix's `tests/fixtures/ironclad-app`, reads its own), falling back to the work tree root's. It prints the Nix store path when the tree the lock's `narHash` implies is already in the store, and otherwise fetches it into `.dev-dist/pins/<repo>-<rev>/` beside that lock. A tree already there is reused only after its NAR hash is recomputed and matches the lock; otherwise it is refetched (no stamp file is trusted, since `.dev-dist` sits in the app checkout, where a PR could commit any tree under that name). A locked `owner` or `repo` that is not a GitHub name, or a `rev` that is not a 40-hex SHA, exits 2 before anything is fetched or written. frappe-nix's own pins (`frappe-semgrep-rules`, `marketplace`, `pilot`) are read only through the lock's `frappe-nix` node (the root's inputs only in frappe-nix's own lock, which has no `frappe-nix` input), so a root input of the same name never shadows them, and each must lock `frappe/semgrep-rules`, `frappe/marketplace` or `frappe/pilot` respectively (case-insensitively); any other repository exits 2. Any other input is the root's own, else frappe-nix's. `IRONCLAD_PIN_URL` (with `{owner}`, `{repo}`, `{rev}`) replaces the GitHub tarball URL, for mirrors and tests. `ironclad config <key>` reads `--pyproject`, by default the nearest `pyproject.toml` found the same way, takes dotted keys (`test.setup`), applies the schema's `default`s, prints a list one item per line, a boolean as `true`/`false`, and a table as one line of JSON.

---

## 2. What an app gets: the managed-file contract

### 2.1 `[tool.ironclad]`: the app's parameters

`[tool.ironclad]` lives in the app's `pyproject.toml` and is **app-owned**: sync creates it once, never rewrites it, and validates it against `ironclad/data/schema/tool-ironclad.schema.json`. Unknown keys are errors (exit 2).

```toml
[tool.ironclad]
schema = 1                                   # required
frappe-major = 16                            # required. Drives every version-<major> string, range and branch
siblings = ["erpnext", "hrms"]               # bench apps besides frappe, in install order. Each is a key of
                                             # ironclad/data/known-apps.json or "Avunu/<repo>". Default when created:
                                             # hooks.required_apps (bare names), in their order
site = "carbon-theme.localhost"              # optional; default "<App-hyphen>.localhost"
pilot-assets = false                         # true → managed .github/workflows/assets.yml and the release dispatch
track-overrides = false                      # true → the test_utils track_overrides hook (needs HASH:/REPO: annotations)
js-coverage-min = 50                         # node:test line-coverage minimum; only used when test/unit/ exists
nightly-suites = []                          # shell commands nightly runs inside the dev shell with the site up,
                                             # e.g. ["node scripts/test-tables.ts"]; env FRAPPE_SITE_URL,
                                             # FRAPPE_ADMIN_PASSWORD and IRONCLAD_ARTIFACT_DIR are set (§4.6)
generated = []                               # globs of tracked generated files, e.g. ["<app>/public/js/generated/**",
                                             # "<app>/public/js/job_timeclock/types.ts", "components.d.ts"].
                                             # Fed into the prek global exclude, oxfmt and oxlint ignorePatterns, and
                                             # the validate_copyright exclude (§2.14). Their freshness is the app's
                                             # job, through shell-checks or ci:typecheck
build = false                                # true: the app's `build` script is a real build step even though no Vite
                                             # config, nested frontend or SPA exists (carbon's patch-assets/audits)
shell-checks = []                            # commands that need the dev shell and a git-clean result afterwards, e.g.
                                             # ["yarn -s generate-types"]; run by `frappe-test --ci` (§5.1 stage 8b)
frappe-node-modules = false                  # true: typecheck installs frappe's node_modules under FRAPPE_PATH (§4.2)

[tool.ironclad.test]
setup = []                                   # steps before the app's tests. Each is "module:<dotted test module>"
                                             # (bench run-tests --module) or "execute:<dotted callable>".
                                             # Default when empty: "module:erpnext.tests.bootstrap_test_data" if
                                             # erpnext is a sibling, else "execute:frappe.utils.install.complete_setup_wizard"

[[tool.ironclad.untested]]                   # testmap exemptions; shrink-only (S24)
target = "ironclad_fixture.api.legacy"       # dotted path of a target (§5.1.1)
reason = "Removed in 16.2; kept for old kiosk bundles"   # ≥ 10 characters

[[tool.ironclad.coverage-omit]]              # extra coverage omit globs; shrink-only (R7), amber in audit A8
glob = "timeclock/www/job_timeclock_legacy/*"
reason = "Legacy kiosk page, replaced by job_timeclock in 16.3"     # ≥ 10 characters

[[tool.ironclad.unchecked-js]]               # desk/web JS files excluded from strict checkJs; shrink-only (R6),
path = "esign/public/js/controls/upload.js"  # amber in audit A7. A path, not a glob; it must be a tracked .js file
reason = "Pre-typing legacy control; JSDoc pass tracked in #41"     # ≥ 10 characters

[[tool.ironclad.override-doctype-class]]     # A.3 allow-list. Every override_doctype_class hook needs an entry
doctype = "Version"
reason = "Must replace, not extend: …"       # ≥ 20 characters

[tool.ironclad.release]
bootstrap-sha = "3c33bb4b…"                  # optional; rendered into release-please-config.json

[tool.ironclad.typescript]                   # all optional
browser = true                               # false: no tsconfig.browser.json even when public/**/*.ts is tracked
browser-include = []                         # replaces the default browser include (§2.9)
web-include = []                             # globs of JS under the package that run on web pages, not the desk
                                             # (e.g. "esign/public/js/web/**"): moved from desk to web in tsc and oxlint
paths = {}                                   # compilerOptions.paths, in tsconfig.base.json
exclude = []                                 # appended to every project's exclude. Only non-source paths: an entry
                                             # matching a tracked .js/.ts/.vue file under the package is exit 2
                                             # (use unchecked-js or an spa entry)
audit-consumer = false                       # true: typecheck runs `frappe-types audit-consumer --strict` (§4.2)

[[tool.ironclad.typescript.spa]]             # a Vue/Vite SPA that lives at the root or inside the package and has no
root = "portal"                              # package.json of its own. "." for the repo root (timeclock)
include = ["portal/src/**"]                  # its sources; removed from the browser, desk and web projects
tsconfig = "portal/tsconfig.json"            # app-owned; MAY extend another SPA's app-owned config (portal's
                                             # "../tsconfig.json" when an SPA owns the root), never a managed file
check = "vue-tsc --noEmit -p portal/tsconfig.json"   # run by `typecheck` and chained into scripts.check

[tool.ironclad.oxlint]
ignore = []                                  # appended to ignorePatterns
globals = {}                                 # merged into the desk-JS override's globals
overrides = []                               # appended; each {files=[…], rules={…}, globals={…}}.
                                             # It MUST NOT name typescript/no-explicit-any, typescript/ban-ts-comment,
                                             # typescript/consistent-type-imports, or change categories (exit 2)

[tool.ironclad.oxfmt]
ignore = []                                  # appended to ignorePatterns

[tool.ironclad.stylelint]
globs = []                                   # default: discovered "<app>/public/**/*.scss" when any exist
```

How the two SPA layouts in the fleet are declared:

```toml
# erpnext_taskview: desk Vue in public/js built by the root vite.config.ts, plus portal/ (no package.json)
[[tool.ironclad.typescript.spa]]
root = "."
include = ["erpnext_taskview/public/js/**"]
tsconfig = "tsconfig.json"                   # app-owned, so the managed solution is tsconfig.ironclad.json
check = "vue-tsc --noEmit -p tsconfig.json"
[[tool.ironclad.typescript.spa]]
root = "portal"
include = ["portal/src/**"]
tsconfig = "portal/tsconfig.json"            # keeps "extends": "../tsconfig.json"
check = "vue-tsc --noEmit -p portal/tsconfig.json"

# timeclock: vite.config.ts and tsconfig.json at the root, sources in the package
[[tool.ironclad.typescript.spa]]
root = "."
include = ["timeclock/public/js/timeclock/**", "timeclock/public/js/job_timeclock/**"]
tsconfig = "tsconfig.json"
check = "vue-tsc --noEmit -p tsconfig.json"
```

`ironclad/data/known-apps.json` (N3a) holds the template for each known sibling. `{n}` is the major and `{n1}` is the major plus one.

```json
{
  "frappe":   { "repo": "frappe/frappe",   "branch": "version-{n}", "range": ">={n}.0.0,<{n1}.0.0", "desk_global": null },
  "erpnext":  { "repo": "frappe/erpnext",  "branch": "version-{n}", "range": ">={n}.0.0,<{n1}.0.0", "desk_global": "erpnext" },
  "hrms":     { "repo": "frappe/hrms",     "branch": "version-{n}", "range": ">={n}.0.0,<{n1}.0.0", "desk_global": "hrms" },
  "payments": { "repo": "frappe/payments", "branch": "version-{n}", "range": ">=0.0.1,<1.0.0",       "desk_global": null },
  "Avunu/*":  { "repo": "Avunu/{name}",    "branch": "version-{n}", "range": ">={n}.0.0,<{n1}.0.0", "desk_global": null }
}
```

For an `Avunu/<repo>` sibling, the app name is the repo name. The flake input name is the app name. The flake URL is `github:<repo>/<branch>`. The `required_apps` spelling is `"Avunu/<repo>"` (D6). The `frappe-dependencies` key is the bare app name.

### 2.2 Discovered facts (never declared)

Sync computes these from `git ls-files` (tracked files only), so the result is the same on every machine. `--check` recomputes them, so adding a first `.ts` file, for example, shows up as drift until sync is run.

| Fact | Rule |
|---|---|
| `spa_globs` | The union of every `[[tool.ironclad.typescript.spa]].include`, plus `<root>/**` for each SPA whose root isn't `.` |
| `desk_js` | Tracked `<app>/**/*.js` excluding `*.bundle.js`, `**/public/dist/**`, `**/node_modules/**`, `<app>/www/**`, `**/web_form/**`, `<app>/templates/**`, every nested frontend, `spa_globs`, `typescript.web-include` and `cfg.generated` |
| `web_js` | Tracked `<app>/www/**/*.js`, `<app>/**/web_form/**/*.js`, `<app>/templates/**/*.js` and `typescript.web-include`, excluding `spa_globs` and `cfg.generated` |
| `browser_ts` | `typescript.browser` is not `false`, and either a non-empty `typescript.browser-include` or tracked `<app>/public/**/*.ts` (not `public/dist`) that remain after removing `spa_globs` and `cfg.generated` |
| `vite` | A tracked `vite.config.*` or `vite.*.config.*` at the root or in a nested frontend |
| `solution` | `"tsconfig.json"`, or `"tsconfig.ironclad.json"` when any SPA's `tsconfig` is `tsconfig.json` |
| `scripts_ts` | Tracked `scripts/**/*.ts`, `marketplace/**/*.ts` or `ci/**/*.ts` |
| `test_ts` | Tracked `test/**/*.ts` |
| `unit_tests` | Tracked `test/unit/**/*.test.ts` |
| `scss` | `stylelint.globs`, or `["<app>/public/**/*.scss"]` when any such file is tracked |
| `nested_frontends` | Top-level directories other than `docs-site`, `node_modules`, `.frappe-nix` and `nix` that contain a tracked `package.json`. A frontend without its own `package.json`, such as taskview's `portal/`, is declared as an SPA instead. |
| `docs_site` | A tracked `docs-site/package.json` |
| `gitmodules` | A tracked `.gitmodules` |
| `has_listing` | A tracked `marketplace/listing.toml` |
| `has_shots` | A tracked `marketplace/screenshots.ts` |
| `app_type` | `listing.toml` `type`; `"extension"` when there is no listing |
| `repo` | `origin`'s URL normalised to `Avunu/<repo>`. If no remote: `Avunu/<app>`. |
| `frappe_nix` | `{rev, version, major}`. `rev` is `flake.lock` → `nodes[root.inputs["frappe-nix"]].locked.rev`, read **after** sync's phase A (§3.3), so a missing flake or a `main`-locked frappe-nix is fixed before anything uses it. `version` is the running `ironclad.__version__`. `--check` fails with exit 3 if the running version's tag commit ≠ `rev` and the environment variable `IRONCLAD_ALLOW_SKEW` is unset (§3.7). |

### 2.3 Template context

Every Jinja template (`StrictUndefined`; files end in `.j2`) receives this context:

```
app, app_hyphen, dist (pyproject [project].name), repo, title, tagline (listing → hooks app_title/app_description),
frappe = {major, next, branch: "version-<major>", preset: "version-<major>", range},
siblings = [{name, input, flake_url, range, desk_global, required: bool}],   # required = in hooks.required_apps
site, cfg (= [tool.ironclad] with defaults applied), discover (§2.2),
frappe_nix = {rev, version, major}, floors (§2.4), app_type
```

### 2.4 Inventory

Every file below is listed in a `manifest.d/*.json` fragment. Each entry has the form `{path, template, strategy, when, floors?}`, and `when` is a Python expression over the context (for example `"discover.scss"`). Files whose `when` is false MUST NOT exist. Sync deletes them only if their content still matches what a past render would produce; otherwise `--check` reports exit 1 "file should not exist".

| Path | Strategy | When | Owner |
|---|---|---|---|
| `flake.nix` | whole | always | N3 |
| `.envrc` | whole | always | N3 |
| `.gitignore` | blocks (`# >>> frappe-nix >>>` block, legacy markers kept) | always | N3 |
| `.editorconfig` | whole + local region `editorconfig` | always | N3 |
| `.pre-commit-config.yaml` | whole + local region `repos`; third-party revs are floors | always | N3 |
| `committed.toml` | whole | always | N3 |
| `tools/pyproject.toml` | whole; version floors checked in `tools/uv.lock` | always | N3 |
| `tools/uv.lock` | seed (`uv lock --project tools`) | always | N3 |
| `pyproject.toml` | toml-merge (§2.12) | always | N3 |
| `<app>/__init__.py` | blocks (`x-release-please` block, §2.13) | always | N3 |
| `package.json` | json-merge (§2.8) | always | N3 |
| `yarn.lock` | seed (`yarn install`) | always | N3 |
| `.oxlintrc.json` | whole (JSON, no header) | always | N3 |
| `.oxfmtrc.jsonc` | whole | always | N3 |
| `.stylelintrc.json` | json-merge | `discover.scss` | N3 |
| `tsconfig.json` or `tsconfig.ironclad.json` (`discover.solution`) | whole | any managed TS project | N3 |
| `tsconfig.base.json` | whole | `browser_ts or scripts_ts or test_ts` | N3 |
| `tsconfig.browser.json` | whole | `browser_ts` | N3 |
| `tsconfig.scripts.json` | whole | `scripts_ts` | N3 |
| `tsconfig.test.json` | whole | `test_ts` | N3 |
| `tsconfig.desk.json` | whole | `desk_js` | N3 |
| `tsconfig.web.json` | whole | `web_js` | N3 |
| `release-please-config.json` | whole (JSON, no header) | always | N3 |
| `.release-please-manifest.json` | seed `{".": "<__version__>"}`; when seeding, sync also sets `package.json` `version` to `__version__` (§2.16) | always | N3 |
| `scripts/ironclad-vite-register.mjs` | whole (`ts` header) | `discover.vite` | N2 |
| `.git-blame-ignore-revs` | seed (header only) and validated (§2.20) | always | N3 |
| `nix/node-locks/<key>/{yarn.lock,source.json}` | seed from `ironclad/data/node-locks/version-<major>/` | sibling present and no lock | N3 |
| `.github/workflows/ci.yml` | whole (frappe-nix SHA sync-owned) | always | N4 |
| `.github/workflows/pr-policy.yml` | whole | always | N4 |
| `.github/workflows/release.yml` | whole | always | N4 |
| `.github/workflows/deps.yml` | whole | always | N4 |
| `.github/workflows/nightly.yml` | whole | always | N4 |
| `.github/workflows/assets.yml` | whole; the pilot SHA is a floor (preserved) | `cfg.pilot-assets` | N4 |
| `.github/dependabot.yml` | whole + local region `updates` | always | N4 |
| `.github/zizmor.yml` | whole | always | N4 |
| `marketplace/shots.d.ts` | whole | `discover.has_shots` | N5 |
| `marketplace/semgrep-baseline.json` | seed `{"schema":1,"findings":[]}` and validated (§5.2.4) | always | N5 |
| `marketplace/listing.toml` | seed from `ironclad/data/templates/marketplace/listing.toml.j2` and validated by frappe-listing | sync `--init-listing` only | N5 |
| `README.md` | blocks (`<!-- ironclad:begin <name> -->`, §2.19) | `discover.has_listing` | N5 |
| `<app>/desktop_icon/<app>.json` | generated by `frappe-icon build --write-fixture`; freshness checked by `frappe-icon check` | `app_type == "application"` | N5 |

These are **not managed**, but are checked or tolerated:

- `docs/` and `docs-site/` are owned by docusystem. Their workflows, `docs.yml` and `docs-publish.yml`, are left alone, but zizmor and actionlint lint them.
- `nix/uv.lock` and `nix/node-locks/**` belong to relock.
- `CHANGELOG.md` belongs to release-please.
- `semgrep/*.yml` is the app's own extra rules, which `lint` runs.
- `nix/local.nix` is the app's flake-parts module.

#### 2.4.1 Retired files (S31)

`manifest.d/core.json` carries a `retire` list (N3 owns it; N4 adds the workflow rule). Sync deletes every tracked match. `--check` reports each one as exit 1 with the problem `legacy file`. Audit row A9 evaluates the same list, through `ironclad sync --check --format json`, so the two can't diverge.

| Rule | Matches |
|---|---|
| Legacy workflow | Any tracked `.github/workflows/*.y*ml` that isn't a managed caller and isn't docusystem's `docs*.yml`, and whose text contains `release-please-action`, `gh pr merge --auto` or `dependabot/fetch-metadata`. In the fleet: carbon's `check.yml`, `release-please.yml`, `dependabot-auto-merge.yml` and `version-branch-guard.yml`; taskview's `check.yml` and `dependabot-auto-merge.yml`. carbon's `check.yml` and `version-branch-guard.yml` match through the second rule. |
| Superseded workflow | `.github/workflows/check.yml` and `.github/workflows/version-branch-guard.yml` in an app (their jobs moved to `ci.yml`, §4.2). |
| Legacy config | `.oxfmtrc.json` (the managed file is `.jsonc`), `.eslintrc*`, `eslint.config.*`, `.prettierrc*`, `prettier.config.*`, `.flake8`, `setup.cfg` with only a `[flake8]` section, `MANIFEST.in`, `requirements.txt`, `nix/node-offline-hashes.json` (frappe-nix throws on its option), `update-assets.mjs`. |

A file that matches but has app-specific content worth keeping is still deleted; the PR diff shows it. Retiring is a one-way step, so PR A is the PR that carries these deletions.

### 2.5 `flake.nix` (whole)

`py/ironclad/ironclad/data/templates/flake.nix.j2`. The output MUST be byte-stable under `nixfmt` (an acceptance test checks this).

```nix
# ironclad:managed — generated by frappe-nix (`frappe-init --sync`); do not edit.
# App-specific Nix goes in nix/local.nix, a flake-parts module imported below when it exists.
{
  description = "{{ app }}: {{ title }}";

  inputs = {
    frappe-nix.url = "github:Avunu/frappe-nix/release-{{ frappe_nix.major }}";
    nixpkgs.follows = "frappe-nix/nixpkgs";
    frappe = {
      url = "github:frappe/frappe/{{ frappe.branch }}";
      flake = false;
    };
{%- for s in siblings %}
    {{ s.input }} = {
      url = "{{ s.flake_url }}";
      flake = false;
    };
{%- endfor %}
  };

  nixConfig = {
    extra-substituters = [ "https://devenv.cachix.org" ];
    extra-trusted-public-keys = [
      "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
    ];
  };

  outputs =
    { frappe-nix, ... }@inputs:
    frappe-nix.lib.mkFlake { inherit inputs; } (
      { lib, ... }:
      {
        imports = [
          frappe-nix.flakeModules.default
        ]
        ++ lib.optional (builtins.pathExists ./nix/local.nix) ./nix/local.nix;

        systems = [
          "aarch64-darwin"
          "aarch64-linux"
          "x86_64-darwin"
          "x86_64-linux"
        ];

        perSystem =
          { pkgs, ... }:
          {
            formatter = pkgs.nixfmt;
            frappe-nix = {
              enable = true;
              siteName = "{{ site }}";
              app = {
                enable = true;
                frappeVersion = "{{ frappe.preset }}";
                inherit (inputs) frappe;
                siblings = [
{%- for s in siblings %}
                  {
                    name = "{{ s.name }}";
                    src = inputs.{{ s.input }};
                  }
{%- endfor %}
                ];
              };
            };
          };
      }
    );
}
```

Sync writes this file and locks it in phase A (§3.3), before anything reads `flake.lock`. `nix` is required for `--sync`; `--check` never runs it and reports missing lock nodes as exit 1.

### 2.6 `.envrc` (whole) and the `.gitignore` block

`.envrc`:

```
# ironclad:managed — generated by frappe-nix; do not edit.
use flake . --no-pure-eval
```

The `.gitignore` managed block (`templates/app/.gitignore` body, between the existing `# >>> frappe-nix >>>` markers) is today's body plus:

```
/.dev-dist/
*.tsbuildinfo
.ruff_cache/
__pycache__/
```

### 2.7 `.editorconfig` (whole + local region)

This fixes two bugs in carbon_frappe's copy: YAML and Nix can't use tabs.

```ini
# ironclad:managed — generated by frappe-nix (`frappe-init --sync`); edit only inside the ironclad:local region.
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true

[*.{py,js,mjs,cjs,ts,mts,vue,css,scss,html,jinja,toml,json,jsonc,sh}]
indent_style = tab
indent_size = 4
max_line_length = 110

[*.{yml,yaml,nix}]
indent_style = space
indent_size = 2

# frappe's exporter: 1-space JSON, no final newline, for everything under the package
[{{ app }}/**.json]
indent_style = space
indent_size = 1
insert_final_newline = false

[*.md]
trim_trailing_whitespace = false

# ironclad:local-begin editorconfig
# ironclad:local-end editorconfig
```

### 2.8 `package.json` (json-merge)

Sync creates `package.json` if it's missing, as `{"name": "<App-hyphen>", "version": "<__version__ or 0.1.0>", "private": true, "description": <tagline>, "license": "MIT", "author": "Avunu LLC"}`. After that, only these keys are managed. Every other key is app-owned (for example `dependencies`, `build`, `codegen`, `ci:lint`, `ci:typecheck`). `version` is owned by release-please, except once: when sync seeds `.release-please-manifest.json`, it first sets `version` to `__version__` (taskview has 1.0.0 against 0.0.1, timeclock 0.0.0 against 0.0.1), because `__version__` is what the registry reads.

| Key | Rule |
|---|---|
| `private` | exactly `true` |
| `type` | exactly `"module"` |
| `license` | exactly `"MIT"` |
| `engines.node` | exactly `">=24"` |
| `packageManager` | exactly `"yarn@1.22.22"` |
| `frappe` | exactly `{"major": "<major>", "branch": "version-<major>"}` (string major, as carbon_frappe has it) |
| `scripts.format` | `"oxfmt"` |
| `scripts.format:check` | `"oxfmt --check"` |
| `scripts.lint` | `"oxlint"`, plus ` && stylelint "<glob>"` for each scss glob |
| `scripts.typecheck` | `"tsc --build <discover.solution>"` when the solution is rendered, followed by ` && <check>` for each `[[tool.ironclad.typescript.spa]]` in order; just the SPA checks joined by ` && ` when there is no solution; absent when there is neither. TS 7's `tsc` can't check `.vue` imports, so SPAs keep `vue-tsc` (a `devDependencies` key the app owns) |
| `scripts.build` | App-owned, but when `discover.vite`: it MUST end with ` && node scripts/ironclad-vite-register.mjs` (sync appends it if missing, exit 1 under `--check`). This is `ironclad compat` C8. |
| `scripts.test:unit` | `"node --test --experimental-test-coverage --test-coverage-include='<app>/public/js/**' --test-coverage-lines=<js-coverage-min> 'test/unit/**/*.test.ts'"`; iff `discover.unit_tests` |
| `scripts.lint:py` | `"uv run --frozen --project tools ruff check . && uv run --frozen --project tools ruff format --check ."` |
| `scripts.typecheck:py` | `"uv run --frozen --project tools ty check --python \"${FRAPPE_BENCH_ROOT:-.frappe-nix/bench}/env\""` |
| `scripts.check` | `"yarn -s format:check && yarn -s lint"` + `" && yarn -s typecheck"` (if present) + `" && yarn -s test:unit"` (if present) |
| `devDependencies.<pkg>` | **floor** (S9). The app's range MUST be a caret or exact range whose minimum is ≥ the floor; otherwise sync sets `^<floor>`. Floors: `oxlint` 1.87.0, `oxfmt` 0.72.0. These go in every app. When any TS or desk project exists: `typescript` 7.0.2, `frappe-types` 16.5.0 (the minor that ships `tsconfig/*` presets, `frappe-types/web` and `gen-doctypes`), `@types/node` 26.6.4. When `discover.scss`: `stylelint` 17.16.0, `stylelint-config-standard-scss` 17.0.0. |

Forbidden (exit 2):

- a `build` script when none of these holds: `discover.vite`, a nested frontend, an SPA entry, or `[tool.ironclad] build = true` (carbon sets it for its patch-assets, audits and ai-chat build). frappe's esbuild runs every app's `build`, so a stray one runs on every bench. Conversely, `build = true` without a `build` script is exit 2.
- `eslint*`, `prettier*`, `@typescript-eslint/*` or `eslint-config-*` in any dependency map;
- `scripts` keys starting with `ironclad:`, which are reserved.

### 2.9 TypeScript configs (whole, JSONC with header)

Every file starts with `// ironclad:managed — generated by frappe-nix (\`frappe-init --sync\`); do not edit. Customise via [tool.ironclad.typescript].` The frappe-types presets come from the `frappe-types` package (≥ 16.5.0): `frappe-types/tsconfig/base.json`, `frappe-types/tsconfig/desk-js.json`, and the `frappe-types/web` types entry.

**The solution file** is named `discover.solution`: `tsconfig.json`, or `tsconfig.ironclad.json` when an SPA owns the root `tsconfig.json` (taskview, timeclock). Sync never writes over an SPA's config. It lists only the projects that exist, in the order browser, scripts, test, desk, web:

```jsonc
{
	"files": [],
	"references": [{ "path": "./tsconfig.browser.json" }, { "path": "./tsconfig.scripts.json" }, { "path": "./tsconfig.test.json" }, { "path": "./tsconfig.desk.json" }, { "path": "./tsconfig.web.json" }]
}
```

`tsconfig.base.json` holds the app-level strictness the frappe-types preset leaves out:

```jsonc
{
	"extends": "frappe-types/tsconfig/base.json",
	"compilerOptions": {
		"noUnusedLocals": true,
		"noUnusedParameters": true,
		"noImplicitReturns": true,
		"paths": { /* [tool.ironclad.typescript].paths; key omitted when empty */ }
	}
}
```

`tsconfig.browser.json`:

```jsonc
{
	"extends": "./tsconfig.base.json",
	"compilerOptions": { "composite": true, "types": ["frappe-types/global"], "allowImportingTsExtensions": true },
	"include": ["<app>/public/js/**/*.ts", "<app>/public/js/**/*.d.ts", "types/**/*.d.ts"],   // or browser-include
	"exclude": ["node_modules", "<app>/public/dist", /* spa_globs, cfg.generated, typescript.exclude */]
}
```

`tsconfig.scripts.json`:

```jsonc
{
	"extends": "./tsconfig.base.json",
	"compilerOptions": { "composite": true, "types": ["node"], "allowImportingTsExtensions": true },
	"include": ["scripts/**/*.ts", "marketplace/**/*.ts", "ci/**/*.ts"],
	"exclude": ["node_modules"]
}
```

`tsconfig.test.json`:

```jsonc
{
	"extends": "./tsconfig.base.json",
	"compilerOptions": { "composite": true, "types": ["node", "frappe-types/global"], "allowImportingTsExtensions": true },
	"include": ["test/**/*.ts", "<app>/public/js/**/*.ts", "scripts/lib/**/*.ts"],
	"exclude": ["node_modules", "<app>/public/dist"]
}
```

`tsconfig.desk.json` checks uncompiled desk JS with JSDoc (D5):

```jsonc
{
	"extends": "frappe-types/tsconfig/desk-js.json",
	"compilerOptions": { "composite": true },
	"include": ["<app>/**/*.js", "types/doctypes.d.ts", "types/<app>.augment.d.ts"],
	"exclude": ["node_modules", "**/*.bundle.js", "**/public/dist/**", "<app>/www/**", "**/web_form/**",
	            "<app>/templates/**", /* each nested frontend dir, spa_globs, typescript.web-include,
	            cfg.generated, each unchecked-js path, typescript.exclude */]
}
```

`tsconfig.web.json` covers web form and www scripts, and the files `typescript.web-include` moves out of the desk (esign's `public/js/web/**`, which uses `frappe.web_form`, `frappe.ready` and `frappe.form_dirty`):

```jsonc
{
	"extends": "frappe-types/tsconfig/desk-js.json",
	"compilerOptions": { "composite": true, "types": ["frappe-types/web"] },
	"include": ["<app>/www/**/*.js", "<app>/**/web_form/**/*.js", "<app>/templates/**/*.js",
	            /* typescript.web-include */ "types/doctypes.d.ts", "types/<app>.augment.d.ts"],
	"exclude": ["node_modules", "**/public/dist/**", /* spa_globs, cfg.generated, each unchecked-js path,
	            typescript.exclude */]
}
```

The desk and web projects are rendered from the same file sets as `discover.desk_js` and `discover.web_js`, and so are the oxlint overrides (§2.10). Both are rendered only when their file set is non-empty after the exclusions, so an app can't hit TS18003 ("no inputs") by excluding everything.

**Migrating to strict checkJs.** The frappe-types track measured esign at 274 errors, timeclock at about 238 (plus 4,056 in a committed legacy bundle), jailbreak 59, postgrid 36 and jwt_auth 6. PR B lists each file it can't fix yet in `[[tool.ironclad.unchecked-js]]` with a reason. That list is rendered into the desk and web excludes and ratchets like `untested`: R6 lets it only shrink. An entry naming a file that is no longer tracked is stale (`ironclad compat` C9, exit 1). An entry whose file no longer has any error is also stale: the `typecheck` job runs `ironclad unchecked-js --stale` (N3), which runs `tsc -p` once over a generated temporary project that includes every listed file and fails naming each listed file with zero diagnostics. Audit row A7 is amber while the list is non-empty. `// @ts-nocheck` stays forbidden (`typescript/ban-ts-comment`). A committed build output such as `timeclock/www/job_timeclock_legacy/index.js` belongs in `cfg.generated` or should be deleted, not listed as unchecked.

**SPAs** (`[[tool.ironclad.typescript.spa]]`) keep their own app-owned tsconfig. Their globs are removed from the browser, desk and web projects and from the desk-globals override; `typecheck` runs each `check`. Nested frontends with a `package.json` work as before: `typecheck` runs their own `typecheck` script.

- `types/doctypes.d.ts`, if the app has it, is the output of `frappe-types gen-doctypes`. It is committed and checked for freshness in `typecheck` (§4.2).
- **App symbols on the frappe namespace.** An app declares the members it adds to `frappe` (taskview's `frappe.views.TasksView`, esign's `frappe.ui.form.ControlFontSelect` and `frappe.esign_context`, jailbreak's `frappe.ui.merge_records`, timeclock's `frappe.ui.form.show_workday_bulk_add`) in exactly one file, `types/<app>.augment.d.ts`, as `declare global { namespace frappe.<sub> { … } }` augmentations. Each member carries a `// app-owned: <reason>` comment on the line before it. App-owned, never managed.
- **Hand-written frappe globals** are an `ironclad compat` C7 failure (exit 1), which is §6 row "no hand-written frappe globals". C7 matches redeclarations only: `declare (var|let|const) frappe`, `interface Window {` containing a `frappe` member, `declare namespace frappe` anywhere, and any `namespace frappe` augmentation outside `types/<app>.augment.d.ts` or without its `// app-owned:` comments. taskview's `public/js/types/frappe.d.ts` and timeclock's `public/js/timeclock/env.d.ts` are deleted in PR B, and their app-owned members move to the augment file.

### 2.10 `.oxlintrc.json` (whole JSON, no comments) and `.oxfmtrc.jsonc` (whole)

`.oxlintrc.json`. The desk and web overrides are generated from the same file sets tsc uses (§2.2), not from fixed globs:

- `<desk-files>` is `discover.desk_js` rendered as a sorted list of paths (oxlint matches literal paths as globs). A new desk file is therefore drift until sync runs, which is the same rule tsconfig already follows.
- `<web-files>` is `discover.web_js`, likewise.
- Files in `spa_globs` get neither override; their globals come from their own imports.

```json
{
	"$schema": "./node_modules/oxlint/configuration_schema.json",
	"plugins": ["typescript", "unicorn", "oxc", "import"],
	"categories": { "correctness": "error", "suspicious": "error", "perf": "warn", "pedantic": "off" },
	"env": { "builtin": true, "es2024": true, "browser": true },
	"rules": {
		"typescript/no-explicit-any": "error",
		"typescript/ban-ts-comment": "error",
		"typescript/consistent-type-imports": "error",
		"no-console": ["warn", { "allow": ["error", "warn"] }],
		"import/no-cycle": "off",
		"no-underscore-dangle": "off",
		"unicorn/consistent-function-scoping": "off",
		"unicorn/no-array-sort": "off"
	},
	"ignorePatterns": ["<app>/public/dist/**", ".frappe-nix/**", ".dev-dist/**", "nix/**", "docs-site/**", "types/doctypes.d.ts"
	                   /* + cfg.generated + [tool.ironclad.oxlint].ignore */],
	"overrides": [
		{ "files": ["scripts/**", "marketplace/**", "ci/**", "test/**"], "env": { "node": true },
		  "rules": { "no-console": "off", "no-await-in-loop": "off", "import/no-unassigned-import": "off" } },
		{ "files": ["<app>/public/js/*.bundle.ts", "<app>/public/js/*.bundle.js"], "rules": { "import/no-unassigned-import": "off" } },
		{ "files": <desk-files>, "globals": { "frappe": "readonly", "__": "readonly", "cur_frm": "readonly",
		  "cur_list": "readonly", "locals": "readonly", "$": "readonly", "jQuery": "readonly", "moment": "readonly"
		  /* + "<desk_global>": "readonly" for each sibling with one, + [tool.ironclad.oxlint].globals */ } },
		{ "files": <web-files>, "globals": { "frappe": "readonly", "__": "readonly", "$": "readonly",
		  "jQuery": "readonly" /* + [tool.ironclad.oxlint].globals */ } }
		/* + [tool.ironclad.oxlint].overrides */
	]
}
```

An override whose file list is empty is omitted.

`.oxfmtrc.jsonc`:

```jsonc
// ironclad:managed — generated by frappe-nix (`frappe-init --sync`); do not edit. Customise via [tool.ironclad.oxfmt].
// Tabs and 110 columns are frappe's convention. Anything .gitignore'd is skipped without being listed.
{
	"$schema": "./node_modules/oxfmt/configuration_schema.json",
	"useTabs": true,
	"printWidth": 110,
	"ignorePatterns": [
		// frappe's exporter writes every JSON under the package and rewrites it on export
		"<app>/**/*.json",
		"nix/**",
		"docs-site/**",
		"docs/screenshots/**",
		// written by release-please
		"CHANGELOG.md",
		".release-please-manifest.json",
		// generated by frappe-types gen-doctypes
		"types/doctypes.d.ts"
		/* + cfg.generated + [tool.ironclad.oxfmt].ignore */
	]
}
```

Every `whole`-strategy text file that oxfmt formats (YAML, TOML, Markdown, JSONC) MUST already be in oxfmt's output form. N3 and N4 acceptance tests run `oxfmt --check` over a freshly rendered fixture.

### 2.11 `.stylelintrc.json` (json-merge, only when `discover.scss`)

Managed keys:

- `extends`: exactly `["stylelint-config-standard-scss"]`;
- `ignoreFiles`: MUST contain `"<app>/public/dist/**"`.

Any other key (`rules`, more `ignoreFiles` entries) is app-owned.

### 2.12 `pyproject.toml` (toml-merge)

The engine is `tomlkit`, which preserves comments and order. Key ownership:

**Managed exact.** Sync sets these and `--check` compares their values.

```toml
[project]
requires-python = ">=3.14"                 # from the frappe-nix preset for version-<major>
dynamic = ["version"]                      # must contain "version"

[build-system]
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[tool.bench.frappe-dependencies]           # exactly {"frappe"} ∪ hooks.required_apps (bare names); values from known-apps.json
frappe = ">=16.0.0,<17.0.0"
erpnext = ">=16.0.0,<17.0.0"

[tool.ruff]
line-length = 110
target-version = "py314"

[tool.ruff.lint]
select = ["F", "E", "W", "I", "UP", "B", "RUF", "SIM", "C4", "PIE", "PERF", "T20"]
ignore = ["E501", "W191"]
typing-modules = ["frappe.types.DF"]

[tool.ruff.format]
quote-style = "double"
indent-style = "tab"
docstring-code-format = true

[tool.ty.environment]
python-version = "3.14"

[tool.ty.src]
include = ["<app>"]

[tool.ty.terminal]
error-on-warning = true

[tool.coverage.run]
omit = ["*/tests/*", "*/test_*.py", "*/patches/*"]   # + each [[tool.ironclad.coverage-omit]].glob, in order.
                                                    # Patches match testmap's own **/patches/** exclusion: a fresh
                                                    # install marks them complete without running them.

[tool.coverage.report]
show_missing = true
skip_covered = true
precision = 1
exclude_also = ["if TYPE_CHECKING:", "raise NotImplementedError", "@(abc\\.)?abstractmethod"]

[tool.vulture]                             # test_utils' static_analysis runs vulture over "."
exclude = [".venv/", "node_modules/", ".frappe-nix/", ".dev-dist/"]
```

**App-owned, but validated.** Sync never touches these.

| Key | Validation |
|---|---|
| `[project]` `name`, `authors`, `description`, `readme`, `license`, `dependencies` | `dependencies` MUST NOT name `frappe`, `erpnext`, `hrms` or `payments` (exit 1) |
| `tool.coverage.report.fail_under` | Required; a number from 0 to 100. Ratcheted (S24). |
| `tool.ruff.extend-exclude` | Allowed. `ironclad-audit` reports a non-empty list as amber. |
| `tool.ruff.lint.per-file-ignores` | Allowed, except that F401 and E402 MUST NOT appear for a glob that matches `<app>/**` or `**` (exit 2) |
| `tool.ty.src.exclude` | Allowed; reported as amber in audit |
| `[dependency-groups]` | App-owned. The app's test-only packages go here (`responses`, …). |
| `[tool.bench.assets]` | App-owned. Required when `pilot-assets = true`: `build_dir`, `out_dir` and `index_html_path` (frappe-listing validates them). |
| `[tool.ironclad]` | §2.1 |

**Forbidden** (exit 2):

- `tool.ruff.lint.extend-select`, `extend-ignore`, `ignore` beyond the managed pair, `unfixable`, `tool.ruff.lint.isort`;
- `tool.ty.rules` with any value other than `"error"`, and `tool.ty.overrides`;
- `tool.coverage.run.source` and `tool.coverage.run.relative_files`, which belong to frappe-test;
- `[tool.poetry]` (D6 uses flit);
- `requirements.txt` existing anywhere tracked.

### 2.13 `<app>/__init__.py` (blocks)

```python
# x-release-please-start-version
__version__ = "16.1.0"
# x-release-please-end
```

- Only comment lines are allowed outside the block, such as test_utils' copyright stamp and its blank line. A docstring and any statement count as side effects (marketplace rule): exit 1.
- The version value is app-owned (release-please writes it). Sync only converts carbon_frappe's trailing `# x-release-please-version` form into the block form and keeps the value.
- Whitelisted functions or any other code in `__init__.py` (jailbreak's `assert_capability` and `check_capability`) move to `<app>/api.py` in PR B. The old dotted paths stay reachable through an `override_whitelisted_methods` shim in `hooks.py` for one minor release, as for renames (§5.10).
- `ironclad compat` requires: `__version__` = `package.json` `version` = manifest `"."`. The registry parses the line `__version__ = "…"` (add_release.py `dynamic_version`), so nothing may follow the closing quote.

### 2.14 `.pre-commit-config.yaml` (whole + local region `repos`; third-party revs are floors)

This is the rendered form. `{% if %}` marks the conditional parts.

```yaml
# ironclad:managed — generated by frappe-nix (`frappe-init --sync`); edit only inside the ironclad:local region.
#
# Run by prek (`prek install` once per clone; the dev shell does it) and by CI's `lint` job:
#   uv run --frozen --project tools prek run --all-files --show-diff-on-failure
# A hook that rewrites a file fails CI. ruff, ty, committed, zizmor, actionlint and shellcheck are pinned
# by tools/uv.lock; oxfmt, oxlint and stylelint by package.json; ssort, pre-commit-hooks and test_utils by the
# `rev:`s below. dependabot moves all three.
default_install_hook_types: [pre-commit, commit-msg]
default_stages: [pre-commit]
default_language_version:
  python: python3.14
fail_fast: false
exclude: ^(nix/|\.frappe-nix/|docs-site/|CHANGELOG\.md$|types/doctypes\.d\.ts${% for g in cfg.generated %}|{{ g | glob_to_regex }}{% endfor %})

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: check-merge-conflict
      - id: check-ast
      - id: check-toml
      - id: check-yaml
        args: [--allow-multiple-documents]
      - id: check-json
        exclude: ^(tsconfig(\.[a-z]+)?\.json|\.vscode/.*)$
      - id: debug-statements
      - id: detect-private-key
      - id: check-added-large-files
        args: [--maxkb=1024]
        exclude: ^docs/screenshots/

  # Definitions before their first use; nothing else reordered. ssort can't resolve names
  # imported inside the class-body `if TYPE_CHECKING:` block frappe generates, so only
  # <doctype>/<doctype>.py controllers are excluded. Tests and helpers in doctype folders are sorted.
  - repo: https://github.com/bwhmather/ssort
    rev: 0.17.0
    hooks:
      - id: ssort
        exclude: '/doctype/([^/]+)/\1\.py$'

  - repo: local
    hooks:
      - id: ruff-isort
        name: ruff import sorter
        language: system
        entry: uv run --frozen --project tools ruff check --select I --fix
        types: [python]
      - id: ruff-check
        name: ruff check
        language: system
        entry: uv run --frozen --project tools ruff check --fix
        types: [python]
      - id: ruff-format
        name: ruff format
        language: system
        entry: uv run --frozen --project tools ruff format
        types: [python]
      - id: oxfmt
        name: oxfmt
        language: system
        entry: yarn -s oxfmt
        pass_filenames: false
        always_run: true
      - id: oxlint
        name: oxlint
        language: system
        entry: yarn -s oxlint
        pass_filenames: false
        types_or: [javascript, jsx, ts, tsx, vue]
{% if discover.scss %}
      - id: stylelint
        name: stylelint
        language: system
        entry: yarn -s stylelint {{ scss globs, quoted }}
        pass_filenames: false
        types: [scss]
{% endif %}
      - id: zizmor
        name: zizmor
        language: system
        entry: uv run --frozen --project tools zizmor --config .github/zizmor.yml .github/workflows .github/dependabot.yml
        pass_filenames: false
        files: ^\.github/
      - id: actionlint
        name: actionlint
        language: system
        entry: uv run --frozen --project tools actionlint
        pass_filenames: false
        files: ^\.github/workflows/
      - id: shellcheck
        name: shellcheck
        language: system
        entry: uv run --frozen --project tools shellcheck --severity=warning
        types: [shell]
        exclude: ^\.envrc$
      - id: ironclad-compat
        name: ironclad compat (versions, ranges, majors)
        language: system
        entry: ironclad compat
        pass_filenames: false
        always_run: true
      - id: committed
        name: committed (conventional commit)
        language: system
        entry: uv run --frozen --project tools committed --commit-file
        stages: [commit-msg]
      - id: ironclad-commit-msg
        name: no major bump through a commit message
        language: system
        entry: ironclad policy --commit-msg-file
        stages: [commit-msg]

  # agritheory/test_utils: selected hooks only (S19). sql_registry/sql_rewriter abort the run,
  # mypy is replaced by ty, and validate_*_dependencies crash outside a bench, so none of those are here.
  - repo: https://github.com/agritheory/test_utils
    rev: v1.30.1
    hooks:
      - id: validate_frappe_project
      - id: validate_patches
        args: [--app, {{ app }}]
      - id: static_analysis
        args: ["."]
      - id: validate_doctype_python_types
      - id: validate_copyright
        args: [--app, {{ app }}]
        # Never stamp a managed file (its header is line 1) or a generated one (its freshness diff would fail).
        exclude: ^(docs/|docs-site/|marketplace/shots\.d\.ts$|types/doctypes\.d\.ts$|scripts/ironclad-vite-register\.mjs$|tsconfig[^/]*\.json${% for g in cfg.generated %}|{{ g | glob_to_regex }}{% endfor %})
      # Bench-aware: silent no-ops outside a bench. CI runs them in a mini-bench (`ironclad minibench`).
      - id: validate_customizations
        stages: [manual]
      - id: clean_customized_doctypes
        stages: [manual]
      # Nightly only (jscpd through npx, needs the network).
      - id: check_code_duplication
        stages: [manual]
{% if cfg["track-overrides"] %}
      - id: track_overrides
        args: [--app, {{ app }}, --base-branch, {{ frappe.branch }}]
{% endif %}

  # ironclad:local-begin repos
  # ironclad:local-end repos
```

**Generated files.** `cfg.generated` reaches every tool that would otherwise rewrite or stamp a generated file: the global `exclude` above (so ruff, ssort and test_utils skip it), `validate_copyright`'s exclude, and the oxfmt and oxlint `ignorePatterns`. `glob_to_regex` is a fixed filter in the engine (`**` → `.*`, `*` → `[^/]*`, anchored at the end with `$`). The managed files whose first line is the ironclad header are excluded from `validate_copyright` by name, so sync and the stamp never rewrite each other.

**zizmor scope.** zizmor audits `.github/workflows` and `.github/dependabot.yml`. Every rendered dependabot entry therefore carries a `cooldown` (§2.17), which zizmor's dependabot-cooldown audit requires. docusystem's `docs*.yml` are audited too.

**Floor semantics (S9).** The renderer emits `rev: {{ rev(url, floor) }}`. When the existing file has a rev for the same `repo:` URL that compares ≥ the floor, that rev is kept. Revs compare with `packaging.version` after stripping a leading `v`. The floors are `v6.0.0`, `0.17.0` and `v1.30.1`.

`ironclad` is on PATH in the dev shell (N3a `shell.nix`) and in CI (§4.1). A commit made outside the dev shell fails the `ironclad-compat` hook with "command not found". That is intended: commit from `nix develop`.

### 2.15 `committed.toml` and `tools/pyproject.toml` (whole)

`committed.toml` is carbon_frappe's file with the managed header:

```toml
# ironclad:managed — generated by frappe-nix (`frappe-init --sync`); do not edit.
# Conventional commits with frappe's commitlint types. `fix:` → patch, `feat:` → minor. There is no way to
# bump the major: it is the Frappe major, and `ironclad policy` refuses `!` and BREAKING CHANGE (S12).
style = "conventional"
allowed_types = ["build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test", "deprecate"]
subject_length = 100
line_length = 0
subject_capitalized = false
subject_not_punctuated = true
imperative_subject = false
no_wip = true
no_fixup = true
merge_commit = false
```

`tools/pyproject.toml`:

```toml
# ironclad:managed — generated by frappe-nix (`frappe-init --sync`); do not edit.
# Python-side tooling. Pinned by tools/uv.lock (dependabot uv, directory /tools). Run as
# `uv run --frozen --project tools <tool>`. A separate project because app mode's workspace lock is nix/uv.lock.
[project]
name = "{{ app_hyphen }}-tools"
version = "0"
requires-python = ">=3.14"
dependencies = ["actionlint-py", "committed", "prek", "ruff", "semgrep", "shellcheck-py", "ty", "zizmor"]

[tool.uv]
package = false
```

**Lock floors (S9).** `--check` reads `tools/uv.lock` and requires:

| Tool | Floor |
|---|---|
| ruff | 0.16.10 |
| ty | 0.0.84 |
| zizmor | 1.30.1 |
| committed | 1.1.11 |
| actionlint-py | 1.7.12.25 |
| semgrep | 1.179.0 |
| prek | 0.5.5 |
| shellcheck-py | 0.11.0.1 |

A lock below a floor is drift (exit 1). Sync fixes it with `uv lock --project tools --upgrade-package <pkg>`.

### 2.16 `release-please-config.json` (whole JSON) and manifest (seed)

```json
{
	"$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
	"include-v-in-tag": true,
	"include-component-in-tag": false,
	"separate-pull-requests": true,
	"bootstrap-sha": "<[tool.ironclad.release].bootstrap-sha; key omitted when unset>",
	"packages": {
		".": {
			"release-type": "node",
			"package-name": "<App-hyphen>",
			"changelog-path": "CHANGELOG.md",
			"extra-files": [{ "type": "generic", "path": "<app>/__init__.py" }]
		}
	}
}
```

- `.release-please-manifest.json` is seeded as `{".": "<__version__>"}`. In the same step sync sets `package.json` `version` to `__version__`, so C5 and L4 hold from the first sync. From then on both are owned by release-please.
- For the first release, PR B's last commit ends with the footer `Release-As: <major>.0.0`, and `npx release-please release-pr --dry-run --target-branch <PR B's branch>` must propose `<major>.0.0` before merging. PR B is then merged with `gh pr merge --squash --body-file <f>` whose last paragraph is that same footer, so the squash commit on develop carries it as a trailing footer (S11). If the release PR still proposes another version, C1 fails it. `ironclad policy --pr` still checks the major in any `Release-As` it finds in PR B's commits.

### 2.17 `.github/dependabot.yml` (whole + local region `updates`)

```yaml
# ironclad:managed — generated by frappe-nix (`frappe-init --sync`); edit only inside the ironclad:local region.
# Every entry targets develop and commits as chore(deps)/chore(deps-dev), so no bump cuts a release.
# deps.yml decides what merges automatically (§4.5).
version: 2
updates:
  # One entry for /, as docusystem's scaffold expects (doctor and upgrade look for exactly this entry and
  # for Avunu/docusystem under cooldown.exclude). Three groups, so a docs or pilot bump never holds up the rest.
  - package-ecosystem: github-actions
    directory: /
    target-branch: develop
    schedule: { interval: daily }
    cooldown: { default-days: 3, exclude: ["Avunu/docusystem"] }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    # frappe-nix's reusable workflows move only through ironclad-rollout and `frappe-init --sync` (S5)
    ignore:
      - dependency-name: Avunu/frappe-nix
    groups:
      docs-actions:             # needs-review: a merge publishes the docs site with the new workflow code
        patterns: ["Avunu/docusystem*"]
      pilot:                    # needs-review: builds release assets
        patterns: ["frappe/pilot*"]
      actions:
        patterns: ["*"]
        exclude-patterns: ["Avunu/docusystem*", "frappe/pilot*"]

  - package-ecosystem: npm
    directory: /
    target-branch: develop
    schedule: { interval: daily }
    cooldown: { default-days: 3 }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    groups:
      toolchain:
        patterns: [oxlint, oxfmt, typescript, frappe-types, "@types/node", "stylelint*"]
        update-types: [minor, patch]
      runtime:
        patterns: ["*"]
        update-types: [minor, patch]
{% for d in discover.nested_frontends %}
  - package-ecosystem: npm
    directory: /{{ d }}
    target-branch: develop
    schedule: { interval: daily }
    cooldown: { default-days: 3 }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    groups:
      {{ d }}: { patterns: ["*"], update-types: [minor, patch] }
{% endfor %}
{% if discover.docs_site %}
  # docs-site/: a merge publishes the site, so deps.yml never merges these automatically
  - package-ecosystem: npm
    directory: /docs-site
    target-branch: develop
    schedule: { interval: weekly }
    cooldown: { default-days: 7, exclude: ["@avunu/docusystem"] }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    groups:
      docs-site-packages: { patterns: ["*"] }
{% endif %}
  - package-ecosystem: uv
    directory: /tools
    target-branch: develop
    schedule: { interval: weekly, day: monday }
    cooldown: { default-days: 3 }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    groups:
      tools: { patterns: ["*"], update-types: [minor, patch] }

  - package-ecosystem: pre-commit
    directory: /
    target-branch: develop
    schedule: { interval: weekly, day: monday }
    cooldown: { default-days: 3 }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    groups:
      hooks:
        patterns: ["*"]
        exclude-patterns: ["*agritheory/test_utils*"]
      test-utils:
        patterns: ["*agritheory/test_utils*"]

  - package-ecosystem: nix
    directory: /
    target-branch: develop
    schedule: { interval: weekly, day: monday }
    cooldown: { default-days: 3 }
    open-pull-requests-limit: 5
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    # frappe-nix moves only through ironclad-rollout (S5): its bump rewrites workflow files, which
    # GITHUB_TOKEN can't push. Dependabot moves frappe and the siblings, which never touch a workflow.
    ignore:
      - dependency-name: frappe-nix
    groups:
      flake-inputs: { patterns: ["*"] }
{% if discover.gitmodules %}
  - package-ecosystem: gitsubmodule
    directory: /
    target-branch: develop
    schedule: { interval: weekly }
    cooldown: { default-days: 3 }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
{% endif %}
  # ironclad:local-begin updates
  # ironclad:local-end updates
```

The rendered output is expanded to block style by oxfmt's rules; the template stores the oxfmt-formatted result. Major npm and uv updates arrive as individual PRs, because the groups only take minor and patch updates. `deps.yml` labels them `needs-review`.

- **docusystem compatibility.** The `/docs-site` npm entry and the github-actions entry for `/` are the entries `docusystem init` scaffolds, with the cooldown exclusions its `doctor` checks (`@avunu/docusystem` and `Avunu/docusystem`). `docusystem upgrade` only adds an entry when none exists for that ecosystem and directory, so on a synced repo it writes nothing. The acceptance test is in N4 (§7).
- **Dependabot ignores for nix.** If the nix ecosystem doesn't honour `ignore` for a flake input, `deps.yml` still never auto-merges a PR that moves the `frappe-nix` lock node: `relock-plan` detects it, and the auto-merge job comments `frappe-nix moves through ironclad-rollout` and closes the PR.

### 2.18 `.github/zizmor.yml` (whole)

```yaml
# ironclad:managed — generated by frappe-nix (`frappe-init --sync`); do not edit.
# Every `uses:` is hash-pinned with a version comment. dependabot moves third-party pins;
# `frappe-init --sync` moves Avunu/frappe-nix's.
rules:
  unpinned-uses:
    config:
      policies:
        "*": hash-pin
```

### 2.19 README generated blocks (N5; `blocks`, only when `discover.has_listing`)

Blocks are delimited by `<!-- ironclad:begin <name> -->` and `<!-- ironclad:end <name> -->`. They are rendered by `frappe-listing readme --write`, which sync calls (§3.5), from `ironclad/data/readme/<name>.md.j2`. The required blocks, in this order, are `header`, `compatibility`, `installation`, `support`, `development` and `license`, plus the hand-written sections in between, as D15 sets out. Missing markers are an error: `frappe-listing readme --check` exits 2 and names the missing block.

- `header`: a centred `<div>` containing:
  - the logo `<img src="<app>/public/images/<app>-logo.svg" width="80">`;
  - the H1 title, and the tagline;
  - badges: CI (`actions/workflows/ci.yml/badge.svg?branch=develop`), latest release (shields `github/v/release`), license MIT, and `Frappe v<major>`;
  - a hero `<picture>` with a dark `<source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/<hero>-dark.webp">` and `<img src="docs/screenshots/<hero>-light.webp" alt="<alt>">`. `<hero>` is the shot with `readme: "hero"`. The `<picture>` is omitted when there are no shots.
- `compatibility`: a table with `Frappe <major> → version-<major>` and each required app with its range.
- `installation`: Frappe Cloud marketplace text plus
  ```
  bench get-app https://github.com/<repo> --branch version-<major>
  bench --site <site> install-app <app>
  ```
- `support`: the issues link and `mail@avu.nu`.
- `development`: `nix develop` (or `direnv allow`), `prek install`, `frappe-test`, and a link to `https://frappe-nix.avunu.net/docs/ironclad/` (S26).
- `license`: `MIT, Copyright (c) <first-commit-year>–present Avunu LLC → license.txt`. `<first-commit-year>` is the year of `git log --reverse --format=%cs | head -1`, which never changes. Nothing in a rendered block depends on today's date (§3.4).

### 2.20 `.git-blame-ignore-revs` (seed + validated)

Seed:

```
# Mass reformats (style: PRs), one squash SHA per line with its subject as a comment.
# GitHub reads this file; locally the dev shell sets blame.ignoreRevsFile.
```

Validation (`ironclad compat`):

- every non-comment line is `<40-hex>  # <subject>` (two spaces before `#`);
- every SHA is reachable from `HEAD`. This is checked only when the history is available (`lint` has `fetch-depth: 0`).

PR B adds PR A's squash SHA. The helper `ironclad compat --add-blame-ignore <sha>` appends the line with the commit subject.

The dev shell's enterShell (N3a `shell.nix`) runs `git config blame.ignoreRevsFile .git-blame-ignore-revs` when the file exists and the setting differs.

### 2.21 Marketplace files (N5)

**`marketplace/listing.toml`** is app-owned and validated by frappe-listing (§5.2):

```toml
schema = 1
title = "Carbon Theme for Frappe"          # ≤ 40 chars; third-party marks only as "… for X" / "… via X"
tagline = "IBM Carbon design for the Frappe desk, website, email, charts and tables"  # 40–80 chars, no trailing period
type = "extension"                         # application | extension | integration | utility
category = "Extensions"                    # Applications | Compliance | Developer Tools | Extensions | Integrations | Utilities
categories = ["Theme", "UI"]               # non-empty
website = "https://avunu.net/open-source/carbon_theme/"
documentation = "https://carbon-theme.avunu.net/"
apps_screen = false

[apps_screen_entry]                        # required iff apps_screen = true
route = "/app/taskview"
has_permission = "erpnext_taskview.api.has_app_permission"

[registry]                                 # optional
stars = 0
channel = "stable"
```

Expected URL patterns:

- `website`: `https://avunu.net/open-source/<app>/`;
- `documentation`: `https://<App-hyphen>.avunu.net/`.

A value that differs is a warning, not an error (docs-and-urls track).

**`marketplace/screenshots.ts`** is app-owned. **`marketplace/shots.d.ts`** is managed: it is the `ShotSpec` types (§5.5), rendered from `ironclad/data/shots/shots.d.ts`, the packaged copy of `lib/shots/shots.d.ts`. The spec file is written as

```ts
import type { ShotSpec } from "./shots.d.ts";
export default { … } satisfies ShotSpec;
```

so Node's type stripping runs it with no runtime import.

**`marketplace/semgrep-baseline.json`** schema:

```json
{ "schema": 1, "marketplace_rev": "<sha it was produced against>",
  "findings": [ { "rule": "frappe-manual-commit", "path": "erpnext_taskview/erpnext_taskview/api.py",
                  "line_sha1": "<sha1 of the stripped matched first line>", "count": 12, "reason": "…" } ] }
```

The baseline is a multiset (S21). `(rule, path, line_sha1)` is unique within the file, and `count ≥ 1`. L7 fails when a key is found more often than its `count` (new finding) or less often (the count must be lowered: stale). R3 compares counts per key, head ≤ base.

**`<app>/demo.py`** is app-owned: `setup(ctx)` (§5.4). **Icons** are app-owned sources: `<app>/public/images/<app>-symbolic.svg`, and `<app>-logo.svg`, which `frappe-icon tile` generates and which is committed.

---

## 3. The managed-file mechanism (N3)

### 3.1 Where the data lives

- **Everything lives inside the package** (S10): templates in `py/ironclad/ironclad/data/templates/**`, with `.j2` meaning Jinja, and **manifest fragments** in `py/ironclad/ironclad/data/manifest.d/*.json`. The engine reads the union of all fragments through `importlib.resources`, so a `uv tool install` from the git subdirectory has the same data as the Nix build. `templates/app/` keeps only `.envrc` and `.gitignore` for `frappe-init --app`'s `install_template`, which copies every file in that tree.
- A path listed in more than one fragment is a frappe-nix build error. `tests/ironclad/sync.nix` checks for it.
- A fragment entry looks like this:

```json
{ "path": ".github/workflows/ci.yml", "template": ".github/workflows/ci.yml.j2", "strategy": "whole",
  "when": "True", "local_regions": [], "floors": {} , "header": "yaml" }
```

`header` is one of `yaml|toml|shell|ini|nix` (`#`), `jsonc|ts` (`//`), or `none` (plain JSON). The header is the first line or lines of the rendered output, and it is part of the byte comparison.

### 3.2 Strategies

| Strategy | Sync writes | Check compares | Local changes |
|---|---|---|---|
| `whole` | The full rendered file | Bytes, after carrying local regions and floors (below) over from the current file | Only in `local_regions` and through `[tool.ironclad.*]` parameters |
| `toml-merge` | Sets managed keys with tomlkit, creating missing tables after the last existing `[tool.*]` table, and leaves every other key byte-for-byte | Parsed values of the managed keys; validation rules for the app-owned keys; forbidden keys | Every key that isn't managed |
| `json-merge` | Sets managed keys and raises floors, keeping key order (new keys appended to the object) and indentation (tabs) | Parsed values; floors as "minimum of range ≥ floor" | Every key that isn't managed |
| `seed` | Only when the file is missing | Presence plus the file's own validation rules | All of it |
| `blocks` | Only the content between markers | Bytes of the block content | Everything outside the markers |

**Local regions** (only in YAML, INI-style and shell-style files) look like this:

```
# ironclad:local-begin <name>
…kept verbatim…
# ironclad:local-end <name>
```

- The names and their order are fixed by the template.
- A missing region is treated as empty and recreated.
- An unknown region, a duplicate region or an unbalanced marker is a configuration error (exit 2).
- After merging, the result MUST still parse (YAML) and MUST NOT redefine a managed hook `id`, an `updates` entry with the same ecosystem and directory, or an EditorConfig section that's already managed. Any of these is exit 2.

**Floors** are expressed in the template as `{{ rev("<repo-url>", "<floor>") }}` and `{{ pin("<owner>/<repo>/<path>", "<sha>", "<version>") }}`, the latter for third-party `uses:` in caller files:

- **pre-commit revs:** the existing value is kept if its version is ≥ the floor.
- **third-party SHA pins** (pilot in `assets.yml`): the existing SHA line is always kept once present, because dependabot owns it.
- **`Avunu/frappe-nix` `uses:`** is never a floor. It is always `@<frappe_nix.rev> # v<frappe_nix.version>`.

**Formatting.** After writing, sync runs `yarn -s oxfmt <written files>` when `node_modules/.bin/oxfmt` exists, and `uv run --frozen --project tools ruff format <app>/__init__.py`. `--check` compares `toml-merge` and `json-merge` files semantically, so formatting changes made by oxfmt are never drift.

### 3.3 `frappe-init --sync` and `--check`: the CLI

```
frappe-init --sync  [--dry-run] [--skip-lock] [--only <path>[,<path>…]] [--init-listing] [--frappe-version version-16]
ironclad sync --write --offline …   # (or IRONCLAD_OFFLINE=1) no nix, uv or yarn: the sandboxed checks
frappe-init --check [--format text|json|github] [--only …] [--expect-rev <sha>]
```

- Both imply `--app` mode. Both run in the current directory, which MUST be a git work tree whose `pyproject.toml` `[project].name` names a package containing `hooks.py`.
- `frappe-init` sources `lib/sh/app-sync.sh`, which `exec`s `ironclad sync --write|--check …` from `ironclad` in its `runtimeInputs`.
- **Bootstrap** (an app with no flake, or one that locks frappe-nix `main`), from outside any dev shell:

  ```
  nix run github:Avunu/frappe-nix/release-1#frappe-init -- --app --sync --frappe-version version-16
  ```

  This is the single documented entry point for PR A. It needs only `nix` and `git`. `release-1` exists from `v1.0.0` on, so migrations start after N6 tags it (S32).
- Later runs inside an app: `nix run .#frappe-init -- --sync` is preferred. It uses the frappe-nix from `flake.lock`, and the app-mode flake exposes `apps.frappe-init` (N3a).
- `frappe-init --app` (a new app) copies `templates/app/` (`.envrc`, `.gitignore`) with `install_template keep`, then runs `ironclad sync --write`. Nothing else is copied (N3).

`--sync` runs in **two phases** (S32). Each step is a no-op when there is nothing to do.

**Phase A: the flake.**

1. Load or create `[tool.ironclad]`. When creating it:
   - `frappe-major` comes from `--frappe-version`, else the existing `flake.nix` `frappeVersion`, else it is an error;
   - `siblings` comes from `hooks.required_apps`, with the existing flake siblings appended.
2. Render and write `flake.nix` and `.envrc` only.
3. If `flake.lock` is missing, its input set differs from `flake.nix`, or its `frappe-nix` node's `original.ref` isn't `release-<N>` for the running ironclad's major: `nix flake lock` (adding `--update-input frappe-nix` in the last case). The lock now holds a `release-1` commit. For frappe-nix's own self-tests only, `IRONCLAD_FRAPPE_NIX_URL` (e.g. `path:$GITHUB_WORKSPACE`) is passed as `--override-input frappe-nix <url>` to the lock step and every later `nix` call; it is never rendered into `flake.nix`, and sync refuses it unless `IRONCLAD_ALLOW_SKEW=1`.
4. Re-read `frappe_nix.rev` from the new lock. If it differs from the running ironclad's own rev (a bootstrap from a newer or older release), sync re-executes itself as `nix run --no-pure-eval .#frappe-init -- --sync <same args>` once, so phase B always renders with the ironclad that the lock pins. The environment variable `IRONCLAD_SYNC_REEXEC=1` prevents a second re-exec.

**Phase B: everything else.** If `yarn` or `uv` is missing from PATH, sync re-enters through `FRAPPE_NIX_CI=1 nix develop --no-pure-eval -c ironclad sync --write --phase b <same args>` once, so the lock steps below always run.

5. Render every other entry whose `when` is true, and apply its strategy.
6. Delete entries whose `when` is false but that still match an earlier render (an empty diff against the would-be render of the previous context); otherwise warn.
7. Delete every `retire` match (§2.4.1).
8. If `tools/pyproject.toml` changed, `tools/uv.lock` is missing, or a lock floor isn't met: `uv lock --project tools [--upgrade-package …]`.
9. If a managed `package.json` key changed or `yarn.lock` is missing: `yarn install --non-interactive`.
10. Seed the missing `nix/node-locks/<key>` for each present sibling, where key ∈ {`frappe/ui`, `erpnext/banking`, `hrms/frontend`, `hrms/roster`}.
11. If phase A changed `flake.lock` (the `frappe-nix` or a sibling node moved) or `nix/uv.lock` is missing, and `--skip-lock` isn't given: `nix run --no-pure-eval .#relock`. (Appendix I: relock writes no header into `nix/uv.lock`, which is uv's file.)
12. If `discover.has_listing`: `ironclad listing readme --write`.
13. `git add -A` every path written or deleted. Nothing is committed.

`--dry-run` prints the unified diff of steps 2, 5, 6 and 7 and the commands the other steps would run, and writes nothing. A dry run on an unbootstrapped app renders phase B against the rev phase A *would* lock, resolved with `git ls-remote https://github.com/Avunu/frappe-nix refs/heads/release-1`.

`--check` performs steps 1, 2, 5, 6 and 7 in memory, then validates:

- the lock floors (step 8);
- that `flake.lock` contains a node for every input in `flake.nix`;
- that the version skew is zero (§3.7);
- the README blocks (`ironclad listing readme --check`, when installed);
- `.git-blame-ignore-revs`;
- the validation rules for app-owned keys (§2.12).

It never runs `nix`, `uv` or `yarn`, so it works in no-Nix CI.

**Exit codes**, which both modes share:

| Code | Meaning |
|---|---|
| 0 | Clean: nothing to write, or everything written. |
| 1 | Drift: a managed file is missing, differs, or should not exist; a retired file is tracked; or a lock floor isn't met. `--check` only. |
| 2 | Invalid configuration that sync can't fix: a `[tool.ironclad]` schema error, an unknown sibling, a forbidden key, a malformed local region, or a missing README marker. |
| 3 | Environment error: not an app, not a git repo, `flake.lock` unreadable, version skew. Also any failure no command anticipated (`ironclad <cmd>: internal error: …`; `IRONCLAD_DEBUG=1` adds the traceback), so a crash never reads as drift. |

When several apply, the highest code wins.

**Output.**

- `text`: one block per file with `path`, `strategy`, the problem, and a unified diff (`--- current` / `+++ rendered`). The last line is `ironclad: N file(s) drifted — run \`frappe-init --sync\`` or `ironclad: clean`.
- `json`: `{"status":"clean|drift|invalid|error","frappe_nix":{"rev","version"},"files":[{"path","strategy","problem","diff"}]}`. `ironclad-audit` consumes it.
- `github`: the text form plus `::error file=<path>::<problem>` annotations. The text form is wrapped in `::stop-commands::<random token>` … `::<token>::`, so no path, problem or diff line runs as a workflow command, and the annotations follow it with the message escaped (`%`, CR, LF) and the path escaped as a property (also `:` and `,`).

### 3.4 Idempotency

- `--sync` run twice in a row MUST leave the tree byte-identical after the second run, and `--check` MUST exit 0 right after any `--sync` that exited 0.
- Rendering MUST be deterministic:
  - no timestamps and nothing derived from the current date (dates come from git history only);
  - sorted iteration wherever order isn't given by data;
  - `git ls-files` discovery;
  - a fixed Jinja configuration (`trim_blocks=True`, `lstrip_blocks=True`, `keep_trailing_newline=True`).

### 3.5 Parameters and app-specific values (summary)

| Value | Source |
|---|---|
| App name | `[project].name` |
| Frappe major and branch | `[tool.ironclad].frappe-major` |
| Siblings and their order | `[tool.ironclad].siblings` (+ `known-apps.json`) |
| Nested frontend dirs | Discovered (§2.2) |
| SPAs without a `package.json` | `[[tool.ironclad.typescript.spa]]` |
| Generated files | `[tool.ironclad].generated` |
| Strict-checkJs and coverage exemptions | `[[tool.ironclad.unchecked-js]]`, `[[tool.ironclad.coverage-omit]]` (shrink-only) |
| Dev-shell freshness checks | `[tool.ironclad].shell-checks` |
| SCSS globs | `[tool.ironclad.stylelint].globs`, or discovered |
| Unit-test paths | Discovered `test/unit/**/*.test.ts`; minimum from `js-coverage-min` |
| Python test setup | `[tool.ironclad.test].setup` |
| Coverage minimum | `[tool.coverage.report].fail_under` (app-owned value, managed table) |
| pilot assets | `[tool.ironclad].pilot-assets` + `[tool.bench.assets]` |
| Version branch | `version-<frappe-major>` |
| Site name | `[tool.ironclad].site` |

### 3.6 Headers

| Kind | Header |
|---|---|
| `yaml`, `toml`, `ini`, `shell`, `nix` | `# ironclad:managed — generated by frappe-nix (\`frappe-init --sync\`); do not edit[; edit only inside the ironclad:local region(s)].` |
| `jsonc`, `ts` | The same text after `// `. |
| `none` | No header, because the format allows no comments. Listed in the manifest and in `docs/ironclad/managed-files.md`. |

The header names no version, so a frappe-nix release only touches files whose content actually changed.

### 3.7 Version skew

Let `rev` be the `frappe-nix` rev in `flake.lock`. All of the following MUST hold:

- Every `Avunu/frappe-nix/.github/workflows/app-*.yml@<sha>` in the caller workflows has `<sha> == rev` and the comment `# v<ironclad.__version__>`.
- The running `ironclad` was built from `rev`. Nix knows this directly, because the dev shell's `ironclad` comes from the locked frappe-nix. In CI the install step records the rev, and `lint` passes it as `--expect-rev "$IRONCLAD_EXPECT_REV"` (§4.1). `ironclad sync --check` is the same as `frappe-init --check`, and both accept `--expect-rev`.

If any of these fails, `--check` exits 3. The exceptions, the only places that set `IRONCLAD_ALLOW_SKEW=1`:

- `ironclad-audit --against latest` and the nightly `drift` job;
- frappe-nix's own self-tests: when an `app-*.yml` gets a non-empty `frappe-nix-override`, the install step exports `IRONCLAD_ALLOW_SKEW=1` and `lint` omits `--expect-rev`. A commit can't contain its own SHA, so the fixture's rendered callers can never match the commit under test.

---

## 4. CI: the reusable workflows and their callers (N4)

### 4.1 Conventions shared by every `app-*.yml`

- **Third-party actions** are SHA-pinned with a version comment, matching the zizmor policy `"*": hash-pin`. The pins below are the current ones from carbon_frappe and frappe-nix; N4 resolves `actions/*` to SHAs.
  - `DeterminateSystems/nix-installer-action@3138316df39ed29be04236d7ffc686fa525866aa # v23` (`determinate: false`)
  - `DeterminateSystems/magic-nix-cache-action@84c0677f58dcedf3b91f8223ce36a9ea5b3c84b7 # v15` (`use-flakehub: false`, `diagnostic-endpoint: ""`; frappe-nix self-tests only, S15)
  - `astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0`
  - `googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7 # v5.0.0`
  - `dependabot/fetch-metadata@25dd0e34f4fe68f24cc83900b1fe3fe149efef98 # v3.1.0`
  - `actions/checkout`, `actions/setup-node`, `actions/cache`, `actions/upload-artifact` and `cachix/cachix-action`, each `@<sha> # vN`.
- **Checkouts** always use `persist-credentials: false`, with no exception. The three jobs that push — `relock-push` and `relock-upgrade` (§4.5) and nightly `shots` (§4.6) — push to an explicit remote, `git push "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" HEAD:<ref>`, with `GH_TOKEN: ${{ github.token }}` in that step's `env:` only. Before pushing, each runs `git diff --name-only <base>..HEAD | grep -q '^\.github/workflows/' && exit 1` (S29).
- **Permissions.** Each workflow sets `permissions: {}` at the top, and every job declares exactly what it needs.
- **Environment.** `FORCE_COLOR: "1"`. Every `run:` uses `set -euo pipefail` (bash), and every `${{ }}` value from an event, an input or a secret goes through a step's `env:` rather than being interpolated into `run:`, as zizmor's template-injection rule requires. A job-level `if:` reads only `inputs`, `github` and `needs`, never `secrets` (the context isn't available there); a condition on a secret is tested inside the script, e.g. `[ -n "$REGISTRY_TOKEN" ] || { echo "::notice::…"; exit 0; }`.
- **Shell conditionals** under `set -e` are written as `if …; then …; fi`, never `[ … ] && …` as a last line, whose false test would fail the step.
- **Working directory.** `app-root` (default `.`) is applied per job as `defaults.run.working-directory: ${{ inputs.app-root }}`; workflow-level `defaults` can't read `inputs`. `uses:` steps ignore it, so every path input of an action is prefixed explicitly: `node-version-file: ${{ inputs.app-root }}/package.json`, `actions/cache` `path:` and `key:` hash globs, setup-uv `cache-dependency-glob`, and `upload-artifact` paths.
- **Install ironclad.** `lint`, `typecheck`, `marketplace` and `pr-policy` all use the same step:

```yaml
- name: Install ironclad at the frappe-nix revision flake.lock pins
  env:
    OVERRIDE: ${{ inputs.frappe-nix-override }}
  run: |
    set -euo pipefail
    rev="$(jq -r '.nodes[.nodes.root.inputs["frappe-nix"]].locked.rev' flake.lock)"
    if [ -n "$OVERRIDE" ]; then
      uv tool install --python 3.14 "$GITHUB_WORKSPACE/$OVERRIDE/py/ironclad"
      echo "IRONCLAD_ALLOW_SKEW=1" >> "$GITHUB_ENV"      # self-test only (§3.7)
      echo "IRONCLAD_EXPECT_REV=" >> "$GITHUB_ENV"
    else
      uv tool install --python 3.14 "ironclad @ git+https://github.com/Avunu/frappe-nix@${rev}#subdirectory=py/ironclad"
      echo "IRONCLAD_EXPECT_REV=$rev" >> "$GITHUB_ENV"
    fi
```

`lint` passes `--expect-rev "$IRONCLAD_EXPECT_REV"` only when the variable is non-empty. The package is self-contained (S10), so this install has every template and data file.

- **Nix setup.** The `test`, `relock-plan` and nightly Nix jobs all use the same steps, in this order:
  1. Free disk: `sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /opt/hostedtoolcache/CodeQL`.
  2. nix-installer.
  3. Cache selection (S15). Let `trusted` be `github.ref == 'refs/heads/develop' && (github.event_name == 'push' || github.event_name == 'schedule')`. A step maps `TOKEN` in its `env:` to `${{ (trusted) && secrets.NIX_CACHE_TOKEN || secrets.NIX_CACHE_READ_TOKEN }}`; nothing else ever sees the write token.
     - `inputs.nix-cache == ''` → **no cache action**. (magic-nix-cache is throttled in every bench-sized job and evicts the node_modules caches; frappe-nix's own self-tests may still use it for their small closures.)
     - `cachix:<name>` → `cachix/cachix-action` with `name: <name>`, `authToken` from that step's mapped token, and `skipPush: ${{ !(trusted) }}`.
     - `attic:<endpoint>/<cache>` → `nix profile install --inputs-from . nixpkgs#attic-client` (pinned by the app's `flake.lock`), then always `attic login ci <endpoint> ${TOKEN:+"$TOKEN"}` (a public cache needs no token but still needs the server configured), then `attic use ci:<cache>`. A final step `if: success() && <trusted>` runs `attic push ci:<cache> ./result .dev-dist/devshell-profile` with the write token.
  4. Every `nix develop` uses `--profile .dev-dist/devshell-profile`, so the dev-shell closure can be pushed.

  Org setup: `IRONCLAD_NIX_CACHE` is an org variable; `IRONCLAD_NIX_CACHE_TOKEN` (write) is an org **Actions** secret only; `IRONCLAD_NIX_CACHE_READ_TOKEN` (pull-only, or unset for a public cache) is an org Actions secret **and** an org Dependabot secret. That is the whole drop-in. Dependabot runs, which execute bumped third-party code, never hold a credential that can write the cache.
- **Self-test only.** The input `frappe-nix-override` (a path relative to the workspace) adds `--override-input frappe-nix "path:$GITHUB_WORKSPACE/<override>"` to every `nix` call, installs ironclad from that path, and allows skew (§3.7).

### 4.2 `app-ci.yml`: the jobs behind `ci / lint`, `ci / typecheck`, `ci / test` and `ci / marketplace`

```yaml
on:
  workflow_call:
    inputs:
      app:                  { type: string, required: true }
      app-root:             { type: string, default: "." }
      nix-cache:            { type: string, default: "" }      # "", "cachix:<name>", "attic:<endpoint>/<cache>"
      test-timeout-minutes: { type: number, default: 75 }
      run-test:             { type: boolean, default: true }   # false skips the `test` job (frappe-nix self-test PRs)
      frappe-nix-override:  { type: string, default: "" }      # frappe-nix self-test only
    secrets:
      NIX_CACHE_TOKEN:      { required: false }                # write; used only on trusted runs (S15)
      NIX_CACHE_READ_TOKEN: { required: false }
    outputs:
      coverage: { value: "${{ jobs.test.outputs.coverage }}" }  # e.g. "63.2"
permissions: {}
```

Every gate job carries `if: ${{ !startsWith(github.ref, 'refs/heads/version-') }}`, and `test` additionally `&& inputs.run-test`. A skipped `test` only happens in frappe-nix's self-test, which isn't a ruleset context.

- **Dispatch mode** is when `github.event_name == 'workflow_dispatch'`. In that mode, the first step of every gate job looks up the open PR whose `headRefName == github.ref_name`. If one exists, it asserts `headRefOid == github.sha` and fails with `::error::stale dispatch` otherwise. Every gate job therefore has `pull-requests: read` (with explicit permissions, an unlisted scope is `none`, and the lookup would fail with "Resource not accessible by integration").
- **Ratchet base** (`BASE`, S24).
  - `pull_request`: `git merge-base "$BASE_SHA" HEAD`, with `BASE_SHA` = `github.event.pull_request.base.sha` passed through `env:`.
  - dispatch: `git merge-base origin/develop HEAD`.
  - push to `develop`, and `schedule`: the ratchet is skipped.

| Job (`name:`) | `runs-on` / timeout | Permissions | Steps |
|---|---|---|---|
| `lint` | ubuntu-latest / 20 | `contents: read`, `pull-requests: read` | <ol><li>dispatch guard (above)</li><li>checkout, `fetch-depth: 0`</li><li>setup-node (`node-version-file: ${{ inputs.app-root }}/package.json`)</li><li>`actions/cache` of `node_modules`, keyed on `yarn.lock`</li><li>`yarn install --frozen-lockfile --non-interactive`</li><li>setup-uv, with `cache-dependency-glob: ${{ inputs.app-root }}/tools/uv.lock`</li><li>`uv lock --check --project tools && uv sync --frozen --project tools`</li><li>install ironclad</li><li>`ironclad sync --check --format github`, plus `--expect-rev "$IRONCLAD_EXPECT_REV"` when that is non-empty</li><li>`uv run --frozen --project tools prek run --all-files --show-diff-on-failure`</li><li>`ironclad minibench --mode customizations --dest .dev-dist/minibench` and then, from the worktree it creates, `prek run --hook-stage manual validate_customizations --all-files --show-diff-on-failure` and the same for `clean_customized_doctypes`. minibench exits 1 if `sites/`, `env/` or `apps/` is missing (the guard).</li><li>semgrep (below)</li><li>`ironclad ratchet --base "$BASE"`, unless the ratchet is skipped for this event</li><li>`yarn -s ci:lint` if `package.json` has that script</li></ol> |
| `typecheck` | ubuntu-latest / 20 | `contents: read`, `pull-requests: read` | <ol><li>dispatch guard</li><li>checkout</li><li>node</li><li>cache</li><li>`yarn install --frozen-lockfile`</li><li>setup-uv; install ironclad</li><li>`export FRAPPE_PATH="$(ironclad pin-path frappe)"` into `$GITHUB_ENV`: the app's locked frappe tree, fetched and narHash-verified (carbon's `compile` and `audit:drift` read it)</li><li>if `[tool.ironclad] frappe-node-modules = true`: `yarn --cwd "$FRAPPE_PATH" install --frozen-lockfile --ignore-scripts --non-interactive`, cached on frappe's `yarn.lock` hash</li><li>if the `typecheck` script exists: `yarn -s typecheck` (the managed solution, then each SPA's `check`)</li><li>if `[[tool.ironclad.unchecked-js]]` is non-empty: `ironclad unchecked-js --stale` (§2.9)</li><li>if `test:unit` exists: assert at least one `test/unit/**/*.test.ts`, then `yarn -s test:unit`</li><li>for each nested frontend whose `package.json` has a `typecheck` script: `yarn --cwd <d> install --frozen-lockfile && yarn --cwd <d> typecheck`</li><li>if `types/doctypes.d.ts` is tracked: `ironclad minibench --mode doctypes --dest .dev-dist/minibench`, then `yarn -s frappe-types gen-doctypes --bench .dev-dist/minibench --app <app> --include-siblings <csv> --out types/doctypes.d.ts --check`. gen-doctypes merges the app's Custom Field definitions (`custom/*.json` and `fixtures/*.json`, which minibench copies) into the core doctypes they extend.</li><li>if `[tool.ironclad.typescript] audit-consumer = true`: `yarn -s frappe-types audit-consumer --app . --strict --types 'types/*.d.ts'`. Otherwise, when the subcommand exists, it runs without `--strict` and its summary is a `::notice::`. The default flips to on in a later frappe-nix MINOR, once frappe-types ships `--types` and the string-literal fix; today `--strict` fails 9 of 12 apps, partly on false positives.</li><li>`yarn -s ci:typecheck` if present (it sees `FRAPPE_PATH`)</li></ol> |
| `test` | ubuntu-latest / `inputs.test-timeout-minutes` | `contents: read`, `pull-requests: read` | <ol><li>dispatch guard</li><li>checkout, `fetch-depth: 0`</li><li>Nix setup (§4.1)</li><li>**Clean bench build**, `nix build .#default -L --no-pure-eval` (also compiles every app's assets, so it exercises S30): always on push, schedule and dispatch; on `pull_request` only when `git diff --name-only "$(git merge-base "$BASE_SHA" HEAD)" HEAD` touches `package.json`, `yarn.lock`, `flake.nix`, `flake.lock`, `nix/**`, `vite*.config.*`, `scripts/ironclad-vite-register.mjs`, `<app>/public/**`, a nested frontend, an SPA root or `<app>/hooks.py`. Every release PR is dispatched, so it always gets the build.</li><li>`FRAPPE_NIX_CI=1 nix develop --no-pure-eval --profile .dev-dist/devshell-profile -c frappe-test --ci` (the single shell entry; §5.1). This includes the `shell-checks` stage.</li><li>`if: failure()`: tail 200 lines of each `.frappe-nix/bench/logs/*.log` in a group</li><li>`if: always()`: upload `.dev-dist/test/` as the artifact `test-report`</li><li>cache push (§4.1, trusted runs only)</li><li>the output `coverage` is read from `.dev-dist/test/ironclad-report.json`</li></ol> |
| `marketplace` | ubuntu-latest / 30 | `contents: read`, `pull-requests: read` | <ol><li>dispatch guard</li><li>checkout</li><li>`sudo apt-get install -y --no-install-recommends pkg-config libmysqlclient-dev` (mysqlclient has no Linux wheel, and L9 installs frappe from source; registry-dryrun track)</li><li>setup-uv, `cache-dependency-glob: ${{ inputs.app-root }}/flake.lock`</li><li>install ironclad</li><li>`EIO_BACKEND=posix ironclad listing check --format github` (§5.2: listing, hooks metadata, pyproject ranges, version, override allow-list, logo structure, registry semgrep at the pinned marketplace rev with the baseline, pilot get-app validator at the pinned pilot rev with the dependency apps on the bench)</li><li>upload `.dev-dist/marketplace/` as the artifact `marketplace-report`</li></ol>The pip/uv caches for L9's validation bench are kept under `actions/cache` keyed on the pinned frappe and pilot revs; `selftest-product` (N5) measures L9 and fails if a cold run exceeds 25 minutes, leaving 5 minutes of margin. |

There is no `version-guard` job. `version-*` only ever moves through the release workflow's REST PATCH, which raises no `push` event, so a push-triggered guard would never run. The fast-forward job asserts the D8 invariant itself right after the PATCH (§4.4), and audit row A2 checks it nightly.

**The semgrep step** in `lint`, with `RULES` set to the `frappe-semgrep-rules` store path read from the app's flake.lock (`ironclad pin-path frappe-semgrep-rules` fetches and verifies that tree, then prints its path):

```
RULES="$(ironclad pin-path frappe-semgrep-rules)"
uv run --frozen --project tools semgrep scan --error --metrics=off --config "$RULES/rules" <app>
uv run --frozen --project tools semgrep scan --error --metrics=off --include='**/test_*.py' \
  --config "$(ironclad data-path semgrep/test-correctness.yml)" <app>
if [ -d semgrep ]; then
  uv run --frozen --project tools semgrep scan --error --metrics=off --config semgrep <app>
fi
```

`r/python.lang.correctness`, which carbon_frappe uses, is dropped because it is unpinned.

### 4.3 `app-pr-policy.yml`: the job behind `ci / pr-policy`

```yaml
on:
  workflow_call:
    inputs:
      app:                 { type: string, required: true }
      frappe-nix-override: { type: string, default: "" }
permissions: {}
jobs:
  pr-policy:
    name: pr-policy
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions: { contents: read, pull-requests: read }
```

Steps:

1. checkout with `fetch-depth: 0`;
2. setup-uv, then `uv sync --frozen --project tools`;
3. install ironclad;
4. `ironclad policy --pr`, with `env` `GH_TOKEN: ${{ github.token }}`, `EVENT_NAME`, `BASE_REF` (`github.event.pull_request.base.ref`), `HEAD_SHA` (`github.event.pull_request.head.sha || github.sha`), `PR_TITLE` and `REF_NAME`. On a dispatch, `BASE_REF` and `PR_TITLE` are empty; `ironclad policy` then takes them from the PR it looks up (§5.9).

What `ironclad policy --pr` checks is in §5.9.

### 4.4 `app-release.yml`: releases without an App

```yaml
on:
  workflow_call:
    inputs:
      app:            { type: string, required: true }
      version-branch: { type: string, required: true }      # "version-16"
      publish:        { type: boolean, default: false }     # caller passes vars.MARKETPLACE_PUBLISH == 'true'
      pilot-assets:   { type: boolean, default: false }
      registry-fork:  { type: string, default: "Avunu/marketplace" }
    secrets:
      REGISTRY_TOKEN: { required: false }
    outputs:
      released: { value: "${{ jobs.release-please.outputs.releases_created }}" }
      tag:      { value: "${{ jobs.release-please.outputs.tag_name }}" }
permissions: {}
```

| Job | `needs` / `if` | Permissions | What it does |
|---|---|---|---|
| `release-please` | `if: github.ref == 'refs/heads/develop'` | `contents: write`, `pull-requests: write` | release-please-action with `config-file`, `manifest-file` and `target-branch: develop`. Outputs: `releases_created`, `sha`, `tag_name`. |
| `dispatch-ci` | needs release-please; `if: always() && needs.release-please.result == 'success'` | `actions: write`, `checks: read`, `pull-requests: read` | Finds the open PR: `gh pr list --base develop --label 'autorelease: pending' --state open --json number,headRefName,headRefOid`. Accepts it only if `headRefName == "release-please--branches--develop--components--<package-name>"` or `"release-please--branches--develop"`. Then checks each required context on the head SHA independently (`gh api repos/$R/commits/$SHA/check-runs?check_name=<ctx>`): if `ci / lint` is absent, `gh workflow run ci.yml --ref "$head"`; if `ci / pr-policy` is absent, `gh workflow run pr-policy.yml --ref "$head"`. A dispatch that failed or was cancelled by a concurrency group is therefore retried by the next run. Idempotent under the daily cron. |
| `fast-forward` | needs release-please; `if: needs.release-please.outputs.releases_created == 'true'` | `contents: write`, `actions: write` | `gh api repos/$R/git/ref/heads/$VB` exists → `gh api -X PATCH repos/$R/git/refs/heads/$VB -f sha=$SHA -F force=false`; otherwise `gh api -X POST repos/$R/git/refs -f ref=refs/heads/$VB -f sha=$SHA`, which happens on the first release. GitHub refuses anything that isn't a fast-forward. `$SHA` is a commit already on develop, so the PATCH never introduces a new workflow file (S29). **Guard (D8):** it then reads the ref back and the newest `v<major>.*` tag (peeled) and fails with `::error::` unless they are equal. Then, if `inputs.pilot-assets`: `gh workflow run assets.yml --ref "$VB"` (S14). |
| `publish` | needs fast-forward; `if: inputs.publish` | `contents: write` (to edit the release notes) | `env: REGISTRY_TOKEN: ${{ secrets.REGISTRY_TOKEN }}`, `FORK: ${{ inputs.registry-fork }}`, `TAG`, `VB`. Fails with `::error::` naming the missing secret if `$REGISTRY_TOKEN` is empty. Otherwise: install ironclad; `GH_TOKEN="$REGISTRY_TOKEN" ironclad listing registry --tag "$TAG" --branch "$VB" --fork "$FORK"`. On success, `gh release edit "$TAG" --notes-file …`, with `GITHUB_TOKEN`, appends `Marketplace: <registry PR URL>`. |

- **Merging the release PR** is the "ship it" moment. Under §7 decision 6, a person or the main agent does it. GitHub then cuts the tag through release-please on the next run: either the `push` from a human merge, or the daily cron if the merge was done with `GITHUB_TOKEN`.
- **Concurrency** is set by the caller: `group: release`, `cancel-in-progress: false`.

### 4.5 `app-deps.yml`: relock, auto-merge, sweep

```yaml
on:
  workflow_call:
    inputs:
      app:        { type: string, required: true }
      nix-cache:  { type: string, default: "" }
      auto-merge: { type: boolean, default: true }
    secrets:
      NIX_CACHE_TOKEN:      { required: false }
      NIX_CACHE_READ_TOKEN: { required: false }
permissions: {}
```

Let `isDependabot` be `github.event_name == 'pull_request' && github.event.pull_request.user.login == 'dependabot[bot]'`. zizmor's bot-conditions rule requires reading the PR author rather than `github.actor`.

**What dependabot `nix` moves.** `frappe` and the siblings only (S5). Their bump changes `flake.lock`, `nix/uv.lock` and possibly `nix/node-locks/**`, never a workflow file, so `relock-push` can push it with `GITHUB_TOKEN`. frappe-nix moves through `ironclad-rollout` (§5.8).

| Job | `if` | Permissions | Steps |
|---|---|---|---|
| `relock-plan` | `isDependabot && startsWith(github.head_ref, 'dependabot/nix/')` | `contents: read` | <ol><li>checkout the PR head SHA, `fetch-depth: 0`</li><li>Nix setup (read token only)</li><li>if the PR moves the `frappe-nix` lock node (compare `flake.lock` against the merge-base): write `frappe-nix-moved` to the job output and stop; the auto-merge job closes the PR (§2.17)</li><li>`nix run --no-pure-eval .#relock`</li><li>`FRAPPE_NIX_CI=1 nix develop --no-pure-eval -c frappe-init --sync --skip-lock`</li><li>if `pyproject.toml` changed: `nix run --no-pure-eval .#relock` again</li><li>`git add -A && git diff --cached --binary > relock.patch` (sync stages its writes, and new files such as fresh `nix/node-locks/**` are untracked, so the plain `git diff` would miss both)</li><li>if `relock.patch` names any path under `.github/workflows/`: fail with `::error::relock would change a workflow; run ironclad-rollout` (S29)</li><li>output `empty=true|false`; upload the patch as an artifact. This job runs PR code, so it has no write token and no secrets apart from the read-only cache token.</li></ol> |
| `relock-push` | needs relock-plan; `needs.relock-plan.outputs.empty == 'false'` | `contents: write`, `actions: write`, `pull-requests: write` | <ol><li>checkout the head ref, `persist-credentials: false`</li><li>download the patch; `git apply --index relock.patch`; nothing else runs</li><li>refuse if the index touches `.github/workflows/` (S29)</li><li>`git -c user.name=github-actions[bot] -c user.email=41898282+github-actions[bot]@users.noreply.github.com commit -m "chore(deps): relock and sync after flake bump [dependabot skip]"`</li><li>push to the explicit token remote (§4.1) `HEAD:$HEAD_REF`</li><li>`gh workflow run ci.yml --ref "$HEAD_REF" && gh workflow run pr-policy.yml --ref "$HEAD_REF"`</li><li>if `inputs.auto-merge`: `gh pr merge --auto --squash "$PR_URL"`, which is the only place a nix PR's auto-merge is enabled after a relock</li></ol>The push creates only 0-job `action_required` runs, so it can't loop. `[dependabot skip]` lets Dependabot keep rebasing; each rebase triggers relock again, and a new head SHA cancels any pending auto-merge only after the required checks re-run on it. |
| `auto-merge` | `isDependabot && inputs.auto-merge` | `contents: write`, `pull-requests: write` | `fetch-metadata`, then the decision table below. When the verdict is auto, `gh pr merge --auto --squash "$PR_URL"`; when it is review, `gh pr edit "$PR_URL" --add-label needs-review`. For `nix` this job never enables auto-merge itself: it `needs: relock-plan` and enables it only when `relock-plan` reported `empty == 'true'` (nothing to relock). Otherwise `relock-push` enables it after its push, so a stale `nix/uv.lock` can't merge on green checks that ran before the relock landed. When `relock-plan` reported `frappe-nix-moved`, it comments and closes the PR. |
| `sweep` | `github.event_name == 'schedule' \|\| github.event_name == 'workflow_dispatch'` | `actions: write`, `checks: read`, `pull-requests: read` | For each open PR whose author is `app/dependabot` or `app/github-actions`, check each required context on its head SHA independently: dispatch `ci.yml` when `ci / lint` is absent, and `pr-policy.yml` when `ci / pr-policy` is absent. This is the fallback if a Dependabot-triggered job can't be granted `actions: write` (inferred, not proven), and it retries a dispatch that failed or was cancelled. |
| `relock-upgrade` | `github.event.schedule == '0 8 * * 1' \|\| (github.event_name == 'workflow_dispatch')` | `contents: write`, `pull-requests: write`, `actions: write` | <ol><li>checkout develop, `persist-credentials: false`</li><li>Nix setup, with the read token only even though it is a schedule: it resolves new third-party packages</li><li>`nix run --no-pure-eval .#relock -- --upgrade`</li><li>if nothing changed, exit 0</li><li>otherwise commit `chore(deps): relock python` on branch `ironclad/relock-python`, refuse workflow paths (S29), and force-push to the token remote (§4.1)</li><li>open or update the PR with `GITHUB_TOKEN`</li><li>dispatch `ci.yml` and `pr-policy.yml` on it</li><li>`gh pr merge --auto --squash`</li></ol> |

**The PAT-free automation spike (N4, not blocking v1).** N4 tests, on a scratch repo, whether a `GITHUB_TOKEN` can land a commit that changes a workflow file through the Git Data API (`POST git/blobs`, `git/trees`, `git/commits`, then `PATCH git/refs/heads/<branch>` with `force=false`). If it can, a later frappe-nix MINOR adds an opt-in `frappe-nix-bump` job that does what `ironclad-rollout` does. If it can't, `ironclad-rollout` stays the only path and nothing changes. The result is written into `docs/ironclad/github.md`.

**Auto-merge decisions**, using `fetch-metadata`'s `package-ecosystem`, `update-type`, `dependency-group`, `dependency-names` and `directory`. The ecosystem strings are fetch-metadata v3's own values; N4 adds a unit test with recorded metadata for each row.

| `package-ecosystem` | Merged automatically | Labelled `needs-review` |
|---|---|---|
| `github_actions` | the `actions` group | the `docs-actions` group (`Avunu/docusystem*`; a merge publishes the docs site) and the `pilot` group (`frappe/pilot*`); `Avunu/frappe-nix` never arrives (ignored) |
| `npm_and_yarn` | minor and patch in `/` and nested frontends | `directory == /docs-site`; any `version-update:semver-major` |
| `uv` (`/tools`) | minor and patch | semver-major |
| `pre_commit` | `hooks` group | the `test-utils` group, i.e. anything touching `agritheory/test_utils` (S19, test-utils track) |
| `nix` | enabled by `relock-push` after its push, or by this job when there was nothing to relock | a PR that moves `frappe-nix` is closed instead (S5) |
| `submodules` | always | — |

The required checks are the gate, and auto-merge waits for them on the newest head SHA.

### 4.6 `app-nightly.yml`

```yaml
on:
  workflow_call:
    inputs:
      app:       { type: string, required: true }
      nix-cache: { type: string, default: "" }
      publish:   { type: boolean, default: false }
    secrets:
      NIX_CACHE_TOKEN:      { required: false }
      NIX_CACHE_READ_TOKEN: { required: false }
      REGISTRY_TOKEN:       { required: false }
permissions: {}
```

| Job | Permissions | Steps |
|---|---|---|
| `integration` | `contents: read` | Nix setup, then **no** `FRAPPE_NIX_CI`, because node modules are needed for `bench build`. Then `nix develop --no-pure-eval -c bash -c 'bash "$(ironclad data-path ci/nightly.sh)"'` (the dev shell's ironclad resolves the path), running the script below. Every failure is kept, and the job fails if the tests, the build or any suite failed. Uploads `.dev-dist/`. |
| `shots` | `contents: write`, `pull-requests: write`, `actions: write` | Runs only if `marketplace/screenshots.ts` is tracked. Nix setup; `nix develop -c frappe-shots --update`. If `git status --porcelain docs/screenshots` is non-empty: commit `docs: refresh screenshots` on branch `ironclad/screenshots`, refuse workflow paths (S29), force-push to the token remote (§4.1), open or update a PR labelled `screenshots`, and dispatch `ci.yml` and `pr-policy.yml`. **Never** auto-merged. The job summary links the diff images artifact. |
| `links` | `contents: read` | Install ironclad; `ironclad listing check --links-only` (both URLs must return 200 over https). With Nix set up, also `nix run --no-pure-eval .#frappe-icon -- check` for the raster checks, so `resvg` comes from the app's locked frappe-nix, not the registry nixpkgs. |
| `duplication` | `contents: read` | `uv run --frozen --project tools prek run --hook-stage manual check_code_duplication --all-files`. The `check_code_duplication` hook is in the managed test_utils block with `stages: [manual]` (§2.14). It runs jscpd through npx, needs the network, and is informational: `continue-on-error: true`. |
| `drift` | `contents: read`, `issues: write` | Installs the **latest** frappe-nix release's ironclad and runs `IRONCLAD_ALLOW_SKEW=1 ironclad sync --check --format json`. If the pinned frappe-nix is older than the latest release, it opens or updates the issue "ironclad: frappe-nix vX.Y.Z available" with the drift summary and the command `ironclad-rollout --to vX.Y.Z --repo <repo>`, and closes it when current. This issue is the reminder for the manual mover (S5). |
| `registry-refresh` | `contents: read` | Runs only if `inputs.publish`. The step maps `REGISTRY_TOKEN: ${{ secrets.REGISTRY_TOKEN }}` into `env:` and exits 0 with a notice when it is empty; otherwise `GH_TOKEN="$REGISTRY_TOKEN" ironclad listing registry --refresh`, which rebuilds the open registry PR branch on the current upstream `main`, because registry CI requires it to be up to date. |

The nightly script (N4, `py/ironclad/ironclad/data/ci/nightly.sh`):

```bash
set -uo pipefail
rc=0
export FRAPPE_ADMIN_PASSWORD="${FRAPPE_TEST_ADMIN_PASSWORD:-admin}"
export IRONCLAD_ARTIFACT_DIR="$PWD/.dev-dist/nightly"; mkdir -p "$IRONCLAD_ARTIFACT_DIR"
frappe-test --ci --keep-up || rc=$?
bench build || rc=1
port="$(jq .webserver_port "$FRAPPE_BENCH_ROOT/sites/common_site_config.json")"
export FRAPPE_SITE_URL="http://127.0.0.1:${port}"
export CF_SITE_URL="$FRAPPE_SITE_URL" CF_SHOT_DIR="$IRONCLAD_ARTIFACT_DIR"   # the names carbon's suites read
while IFS= read -r s; do
  bash -c "$s" || rc=1
done < <(ironclad config nightly-suites)
frappe-test --down
exit "$rc"
```

`ironclad config <key>` (N3a) prints a `[tool.ironclad]` value, one list item per line.

### 4.7 Caller files (rendered into each app; whole; N4)

Every caller file has the YAML header, `permissions: {}`, and exactly one job. `<rev>` is `frappe_nix.rev` and `<v>` is `frappe_nix.version`.

`.github/workflows/ci.yml`:

```yaml
name: ci
on:
  push:
    branches: [develop]
  pull_request:
  schedule:
    - cron: "0 6 * * *"     # develop, nightly: GITHUB_TOKEN auto-merges raise no push
  workflow_dispatch:
permissions: {}
concurrency:
  group: ci-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  ci:
    permissions:
      contents: read
      pull-requests: read
    uses: Avunu/frappe-nix/.github/workflows/app-ci.yml@<rev> # v<v>
    with:
      app: <app>
      nix-cache: ${{ vars.IRONCLAD_NIX_CACHE || '' }}
    secrets:
      NIX_CACHE_TOKEN: ${{ secrets.IRONCLAD_NIX_CACHE_TOKEN }}
      NIX_CACHE_READ_TOKEN: ${{ secrets.IRONCLAD_NIX_CACHE_READ_TOKEN }}
```

`.github/workflows/pr-policy.yml`:

```yaml
name: pr-policy
on:
  pull_request:
    types: [opened, edited, synchronize, reopened]
  workflow_dispatch:
permissions: {}
concurrency:
  group: pr-policy-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
jobs:
  ci:
    permissions:
      contents: read
      pull-requests: read
    uses: Avunu/frappe-nix/.github/workflows/app-pr-policy.yml@<rev> # v<v>
    with:
      app: <app>
```

`.github/workflows/release.yml`:

```yaml
name: release
on:
  push:
    branches: [develop]
  schedule:
    - cron: "30 5 * * *"    # daily: auto-merged commits raise no push
  workflow_dispatch:
permissions: {}
concurrency:
  group: release
  cancel-in-progress: false
jobs:
  release:
    permissions:
      contents: write
      pull-requests: write
      actions: write
      checks: read
    uses: Avunu/frappe-nix/.github/workflows/app-release.yml@<rev> # v<v>
    with:
      app: <app>
      version-branch: version-<major>
      publish: ${{ vars.MARKETPLACE_PUBLISH == 'true' }}
      pilot-assets: <true|false>
    secrets:
      REGISTRY_TOKEN: ${{ secrets.REGISTRY_TOKEN }}
```

`.github/workflows/deps.yml`:

```yaml
name: deps
on:
  pull_request:
  schedule:
    - cron: "0 9 * * *"     # daily sweep
    - cron: "0 8 * * 1"     # weekly python relock (must match app-deps.yml)
  workflow_dispatch:
permissions: {}
jobs:
  deps:
    permissions:
      contents: write
      pull-requests: write
      actions: write
      checks: read
    uses: Avunu/frappe-nix/.github/workflows/app-deps.yml@<rev> # v<v>
    with:
      app: <app>
      nix-cache: ${{ vars.IRONCLAD_NIX_CACHE || '' }}
    secrets:
      NIX_CACHE_TOKEN: ${{ secrets.IRONCLAD_NIX_CACHE_TOKEN }}
      NIX_CACHE_READ_TOKEN: ${{ secrets.IRONCLAD_NIX_CACHE_READ_TOKEN }}
```

`.github/workflows/nightly.yml`:

```yaml
name: nightly
on:
  schedule:
    - cron: "0 7 * * *"
  workflow_dispatch:
permissions: {}
concurrency:
  group: nightly
  cancel-in-progress: false
jobs:
  nightly:
    permissions:
      contents: write
      pull-requests: write
      actions: write
      issues: write
    uses: Avunu/frappe-nix/.github/workflows/app-nightly.yml@<rev> # v<v>
    with:
      app: <app>
      nix-cache: ${{ vars.IRONCLAD_NIX_CACHE || '' }}
      publish: ${{ vars.MARKETPLACE_PUBLISH == 'true' }}
    secrets:
      NIX_CACHE_TOKEN: ${{ secrets.IRONCLAD_NIX_CACHE_TOKEN }}
      NIX_CACHE_READ_TOKEN: ${{ secrets.IRONCLAD_NIX_CACHE_READ_TOKEN }}
      REGISTRY_TOKEN: ${{ secrets.REGISTRY_TOKEN }}
```

`.github/workflows/assets.yml`, only when `pilot-assets = true`. The pilot SHA is a floor and is moved by dependabot.

```yaml
name: assets
on:
  workflow_dispatch:
permissions: {}
jobs:
  assets:
    if: startsWith(github.ref, 'refs/heads/version-')
    permissions:
      contents: write
    uses: frappe/pilot/.github/workflows/app-assets.yml@e2364936eb0a4f89a5309d22fb198056f14ff86a # develop
```

- zizmor MUST pass on every rendered caller, and on frappe-nix's own `app-*.yml`, with the shipped `zizmor.yml`. If zizmor's `excessive-permissions` audit flags a caller job's union of permissions, N4 adds a narrowly scoped `rules.excessive-permissions.ignore` for `ci.yml`, `release.yml`, `deps.yml` and `nightly.yml` to the template, with a comment that reusable callers must grant the union.
- `docs.yml` and `docs-publish.yml` stay as docusystem renders them.
- The scheduled triggers in `ci.yml`, `release.yml`, `deps.yml` and `nightly.yml` assume a public repo (unlimited Actions minutes). Under §7 decision 2, a private repo (timeclock today) is made public **before** its PR A; `ironclad-apply --phase full` refuses an `apps.json` entry with `private: true` (§5.6).
- `ci.yml` doesn't run on `version-*`: that branch only moves by the release workflow's PATCH, which raises no event (§4.2).

### 4.8 Rulesets (`ironclad/rulesets/*.json`, N4)

`develop.json`:

```json
{
  "name": "develop",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/develop"], "exclude": [] } },
  "bypass_actors": [],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "allowed_merge_methods": ["squash"],
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          { "context": "ci / pr-policy",   "integration_id": 15368 },
          { "context": "ci / lint",        "integration_id": 15368 },
          { "context": "ci / typecheck",   "integration_id": 15368 },
          { "context": "ci / test",        "integration_id": 15368 },
          { "context": "ci / marketplace", "integration_id": 15368 } ] } }
  ]
}
```

`develop-provisional.json` is the same as `develop.json` without the `required_status_checks` rule, and with the same `name` ("develop"), so the full ruleset later replaces it in place.

`version.json`:

```json
{
  "name": "version-* (release automation only)",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/version-*"], "exclude": [] } },
  "bypass_actors": [ { "actor_type": "Integration", "actor_id": 15368, "bypass_mode": "always" } ],
  "rules": [
    { "type": "creation" },
    { "type": "update", "parameters": { "update_allows_fetch_and_merge": false } },
    { "type": "deletion" },
    { "type": "non_fast_forward" }
  ]
}
```

`tags.json`:

```json
{
  "name": "v* tags",
  "target": "tag",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/tags/v*"], "exclude": [] } },
  "bypass_actors": [],
  "rules": [
    { "type": "deletion" },
    { "type": "update", "parameters": { "update_allows_fetch_and_merge": false } },
    { "type": "non_fast_forward" }
  ]
}
```

Creating tags isn't restricted, because release-please creates them through the Releases API with `GITHUB_TOKEN`. `assets-version-16` doesn't match `v*`.

If the API rejects `Integration 15368` with a 422, `ironclad-apply` stops for that repo and prints the two fallbacks, in order:

1. `version.user.json`: the same file with `bypass_actors: [{"actor_type": "User", "actor_id": <machine user id>, "bypass_mode": "always"}]` (the `User` bypass actor type, GA 2026-05-07, github-capabilities track). The fast-forward step then uses that machine user's token (`VERSION_BRANCH_TOKEN`) instead of `GITHUB_TOKEN`.
2. `version.deploykey.json`: `bypass_actors: [{"actor_type": "DeployKey", "actor_id": null, "bypass_mode": "always"}]`. Unusable today: the org has deploy keys disabled (`deploy_keys_enabled_for_repositories: false`), and only an org owner can enable them.

Both need the user (Q2).

### 4.9 Repo settings (`ironclad/repo-settings.json`, N4)

```json
{
  "repository": {
    "default_branch": "develop",
    "allow_squash_merge": true,
    "allow_merge_commit": false,
    "allow_rebase_merge": false,
    "allow_auto_merge": true,
    "allow_update_branch": true,
    "delete_branch_on_merge": true,
    "squash_merge_commit_title": "PR_TITLE",
    "squash_merge_commit_message": "COMMIT_MESSAGES",
    "has_wiki": false,
    "has_projects": false,
    "homepage": "{documentation}",
    "security_and_analysis": {
      "secret_scanning": { "status": "enabled" },
      "secret_scanning_push_protection": { "status": "enabled" }
    }
  },
  "actions_workflow_permissions": { "default_workflow_permissions": "read", "can_approve_pull_request_reviews": true },
  "vulnerability_alerts": true,
  "automated_security_fixes": true,
  "labels": [
    { "name": "dependencies", "color": "0366d6", "description": "Dependency updates" },
    { "name": "needs-review", "color": "d93f0b", "description": "Not merged automatically; a person reviews it" },
    { "name": "screenshots",  "color": "c5def5", "description": "Screenshot refresh from nightly" }
  ]
}
```

- `{documentation}` is substituted from `ironclad/apps.json`. The field is left unchanged when that value is empty.
- `can_approve_pull_request_reviews: true` is required so `GITHUB_TOKEN` can open PRs (release-please, relock-upgrade, shots).

`ironclad/apps.json` (N4) is the fleet registry that `--all` iterates:

```json
{ "schema": 1, "apps": [
  { "repo": "Avunu/carbon_frappe", "app": "carbon_frappe", "target_app": "carbon_theme", "private": false, "list": true,
    "documentation": "https://carbon-theme.avunu.net/" }
] }
```

It holds one entry per app for all 12 apps. `target_app` is the post-rename package name, and a repo rename updates `repo`. `rename_mode` is `"rename"` (the default, `frappe-rename-app`) or `"replace"` (jailbreak → data_steward, §5.10). `private` gates `ironclad-apply --phase full` (§5.6).

### 4.10 frappe-nix's own settings (N6)

- `ironclad/self/repo-settings.json` is the same as §4.9, except `default_branch: "main"` and no labels.
- `ironclad/self/rulesets/main.json` is the `develop` ruleset with the ref `refs/heads/main` and the required contexts `lint` and `flake` (frappe-nix's own `check.yml` jobs) and `pr-policy`. None is a reusable-workflow job, so the names are bare.
- **`pr-policy`** lives in its own `.github/workflows/pr-policy.yml` (N6), not in `check.yml`, for the reason S4 gives: it runs on `pull_request` types `opened`, `edited`, `synchronize` and `reopened`, plus `workflow_dispatch`, and re-running `check.yml` on every retitle would rebuild `flake`. Its one job is named `pr-policy` and isn't reusable. It installs ironclad from the checkout (`uv tool install ./py/ironclad`), because frappe-nix's own `flake.lock` has no `frappe-nix` node, and runs `ironclad policy --pr --major-free` (no `Release-As` major rule; frappe-nix has its own semver, §6.3).
- **`check.yml`** gains a boolean `workflow_dispatch` input `vm-tests` (default `false`), and its `vm-tests` job runs only when `inputs.vm-tests == true`. frappe-nix's release flow dispatches `check.yml` on its release PR without it, so a release dispatch never starts the NixOS VM tests, which a hosted runner can't carry.
- `ironclad/self/rulesets/release.json` is `version.json` with the ref `refs/heads/release-*`.
- `ironclad/self/rulesets/tags.json` is `tags.json`.

The selftest workflows are not required checks, because they are path-filtered and expensive.

---

## 5. Tool CLIs

Every Nix-side tool is reachable in two ways:

- as a dev-shell command in app mode (`lib/scripts.d/*.nix` or `lib/ironclad/shell.nix`);
- as a flake app (`nix run github:Avunu/frappe-nix#<tool>`, or `.#<tool>` in an app, through `lib/ironclad/outputs.nix`).

The Python-side commands are `ironclad <cmd>`. The Nix wrappers are named `frappe-listing` (= `ironclad listing`) and `frappe-icon` (= `ironclad icon`, with `resvg` on PATH), and `ironclad-apply`, `ironclad-audit` and `ironclad-rollout` (with `gh` on PATH).

### 5.1 `frappe-test` (N1)

```
frappe-test [--app APP] [--site SITE] [--reuse-site] [--keep-up | --down] [--ci]
            [--module M] [--doctype DT] [--test T]
            [--no-coverage] [--no-testmap] [--no-composition] [--ty] [--nix-lint] [--shell-checks]
            [--junit PATH] [--out DIR]
```

**Environment.** The dev shell provides `FRAPPE_BENCH_ROOT`, `FRAPPE_SITE`, `DEVENV_ROOT` (the repo root) and `PC_SOCKET_PATH`. The script also reads:

- `FRAPPE_NIX_CI`;
- `GITHUB_STEP_SUMMARY`;
- `FRAPPE_TEST_ADMIN_PASSWORD` (default `admin`).

**Defaults.**

| Option | Default |
|---|---|
| `--app` | `frappe-nix.app.name` |
| `--site` | `$FRAPPE_SITE` |
| `--out` | `$DEVENV_ROOT/.dev-dist/test` |

`--ci` implies `--ty --nix-lint --shell-checks --junit $OUT/junit.xml`, plus the step summary and `ironclad-report.json`.

**Stages.** frappe-test runs these in order. Every stage after 3 runs even when an earlier one failed.

1. **Up.**
   - If no process-compose is listening on `$PC_SOCKET_PATH`: `DEVENV_IN_DIRENV_SHELL=true PC_TUI_ENABLED=0 devenv up -D`, with a trap that runs `process-compose -U -u "$PC_SOCKET_PATH" down` unless `--keep-up` is given.
   - Wait at most 300 s for `mariadb-admin --socket "$FRAPPE_DB_SOCKET" ping`, then for an HTTP answer on `127.0.0.1:$(jq .webserver_port sites/common_site_config.json)`.
   - Failure → exit 10.
2. **Site.**
   - Unless `--reuse-site` is given and the site exists: `printf '\n' | provision-site "$ADMIN_PW"`.
   - Then `bench --site S set-config allow_tests true`.
   - Then each `[tool.ironclad.test].setup` step, or the default (§2.1):
     - `module:X` → `bench --site S run-tests --module X`, with no coverage;
     - `execute:X` → `bench --site S execute X`.
   - Failure → exit 10.
3. **Tests with coverage.** `--no-coverage` drops the `coverage run` wrapper. From `$FRAPPE_BENCH_ROOT/sites`:
   ```
   "$FRAPPE_BENCH_ROOT/env/bin/python" -m coverage run \
       --rcfile="$DEVENV_ROOT/pyproject.toml" --data-file="$OUT/.coverage" \
       --source="$DEVENV_ROOT/$APP" \
       -m frappe.utils.bench_helper frappe --site "$SITE" run-tests --app "$APP" \
       [--module M] [--doctype DT] [--test T] [--junit-xml-output "$JUNIT"]
   ```
   frappe's `--coverage` MUST NOT be passed (S17). Test failure → stage verdict 1.
4. **Coverage gate.**
   - `coverage json -o $OUT/coverage.json`, `coverage xml -o $OUT/coverage.xml`, then `coverage report`. The report reads `fail_under` from `[tool.coverage.report]`, and coverage's exit status 2 maps to stage verdict 2.
   - A per-module table is written to the step summary.
   - **Upward ratchet (S24, decision 5).** If `fail_under < 80` and `total ≥ fail_under + 2.0`, the stage fails with verdict 2 and the message `coverage is <total>; raise [tool.coverage.report] fail_under to <min(80, floor(total) - 1)>`. After that raise, `total - fail_under < 2`, so the rule settles. The PR that adds the tests carries the raise.
   - The gate is skipped when any filter (`--module`, `--doctype`, `--test`) is given.
   - **Scoping.** coverage stores real paths. `--source` is the repo's package dir, so `.frappe-nix/bench/apps/*`, which holds real directories under the repo root but outside `<app>/`, is never measured.
   - **Acceptance.** No key of `coverage.json` `files` may contain `/.frappe-nix/`.
5. **Testmap** (§5.1.1) → verdict 3. Skipped with filters.
6. **Composition** (§5.1.2) → verdict 4.
7. **ty** (`--ty`). From the repo root: `uv run --frozen --project tools ty check --python "$FRAPPE_BENCH_ROOT/env" --output-format concise`. Records the diagnostic count and the `ty: ignore` count. → verdict 5.
8. **nix-lint** (`--nix-lint`). `nixfmt --check flake.nix $(git ls-files 'nix/*.nix')`, `statix check .` and `deadnix --fail --exclude .frappe-nix .`. The binaries are pinned through frappe-nix's nixpkgs and added to the app-mode shell by N1. → verdict 6.
   - **8b. Shell checks** (`--shell-checks`). For each `[tool.ironclad] shell-checks` command, in order, from the repo root: run it (`bash -c`), then `git status --porcelain` must be empty, so a generator whose output is committed (timeclock's pydantic2ts `generate-types`, carbon's `codegen`) proves its output fresh. Each failure is listed with the diff. → verdict 7. Node-based checks here may need node_modules, which CI mode doesn't install; such a command installs them itself (`yarn install --frozen-lockfile &&` …), or the check moves to `ci:typecheck`, which runs in the no-Nix typecheck job with node_modules and `FRAPPE_PATH`.
9. **Report.** Write `$OUT/ironclad-report.json` (below) and `$OUT/summary.md`, and append the summary to `$GITHUB_STEP_SUMMARY`.

**Exit status** is the verdict of the **first failing stage**, in the order 1 (tests), 2, 3, 4, 5, 6, 7. It is 10 for an environment failure in stage 1 or 2, and 0 otherwise.

`ironclad-report.json`:

```json
{ "schema": 1, "app": "carbon_frappe", "frappe_nix": {"rev": "…", "version": "1.0.0"}, "git_sha": "…",
  "tests": {"ran": 39, "failures": 0, "errors": 0, "skipped": 0},
  "coverage": {"percent": 63.2, "fail_under": 60.0, "modules": {"carbon_frappe/api.py": 81.0}},
  "testmap": {"targets": 14, "untested": [], "exempt": 1, "stale_exemptions": []},
  "composition": {"ok": true, "doctypes": {"ToDo": {"layers": ["…"], "app_first_ok": true}}},
  "ty": {"diagnostics": 0, "ignores": 3}, "nix_lint": "ok", "exit": 0 }
```

`--down` stops a bench that `--keep-up` left running, and exits.

#### 5.1.1 Testmap: the whitelist and hook coverage algorithm (N1, `ironclad testmap`)

frappe-test invokes it with the bench interpreter, so the evaluated hooks are visible:

```
$FRAPPE_BENCH_ROOT/env/bin/python <store>/ironclad/bench/testmap_probe.py --site S --app A \
    --coverage-json $OUT/coverage.json --pyproject $DEVENV_ROOT/pyproject.toml --out $OUT/testmap.json
```

The probe runs `frappe.init(site)` and `frappe.connect()` (the site exists by stage 5), then `hooks = frappe.get_hooks(app_name=A)`. Because the hooks are evaluated, conditional hooks are included. Connecting matters because some modules query the database when imported (timeclock's `utilities.py` calls `frappe.get_all` at module level). A target whose module fails to import is reported as untested, with the exception text in its `error` field; the probe itself never crashes on it.

**Targets.** Each target is a dotted path to a Python function or method:

- **T1:** every `FunctionDef` or `AsyncFunctionDef` under `<app>/`, excluding `**/tests/**`, `**/test_*.py` and `**/patches/**`, that is decorated with `frappe.whitelist`, `frappe.whitelist(...)`, `whitelist` or `whitelist(...)` (where `whitelist` is imported from `frappe`). Methods are named `module.Class.method`.
- **T2:** every string in `hooks["scheduler_events"]`, for all keys including `cron`, that starts with `A.`.
- **T3:** every handler in `hooks["doc_events"][*][*]`, string or list, that starts with `A.`.
- **T4:** for each `extend_doctype_class` or `override_doctype_class` value that starts with `A.`: import the class, and take every function defined in its own `__dict__`. Dunders are skipped except `__init__`, and names starting with `_` are skipped.
- **T5:** values of `override_whitelisted_methods`, `permission_query_conditions` and `has_permission` that start with `A.`.
- **T6:** every string, or string in a list, that starts with `A.` under these callable-valued hooks: `auth_hooks`, `before_request`, `after_request`, `on_session_creation`, `on_login`, `on_logout`, `boot_session`, `jinja.methods`, `jinja.filters`, `additional_timeline_content` (every list in the dict), `website_context` values that are dotted callables or `/api/method/A.…` URLs (the path after `/api/method/`), `after_install`, `after_sync`, `after_migrate`, `before_uninstall`, `before_tests`. This covers jwt_auth's `validate_auth`, `handle_redirects` and `on_logout`, carbon's `after_request` injector, and esign's jinja and timeline methods. The install and migrate kinds (`after_install`, `after_sync`, `after_migrate`, `before_uninstall`) run before coverage starts, so a test must call them directly or they need a `[[tool.ironclad.untested]]` entry with a reason.

**Body lines.** Resolve each target, `inspect.unwrap` it, and call `inspect.getsourcelines`. The body lines are the executable lines after the `def` line, its decorators and its docstring, taken from coverage's analysis of that file (`coverage.json` `files[realpath].executed_lines` ∪ `missing_lines`).

**Tested** means at least one body line is in `executed_lines`.

**Exemptions.** A target listed in `[[tool.ironclad.untested]]` is exempt. An exemption whose target is now tested, or is no longer a target, is **stale** and counts as a failure: the list only shrinks.

**Output.** `testmap.json` holds `{targets:[{path, kind, file, line, tested, exempt, error?}], untested:[…], stale_exemptions:[…]}`, and a markdown table goes to the summary. The verdict is 3 when `untested` or `stale_exemptions` is non-empty.

#### 5.1.2 Composition check (A.3)

`<store>/ironclad/bench/composition.py --site S --app A` connects to the site. Then:

1. Collect every doctype that A names in `extend_doctype_class` or `override_doctype_class`, read from the merged hooks of all installed apps.
2. For each, after clearing frappe's controller cache (`frappe.controllers = {}` plus `frappe.clear_cache()`), call `frappe.get_controller(dt)`. Assert that no `TypeError` is raised and that A's class is in `__mro__`.
3. Repeat with `frappe.get_installed_apps` monkeypatched to return the real order with A moved directly after `frappe`. This is the A.0 #4 failure mode.

The verdict is 4 on any failure, and the message names the doctype and the order.

### 5.2 `frappe-listing` (N5; `ironclad listing`)

```
frappe-listing check    [--release --tag vX.Y.Z] [--links-only] [--no-getapp] [--format text|json|github]
frappe-listing registry [--tag vX.Y.Z | --ref SHA] [--branch version-16] [--onboard] [--refresh]
                        [--fork Avunu/marketplace] [--upstream frappe/marketplace] [--dry-run]
frappe-listing readme   --write | --check
```

**Pins.**

- The marketplace and pilot revisions are the app's `flake.lock` nodes reached as `nodes[nodes["frappe-nix"].inputs.marketplace]` and `….pilot`. They come from frappe-nix's flake inputs (S20).
- In Nix they are store paths. In no-Nix CI, `ironclad` fetches `https://codeload.github.com/frappe/<repo>/tar.gz/<rev>` into `.dev-dist/pins/<repo>-<rev>/` and verifies the tree against the lock's `narHash`, using `nix-hash` semantics implemented in Python (`ironclad.common.nar`).
- If the hash doesn't match, exit 3.

**`check` rules.** These rules apply whatever `listing.toml` says. L1, L2 and L8 apply only when `listing.toml` exists.

| Id | Rule | Severity |
|---|---|---|
| L1 | `listing.toml` passes its schema (§2.21): the enums, the tagline at 40–80 characters with no trailing `.`, the title at ≤ 40 characters, a non-empty `categories`, and `https` URLs | error |
| L2 | hooks.py, read by AST at top level, with any conditional assignments reported as warnings: `app_name == <app>`, `app_title == title`, `app_description == tagline`, `app_publisher == "Avunu LLC"`, `app_email == "mail@avu.nu"`, `app_license == "MIT"`. No `app_logo_url`. No `app_icon == "octicon octicon-file-directory"` and no `app_color == "grey"`. `add_to_apps_screen` present iff `apps_screen`, and if present its logo is `/assets/<app>/images/<app>-logo.svg`, with the route and `has_permission` taken from `[apps_screen_entry]`. | error |
| L3 | `[tool.bench.frappe-dependencies]`: the keys are exactly `{frappe} ∪ required_apps (bare)`; each value equals the known-apps range; each parses with `packaging.SpecifierSet`; the lower bound's major equals `frappe-major`, except for payments; `frappe` isn't in `required_apps`; `[project.dependencies]` names none of frappe, erpnext, hrms or payments | error |
| L4 | The version block is in block form. `__version__` = `package.json` = manifest. With `--release`: `__version__ == tag.lstrip("v")`, and the tag's commit is an ancestor of `origin/<branch>` | error |
| L5 | `__init__.py` has no side effects (§2.13). No tracked symlink points outside the repo. | error |
| L6 | Every `override_doctype_class` key in hooks has a `[[tool.ironclad.override-doctype-class]]` entry, and every entry has a hook | error |
| L7 | **Registry semgrep.** Import `validation/semgrep_check.py` from the pinned marketplace tree (`SemgrepValidator(repo_root, app)`, run as registry CI runs it, with `EIO_BACKEND=posix`). Each blocking finding is keyed `(rule, path, sha1(stripped first matched line))` (S21), and findings are counted per key. For every key, the number found must equal the baseline entry's `count` (absent = 0): more is a new finding, fewer means the count must be lowered (stale). Either is an error, so the baseline only shrinks. With `--release`, the baseline MUST be empty. Advisory findings go into the report only. | error |
| L8 | Logo structure: `ironclad icon check --structural` (§5.3) | error |
| L9 | **pilot get-app.** Build a temporary validation bench with `pilot.core.bench.Bench`. Python is `>=3.14,<3.15` from uv. `apps/frappe` and each dependency app are source trees at the app's `flake.lock` revisions, fetched like the pins; this mirrors marketplace#29. Install the app, and run every check in `pilot.core.app.validator.validator._all_checks()` individually, as the Phase 0 registry-dryrun prototype (`scripts/run_sim.py`) does. Building frappe's `mysqlclient` needs `pkg-config` and the MySQL client headers on the host; the `marketplace` job installs them (§4.2), and in Nix they come from the dev shell. Every check must pass. `--no-getapp` skips this rule, for local use only. | error |
| L10 | With `--links-only` or `--release`: `website` and `documentation` return 200 over https after redirects, using GET with a 20 s timeout and 2 retries | error |
| L11 | With `--release`: every `Avunu/*` entry in `required_apps` appears in the upstream `apps.json` on `main` | error |
| L12 | `website` and `documentation` follow the expected patterns (§2.21) | warning |

**`check` exit codes:** 0 pass (warnings allowed); 1 any error; 2 a config or parse error in `listing.toml` or `pyproject.toml`; 3 an environment problem (network, a pin hash mismatch, the pilot bench couldn't be built).

The JSON report goes to `.dev-dist/marketplace/report.json`.

**`registry`.** The token is `GH_TOKEN`: in CI that is `REGISTRY_TOKEN`, and locally it is `gh auth token`, which under decision 15 is the user's own account.

1. Resolve the commit to its full 40-character SHA, and check that it is reachable from `origin/<branch>`.
2. Run `check --release --tag`. Abort on failure.
3. Clone the upstream `main` shallowly into a temp dir, and create the branch `avunu/<app>` from it, so it is always up to date with `main`.
4. Collect the pending entries: the new entry, plus every entry in the fork branch's `apps/<app>.json` whose `commit` isn't in upstream's file.
5. For each pending entry, run the pinned `tools/add_release.py`, with `APP`, `BRANCH`, `COMMIT` and `CHANNEL=stable` set, `--app-dir` as a `git worktree` of that commit, and `--registry` as the clone.
6. With `--onboard`, or when the app isn't in upstream `apps.json`, append the index entry to `apps.json` and create `apps/<app>.json` with `{"name": app, "releases": []}` before step 5. The index entry is `{name, title, description: tagline, repo: "https://github.com/<repo>", logo_url: "https://raw.githubusercontent.com/<repo>/<branch>/<app>/public/images/<app>-logo.svg", website, documentation, category, categories, stars: <registry.stars or 0>, releases: "apps/<app>.json"}`. It keeps the file's existing indentation.
7. Commit with the message `<app>: <version>`, or `<app>: onboard and <version>`. Force-push to `<fork>:avunu/<app>`.
8. Run `gh pr create --repo <upstream> --base main --head <fork-owner>:avunu/<app>`, or `gh pr edit` when one is open. The title is `<app>: <newest version>`, and the body is generated: the pending versions, the local `check` summary, and the semgrep baseline state.
9. Print the PR URL. `--refresh` does steps 3–8 with no new entry, and only when the fork branch is behind upstream `main`. `--dry-run` stops before the push and prints the diff.

**Exit codes:** 0 PR opened or updated (or nothing to do); 1 the gate failed; 3 a git, gh or network error.

**`readme`.** It renders the §2.19 blocks from `ironclad/data/readme/*.md.j2`. Its exit codes match `sync`'s (0, 1 for drift, 2 for a missing marker).

### 5.3 `frappe-icon` (N5; `ironclad icon`, with `resvg` from nixpkgs)

```
frappe-icon check [--structural] [--format …]     # --structural: no rasterisation (no-Nix CI)
frappe-icon tile                                   # <app>-symbolic.svg → <app>-logo.svg (deterministic)
frappe-icon build [--out .dev-dist/icons] [--write-fixture]
```

**Inputs.** `<app>/public/images/<app>-symbolic.svg`, and the generated, committed `<app>-logo.svg`.

**Structural checks** (pure Python, `xml.etree` plus a path-bbox parser):

- symbolic:
  - `viewBox="0 0 32 32"`;
  - only `<svg>`, `<g>`, `<path>`, `<circle>`, `<rect>`, `<ellipse>`, `<polygon>`, `<polyline>` and `<title>`;
  - no `<text>`, `<image>`, `<style>`, `class`, gradients, filters, masks or clip paths;
  - every fill is `currentColor` or `none`;
  - no stroke, so outlines must be converted to paths;
  - the union bbox lies within `[2, 30]` on both axes (safe area).
- tile:
  - `viewBox="0 0 1024 1024"`;
  - the first child is a `<rect width=1024 height=1024 rx=ry=293>` (28.6%, ±1) with `fill="#834AFF"`;
  - the glyph group is all `#FFFFFF`, centred to within ±4 units;
  - the glyph bbox's longer side is 573 (56%, ±12);
  - it equals `frappe-icon tile`'s output byte for byte (freshness).

**Raster checks** (resvg, Nix only). They run under `frappe-icon check` without `--structural`: in the dev shell, in `selftest-product`, and in the nightly `links` job through `nix run --no-pure-eval .#frappe-icon -- check`, so `resvg` is the one frappe-nix's locked nixpkgs provides. They are not part of a required check.

- At 16, 32 and 256 px, render the symbolic icon black on transparent.
- The alpha coverage at 16 and 32 px is within [0.10, 0.65].
- The number of 8-connected components at 32 px equals the number at 256 px, so no feature merges or disappears at the marketplace-card size.

**Build outputs** go to `.dev-dist/icons/` and are not committed: `logo-512.png`, `favicon-32.png` and `favicon-16.png`. When `app_type == "application"` and `--write-fixture` is given, it writes `<app>/desktop_icon/<app>.json` (S22):

```json
{ "app": "<app>", "doctype": "Desktop Icon", "name": "<title>", "label": "<title>", "icon_type": "App",
  "link_type": "External", "link": "<apps_screen_entry.route>", "logo_url": "/assets/<app>/images/<app>-logo.svg",
  "hidden": 0, "standard": 1, "idx": 100, "roles": [], "docstatus": 0, "owner": "Administrator",
  "modified_by": "Administrator", "creation": "<kept or first-write time>", "modified": "<kept unless content changed>" }
```

`check` compares the fixture ignoring `creation` and `modified`.

**Exit codes:** 0 pass; 1 a rule failed (each failure listed); 2 a file is missing or unparsable.

### 5.4 `frappe-demo` (N5; dev-shell script)

```
frappe-demo [--site S] [--fresh] [--erpnext-demo | --no-erpnext-demo] [--date YYYY-MM-DD] [--seed N] [--no-up]
```

The defaults come from `marketplace/screenshots.ts`'s `demo` block when it exists, otherwise:

| Option | Default |
|---|---|
| date | `2026-01-15` |
| seed | `1` |
| ERPNext demo data | off |

1. Bring the bench up as frappe-test stage 1 does, unless `--no-up`. With `--fresh`, or when the site is missing, run `provision-site`.
2. Run the setup wizard with fixed arguments:
   ```
   language English, country United States, currency USD, timezone America/New_York,
   company_name "Avunu Demo", company_abbr "AD", fy_start_date <year>-01-01, fy_end_date <year>-12-31,
   chart_of_accounts Standard, full_name "Demo User", email demo@example.com, password <admin pw>
   ```
   The company and fiscal-year keys are passed only when erpnext is installed.
3. When ERPNext demo data is on and erpnext is installed: `erpnext.setup.demo.setup_demo_data()`.
4. If `<app>.demo` is importable, call `setup(ctx)`, where

   ```python
   class DemoContext(TypedDict):
       today: datetime.date        # the --date value; never date.today()
       seed: int
       company: str | None         # "Avunu Demo" when erpnext is installed
       erpnext_demo: bool
   ```

   **The hook contract:**
   - it runs as Administrator;
   - it MUST be idempotent: a second call creates nothing new (named records, or `ignore_if_duplicate`);
   - it MUST NOT reach external services; mock clients instead;
   - it MUST derive every date from `ctx["today"]`;
   - it MUST NOT commit. frappe-demo commits once at the end.
5. Write `sites/<site>/demo.json` with `{date, seed, apps: {name: version}, erpnext_demo}`.

When frappe-shots invokes it, the whole bench runs under libfaketime (§5.5).

**Exit codes:** 0 ok; 1 the hook raised; 10 an environment error.

### 5.5 `frappe-shots` (N5)

```
frappe-shots [--spec marketplace/screenshots.ts] [--only a,b] [--theme light|dark]
             [--update | --check] [--reuse-site] [--out docs/screenshots] [--video <name>]
```

The default is `--update`. Nightly uses `--update`, then checks `git status`. `--check` writes diffs only and exits 1 if anything differs.

**The engine** is `lib/shots/`:

- `cdp.ts` is carbon_frappe's zero-dependency CDP driver, moved here.
- `runner.ts`, `diff.ts` and `shots.d.ts` are new.
- It runs under Node 24 with type stripping.
- `pixelmatch` and `pngjs` come from `lib/shots/yarn.lock`, built as a Nix node_modules.
- `cwebp`, `dwebp` (libwebp), `chromium`, `ffmpeg` and libfaketime come from nixpkgs.
- Fonts come from a fixed `FONTCONFIG_FILE`: the Inter, IBM Plex and Noto sets from nixpkgs.

**`ShotSpec`.** This is `lib/shots/shots.d.ts`, copied to `marketplace/shots.d.ts`:

```ts
export type Theme = "light" | "dark";
export interface Viewport { width: number; height: number; deviceScaleFactor?: number }
export type Action =
	| { click: string } | { hover: string } | { press: string }
	| { type: { selector: string; text: string } } | { scroll: { selector?: string; y: number } }
	| { wait: number } | { waitFor: string } | { eval: string };
export interface Shot {
	name: string;                 // /^[a-z0-9-]+$/, unique
	route: string;                // starts with "/"
	alt: string;                  // required; used by the README and avunu.net
	waitFor?: string;             // CSS selector
	waitForFn?: string;           // JS expression, truthy when ready
	actions?: Action[];
	clip?: string;                // selector; capture its bounding box
	fullPage?: boolean;
	mask?: string[];              // painted #8C8C8C
	hide?: string[];              // visibility: hidden
	themes?: Theme[];
	viewport?: Viewport;
	featured?: boolean;           // avunu.net shows featured shots
	readme?: "hero" | "feature";  // at most one "hero"
	threshold?: number;           // pixelmatch per-pixel threshold, default 0.1
	maxDiffRatio?: number;        // allowed share of differing pixels, default 0.001
}
export interface VideoFlow { name: string; route: string; steps: Action[]; seconds?: number }
export interface ShotSpec {
	viewport?: Viewport;          // default { width: 1440, height: 900, deviceScaleFactor: 2 }
	themes?: Theme[];             // default ["light", "dark"]
	timezone?: string;            // default "America/New_York"
	locale?: string;              // default "en-US"
	user?: string;                // default "Administrator"
	demo?: { erpnextDemo?: boolean; date?: string; seed?: number };
	css?: string;
	shots: Shot[];
	videos?: VideoFlow[];
}
```

**How a run works.**

1. Unless `--reuse-site`: export `LD_PRELOAD=<libfaketime>` and `FAKETIME="@<demo.date> 09:00:00"` (a start time; the clock advances) into the environment that `devenv up` is started from, so every bench process (web, workers, MariaDB) sees the same day. Then run `frappe-demo --fresh` with the spec's demo options. Then `bench build`. Shots therefore run without `FRAPPE_NIX_CI`.
2. Launch chromium (`cdp.launch`) with `--headless=new --hide-scrollbars --force-color-profile=srgb --disable-lcd-text --font-render-hinting=none --disable-gpu`. Log in through the API.
3. For each theme and each shot:
   - set the user's `desk_theme` (`Light` or `Dark`) through `frappe.client.set_value`, and use `Emulation.setEmulatedMedia` for `prefers-color-scheme`;
   - set `Emulation.setTimezoneOverride`, `Emulation.setLocaleOverride`, and the viewport and DPR;
   - navigate;
   - inject CSS that turns off animations, transitions and the caret (`*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}`), plus `spec.css`;
   - wait for `document.fonts.ready`, then network idle (no request in flight for 500 ms), then `waitFor` or `waitForFn` (timeout 30 s → exit 4);
   - run the actions, apply `hide` and `mask`;
   - capture `clip`, `fullPage` or the viewport as PNG.
4. Write the master to `.dev-dist/shots/<name>-<theme>.png`. Decode the committed `docs/screenshots/<name>-<theme>.webp` with `dwebp` and run `pixelmatch(threshold)`. If the differing ratio is above `maxDiffRatio`: in update mode, write `cwebp -lossless -exact -z 9`; in check mode, record the diff PNG under `.dev-dist/shots/diff/`.
5. Write `docs/screenshots/manifest.json` (committed): `[{name, theme, alt, featured, readme, width, height, sha256}]`, sorted. The README and avunu.net read it.

**Exit codes:** 0 no differences, or updated; 1 differences in `--check`; 2 a spec error (validation against the types runs at runtime); 3 environment; 4 a capture error (navigation, timeout or JS error).

With `--video <name>`, it records the flow with `Page.startScreencast` and encodes `.dev-dist/shots/<name>.mp4` with ffmpeg. Never committed.

### 5.6 `ironclad-apply` (N4)

```
ironclad-apply <owner/repo>… | --all  [--phase provisional|full] [--self] [--dry-run] [--settings-only | --rulesets-only]
```

It needs `gh` authenticated with `repo` scope, and admin on the target. It never needs `admin:org`.

1. Read `ironclad/repo-settings.json`, `ironclad/rulesets/*.json` and `ironclad/apps.json`. With `--self`, it reads `ironclad/self/*` and targets Avunu/frappe-nix.
2. `PATCH /repos/{r}` with the settings, after substituting `{documentation}`.
3. `PUT /repos/{r}/actions/permissions/workflow`.
4. `PUT vulnerability-alerts`, and `PUT automated-security-fixes`.
5. Create or update each label.
6. Rulesets, matched by `name`: `GET /repos/{r}/rulesets`, then for each wanted ruleset `PUT …/rulesets/{id}` if it differs, or `POST` if it is missing.
   - provisional = `develop-provisional.json`;
   - full = `develop.json` + `version.json` + `tags.json`.
   - Unknown rulesets are listed, never deleted.
7. **Private repos.** `--phase full` requires `private: false` in `ironclad/apps.json` and refuses otherwise (exit 1 for that repo, naming §7 decision 2): rulesets return 403 on a private Free-plan repo, so the required checks and the `version-*` protection would silently be missing, and the scheduled callers would spend private Actions minutes (about 2,000 a month for the crons alone). `--phase provisional` on a private repo applies the settings and labels only, with a warning. timeclock goes public before its PR A.

`--dry-run` prints a JSON diff per repo and changes nothing.

**Exit codes:** 0 applied or already matching; 1 some repo failed (the rest continue); 2 config error.

### 5.7 `ironclad-audit` (N4): the §6 scorecard, mapped to checks

```
ironclad-audit [--repo R… | --all] [--format md|json] [--out PATH] [--issue] [--against pinned|latest]
```

For each repo, the audit makes a shallow clone of develop, installs ironclad at the repo's `flake.lock` rev into a temp venv (or the latest release's, with `--against latest`), and evaluates:

| Row | §6 item | Concrete check | Data source |
|---|---|---|---|
| A1 | default branch `develop` | `repo.default_branch == "develop"` | REST |
| A2 | `version-<major>` exists and equals the newest `v<major>.*` tag | ref SHA == the peeled tag SHA | REST |
| A3 | settings and rulesets match `ironclad/` | Field-by-field diff of the settings; normalised ruleset diff, by name (private repo → n/a, per decision 2) | REST |
| A4 | managed files present and `--check` clean at the pinned version | `ironclad sync --check --format json` → `status == clean` | clone |
| A5 | the five required checks are green on develop HEAD, and nightly is green | The latest completed `ci.yml` run on develop (push or schedule) concluded `success` with all four gate jobs successful; the latest `pr-policy` on any merged PR is green; the latest `nightly.yml` run concluded `success` | REST runs |
| A6 | ty: 0 diagnostics, and the ignore count ≤ baseline | The `test-report` artifact of the run in A5 → `ty.diagnostics == 0`. The ignore count isn't higher than in the report 30 days earlier, and it is enforced per PR by the ratchet. | artifact |
| A7 | tsc clean on every project; no hand-written frappe globals | `typecheck` job success (A5), plus `ironclad compat` C7 (redeclarations only; augmentations confined to `types/<app>.augment.d.ts`). The augment file's member count is reported. Amber while `[[tool.ironclad.unchecked-js]]` is non-empty. | clone |
| A8 | coverage ≥ minimum, rising towards 80; testmap passing | report: `coverage.percent ≥ fail_under` and `testmap.untested == []`. Amber when `fail_under < 80` or `[[tool.ironclad.coverage-omit]]` is non-empty. | artifact |
| A9 | no legacy tooling | No file matches the `retire` list (§2.4.1), read from the same `sync --check --format json` as A4 so the lists can't diverge; no tracked `**/public/dist/**`; no `pyupgrade` or `codesorter` in the pre-commit config; ruff and ssort managed (A4). Amber when `tool.ruff.extend-exclude` is non-empty. | clone |
| A10 | dependabot: every ecosystem configured; the last nix bump merged automatically with relock; frappe-nix current | A4 covers the config. The newest merged PR with head `dependabot/nix/*` (frappe and siblings): merged through auto-merge, and it contains a commit starting `chore(deps): relock and sync` unless its relock was empty. The pinned frappe-nix is the latest `v1.*` release, or one released less than 14 days ago; otherwise amber, naming the `ironclad-rollout` command (S5). | REST |
| A11 | at least one release cut by release-please | A `v<major>.*` tag exists, and its GitHub release was authored by `github-actions[bot]` | REST |
| A12 | README follows D15 and its blocks are in sync | `ironclad listing readme --check` == 0, and the README contains the S26 link | clone |
| A13 | icons pass; hooks wired by type; no `app_logo_url` or scaffold `app_icon` | `ironclad icon check --structural` == 0, and L2 | clone |
| A14 | `frappe-listing check` clean; semgrep baseline empty; links 200 | `ironclad listing check --links-only` and `check` (without `--no-getapp`) == 0, and the baseline's `findings == []` | clone |
| A15 | screenshot spec runs in CI; committed shots match the latest nightly | The latest nightly `shots` job succeeded, and there is no open `screenshots` PR older than 7 days | REST |
| A16 | registry index merged; newest registry release == latest tag | The upstream `apps.json` on `main` has the app, and `apps/<app>.json` `releases[0].version == latest tag` | raw.githubusercontent |
| A17 | docs site (docs-and-urls track) | `GET /repos/{r}/pages`: `cname` set and `https_enforced: true`; `DOCS_SITE_ENABLED == "true"` | REST |

- Every row is `green`, `amber`, `red`, `n/a` or `unknown`. `unknown` means the data couldn't be read, for example because the token lacks admin. Rows A14–A16 are `n/a` for an app with `list: false`.
- `--issue` opens or updates one issue per app in Avunu/frappe-nix titled `scorecard: <repo>`.
- The org-wide nightly is `.github/workflows/ironclad-audit.yml` in frappe-nix. It uses `secrets.IRONCLAD_AUDIT_TOKEN`, a read-only fine-grained PAT with Administration and Contents read. Without that token, the settings and ruleset rows are `unknown`.

**Exit codes:** 0 everything green or n/a; 1 any amber or red; 2 error.

### 5.8 `ironclad-rollout` (N4)

```
ironclad-rollout --to vX.Y.Z [--repo R… | --all] [--no-auto-merge] [--dry-run]
```

This is **the** path by which a frappe-nix release reaches an app (S5). It runs locally, needs Nix, and uses the user's `gh` token, which must have the `workflow` scope (`gh auth refresh -s workflow`), because the commit rewrites the caller workflows' `uses:` SHAs. For each repo, in a temp clone of develop:

```
nix flake update frappe-nix && nix run --no-pure-eval .#relock && nix run --no-pure-eval .#frappe-init -- --sync
```

`frappe-init --sync` runs from the newly locked frappe-nix (phase A re-execs if needed, §3.3). Rollout then commits `chore(deps): frappe-nix vX.Y.Z`, pushes `ironclad/frappe-nix-vX.Y.Z`, opens the PR with the user's token, and, unless `--no-auto-merge`, runs `gh pr merge --auto --squash`. A user-token push triggers CI normally, and the user-token merge raises a normal push event. If a stricter rule in the new version fails the app, the PR waits for a fix pushed to its branch.

The main agent runs `ironclad-rollout --to vX.Y.Z --all` after each frappe-nix release; the nightly `drift` issue (§4.6) and audit row A10 are the reminders.

### 5.9 `ironclad policy`, `ironclad ratchet`, `ironclad compat`, `ironclad minibench`

**`ironclad policy --commit-msg-file F`** is the commit-msg hook. It rejects:

- a subject matching `^[a-z]+(\([^)]*\))?!:`;
- a `BREAKING[ -]CHANGE:` footer;
- a `Release-As: X.y.z` whose major isn't `frappe-major`.

**`ironclad policy --pr`** runs in CI. When `EVENT_NAME == workflow_dispatch`, it finds the PR with `gh pr list --head $REF_NAME --state open --json number,title,baseRefName,headRefOid` and asserts that `headRefOid == HEAD_SHA`. It then takes the title and base from that result instead of `PR_TITLE` and `BASE_REF`, which are empty on a dispatch; if either is missing from the result, it fails rather than check an empty value. If there is no PR, it passes, for example on a branch dispatched by the sweep after the PR closed. It then checks:

1. `base == develop`. The error message is `retarget: gh pr edit <n> --base develop`.
2. The title passes `committed --commit-file <(title)` and the subject rules above.
3. Every commit in `git rev-list --no-merges base..head` passes `committed <sha>` and the rules above. This includes the `Release-As` major rule.
4. On a `release-please--*` branch: `ironclad compat`. C1 then requires the proposed `package.json` version's major to equal `frappe-major`.

Exit 0 or 1. `--major-free` (frappe-nix itself, §4.10) drops the `Release-As` major rule and check 4.

**`ironclad compat`** is the prek hook (C1–C9):

| Id | Rule |
|---|---|
| C1 | The `package.json` version's major is ≤ `frappe-major`. It MUST equal `frappe-major` once any `v<major>.*` tag exists, or on a `release-please--*` branch. |
| C2 | `package.json` `frappe` = `{major, branch}`. |
| C3 | `flake.nix` `frappeVersion` and the `flake.lock` `frappe` and siblings' `original.ref` are `version-<major>`. |
| C4 | The L3 range rules (shared code). |
| C5 | The version block holds, and `__version__` = `package.json` = manifest. |
| C6 | `hooks.required_apps` ⊆ `siblings`; `frappe` isn't in `required_apps`. |
| C7 | No hand-written frappe globals (§2.9): no `declare (var\|let\|const) frappe`, no `interface Window {` with a `frappe` member, no `declare namespace frappe`, and `namespace frappe` augmentations only inside `declare global` in `types/<app>.augment.d.ts`, each member preceded by `// app-owned: <reason>`. Also: `.git-blame-ignore-revs` is valid (§2.20). |
| C8 | When `discover.vite`: `scripts.build` ends with `node scripts/ironclad-vite-register.mjs` (S30). |
| C9 | Every `[[tool.ironclad.unchecked-js]]` path and every `[[tool.ironclad.coverage-omit]]` glob matches at least one tracked file; reasons are ≥ 10 characters. |

Exit 0 or 1, or 2 when a file can't be parsed.

**`ironclad ratchet --base <sha>`** applies only if the base's `pyproject.toml` has `[tool.ironclad]` (S24).

| Id | Rule |
|---|---|
| R1 | `fail_under(head) ≥ fail_under(base)` |
| R2 | The count of `ty: ignore` comments in head ≤ the count in base. Every `ty: ignore` matches `#\s*ty:\s*ignore\[[a-z0-9-]+(,\s*[a-z0-9-]+)*\]\s+#\s+\S.{9,}`, so it names a rule and gives a reason of at least 10 characters. |
| R3 | For every semgrep baseline key, `count(head) ≤ count(base)`, and head has no key that base lacks |
| R4 | The `[[tool.ironclad.untested]]` targets in head ⊆ those in base |
| R6 | The `[[tool.ironclad.unchecked-js]]` paths in head ⊆ those in base (a renamed file counts as new) |
| R7 | The `[[tool.ironclad.coverage-omit]]` globs in head ⊆ those in base |
| R5 | Every `nosemgrep` comment names a rule (`# nosemgrep: <rule-id>`), and the same or the previous line carries a justification comment. A format rule, not a count. |

Exit 0 or 1, listing each violation.

**`ironclad minibench --mode customizations|doctypes --dest D`** builds a fake bench from the app's `flake.lock`. For frappe and each sibling: `git clone --filter=blob:none --no-checkout --sparse https://github.com/<repo>`, check out the locked rev, and set the sparse patterns (non-cone): `/*/modules.txt`, `/*/hooks.py`, `/pyproject.toml`, `/*/*/doctype/**/*.json`, `/*/*/custom/*.json`, `/*/fixtures/*.json`. Then:

- write `D/sites/apps.txt` and `D/sites/apps.json`, listing frappe, the siblings and the app;
- `mkdir D/env`;
- in `customizations` mode, `git worktree add --detach D/apps/<app> HEAD`; in `doctypes` mode, a sparse copy of the app's `/<app>/*/doctype/**/*.json`, `/<app>/*/custom/*.json` and `/<app>/fixtures/*.json`, so gen-doctypes sees the app's Custom Fields on core doctypes (postgrid's address and notification, jailbreak's version, automated_subscriptions' sales_invoice, timeclock's fixtures) and merges them into `FrappeDocTypes`.

The guard: exit 1 unless `D/sites`, `D/env` and `D/apps/frappe/frappe/modules.txt` exist. In customizations mode, the caller then runs the manual-stage hooks from `D/apps/<app>`, and `--show-diff-on-failure` reports what they rewrote.

### 5.10 `frappe-rename-app` (N1; dev-shell script and NixOS step)

This follows the rename-mechanics track. The **code half** is for PR 0, and the **site half** runs before migrate.

**Code half:**

```
frappe-rename-app code --from OLD --to NEW [--dry-run]
```

0. Refuse (exit 1) when OLD is an `ironclad/apps.json` entry with `"rename_mode": "replace"` (jailbreak → data_steward), which goes through the replace path below instead.
1. `git mv OLD NEW`. Every module folder and every `modules.txt` line stays the same. Then `git mv` every tracked file under `NEW/` whose basename starts with `OLD.` to the same name with `NEW.` (esign's `esign.desk.bundle.js` and `esign.control.bundle.js`, timeclock's `timeclock.app.bundle.js`, their `.css`/`.scss` twins), so the bundle names in hooks and the files agree.
2. Rewrite the tracked text files with the boundary regex `(?<![\w./])OLD(?=[.\/])`, covering imports, dotted paths in `hooks.py`, `patches.txt` (line by line), JS method strings, `/assets/OLD/` and `OLD/templates/…`. A match inside a string that is a bare filename (`OLD.<rest>` with no `/` and no further dotted module segment that resolves) is rewritten only if step 1 renamed a file with that basename; otherwise it is left alone and listed in the report. Also rewrite `pyproject` `[project].name`, `package.json` `name`, `app_name`, flit's include, the CI `--app` and `release-please` `package-name`.
3. Bump `modified` in every standard JSON whose content changed (`import_file.py:150`).
4. Append an `override_whitelisted_methods` shim, mapping each old whitelisted dotted path to its new one, between `# ironclad:rename-shim-begin OLD` and `# ironclad:rename-shim-end` in `hooks.py`. Keep it for at least one minor release.
5. Print what it deliberately left alone: custom fieldnames, DocType names, Communication types, CSS classes, localStorage keys, and module names. Module names are kept (Q5).

Exit 0, or 1 when the tree is dirty or the pair is a replace.

**Replace path (jailbreak → data_steward, A.2).** data_steward is a new app with its own module (`Data Steward`) and doctypes (`Data Steward Settings`), so it never collides with jailbreak's `Jailbreak` Module Def or `Jailbreak Settings` while both are installed; Q5's keep-the-module-names rule doesn't apply to it. Its `after_install` copies the Jailbreak Settings values across, mapping each capability, and is idempotent. Wiring:

- **NixOS:** `services.frappe.sites.<site>.replacedApps = { jailbreak = "data_steward"; }`. Inside the existing snapshot and rollback, after `setMaintenance on`: `bench --site S install-app data_steward` (if not installed), then `bench --site S uninstall-app jailbreak --yes --no-backup` (the snapshot is the backup), then migrate as usual. When jailbreak isn't installed it is a no-op.
- **devenv:** `frappe-nix.replacedApps` does the same in `reconcile-apps`.
- jailbreak's whitelisted `assert_capability` and `check_capability` lived in `jailbreak/__init__.py`; in data_steward they are `data_steward.api.assert_capability` and `data_steward.api.check_capability` (L5 forbids code in `__init__.py`). The JS namespace changes from `jailbreak.check_capability(...)` to `data_steward.check_capability(...)` in `public/js/data_steward.js`, and capabilities are delivered at boot (A.2). No shim is kept: the old app is uninstalled, so nothing can call the old paths.

**Site half:**

```
frappe-rename-app --site S OLD=NEW [OLD=NEW…] [--dry-run] [--scan] [--yes]
```

**Abort conditions** (exit 1):

- OLD and NEW are both installed;
- NEW isn't in `apps.txt`;
- NEW's `modules.txt` doesn't declare every module OLD owns.

When OLD isn't installed, it prints `OLD not installed on S: nothing to do` and exits 0.

**One transaction:**

- the `installed_apps` global in `tabDefaultValue`, with order kept;
- `Module Def.app_name`, including custom rows;
- `Patch Log.patch`, for the `OLD.` prefix and `execute:` lines;
- `Scheduled Job Type.method` and `Scheduler Event.method`, in place;
- the exact-match app columns: `Desktop Icon.app`, `Dock.app`, `Sidebar.app`, `Sidebar Item Group.app`, `Workspace Sidebar.app`, `Notification Log.app`, `User.default_app`, `System Settings.default_app`, `User Invitation.app_name` and `Website Theme Ignore App.app`;
- token rewrites (`(?<![\w./])OLD\.` and `/assets/OLD/`) in `Report.javascript` and `report_script`, `Server Script.script`, `Client Script.script`, `Print Format.html` and `css`, `Web Form.client_script` and `custom_css`, `Web Page.main_section`, the Web Template fields, `Custom HTML Block.html`, `script` and `style`, `Letter Head.content`, `Website Route Redirect.source` and `target`, `Number Card.method`, `DocType Action.action` and `Navbar Item.action`.

**After commit:** rewrite the `site_config.json` `installed_apps` mirror, and delete the redis keys `app_modules`, `installed_app_modules`, `installed_apps` and `all_apps`.

`--scan` is read-only. It reports every remaining regex match across all text columns, from `information_schema`.

**Exit codes:** 0 ok; 1 a precondition failed; 2 a database error, rolled back.

**Wiring:**

- **NixOS:** `services.frappe.sites.<site>.renamedApps = { esign = "esign_webforms"; }` runs the site half right after `setMaintenance on`, and before offline-migrate and `bench migrate`, inside the existing snapshot and rollback.
- **devenv:** `frappe-nix.renamedApps` makes `reconcile-apps` run the same step before `install-app`.
- **Before production:** pause the scheduler and drain RQ (rename-mechanics track, deploy order).

### 5.11 Vite manifest registration and the docs-site exclusion (N2)

**One implementation, two callers (S30).** `lib/js/vite-register.cjs` exports `register({ appDir, app, sitesDir, log })`:

1. Glob `<appDir>/<app>/public/dist/**/manifest*.json` and `<appDir>/<app>/public/dist/**/.vite/manifest*.json`.
2. For each manifest entry with `isEntry: true`, consider `file` and every member of `css`. For each path whose basename matches `^(?<name>[^/]+)\.bundle\.[A-Za-z0-9_-]{6,}\.(?<ext>js|css)$`, produce `"<name>.bundle.<ext>": "/assets/<app>/dist/<path relative to public/dist>"`.
3. Merge into `<sitesDir>/assets/assets.json`: write a temp file and rename it. Vite keys overwrite same-named esbuild keys, and each overwrite is logged as `<app>: <key> -> <value> (vite)`. The result is the same whichever caller runs first, and running both changes nothing the second time.
4. If `<sitesDir>/assets/<app>` is a real directory rather than a symlink to `<appDir>/<app>/public` (`bench build --hard-link`, or an image build that copied `public/` before the app's build ran), copy `<appDir>/<app>/public/dist/` and every other directory under `public/` that a Vite config writes to (taskview's `public/portal/`) into it. This replaces taskview's `copyPortal()`.
5. Leave `assets-rtl.json` alone.

**Caller 1: the app's own build** (every bench, including Frappe Cloud and pilot's get-app bench). `scripts/ironclad-vite-register.mjs` is a managed copy of the module with a small entry point, and `scripts.build` ends with `node scripts/ironclad-vite-register.mjs` (C8). frappe's esbuild.js writes `assets.json` first and then runs each app's `yarn build`, with cwd `<bench>/apps/<app>`, so the registration lands after esbuild's own write. The entry point resolves `sitesDir` as `$FRAPPE_BENCH_ROOT/sites` when set, else `path.resolve(process.env.PWD ?? process.cwd(), "../../sites")` (the logical path, since `apps/<app>` may be a symlink), else the realpath of `../../sites`, and uses the first one containing `apps.txt`. When none does (a standalone `yarn build` outside a bench), it prints `ironclad-vite-register: no bench found; skipped` and exits 0.

**Caller 2: `lib/js/esbuild-preload.js`** (Nix builds, where the app dir is a store path). The `child_process` wrapper returned to esbuild.js becomes unconditional. Only the carry-on behaviour stays behind `FRAPPE_NIX_KEEP_GOING`. After every `execSync` whose command matches `/^yarn( run)? build\b/` and which succeeds, with cwd `<bench>/apps/<x>` and `<x> != "frappe"`, it calls `register` with `sitesDir` = `$FRAPPE_BENCH_ROOT/sites` when set, else three levels above esbuild.js's `parent.filename`.

frappe's `--using-cached` glob uses the same `<name>.bundle.<ext>` keys, so pilot's prebuilt assets resolve identically.

**Conventions for apps** (`docs/ironclad/assets.md`):

- A Vite entry is emitted as `<name>.bundle.[hash].js` and `.css` under `public/dist/`, with `build.manifest: true`.
- Vite sources MUST NOT be named `*.bundle.*` under `public/`, or esbuild compiles them a second time; taskview moves them to `src/*.entry.ts`.
- `www` templates use `{{ bundled_asset('<name>.bundle.js') }}` instead of fixed paths, because production serves `/assets` with a one-year cache.
- `update-assets.mjs` is deleted (it is on the retire list, §2.4.1); `scripts/ironclad-vite-register.mjs` replaces it.
- `[tool.bench.assets]`, for pilot, is declared only by apps with `pilot-assets = true`.

**`lib/node-targets.nix` and `lib/node-locks.nix`.** A nested directory named `docs-site` is never a target. Both discovery implementations keep the same rule, and an app can't override it: docusystem sites are built by their own workflow, never by the bench.

### 5.12 CI mode and the worktree port salt (N1, `modules/devenv.nix`)

**`FRAPPE_NIX_CI=1`** is read when enterShell runs, not at evaluation, so one shell derivation serves both modes. When it is set, enterShell:

- skips `frappe-nix-node-verify` and `frappe-nix-node-modules` for every app;
- skips `frappe-nix-apps-report`, which only runs in bench mode anyway;
- replaces the banner with the single line `frappe-nix: CI mode (<bench>, site <site>)`;
- exports `DEVENV_IN_DIRENV_SHELL=true` and `PC_TUI_ENABLED=0`.

Everything else, including app materialisation, the env symlink and bench patches, runs unchanged. `frappe-test`, ty and `bench --site … run-tests` don't need node_modules. `bench build` does, and so do the shots, so the nightly `integration` and `shots` jobs don't set the flag.

**Ports.** In app mode, define `seed`:

- `cfg.benchName + "@" + builtins.getEnv "PWD"` when `PWD/.git` is a regular file, which means a linked worktree;
- otherwise `cfg.benchName`, which keeps today's ports for the primary checkout.

The new option `frappe-nix.ports.offset` defaults to `portOffsetFor seed`. The environment variable `FRAPPE_NIX_PORT_OFFSET`, read with `builtins.getEnv` (evaluation is already impure), overrides it when it is a number from 0 to 899.

`webBase`, `dbBase` and the Mailpit SMTP, HTTP and POP3 defaults (devenv.nix:752, :770 and :816) all derive from `ports.offset`. The `frappe:config` task fails before start-up when nginx's or Mailpit's port is already bound, naming the port and suggesting `FRAPPE_NIX_PORT_OFFSET`.

Bench mode is unchanged. Its `common_site_config.json` is committed, so a port derived from the path would dirty the tree.

---

## 6. Versioning and compatibility

### 6.1 How an app pins frappe-nix

- `flake.nix`: `frappe-nix.url = "github:Avunu/frappe-nix/release-1"`.
- `flake.lock` records the commit, which is always a `v1.*` tag commit, because `release-1` only fast-forwards to tags.
- The caller workflows use `@<that commit> # v<version>`, which sync enforces (§3.7).
- The no-Nix jobs install `ironclad` at the same commit (§4.1).

The result is one frappe-nix version per app commit, everywhere.

### 6.2 How a release propagates

1. A frappe-nix PR merges to `main`. frappe-nix's release-please opens or updates its release PR. Its `dispatch-ci` dispatches `check.yml` (with `vm-tests` false) and `pr-policy.yml` on that PR. A person or the main agent merges it.
2. release-please tags `vX.Y.Z`. frappe-nix's `release.yml` fast-forwards `release-1` to the tag (REST; the `release-*` ruleset's only bypass is integration 15368).
3. The main agent runs `ironclad-rollout --to vX.Y.Z --all` (§5.8), with the user's `gh` token. For each app it updates the `frappe-nix` lock node, relocks, runs `frappe-init --sync` (which rewrites the caller SHAs and every changed managed file), and opens `chore(deps): frappe-nix vX.Y.Z` with auto-merge on. Until it does, each app's nightly `drift` job keeps an issue open and audit row A10 turns amber after 14 days.
4. Auto-merge happens once `ci / *` is green. If a stricter rule in the new version fails the app, the PR waits, and a person or agent pushes the fix to the PR branch.

Dependabot `nix` never moves frappe-nix (S5). It moves frappe and the siblings weekly; `deps.yml` relocks those PRs with `GITHUB_TOKEN`, which works because they change no workflow file.

### 6.3 frappe-nix's own versioning (N6)

- **Release.** `release-please-config.json`:
  - `release-type: simple`, which uses `version.txt`;
  - `include-v-in-tag: true` and `target-branch: main`;
  - extra-files: `py/ironclad/ironclad/__init__.py` (generic block) and `py/ironclad/pyproject.toml` (`type: toml`, `jsonpath: $.project.version`).
- **First release.** The first release is `v1.0.0`, via `Release-As: 1.0.0` in N6's commit.
- **Commit types for dependabot in frappe-nix:**
  - `fix(deps)` for `nix` (nixpkgs, devenv, and the marketplace, pilot and semgrep-rules pins), for `github-actions` (the action SHAs inside the `app-*.yml` files), and for `pre-commit`, so each bump that changes what apps run ships as a patch release;
  - `chore(deps)` for `uv /dev` and `npm /docs-site`.
- **Semver contract:**
  - **MAJOR:** a removed or renamed reusable-workflow input, secret, output or job name; changed required-check contexts; a new `[tool.ironclad] schema`; a strategy change that can't be applied by `--sync` alone; or a removed tool or CLI flag. A new major gets a new `release-<N>` branch, and apps move by editing the flake URL in a deliberate PR.
  - **MINOR:** new managed content or checks that `--sync` applies, even if they make previously passing code fail (it then waits in the bump PR); new optional inputs with defaults; new tools or flags.
  - **PATCH:** fixes and pin bumps.
- **Compatibility guarantees within v1:**
  - `app-*.yml` at any `v1.y` accepts a caller rendered by any `v1.x`, because inputs are only ever added, with defaults;
  - `[tool.ironclad] schema = 1` keys are never removed;
  - deprecated keys keep working for one minor and `--check` warns about them, with exit 0.
- **Frappe major bump (v17).** Set `frappe-major = 17`, run `--sync` (the flake inputs, ranges, `package.json` stanza and release branch all follow), and land a `Release-As: 17.0.0` commit. Keeping `version-16` maintained alongside it is out of scope for v1 of this spec. Reserved key: `[tool.ironclad] maintenance-branches`.

---

## 7. Acceptance tests per PR

Each PR's own CI MUST prove these. A "nix check" is under `tests/ironclad/` and runs in `check.yml` through `ironclad-all`. A "selftest" is the PR's own `selftest-*.yml` workflow, path-filtered to its files plus `workflow_dispatch`.

**N3a: hook points**
- `nix flake check --no-build` passes, and `nix build .#ironclad` builds against the locked nixpkgs (tomlkit 0.15.0) with `pythonRuntimeDepsCheck` on. `ironclad --version` prints `0.0.0`, and `ironclad nonexistent` exits 2 listing the commands.
- **Self-contained package:** in a job with no frappe-nix checkout, `uv tool install "ironclad @ git+file://$PWD#subdirectory=py/ironclad"` from a clean clone, then `ironclad data-path known-apps.json` and `ironclad data-path schema/tool-ironclad.schema.json` print existing files. (N3 extends this to `ironclad sync --check` on the fixture.) N3a owns no workflow, so its CI proves the same property through the Nix build, whose source is `py/ironclad` alone, and `ironclad-cli`, which runs `data-path` against the installed output; the `uv tool install` run is in the PR description, and N3's `selftest-scaffold.yml` makes it a CI job.
- `.#checks.x86_64-linux.ironclad-all` builds. At this point it holds only N3a's own checks: `ironclad-cli`, `ironclad-loaders` and `ironclad-app-flake`.
- A test `lib/scripts.d/` file and a test `lib/ironclad/tools/` file (fixtures under `tests/ironclad/fixtures/`) are picked up by the loaders, proven by a nix eval check (`ironclad-loaders`, which also proves a drop-in that redefines a script fails evaluation).
- The app-mode shell exposes `ironclad` and `frappe-init`, and the flake exposes `apps.frappe-init`. `ironclad-app-flake` evaluates the fixture app's flake with this checkout as `frappe-nix` (what `--override-input frappe-nix path:.` does) for `apps.frappe-init`, `apps.ironclad` and `apps.relock`, and checks the shell fragment `lib/ironclad/shell.nix` hands `modules/devenv.nix`. Evaluating the whole dev shell needs the fixture's `nix/uv.lock` and import-from-derivation, which `nix flake check --no-build` can't do, so the shell itself is proven by `nix develop` on the relocked fixture (PR description) and, from N1 on, by `selftest-runtime.yml`.
- The docs stubs pass `docusystem check --ci` (the existing docs workflow).

**N1: runtime**
- **Nix checks:**
  - Port offset: the primary checkout's offset is unchanged from today; a linked worktree (`.git` is a file) gets a different one; `FRAPPE_NIX_PORT_OFFSET=123` sets all of web, db and the three Mailpit ports from 123.
  - `coverage` is importable from the generated root's dev env (`python -c 'import coverage'` over `devPythonEnv`).
  - `FRAPPE_NIX_CI=1` enterShell renders without the node-verify and node-modules calls (string assertion on the rendered enterShell).
  - The generated bench root's dev group has `coverage` and has none of `ruff`, `pre-commit` or `semgrep`, also after `ensure-root` runs on a root that had them.
- **`selftest-runtime.yml`** on the fixture app:
  - `FRAPPE_NIX_CI=1 nix develop --override-input frappe-nix path:$GITHUB_WORKSPACE -c frappe-test --ci` exits 0, and the log contains no `Installing node_modules`.
  - `coverage.json` has no key containing `/.frappe-nix/`, and contains `ironclad_fixture/api.py`.
  - A variant with `fail_under = 100` exits 2, and a variant with `fail_under` 5 points below the measured total exits 2 with the `raise … fail_under` message.
  - A variant with the exemption removed exits 3 and names `ironclad_fixture.api.legacy`.
  - A variant whose extension class subclasses another app's extension (the A.0 #4 shape) exits 4.
  - `ironclad-report.json` validates against its schema.
  - The default setup works with erpnext as a sibling (`module:erpnext.tests.bootstrap_test_data`), in a variant with the erpnext sibling.
  - The fixture's `after_request` hook (T6) is a target and is reported tested; a variant whose module calls `frappe.get_all` at import time is probed without crashing, and a variant whose module raises on import reports that target untested with the error.
  - A variant with `shell-checks = ["touch stray.txt"]` exits 7 and names the untracked file.
- **`frappe-rename-app`:**
  - On a scratch site, the fixture renamed to `ironclad_fixture2` migrates cleanly. Data, the Patch Log and the Scheduled Job Type `name` and `stopped` values are kept.
  - A second run prints `nothing to do`.
  - `code --dry-run` shows the expected diff.
  - On `tests/fixtures/rename-esign`, `code --from esign --to esign_webforms` renames `esign.desk.bundle.js` and `esign.control.bundle.js` to `esign_webforms.*`, and every `app_include_js` entry in the rewritten `hooks.py` names an existing file. A bare `esign.<x>` string with no matching file is left alone and reported.
  - `code --from jailbreak --to data_steward` exits 1 (replace pair). The `replacedApps` NixOS check installs a stand-in new app, uninstalls the old one inside the snapshot, and is a no-op on a second run.
- **Docs:** `docs/development/README.md:36` and `docs/reference/dev-shell-options.md:12` no longer claim the allocator walks forward.

**N2: assets**
- **Nix check** `tests/esbuild-preload.js`, extended:
  - a fixture app's `public/dist/.vite/manifest.json` with `js/foo.bundle.AbC123.js` and `css/foo.bundle.XyZ.css` registers `foo.bundle.js` and `foo.bundle.css` in `assets.json` under `/assets/<app>/dist/…`;
  - a non-matching `index-AbC.js` is ignored;
  - registration happens without `FRAPPE_NIX_KEEP_GOING`;
  - frappe's own keys aren't touched.
- **Nix check:** the node-targets fixture with `<app>/docs-site/package.json` yields no `docs-site` target in either the Nix discovery or `node-locks.sh`.
- **Nix check:** `tests/fixtures/spa-app/`, whose `build` script writes a hashed bundle and a manifest with no network, built as a builtBench, has `assets.json` mapping `spa.bundle.js` to the hashed file.
- **Stock bench (no preload).** In `selftest-assets.yml` (N2): a plain frappe bench made with `bench init` from the pinned frappe, no `NODE_OPTIONS`, `bench get-app` of the spa-app fixture, `bench build --app spa_app`; `sites/assets/assets.json` maps `spa.bundle.js` and `spa.bundle.css` to the hashed files. A second `bench build` under frappe-nix's preload leaves `assets.json` byte-identical (idempotent). With `--hard-link`, `sites/assets/spa_app/dist/` contains the hashed bundle.
- **Nix check:** `lib/js/vite-register.cjs` and the packaged `scripts/ironclad-vite-register.mjs` template contain the same function body (a string comparison after stripping the entry point).

**N3: scaffold**
- **Nix check `ironclad-sync`,** on a copy of the fixture app:
  - `--sync`, then `--check`, exits 0;
  - a second `--sync` changes no bytes;
  - editing a managed file gives `--check` exit 1 with the diff;
  - content in a local region survives `--sync`;
  - an app-owned pyproject key (`extend-exclude`) and app-owned package.json scripts survive;
  - adding `ignore = ["F401"]` gives exit 2;
  - raising the pre-commit ssort rev to `0.18.0`, the `oxlint` range to `^1.99.0` and the `ruff` lock to `0.17.0` is accepted, while lowering any of them gives exit 1;
  - a malformed local region gives exit 2;
  - the `__init__.py` trailing-comment form is converted to the block form with the value kept;
  - adding a first `.ts` file under `scripts/` makes `--check` report the missing `tsconfig.scripts.json`;
  - a tracked `.github/workflows/old.yml` containing `release-please-action`, a `.oxfmtrc.json` and an `update-assets.mjs` are each reported as `legacy file` by `--check` and deleted by `--sync`;
  - with an SPA entry whose `tsconfig` is `tsconfig.json`, the solution is rendered as `tsconfig.ironclad.json`, the app's `tsconfig.json` is untouched, and `scripts.typecheck` is `tsc --build tsconfig.ironclad.json && vue-tsc …`; with `typescript.browser = false` no browser project exists;
  - a `web-include` glob moves a file from `tsconfig.desk.json` and the oxlint desk override to `tsconfig.web.json` and the web override;
  - an `unchecked-js` entry appears in the desk exclude; one naming an untracked file is C9 exit 1; `typescript.exclude` naming a tracked `.js` under the package is exit 2;
  - a `generated` glob appears in the prek exclude, the `validate_copyright` exclude and the oxfmt and oxlint ignorePatterns, and `prek run validate_copyright --all-files` followed by `--check` exits 0 (no stamp/sync fight on `marketplace/shots.d.ts`);
  - a fixture with package.json `1.0.0` and `__version__ = "0.0.1"` and no manifest gets package.json `0.0.1` and manifest `0.0.1` on its first sync, and C5 passes;
  - the README `license` block renders identically with the system clock set to 31 December and to 1 January.
- **Nix check:** no path appears in two manifest fragments; every template renders for the fixture contexts {plain, erpnext+hrms siblings, scss, nested frontend, SPA at root, SPA in `portal/` without package.json, docs-site, pilot-assets, Vite}.
- **Nix check:** `templates/app/` contains exactly `.envrc` and `.gitignore`, and `frappe-init --app` in an empty dir produces those two plus what `ironclad sync --write` renders, nothing from `ironclad/data/`.
- **`selftest-scaffold.yml`,** on the rendered fixture:
  - `nixfmt --check flake.nix`, `oxfmt --check`, `ruff format --check`, `actionlint`, `zizmor` and `uv lock --check --project tools` all pass;
  - after `yarn install`, `prek run --all-files` passes; this needs the network for the remote hooks;
  - `tsc --build` passes, against the published `frappe-types` ≥ 16.5.0, or a packed tarball of the frappe-types branch until it is published;
  - `nix flake lock && nix eval .#packages.x86_64-linux.default.drvPath` succeeds;
  - `frappe-init --check` on a read-only clone of carbon_frappe exits 1 with a drift report and no crash.
  - **Bootstrap:** on a copy of the fixture with no `flake.nix` and no `flake.lock` (the postgrid and jwt_auth shape), and on one whose lock pins frappe-nix `main` (the carbon, taskview and timeclock shape), one `frappe-init --sync --frappe-version version-16` run with `IRONCLAD_FRAPPE_NIX_URL=path:$GITHUB_WORKSPACE IRONCLAD_ALLOW_SKEW=1` and **without yarn or uv on PATH** leaves a tree on which `--check` exits 0 immediately: phase A locked, phase B re-entered the dev shell, `yarn.lock` and `tools/uv.lock` exist.
  - **No-Nix install:** `uv tool install "ironclad @ git+file://…#subdirectory=py/ironclad"` in a job without the frappe-nix checkout on disk, then `ironclad sync --check` on the rendered fixture, exits 0 (`IRONCLAD_ALLOW_SKEW=1`).
- **Nix check:** with the erpnext and hrms siblings, node-lock seeding writes `nix/node-locks/{frappe/ui,erpnext/banking,hrms/frontend,hrms/roster}` into an empty fixture and leaves existing ones alone.

**N4: CI**
- `actionlint` and `zizmor` (strict `hash-pin`) pass on frappe-nix's `.github/workflows/*.yml`, on every rendered caller and on the rendered `.github/dependabot.yml` (every entry has a `cooldown`). A planted dependabot entry without `cooldown` makes the zizmor hook fail. actionlint accepts per-job `defaults.run.working-directory: ${{ inputs.app-root }}` and finds no job-level `if:` reading `secrets`.
- **docusystem compatibility:** on the rendered fixture with `docs-site/`, `docusystem doctor` reports no dependabot warning and `docusystem upgrade --dry-run` plans no change to `.github/dependabot.yml`.
- **Python tests** (`py/ironclad/tests`, run as a nix check with pytest):
  - **policy:** rejects `feat!: x`, a `BREAKING CHANGE:` footer, and `Release-As: 17.0.0` when the major is 16; accepts `chore(deps): bump …` and `chore(develop): release 16.1.0`;
  - **ratchet:** R1–R5 positive and negative cases, and inactive when the base lacks `[tool.ironclad]`;
  - **minibench:** the layout guard;
  - **ruleset and settings JSON:** validated against a vendored schema of GitHub's ruleset API;
  - **apply:** `--dry-run` produces no PUTs for an already-matching mocked API; `--phase full` on an `apps.json` entry with `private: true` exits 1.
  - **auto-merge decisions:** recorded fetch-metadata v3 outputs for each ecosystem row (`github_actions` with the `docs-actions`, `pilot` and `actions` groups, `npm_and_yarn`, `uv`, `pre_commit`, `nix`, `submodules`) give the table's verdicts, and a `nix` PR never gets `--auto` from the auto-merge job when relock has a patch.
  - **relock patch:** a fixture where sync writes a new untracked `nix/node-locks/x/yarn.lock` and stages a change produces a patch that `git apply --index` reproduces exactly; a patch touching `.github/workflows/` is refused.
  - **nightly script:** with a stubbed `frappe-test` that exits 1 and a suite that exits 0, `nightly.sh` exits non-zero; with all stubs passing it exits 0.
- **`selftest-ci.yml`:**
  - calls `./.github/workflows/app-ci.yml` with `app-root: tests/fixtures/ironclad-app`, `frappe-nix-override: .` (which allows skew, §3.7) and `run-test: ${{ github.event_name != 'pull_request' }}`; `lint`, `typecheck` and `marketplace` succeed on every PR touching `.github/`, `py/ironclad/` or `templates/`, and `test` runs weekly and on dispatch;
  - on a dispatch, every gate job's dispatch guard succeeds (it has `pull-requests: read`), and `app-pr-policy.yml` passes with no PR found;
  - in the minibench job, a planted `owner: someone@example.com` in a fixture `custom/*.json` makes `validate_customizations` fail.
- **`ironclad-audit --repo Avunu/carbon_frappe --format json`** runs end to end with `GITHUB_TOKEN`, where rows needing admin are `unknown`, and produces all 17 rows.
- **Spike (recorded, not gating):** on a scratch repo, whether `GITHUB_TOKEN` can land a workflow-file change through the Git Data API (§4.5). The result goes into `docs/ironclad/github.md`.

**N5: product tools**
- **`frappe-listing`:**
  - `check --no-getapp` passes on the fixture;
  - negative fixtures each fail with their rule id: the tagline is 85 characters (L1); the `-dev` range (L3); `frappe` in `required_apps` (L3); `__version__` with a trailing comment (L4); a print in `__init__` (L5); an override with no allow-list entry (L6); a stale baseline entry (L7); three identical findings against a baseline `count` of 2 (L7, new finding) and one against a `count` of 2 (L7, stale count).
- **`selftest-product.yml`:**
  - `check` with L9 passes on the fixture with the pinned pilot, on `ubuntu-latest` with only the `marketplace` job's apt packages added, and a cold run takes under 25 minutes;
  - `registry --dry-run --onboard` produces an `apps/<app>.json` entry byte-identical to running the pinned `tools/add_release.py` directly.
- **`frappe-icon`:**
  - check passes on the good fixture pair, and fails one by one on the bad fixtures: `<text>`, a non-square viewBox, a stroke, outside the safe area, and a stale tile;
  - `build` output hashes are stable across two runs.
- **`frappe-demo`:** run twice on one site, a record count per doctype is unchanged the second time.
- **`frappe-shots`:**
  - two runs from two fresh sites in the same job are pixel-identical (`maxDiffRatio 0`);
  - `--check` against the committed fixture shots exits 0;
  - a 1-pixel CSS change in a variant exits 1 and writes a diff PNG.
- **`readme --check`** passes on the fixture, and removing a marker exits 2.

**N6: frappe-nix self-release**
- `npx release-please release-pr --dry-run --repo-url Avunu/frappe-nix --target-branch main` proposes `1.0.0` from `Release-As`, and bumps `version.txt` and both ironclad version locations.
- Every `uses:` in frappe-nix's own workflows is SHA-pinned (zizmor).
- `.github/workflows/pr-policy.yml` has one non-reusable job named `pr-policy` (`committed` plus the no-`!` rule, `--major-free`) that installs ironclad from the checkout, fails a `feat!:` title in a test PR, and re-runs on a retitle without re-running `check.yml`.
- A `workflow_dispatch` of `check.yml` without inputs (what the release flow does) skips `vm-tests`; with `vm-tests: true` it runs it.
- The `release-1` fast-forward step is exercised in `--dry-run` mode, printing the REST calls.
- `ironclad/self/rulesets/*.json` pass the N4 schema test.

---

## 8. Open questions that need the user

Everything else in this spec is decided. These need the user, and none blocks writing the code.

1. **Q1. Binary cache.** Attic (host plus S3) or Cachix (plan and token)? Then set the org variable `IRONCLAD_NIX_CACHE`, the write secret `IRONCLAD_NIX_CACHE_TOKEN` (Actions store only), and, for a private cache, the pull-only `IRONCLAD_NIX_CACHE_READ_TOKEN` (Actions and Dependabot stores).
   - Until then, `ci / test` takes about 26–28 minutes (ci-timing track), against a 15-minute target.
   - The CI design doesn't change when the cache is added.
2. **Q2. The `version-*` bypass actor.** This spec uses GitHub Actions (integration 15368); only applying a ruleset proves it's accepted. If it returns 422: may we create a machine user for a `User` bypass (its token stored as `VERSION_BRANCH_TOKEN`), or would you rather enable deploy keys for the org? Also confirm that any workflow in the repo with `contents: write` being able to move `version-*` is acceptable.
3. **Q3. Registry credential.** `REGISTRY_TOKEN`, a classic PAT with `public_repo` from your account (decision 15), has to be added as a repo secret on each listed app before Phase 6, along with the `Avunu/marketplace` fork. Alternatively, publishing stays local: `frappe-listing registry` uses your `gh` login.
4. **Q4. `IRONCLAD_AUDIT_TOKEN`.** A read-only fine-grained PAT (Administration and Contents read, on the 13 repos), so the org-wide nightly scorecard can read settings and rulesets. Without it, those rows are `unknown`.
5. **Q5. Module names under in-place renames.** The validated zero-data-move path keeps the old module names, such as "Carbon Frappe", "Frappe UI Editor Integration", "JWT Auth" and "Mercury Integration". Is keeping marks like "Frappe" in module names acceptable? Renaming them needs a module-rename mode in `frappe-rename-app`, which isn't specified here. (data_steward is a fresh install and gets new module names regardless.)
6. **Q6. frappe/marketplace#29.** Until it merges, upstream registry CI fails ImportCheck for the 5 apps that import erpnext or hrms, even though our `marketplace` check (which mirrors #29) passes. Engage upstream, or wait?

Not questions, but things the user does once: make timeclock public before its PR A (§7 decision 2), and refresh the `gh` login with the `workflow` scope (`gh auth refresh -s workflow`) on the machine that runs `ironclad-rollout`. The former Q6 (`validate_copyright` stamping) is settled: D7 keeps it, and §2.14 now excludes managed and generated files, so it no longer fights sync.

---

## Appendix R. Review log (spec 1.0 → 1.1)

Each review issue was checked against the sources named in its row. "Fixed" means the spec now says what the row lists. Nothing was rejected outright; two issues were resolved differently from the reviewer's first suggestion, with the reason given.

| # | Sev. | Issue | Verified against | Verdict and resolution |
|---|---|---|---|---|
| 1 | blocker | relock-push can't push the caller-SHA rewrite of a frappe-nix bump with `GITHUB_TOKEN` | GitHub's documented `workflows`-permission refusal for GitHub App tokens; github-capabilities track (version-16 REST move across existing workflow commits) | **Fixed, via the reviewer's fallback rather than a PAT or floating refs.** S5, S29, §2.17, §4.5, §5.8, §6.2, A10: dependabot ignores frappe-nix in both ecosystems; `ironclad-rollout` (user token with `workflow` scope, auto-merge on) is the only mover; relock/upgrade/shots refuse workflow paths; nightly `drift` and A10 remind. Rejected alternatives: (a) a fine-grained PAT with `workflows` on all app repos is a new long-lived write credential, against the no-App, minimum-credential stance; (b) `@release-1` callers would let the workflow code float ahead of the tools the app pins (`job_workflow_sha` ≠ flake.lock), breaking §6.1's one-version-per-commit and the zizmor `hash-pin` policy. The Git Data API path is an N4 spike that can automate this in a later MINOR (§4.5). |
| 2 | blocker | Vite bundles unregistered on stock benches | frappe `esbuild/esbuild.js` (writes `assets.json`, then runs app builds); taskview `hooks.py` `app_include_js`; taskview `update-assets.mjs` | **Fixed.** S30, §2.4, §2.8 `scripts.build`, C8, §5.11 (one module `lib/js/vite-register.cjs`, two callers, sites resolution, hard-link copy replacing `copyPortal`), N2 stock-bench acceptance test in `selftest-assets.yml`. |
| 3 | blocker | Root/in-package Vue SPAs inexpressible | timeclock root `vite.config.ts`/`tsconfig.json` and `public/js/{timeclock,job_timeclock}`; taskview `portal/tsconfig.json` extends `../tsconfig.json`, `scripts.typecheck` uses vue-tsc | **Fixed.** §2.1 `[[tool.ironclad.typescript.spa]]` and `typescript.browser`, with both fleet layouts written out; §2.2 `spa_globs`, `solution`; §2.8 `scripts.typecheck`; §2.9 solution renamed `tsconfig.ironclad.json` when an SPA owns the root, empty projects omitted (no TS18003); N3 tests. |
| 4 | major | Strict checkJs with no honest migration path | frappe-types track error counts | **Fixed.** `[[tool.ironclad.unchecked-js]]` (§2.1, §2.9), R6, C9, `ironclad unchecked-js --stale` in typecheck, amber A7; `typescript.exclude` limited to non-source paths (exit 2). |
| 5 | major | C7 forbids the augmentations apps need | frappe-types track (taskview, esign, jailbreak, timeclock symbols) | **Fixed.** C7 narrowed to redeclarations; augmentations only in `types/<app>.augment.d.ts` with `// app-owned:` per member (§2.9, §5.9); A7 counts members. |
| 6 | major | esign web scripts under `public/js` classed as desk; oxlint globs ≠ tsconfig sets | esign `public/js/web/*.js` (`frappe.web_form`, `frappe.ready`, `frappe.form_dirty`) | **Fixed.** `typescript.web-include` (§2.1, §2.2); tsconfig and oxlint overrides generated from the same file sets, with a web override (§2.9, §2.10). |
| 7 | major | No way to retire legacy files | carbon and taskview `.github/workflows`; timeclock `.oxfmtrc.json`, `MANIFEST.in`, `requirements.txt`; carbon `nix/node-offline-hashes.json`; frappe-nix `modules/devenv.nix:1858` throw | **Fixed.** S31, §2.4.1 retire list, sync step 7, exit 1 `legacy file`, A9 reads the same list. |
| 8 | major | Bootstrapping undefined | postgrid and jwt_auth have no flake; carbon/taskview/timeclock lock `main` | **Fixed.** S32, §3.3 two-phase sync with self re-exec and dev-shell re-entry, single bootstrap command, migrations after `v1.0.0` (§1.1); N3 bootstrap test. |
| 9 | major | Rename leaves bundle files with old names; jailbreak replace path missing | esign and timeclock `hooks.py`; jailbreak `__init__.py` whitelisted functions and JS callers; plan A.2 | **Fixed.** §5.10 step 0/1/2 (rename `OLD.*` files, bare-filename guard), replace path with `replacedApps`, `rename_mode` in apps.json, `__init__` functions to `api.py` (§2.13); N1 tests with an esign fixture. |
| 10 | major | Date-dependent README license block | §3.4 | **Fixed.** `<first-commit-year>–present` (§2.19); §3.4 forbids current-date inputs; N3 test across 31 Dec/1 Jan. |
| 11 | major | `validate_copyright` fights managed and generated files | §2.14, §2.21; carbon `public/js/generated/*`, timeclock `types.ts`, `components.d.ts` | **Fixed.** `[tool.ironclad] generated` fed to prek, validate_copyright, oxfmt, oxlint; managed headed files excluded by name (§2.14); N3 test. Q6 removed. |
| 12 | major | Set-keyed semgrep baseline hides identical findings | taskview `api.py`: 12 identical `frappe.db.commit()` | **Fixed.** Multiset with `count` (S21, §2.21, L7, R3); N5 tests. |
| 13 | major | Nightly script masks failures | §4.6 command (exit status of `--down`) | **Fixed.** Full script with `rc` accumulation, admin password, artifact dir and carbon's `CF_*` names (§4.6); N4 stub test. |
| 14 | major | Security hook callables not testmap targets | jwt_auth, carbon, esign, automated_subscriptions `hooks.py` | **Fixed.** T6 (§5.1.1), install/migrate kinds exemptable; N1 test. |
| 15 | major | Coverage omit not extensible | §2.12 | **Fixed.** `*/patches/*` in managed omit; `[[tool.ironclad.coverage-omit]]`, R7, C9, amber A8. |
| 16 | major | No CI hook for frappe-tree and shell-only checks | carbon `check.yml` lines 124–143 (`codegen`, `FRAPPE_PATH`, `compile`, `audit:drift`) | **Fixed.** typecheck exports `FRAPPE_PATH` via `ironclad pin-path frappe` (app inputs supported), opt-in `frappe-node-modules`; `shell-checks` run by `frappe-test --ci` stage 8b, verdict 7. |
| 17 | major | Dependabot actions group and docusystem's entries | docusystem `scaffold/dependabot-*.yml`, `src/lib/dependabot.ts` `ensureDependabotEntries`, `src/commands/doctor.ts` | **Fixed.** One github-actions entry with `docs-actions`, `pilot` and `actions` groups and the cooldown exclusions docusystem checks (§2.17). Two entries for the same ecosystem and directory aren't allowed, and docusystem's `upgrade` only adds an entry when none exists, so one entry satisfies both; N4 doctor/upgrade test. |
| 18 | major | `audit-consumer --strict` turns on by itself | frappe-types track (9 of 12 fail, false positives, no `--types`) | **Fixed.** Opt-in `typescript.audit-consumer`; notice otherwise; default flips in a later MINOR (§4.2). |
| 19 | minor | `policy --pr` vacuous on dispatch | §4.3 env | **Fixed.** Title and base from the looked-up PR, fail if missing (§4.3, §5.9). |
| 20 | minor | semgrep `[ -d ] &&` under `set -e` | §4.2 | **Fixed** (`if … fi`), plus a general rule in §4.1. |
| 21 | minor | Version mismatch at first sync | taskview 1.0.0/0.0.1, timeclock 0.0.0/0.0.1 | **Fixed.** Seed from `__version__` and set package.json (§2.4, §2.8, §2.16); N3 test. |
| 22 | minor | Coverage ratchet never forced upward | decision 5 | **Fixed.** Stage 4 upward rule, verdict 2 (§5.1, S24); N1 test. |
| 23 | minor | magic-nix-cache as fallback | ci-timing track (48/48 throttled, 9.09 GB used) | **Fixed.** No cache action for app Nix jobs when unset; magic-nix-cache only for frappe-nix self-tests (S15, §4.1). |
| 24 | minor | Unpinned `nixpkgs#attic-client` / `nixpkgs#resvg` | §4.1, §4.6, §5.3 | **Fixed.** `--inputs-from .` for attic; `.#frappe-icon` for resvg. |
| 25 | minor | Probe doesn't connect | timeclock `utilities.py` module-level `frappe.get_all` (verified) | **Fixed.** `frappe.connect()`, import failures reported as untested with `error` (§5.1.1). |
| 26 | minor | gen-doctypes misses Custom Fields | minibench sparse patterns | **Fixed.** Doctypes mode copies `custom/*.json` and `fixtures/*.json`; gen-doctypes merges them (§4.2, §5.9). |
| 27 | minor | "App-owned build step" undefined | carbon `package.json` `build` | **Fixed.** `[tool.ironclad] build = true` (§2.1, §2.8). |
| 28 | minor | Second ruff in the bench env | `lib/frappe-workspace.py:140-141` (`ruff>=0.15.0`, `pre-commit`, `semgrep`) | **Fixed.** N1 removes them and reconciles existing roots (S1, §1.2, §1.3); N1 test. |
| 29 | minor | Two evaluations per PR test run | ci-timing track | **Fixed differently.** A single evaluation for both `nix build` and `nix develop` isn't available (a `--profile` from `nix build` isn't a dev-shell env, and `--no-pure-eval` disables the eval cache). Instead the clean build runs on PRs only when build inputs change, and always on push, schedule and dispatch (S16, §4.2). |
| 30 | minor | Private timeclock gets no protection and burns minutes | github-capabilities track (403 on rulesets) | **Fixed.** `--phase full` requires `private: false` (§5.6); §4.7 note; decision 2 (public first). |
| 31 | blocker | Duplicate of #1 (relock-push vs workflow files) | as #1 | **Fixed** as #1. The reviewer's Git Data API primary is kept as the N4 spike; its fallback (dependabot ignores frappe-nix, rollout only) is the v1 design, since it works regardless of the spike's outcome. |
| 32 | blocker | Wheel from `#subdirectory=py/ironclad` lacks templates and data | §1.2 tree, §3.1 | **Fixed.** S10: all data under `py/ironclad/ironclad/data/` read via `importlib.resources` (§1.2, §3.1); N3a and N3 no-checkout install tests. |
| 33 | major | Gate jobs lack `pull-requests: read` for the dispatch guard | §4.2 table | **Fixed.** Every gate job has it (§4.2); N4 dispatch test. |
| 34 | major | relock patch misses staged and untracked files | sync step 10 `git add`; `git diff --binary` | **Fixed.** `git add -A && git diff --cached --binary`; `git apply --index`; N4 test (§4.5). |
| 35 | major | nix auto-merge enabled before relock lands | §4.5 | **Fixed.** auto-merge job never enables it for nix unless the relock was empty; `relock-push` enables it after pushing (§4.5). |
| 36 | major | selftest-ci always skews; no way to skip `test` | §3.7, §4.2 | **Fixed.** Override sets `IRONCLAD_ALLOW_SKEW=1` and drops `--expect-rev` (§3.7, §4.1); `run-test` input (§4.2); N4 test. |
| 37 | major | frappe-nix release dispatch runs VM tests; pr-policy inside check.yml | frappe-nix `check.yml:86-88` (`vm-tests` on any `workflow_dispatch`) | **Fixed.** `vm-tests` dispatch input; separate non-reusable `pr-policy.yml` installing from the checkout, `--major-free` (§1.2, §4.10, §6.2); N6 tests. |
| 38 | major | Cache write token exposed to untrusted runs | S15, §4.1 | **Fixed.** Write token only on develop push/schedule; separate read token in the Dependabot store; S15 text corrected (S15, §4.1, callers). |
| 39 | major | `frappe-init --app` would copy all of `templates/app` | frappe-nix `lib/sh/template.sh` `render_template`/`install_template` (cp -R, find -type f) | **Fixed.** `templates/app/` keeps only `.envrc` and `.gitignore`; `cmd_app_init` delegates to `ironclad sync --write` (§1.2, §1.3, §3.3); N3 test. |
| 40 | major | `tomlkit<0.14` vs nixpkgs 0.15.0 | `nix eval --inputs-from . nixpkgs#python314Packages.tomlkit.version` → 0.15.0 | **Fixed.** `tomlkit>=0.13`, no cap; nix check builds with runtime-deps check on (§1.2, §7 N3a). |
| 41 | minor | version-guard never runs | §4.2; REST PATCH raises no push | **Fixed.** Job removed; the fast-forward job asserts the invariant after the PATCH; A2 nightly (§4.2, §4.4); `ci.yml` no longer triggers on `version-*`. |
| 42 | minor | Ratchet base is base tip; no schedule rule | S24, §4.2 | **Fixed.** `git merge-base "$BASE_SHA" HEAD`; skipped on schedule (S24, §4.2). |
| 43 | minor | `Release-As` lost in a multi-commit squash | carbon 3c16b00 (direct commit, footer followed by a Co-Authored-By trailer) | **Fixed.** Merge body set explicitly, last commit carries the footer, dry-run before merge (S11, §2.16). |
| 44 | minor | Workflow-level `defaults` can't read `inputs`; `uses:` ignores working-directory | GitHub Actions contexts | **Fixed.** Per-job defaults and explicit path prefixes (§4.1, §4.2). |
| 45 | minor | Other pushing jobs not listed | §4.5, §4.6 | **Fixed.** All checkouts `persist-credentials: false`; the three pushing jobs use the token remote (§4.1). |
| 46 | minor | Secrets in job-level `if:`; `${{ inputs }}` in `run:` | §4.4, §4.6 | **Fixed.** Rule in §4.1; publish and registry-refresh use step `env:` (§4.4, §4.6). |
| 47 | minor | Attic needs login without a token; unpinned client | §4.1 | **Fixed** (always `attic login … ${TOKEN:+"$TOKEN"}`, `ci:<cache>`, `--inputs-from .`). |
| 48 | minor | Dispatch idempotency checks only `ci / lint` | §4.4, §4.5 | **Fixed.** Each required context checked independently (§4.4, §4.5 sweep). |
| 49 | minor | One actions group; ecosystem strings | §2.17, §4.5 | **Fixed.** Separate groups (see #17); table uses fetch-metadata's `github_actions`, `npm_and_yarn`, `uv`, `pre_commit`, `nix`, `submodules`, with a recorded-metadata unit test in N4. |
| 50 | minor | DeployKey fallback unusable | github-capabilities track (deploy keys disabled; `User` bypass GA 2026-05-07) | **Fixed.** `version.user.json` as the first fallback (§4.8), Q2 updated. |
| 51 | minor | L9 needs system libraries | registry-dryrun track (`pkg-config`, `libmysqlclient`) | **Fixed.** apt step in `marketplace` (§4.2), note in L9, N5 timing test (< 25 min cold). |
| 52 | minor | zizmor scope vs dependabot audits | §2.14 | **Fixed by decision:** zizmor audits `.github/workflows` and `.github/dependabot.yml`; every rendered entry carries a `cooldown` (§2.14, §2.17); N4 test. |

## Appendix I. Implementation deviations

Changes an implementing PR made to this spec, with the reason. Each PR adds its rows.

| PR | Section | Change | Why |
|---|---|---|---|
| N3a | S20 | Full SHAs and URLs for the three inputs; `frappe-semgrep-rules` is locked at the `develop` head of 2026-10-06 (81a6e3d). | S20 gave no revision for `frappe-semgrep-rules`. The flake URLs name branches, not SHAs, so Dependabot `nix` can move the locks. |
| N3a | §7 N3a | `ironclad-all` holds N3a's three checks instead of being empty. | The loader proof is itself a nix check under `tests/ironclad/`, which runs through `ironclad-all`. |
| N3a | §7 N3a | The app-mode dev shell is proven by `nix develop`, not by an evaluation check. | The shell's evaluation imports the generated bench workspace (IFD) and needs the fixture's `nix/uv.lock`, which only a relock produces. The flake's apps and the shell fragment are evaluation checks. |
| N3a | §7 N3a | The no-checkout `uv tool install` is shown in the PR, not run by N3a's CI. | N3a owns no workflow; the Nix build from `py/ironclad` alone proves the same self-containment. N3's `selftest-scaffold.yml` runs the install. |
| N3a | §1.2 | N3a commits the fixture's `flake.nix`, hand-rendered from §2.5 for an app with no siblings. N3 owns it from then on. | §7 N3a evaluates the fixture's flake, so it must exist before N3's renderer does. For no siblings, §2.5's template renders `siblings = [` and `];` on separate lines, which `nixfmt` collapses to `siblings = [ ];`: N3's template must special-case the empty list to be nixfmt-stable. |
| N3a | §1.2 | `ty.toml` and `dev/env.nix` gain `py/ironclad` (owner N3a). | `ty` checks the whole tree and must resolve `ironclad` imports. `tomlkit` is not in `dev/uv.lock`, so N3 adds it to `dev/pyproject.toml`'s dev group when its code first imports it. |
| N3a | §1.2 | `modules/devenv.nix` keeps `apps.relock` inside an `lib.mkMerge` with the app-mode `apps`. | Nix can't define `apps.relock` and `apps = mkIf …` side by side. The app-mode flake gets every `lib/ironclad/tools` app (`.#<tool>`, as §5 requires) as well as `.#frappe-init`. |
| N3a | §1.4 | New: the seam interfaces. | §1.2 names the seams but not what a contribution looks like. |
| N3a | §1.4 | `pin-path` and `config` default to the nearest `flake.lock`/`pyproject.toml` within the git work tree, not the work tree root's. | §4.1 runs every step in `inputs.app-root`, and §7 N4's `selftest-ci` passes `app-root: tests/fixtures/ironclad-app`: the root's lock is frappe-nix's own, which has no `frappe` input. |
| N3a | §1.4 | `tests/ironclad` checks must be named `ironclad-<name>`; tool names `default`, `frappe-init`, `backup-fetch` and `relock` are refused. | flake.nix merges both seams with `//`, so a clash would otherwise replace frappe-nix's own output silently in one flake and fail with an option conflict in the other. A prefix keeps the guard in the seam instead of reindenting flake.nix's `checks`. Every check §7 names already has it. |
| N3a | §1.4 | A tool must propagate nothing; `packages.ironclad` is a link to the package's `bin/ironclad`, not the Python package. | The app-mode shell adds every tool to devenv's `packages`, which `mkShell` takes as `nativeBuildInputs`: a propagated python3 runs its setup hook and appends nixpkgs' `jinja2`, `markupsafe`, `packaging` and `tomlkit` to `PYTHONPATH`, which comes before the venv's site-packages, so a bench in the app's shell would import those instead of its `nix/uv.lock` versions. |
| N3a | §1.4 | `pin-path` rehashes a cached `.dev-dist/pins` tree before reusing it and writes no `.narHash` stamp. | The cache lives in the app checkout, so a PR could commit a weakened `frappe-semgrep-rules` tree with a matching stamp and the CI semgrep gate would run it. The trees are small and hashed once per job. |
| N3a | §1.4 | `pin-path` reads frappe-nix's three pins only through the `frappe-nix` node and refuses (exit 2) one that locks any repository but its own (`frappe/semgrep-rules`, `frappe/marketplace`, `frappe/pilot`). | A PR's `flake.lock` could otherwise retarget `frappe-semgrep-rules` at a repository holding `rules: []` with that repository's `narHash`, or add a root input of that name, and the narHash check would pass. Still accepted: a lock edit that moves one of them to another commit of its own repository (an older or newer rev). Such an edit is a visible `flake.lock` diff, and refusing it would need the installed `ironclad` to carry frappe-nix's lock, which Dependabot's lock-only bumps would then have to keep in step. |
| N3a | §3.3 | Unexpected failures exit 3; `github` output fences the text in `::stop-commands::` and escapes the annotations. | Exit 1 is drift, so an uncaught exception in `--check` read as drift in CI. Paths come from `git ls-files` and diffs hold file contents, so unescaped they could break annotations or run workflow commands. |
| N3 | §3.3 | `ironclad sync --write --offline` (or `IRONCLAD_OFFLINE=1`) runs no `nix`, `uv` or `yarn` command; the locks it skips are reported, not drift for the exit code. | The flake checks run sync in the build sandbox, where there is no network, and `frappe-init --app` is checked there too. |
| N3 | §3.3, S32 | `frappe-init` carries `uv`, `yarn`, Node 24 and Python 3.14 on its own `PATH`, so a bootstrap through it never re-enters the dev shell; the re-entry stays the fallback for a bare `ironclad sync --write`, and relocks first when `nix/uv.lock` is missing. | The dev shell evaluates the bench workspace, which needs `nix/uv.lock`: on a fresh app (postgrid, jwt_auth) `nix develop` fails before phase B could create it. |
| N3 | §3.3 step 4 | The re-exec compares the locked frappe-nix's `version.txt` (read from the store with `builtins.fetchTree`) with the running `ironclad.__version__`, and is skipped under `IRONCLAD_FRAPPE_NIX_URL`. | A package built from a source tree does not know its own commit; releases are tags, so equal versions mean the same release. |
| N3 | §3.3 step 3 | `nix flake update frappe-nix` moves frappe-nix to `release-<N>`; under `IRONCLAD_FRAPPE_NIX_URL` every write runs `nix flake lock --override-input frappe-nix <url>`. | `nix flake lock --update-input` is deprecated. |
| N3 | §3.3 step 11 | Relock runs when phase A changed `flake.lock` or `nix/uv.lock` is missing. | Relock writes no header into `nix/uv.lock` (uv owns that file and would drop a comment). |
| N3 | §2.3 | `frappe_nix.major` is 1 while `ironclad.__version__` is `0.x` (before `v1.0.0`). | Otherwise every pre-release render pins `release-0`. |
| N3 | §2.12 | `[tool.vulture] exclude` is managed. A missing `fail_under` is seeded as 0 (then the app's). | test_utils' `static_analysis` runs vulture over `.` and reads this table; without it, `tools/.venv` (made by every `uv run --project tools`) and `.frappe-nix/` are reported as the app's dead code and the hook always fails. Sync can't choose a coverage floor, and a new app must not fail its own first check. |
| N3 | §2.4.1, §2.12 | `requirements.txt` is retired anywhere (exit 1, deleted by sync) rather than forbidden (exit 2). | The two sections disagreed; the retire list is the one sync can act on. |
| N3 | §2.8 | A `devDependencies` entry that names a tarball, a git ref or a path is left as the app wrote it. The fixture app installs frappe-types that way. | It has no minimum to compare with a floor, and frappe-types 16.5.0 (the presets) is not published yet. |
| N3 | §2.9, §2.10 | The `tsconfig*.json` files have no trailing commas; `.oxfmtrc.jsonc` has them. Desk and web projects are rendered when their file set minus the unchecked-js paths and `typescript.exclude` is non-empty; the oxlint overrides use the full sets. `committed.toml` is in oxfmt's form (one type per line). | That is what oxfmt 0.72.0 prints for each extension, and a whole file must already be in oxfmt's form. "After the exclusions" (§2.9) needs the excluded set. |
| N3 | §2.6, §3.6 | `.envrc` carries the standard §3.6 header. | One header text for every `#` file; templates/app/.envrc is the same file. |
| N3 | §3.2 | A local region is checked structurally (hook ids, dependabot `ecosystem`/`directory` pairs, EditorConfig sections); the merged YAML is not parsed. | The package has no YAML parser among its three dependencies; prek, zizmor and actionlint parse it in CI. |
| N3 | §2.4 | A file whose `when` turned false is deleted when it equals the current render or still opens with its managed header and has empty local regions. | The previous context is not known to the run that sees the condition change. |
| N3 | §3.3 step 1 | Creating `[tool.ironclad]` sets `build = true` when `package.json` has a `build` script and nothing sync can see builds (no Vite config, no nested frontend). | Otherwise the created configuration fails its own §2.8 rule (carbon_frappe). |
| N3 | §1.2 | New: `.prekignore` (`tests/fixtures/`); `lib/sh/template.sh` tolerates a template without tokens; the fixture's sources gain test_utils' copyright stamps, a `patches/` package and an `after_request` hook that takes only `response`; `docs/scaffolding/app-mode.md` and `docs/reference/scaffolder.md` describe `--sync` and `--check`. | prek discovers nested configs as projects, so frappe-nix's own lint would run the fixture's hooks. `templates/app` no longer has a token, and `grep` finding none ended the script under pipefail. The fixture must pass its own hooks (`validate_patches` needs a patches directory; vulture flags the unused `request`). |
| N3 | §7 N3 | The fixture's `flake.lock` pins frappe-nix by commit (the N3a head) until `release-1` exists; the self-tests override it. `selftest-scaffold` accepts exit 1 or 2 from carbon_frappe (its develop sets `[tool.ty.rules]` to `warn`, which §2.12 forbids), runs `tsc --build` with a stand-in `marketplace/shots.d.ts` until N5's lands, and the README-clock case is proven on the context (no clock in it), since the README blocks are N5's. | `release-1` and the README templates do not exist yet. |
| N3 | §3.1 | A manifest entry may also carry `handler` (the Python strategy for a merge, block or seed file), `command` (the tool that makes a seeded lock), `phase` (`a` for `flake.nix` and `.envrc`) and `header_note`; a fragment may carry `floors` by kind (`npm`, `npm-ts`, `npm-scss`, `uv`). | The merge and block strategies need per-file rules, and the floors are data every fragment can extend. |
