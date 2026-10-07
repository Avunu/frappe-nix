---
title: Interface spec
description: "The contract the app standards are built to: managed files, profiles, CI gates, tools, versioning and acceptance tests."
order: 12
tags: [app-standards, spec]
updated: 2026-10-07
---

# App Standards and Quality Gates: Interface Spec (frappe-nix v1)

- **Spec version:** 1.2, 2026-10-07. This is 1.1 with two changes: every internal name is replaced by a neutral one (S33), and the standards are opt-in and profile-driven (S34–S38, §8). Appendix C lists what changed from 1.1. Appendix R is the 1.0 → 1.1 review log, and Appendix R2 the review log of this 1.2 text (31 issues, each verified, fixed or rejected with a reason). The spec covers frappe-nix `v1.x`, `[tool.frappe-nix] schema = 1` and profile `schema = 1`.
- **Inputs:**
  - the approved internal project plan (D1–D16, Phases 1 and 1.5, §6, and §7 including the 2026-10-07 defaults). "Plan §N" below refers to that plan, never to this spec;
  - the Phase 0 track results (`p0-results.json`);
  - carbon_frappe on PR #53;
  - frappe-nix `main` at 9e63c96;
  - docusystem v0.1.0;
  - the in-progress frappe-types worktrees (`feat/presets-and-jsdoc`, `feat/gen-doctypes-and-drift-ci`).
- **Audience:**
  - the agents implementing frappe-nix PRs N1–N6 in parallel, and the profile repository P;
  - anyone using frappe-nix app mode, inside or outside Avunu;
  - the 12 Avunu app migrations, which are the first adopters and the worked example.
- **Normative language:** MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119. "Render" means "produce with the template engine in §3". "The app" means a Frappe app repository in frappe-nix app mode. `<app>` is its Python package name, `<App-hyphen>` is that name with `_` replaced by `-`, and `<major>` is the Frappe major (16 in the examples).
- **Opted-in app.** An app is opted in when its `pyproject.toml` has a `[tool.frappe-nix]` table (S35). Everything in §2–§5 applies only to opted-in apps, and within one only to the modules its resolved profile enables (§8). Without the table, `frappe-init` behaves exactly as it does in frappe-nix `main` today.
- **Built-in and org values.** frappe-nix itself carries only vendor-neutral values. Organisation values (publisher, contact e-mail, copyright holder, licence, brand colour, URL templates, GitHub owner, registry fork, README badges and links) come from a profile or the app's config (S37). Where this spec names an Avunu value, the text says "Avunu profile" or "Avunu fleet": those values live in `Avunu/frappe-standards-profile` (§8.6), not in frappe-nix.

---

## 0. Decisions this spec makes

These either settle points the plan leaves open or resolve contradictions the Phase 0 tracks found. Every later section assumes them. S1–S32 are from 1.1, reworded only where the profile model changes them; S33–S43 are new in 1.2. A decision about a module applies only while that module is enabled (§8.2).

| # | Decision | Why |
|---|---|---|
| S1 | **ruff is pinned once, in `tools/uv.lock`.** It runs as `repo: local` hooks through `uv run --frozen --project tools ruff …`. The remote `astral-sh/ruff-pre-commit` hook is forbidden in apps. | The ssort-prek track found D3 and A.4 contradict each other. The uv lock is what carbon_frappe already uses, and one pin then serves the hook, CI, `yarn lint:py` and editors. dependabot `uv` (/tools) is the single mover, so a remote rev and a lock can never disagree. All write-mode hooks give the ssort → isort → lint → format order D4 needs. To keep it the only ruff **in an opted-in app**, N1's `ensure-root` (`lib/frappe-workspace.py`, which adds `ruff>=0.15.0`, `pre-commit>=4.5.1` and `semgrep` to a new root's dev group today) drops each of the three from the generated app-mode root only when the app's tracked `tools/pyproject.toml` pins its replacement: `ruff` when it lists `ruff`, `semgrep` when it lists `semgrep`, `pre-commit` when it lists `prek`. That file exists only in an opted-in app with those modules on (§2.15). Every other root keeps them: a non-opted-in app-mode root, every bench-mode root, and `templates/bench/pyproject.toml`, from which N1 removes nothing (it only adds `coverage` and `unittest-xml-reporting`, which frappe-test needs; S35). |
| S2 | **ssort stays a remote hook.** It is `bwhmather/ssort` with rev floor `0.17.0`, `language_version: python3.14`, and the narrow exclude `'/doctype/([^/]+)/\1\.py$'`. dependabot `pre-commit` moves it. | This is D3 and A.4 as written. prek 0.5.5 accepts the backreference (ssort-prek track). |
| S3 | **The required check contexts are fixed names, one per gate:** `ci / pr-policy`, `ci / lint`, `ci / typecheck`, `ci / test` and `ci / marketplace`, each with `integration_id: 15368`. An app requires exactly the contexts of the gates its profile enables (S39); a context's name never depends on which other gates are on. | A job of a reusable workflow reports as `<caller job> / <job name>` (github-capabilities track). Every caller job that calls an `app-*.yml` gate carries `name: ci`, and every reusable job name equals the gate's name. |
| S4 | **`pr-policy` has its own caller file** (`.github/workflows/pr-policy.yml`), separate from `ci.yml`. | It must re-run on `edited`, when a PR is retitled. If `ci.yml` itself re-ran on `edited` and skipped `test`, the skipped `ci / test` would replace an earlier failure, and GitHub treats skipped as passing. |
| S5 | **frappe-nix reaches an app with CI callers only through `frappe-nix repo rollout`** (§5.8) or the equivalent manual `nix flake update frappe-nix && frappe-init --sync` commit, made with the user's `gh` token (scope `workflow`). The same holds for an org profile input (S38). The app's flake input is `github:Avunu/frappe-nix/release-1`. `frappe-init --sync` rewrites the caller workflows' `uses: …@<sha> # vX.Y.Z` from `flake.lock`. **Both** dependabot entries ignore frappe-nix: `github-actions` ignores `Avunu/frappe-nix`, and `nix` ignores the input `frappe-nix`. Dependabot `nix` still moves `frappe` and the siblings, which never touch a workflow file. No workflow ever pushes a commit that changes `.github/workflows/*` (S29). | This changes D9 on purpose. A frappe-nix bump has to rewrite the caller SHAs, and GitHub refuses a `GITHUB_TOKEN` push that creates or changes a workflow file ("refusing to allow a GitHub App to create or update workflow … without `workflows` permission"). `permissions:` can't grant that scope, so relock-push could never finish a frappe-nix bump. One pin plus sync still keeps the workflow SHA and the tool versions identical by construction. The nightly `drift` job and audit row A10 report when an app is behind. A later MINOR can automate the bump if the N4 spike in §4.5 proves a path that works without a PAT. |
| S6 | **frappe-nix stays on `main`.** Its releases are tags `vX.Y.Z`, and a moving branch `release-<major>` (`release-1`) is fast-forwarded to each new tag. | Apps need a ref that only moves on a release, so dependabot nix proposes releases rather than every `main` commit. frappe-nix has no Frappe major to track, so `version-*` branches have no meaning for it. |
| S7 | **App parameters live in `[tool.frappe-nix]` in the app's `pyproject.toml`.** Facts that can be read from the tracked tree are discovered at sync time, never declared (§2.2). | This keeps one file, and A.3 already puts the override allow-list in pyproject. The registry and pilot ignore unknown `tool.*` tables. |
| S8 | **Managed files use one of five strategies** (§3.2): `whole`, `whole` with `local` regions, `toml-merge`, `json-merge`, `seed` and `blocks`. JSON and JSONC tool configs are customised **only** through `[tool.frappe-nix.<tool>]` parameters, never through text regions. | Text regions inside JSON need trailing-comma tolerance that oxlint's parser doesn't promise. Parameters can be validated. |
| S9 | **Versions moved by dependabot are floors, not values.** These are pre-commit `rev:`s of third-party hooks, the toolchain `devDependencies` ranges, the versions in `tools/uv.lock`, and third-party `uses:` SHAs in caller files. Sync never lowers one and `--check` accepts anything at or above the floor. | Otherwise every dependabot bump is drift, and sync reverts it. |
| S10 | **One self-contained Python package, `frappe-nix-tools` (import name `frappe_nix_tools`, CLI `frappe-nix`), lives at `frappe-nix/py/frappe_nix_tools`.** It serves both sides. Every data file it reads ships inside the package, under `py/frappe_nix_tools/frappe_nix_tools/data/`: the built-in profiles, the templates, manifest fragments, README templates, node-lock seeds, `known-apps.json`, the schemas and the semgrep rules. It reads them through `importlib.resources`. In the Nix dev shell it is built by `lib/standards/package.nix`. In no-Nix CI jobs it is installed with `uv tool install "frappe-nix-tools @ git+https://github.com/Avunu/frappe-nix@<rev>#subdirectory=py/frappe_nix_tools"`, where `<rev>` is read from the app's `flake.lock`. | `lint`, `typecheck`, `pr-policy` and `marketplace` stay Nix-free (D3), yet they run exactly the tool version the app pins (plan §6 DoD row 3). A wheel built from the subdirectory contains nothing outside it, so any data kept elsewhere in frappe-nix would be missing in CI. |
| S11 | **With `releases.version-scheme = "frappe-major"`, the first release uses a `Release-As: <major>.0.0` footer on the squash commit of the app's adoption PR (Avunu fleet: PR B).** PR B is merged with `gh pr merge --squash --body-file <f>`, where the last paragraph of `<f>` is exactly `Release-As: <major>.0.0`. Before merging, `npx release-please release-pr --dry-run` on PR B's head must propose `<major>.0.0`. The repo setting `squash_merge_commit_message` stays `COMMIT_MESSAGES`. No `release-as` key goes in the config. | carbon_frappe cut 16.0.0 with this footer (3c16b00, a direct commit). In a squash of several commits, `COMMIT_MESSAGES` produces a body of `* subject` blocks, and release-please reads only a trailing footer, so the merge body is set explicitly. A `release-as` key in the config would have to be removed after the release, and until then it is drift. |
| S12 | **There is no separate guard-major commit status.** Under `version-scheme = "frappe-major"` the major rule is part of `frappe-nix compat` (a prek hook in `lint`) and of `pr-policy`, and both run on the dispatched release PR. Under `"semver"` there is no major rule. | Fewer moving parts. The rule then blocks merging through a required check instead of an advisory status. |
| S13 | **The release flow uses no App.** Its parts, for the `develop+version` branching model (under `main+tags`, S41 and §4.4, there is no fast-forward): <ul><li>release-please runs with `GITHUB_TOKEN`;</li><li>`dispatch-ci` runs `ci.yml` and `pr-policy.yml` on the release branch;</li><li>a REST fast-forward moves `version-<major>`, with GitHub Actions (integration 15368) as the only bypass actor on `version-*`;</li><li>release runs on a daily cron;</li><li>a nightly `ci.yml` cron runs on develop;</li><li>a daily `sweep` re-dispatches CI for bot PRs whose head has no `ci / *` runs.</li></ul> | This is plan §7 decision 3 as the github-capabilities track refined it. The org has deploy keys disabled, so a DeployKey bypass isn't available. |
| S14 | **pilot assets are built by `gh workflow run assets.yml --ref version-<major>`,** dispatched after the fast-forward. | pilot's `app-assets.yml` names its release `assets-$GITHUB_REF_NAME`. Called from the release run, which is on `develop`, it would publish `assets-develop`. |
| S15 | **The binary cache is a drop-in, and only trusted runs can write to it.** The caller passes `vars.FRAPPE_NIX_CACHE` (empty, `cachix:<name>` or `attic:<endpoint>/<cache>`) and two secrets: `FRAPPE_NIX_CACHE_TOKEN` (write; org **Actions** secret only) and `FRAPPE_NIX_CACHE_READ_TOKEN` (pull-only; org Actions **and** Dependabot secret; leave it unset for a public cache). The write token reaches a step only when `github.ref == 'refs/heads/<integration branch>' && (github.event_name == 'push' \|\| github.event_name == 'schedule')`, where the integration branch is `branches.integration` (§2.3). Every other run (PRs, Dependabot, dispatched release, relock and sweep runs) gets the read token or none. When `FRAPPE_NIX_CACHE` is empty, the app's Nix jobs use **no** cache action at all; magic-nix-cache is kept only for frappe-nix's own small self-test closures. | Today there is no cache (ci-timing track). Adding one later is an org variable plus secrets, with no app PR. Dependency bumps run third-party code in the same job, so they must never hold a credential that could poison the cache. magic-nix-cache was throttled in 48 of 48 carbon bench and integration jobs, and it competes with the node_modules caches for the repo's 10 GB Actions cache (9.09 GB already used). |
| S16 | **CI mode is `FRAPPE_NIX_CI=1`, with exactly one `nix develop --no-pure-eval -c frappe-test --ci` per job.** CI mode skips the shell hook's node verify and yarn installs and its banner. The clean `nix build .#default` (a second evaluation) runs on PRs only when the diff touches build inputs (§4.2), and always on develop pushes, schedules and dispatches. | The ci-timing track found 2.1–2.2 minutes of yarn per shell entry, about 1.8 minutes of evaluation per extra `nix develop`, and a 7.6-minute p50 for the bench build. |
| S17 | **frappe-test measures coverage itself.** It runs `coverage run --source=<repo>/<app> -m frappe.utils.bench_helper frappe … run-tests` and never uses frappe's `--coverage`. `coverage` goes into the bench root's dev group. | In app mode, frappe's `--coverage` counts `.frappe-nix/bench/apps/{frappe,erpnext,hrms}` (frappe-nix-runtime track). |
| S18 | **The whitelist and hook test check is dynamic.** It reads the evaluated hooks from `frappe.get_hooks(app_name=…)`, and a target counts as tested when at least one line of its body ran during the same coverage run. | Static parsing misses conditional hooks such as esign's `if frappe_version >= 16:` (rename-mechanics track). Coverage data is already there. |
| S19 | **The `test-utils` module (off by default) provides selected agritheory/test_utils hooks only, at rev floor `v1.30.1`.** The ones that work standalone are in the default stage. `validate_customizations` and `clean_customized_doctypes` are `stages: [manual]`, run in `lint` inside a fabricated sparse mini-bench (§5.9), with a guard that fails when the bench layout is missing. The fixtures are not used. | This follows the test-utils track: outside a bench those two hooks are silent no-ops, `sql_registry` aborts the run, and the repo has no license. |
| S20 | **Registry checks are pinned through frappe-nix flake inputs:** `marketplace` (`frappe/marketplace` 2dd4be42…), `pilot` (`frappe/pilot` e2364936…) and `frappe-semgrep-rules`. They reach apps through the app's `flake.lock`. The get-app validator runs with the declared dependency apps on its bench, which mirrors `frappe/marketplace#29`. | The plan wants pinned copies, and frappe-nix's dependabot nix moves them. Without the dependency apps on the bench, 5 apps fail ImportCheck for reasons that aren't theirs (registry-dryrun track). |
| S21 | **The semgrep baseline is a multiset.** An entry is keyed by `(rule, path, sha1(normalised matched line))` and carries a `count`. A key found more often than its count fails, and so does a count that is now too high. Baselines only shrink, and publishing requires an empty one. | Line numbers move. OSS semgrep returns `"requires login"` as the fingerprint. Identical matched lines share a key: erpnext_taskview's `api.py` has 12 identical `frappe.db.commit()` lines, and a set would let one entry cover any number of new ones. |
| S22 | **With the `icons` module on, the `<app>/desktop_icon/<app>.json` fixture is committed**, for application-type apps only. It is generated by `frappe-icon` and checked for freshness. The PNGs are not committed. | This deviates from D14 because frappe v16 imports `desktop_icon/*.json` from the installed package (`import_desktop_icon_fixtures`). A file that isn't committed never ships. |
| S23 | **With the `screenshots` module on, screenshots are made repeatable** by running the bench under libfaketime with a fixed start time (`FAKETIME="@<demo date> 09:00:00"`, so the clock advances from there), plus CDP timezone, locale and media overrides, no animations, masks, and lossless WebP. The refresh PR is never merged automatically. | ERPNext demo data is relative to "today". Freezing the clock outright would hang timeouts. |
| S24 | **Ratchets** are coverage `fail_under`, the `ty: ignore` count, semgrep baseline counts, and the `[[tool.frappe-nix.untested]]`, `[[tool.frappe-nix.unchecked-js]]` and `[[tool.frappe-nix.coverage-omit]]` lists. They are compared against the PR's merge-base (`git merge-base "$BASE_SHA" HEAD`), never the base tip. They apply only once the base already has `[tool.frappe-nix]` (in the Avunu fleet, from PR B onward), and are skipped on integration-branch pushes and schedules. Each ratchet belongs to its module and is off with it. Coverage also ratchets upward: frappe-test fails when coverage has climbed `tests.coverage.raise-margin` points (default 2) past a `fail_under` below `tests.coverage.target` (default 80) (§5.1 stage 4). | The adoption PR (PR B) is the PR that sets each starting value. The base tip would fail a PR that branched before develop raised `fail_under`. Plan decision 5 wants a floor that only rises towards the target, and a notice alone never raises it. |
| S25 | **Release PRs get screenshots from the nightly run, not a dispatched run.** | A release branch differs from develop only in the version and changelog, so a separate run would show nothing new. This adjusts D16. |
| S26 | **The README "Development" block links to the profile value `org.dev-docs-url`.** The built-in default is frappe-nix's own docs in its repository, `https://github.com/Avunu/frappe-nix/tree/main/docs/app-standards`. The Avunu profile sets `https://frappe-nix.avunu.net/docs/app-standards/`. | A link to frappe-nix's own repository is not an org value; a site an organisation hosts is. Avunu's site exists and returns 200; the profile changes it in one profile release when avunu-docs gets a public URL. |
| S27 | **`ruff` rules.** With `python-lint` on, an app gets these values from the `recommended` profile (carbon_frappe's set), each one a parameter (§8.2): <ul><li>`select = F,E,W,I,UP,B,RUF,SIM,C4,PIE,PERF,T20`;</li><li>`ignore` is exactly `E501`, `W191`;</li><li>tabs, 110 columns, `typing-modules = ["frappe.types.DF"]`.</li></ul> frappe-nix's own Frappe-tracking ruff config (v0.14.10) is unrelated and unchanged. | This is D4. The completeness critic confirmed it isn't a user decision. |
| S28 | **`[tool.bench.frappe-dependencies]` ranges always use the comma form** `">=16.0.0,<17.0.0"`. `payments` is `">=0.0.1,<1.0.0"`. | `packaging.SpecifierSet`, which pilot uses, rejects the `-dev` space form (completeness critic). payments' `version-16` `__version__` is 0.0.1 (registry-dryrun track). |
| S29 | **No workflow pushes a commit that changes `.github/workflows/*`.** relock-push, relock-upgrade and nightly `shots` refuse a patch that touches that path (exit 1, naming the file). Moving `version-*` and `release-*` with a REST ref PATCH to a commit that already exists on develop or main is allowed. | GitHub refuses such pushes from `GITHUB_TOKEN`. The REST fast-forward is proven: carbon_frappe's `version-16` was moved by github-actions[bot] from 1ed9df5 to 30a4be4 across workflow changes that were already on develop. |
| S30 | **Vite outputs are registered by the app's own build, not only by Nix.** Every app with a tracked Vite config gets the managed `scripts/vite-register.mjs`, and its `build` script must end with `node scripts/vite-register.mjs`. frappe-nix's esbuild-preload runs the same module (§5.11), and running both is idempotent. | frappe's esbuild.js writes `assets.json` and then runs each app's `yarn build`, so a post-build step in the app works on a stock bench: Frappe Cloud, pilot's get-app bench and registry installs. Without it, `taskview.bundle.js`, `timerdock.bundle.js` and timeclock's bundle 404 everywhere except Nix. |
| S31 | **Legacy files are retired by sync.** A `retire` list in the manifest names files that sync deletes and that `--check` reports as drift (§2.4.1). | Otherwise carbon's and taskview's old `check.yml`, `release-please.yml` and `dependabot-auto-merge.yml` keep running beside the new callers: two release flows race for tags, and an auto-merger ignores `needs-review`. |
| S32 | **Sync bootstraps in two phases** (§3.3): phase A writes `flake.nix` and `.envrc`, locks, and re-reads the frappe-nix rev; phase B renders everything else. App migrations start only after frappe-nix `v1.0.0` is tagged, because `release-1` doesn't exist before that. | postgrid_integration and jwt_auth have no flake; carbon, taskview and timeclock lock frappe-nix `main`. A single pass would render the callers with a rev that the same run then changes. |
| S33 | **Neutral names only.** The concept is "app standards" (docs section "App standards and quality gates", `docs/app-standards/`). The Python project is `py/frappe_nix_tools` (distribution `frappe-nix-tools`, import name `frappe_nix_tools`) with the CLI entry point `frappe-nix`; the former stand-alone apply, audit and rollout tools are `frappe-nix repo apply\|audit\|rollout`. The app table is `[tool.frappe-nix]`; markers are `frappe-nix:managed`, `frappe-nix:local-begin <name>` and `frappe-nix:local-end <name>`; repo policy JSON lives in `repo-policy/`; the cache variables are `FRAPPE_NIX_CACHE`, `FRAPPE_NIX_CACHE_TOKEN` and `FRAPPE_NIX_CACHE_READ_TOKEN`, and every other tool variable is `FRAPPE_NIX_*`. The conventional-commit scope for this work is `standards`, and its branches start with `standards/`. No public artifact (code, names, tables, markers, docs, workflows, variables, branches, PR text, commit messages) uses the internal project codename. | frappe-nix is used outside Avunu. An internal codename in a config table or a marker means nothing to those users and leaks an internal plan. §9 lists what the in-flight PRs rename. |
| S34 | **Standards are profile-driven.** A profile is a TOML file (§8.1) that enables modules and sets their parameters and the org values. frappe-nix ships two built-in profiles, `minimal` (the dev shell only) and `recommended` (vendor-neutral defaults, shipped as frozen snapshots, S42). An org profile layers on top of one built-in. The resolved configuration is the deep merge built-in < org profile < the app's `[tool.frappe-nix]` (§8.4). | Other agencies and developers use frappe-nix with their own conventions. One file per organisation, pinned like any other input, keeps the fleet consistent without frappe-nix carrying any organisation's choices. |
| S35 | **Opt-in, never by default.** Without `[tool.frappe-nix]`, `frappe-init --app` writes only `flake.nix`, `.envrc` and `.gitignore`, exactly as frappe-nix `main` does, and `frappe-init --sync`/`--check` refuse with exit 2 and a hint. An app opts in by adding the table or with `frappe-init --standards <profile>`, which creates it. A table without `profile` means `profile = "minimal"`. The opt-in also gates what the dev shell adds: `frappe-nix`, nixfmt/statix/deadnix and the `blame.ignoreRevsFile` setting appear only in an opted-in app's shell. `lib/standards/shell.nix` decides this with a **line match, never a TOML parse**: it reads `pyproject.toml` as a string and tests for a line matching `^\[tool\.frappe-nix[].]` (`builtins.split` plus `builtins.match`). Nix's `fromTOML` rejects valid TOML (datetimes) and its errors escape `builtins.tryEval`, so a parse would break evaluation for apps that never opted in. The same opt-in gates the bench root's dev-group trim (S1), the worktree port salt (§5.12) and the `docs-site` node-target exclusion (§5.11). The runtime changes that apply to every app-mode user are `FRAPPE_NIX_CI` (inert unless set) and the Vite registration in the Nix preload, which only adds `assets.json` keys for hashed Vite bundles that 404 today (§5.11; Q7). One more change reaches every app's repository but not its shell or outputs: on its next frappe-nix update, the app's `flake.lock` gains the three `flake = false` nodes S20 adds (`marketplace`, `pilot`, `frappe-semgrep-rules`, about 9 MB fetched once). Its dev shell, packages, apps and checks stay exactly `main`'s. | Existing app-mode users must see no change from upgrading frappe-nix. `minimal` is the smallest step that still puts the app under sync. |
| S36 | **Every gate and module is individually toggleable, and a file or job exists only for an enabled module.** Each manifest entry names its module (§2.4); a caller job exists only for an enabled gate (S39). Turning a module off makes sync delete its whole files and retract its merged keys (§3.3 step 6). Modules that need each other say so (§8.2), and an inconsistent combination is exit 2, never a silent fix. Tool choices are parameters where more than one tool is reasonable (`js.tool = "oxc" \| "none"`). | Teams adopt standards piecemeal. A module that can't be switched off forces a fork of frappe-nix. |
| S37 | **No organisation value is hard-coded in frappe-nix.** Publisher, contact e-mail, copyright holder, licence, brand colours, website and docs URL templates, GitHub owner, registry fork, README badges and links come from `[org]` in a profile or the app's `[tool.frappe-nix.org]` (§8.1). Built-in profiles leave them empty, and a module that needs an empty value is exit 2 naming the key. A frappe-nix nix check (`standards-vendor-neutral`, §7) fails when an Avunu value (`Avunu LLC`, `avunu.net`, `avu.nu`, `#834AFF`, any `Avunu/` other than `Avunu/frappe-nix` and `Avunu/frappe-runtime`, …) appears in a built-in profile, template, manifest, schema, `known-apps.json`, `repo-policy/` default, the frappe-nix-tools Python code, `lib/**`, or the reusable `app-*.yml` and `fleet-audit.yml` workflows. Docs may show such values only inside a block marked as the Avunu example. | The same tool must render a correct app for any organisation. A check is the only way the rule survives later edits. |
| S38 | **Org profiles live in their own repository and reach an app as a pinned flake input.** `profile = "github:<owner>/<repo>[/<ref>]"` (or a `gitlab:` or `git+https:` flake URL, §8.3) makes sync's phase A add the input `standards-profile` (`flake = false`) to `flake.nix` and lock it. A single-app team that has no profile repository writes `profile = "./<dir>"` instead: an in-repo profile with the same layout, versioned with the app itself (§8.3). The profile is read from the locked tree (`frappe-nix pin-path standards-profile` in no-Nix CI). Dependabot ignores the input, as it does frappe-nix, because a profile change can rewrite workflow files (S29); it moves with `frappe-nix repo rollout --profile-to` or a manual `nix flake update standards-profile && frappe-init --sync` commit. Avunu's choices are the profile `Avunu/frappe-standards-profile` (§8.6), created by item P (§1.3), which frappe-nix does not own. | The profile then moves through the same reviewed, versioned path as frappe-nix itself, and every app commit renders from exactly one profile revision. |
| S39 | **CI callers contain only enabled gates.** The single `app-ci.yml` of 1.1 becomes one reusable workflow per gate: `app-lint.yml`, `app-typecheck.yml`, `app-test.yml` and `app-marketplace.yml` (§4.2). The rendered `ci.yml` has one caller job per enabled gate (job ids `lint`, `typecheck`, `test`, `marketplace`), each with `name: ci`, so the contexts stay `ci / <gate>`. `pr-policy.yml`, `release.yml`, `deps.yml`, `nightly.yml` and `assets.yml` exist only when their module is on. The required checks a ruleset lists are computed from the same enabled set (§4.8). | A disabled gate should leave no skipped job, which GitHub would show and which a ruleset would treat as passing. Stable context names mean turning a gate on later never renames another one. |
| S40 | **Repo policy is optional.** `repo-policy.enable` (off in `recommended`) decides whether `frappe-nix repo apply` manages an app's settings and rulesets and whether audit rows A1 and A3 apply. The fleet list that `--all` iterates is not part of frappe-nix: it is `fleet.json` in an org profile repository, passed with `--fleet` (§5.6). The org-wide scorecard workflow lives there too. | Applying rulesets needs repo admin and encodes review policy, which differs between teams. A fleet list is an organisation's inventory. |
| S41 | **The branching model and the version scheme are parameters.** `releases.branching` is `"develop+version"` (integration branch `develop`, release branch `version-<major>` fast-forwarded to each tag; the Avunu choice and the Frappe convention) or `"main+tags"` (integration branch `main`, tags only). `releases.version-scheme` is `"frappe-major"` (the app's major equals the Frappe major, with the S12 rule) or `"semver"`. Every `develop` and `version-<major>` in §2–§5 is the context value `branches.integration` or `branches.release` (§2.3). The integration branch's name is its own app key, `[tool.frappe-nix] integration-branch`, which defaults to the branching model's (`develop` or `main`) and may name any branch (`master`, `dev`, `trunk`), with or without `releases` (§2.1). | Both models are common among Frappe app publishers. Encoding one in templates would force it on everyone. |
| S42 | **Built-in profiles are versioned snapshots.** `recommended` ships as frozen files `recommended@<frappe-nix minor>` (`recommended@1.0` first). A snapshot never changes after its release, apart from fixes that make it accept strictly more code. `profile = "recommended"` means the newest snapshot of the running frappe-nix-tools; `profile = "recommended@1.0"` (and an org profile's `extends = "recommended@1.0"`) stays on that snapshot through every later `v1.x`. `minimal` is frozen for all of v1 and has no snapshots. | A MINOR may tighten `recommended` (§6.3). Without snapshots, taking a frappe-nix bug fix would also turn on new gates, and the only way to stay stable would be to maintain an org profile repository. |
| S43 | **The gates are frappe-nix commands; GitHub is one host.** Each no-Nix gate's steps live in `frappe-nix gate lint\|typecheck\|marketplace` (N4), which any CI runs; the GitHub reusable workflows only set up node, uv and the tools and then call it. The `test` gate is already `frappe-test --ci`. Siblings, org profiles, `pin-path` and `minibench` accept `github:`, `gitlab:` and `git+https:` sources, and use the optional fetch token `FRAPPE_NIX_FETCH_TOKEN` for private ones. The modules that are GitHub-only in v1 are listed in §8.2; on another host they resolve to off. | Other hosts must be able to run the same checks without copying step lists out of GitHub YAML. Agencies keep client apps and shared libraries private. |

---

## 1. Repository layout in frappe-nix, and PR ownership

### 1.1 Landing order

1. **N3a "hook points"** lands first. It is a small PR, owned by the N3 agent, that only creates seams. It MUST merge before any other N-PR is rebased for merge. Every other PR MAY start before it merges, by branching from N3a's branch.
2. **Development** of N1, N2, N3 (the rest), N4, N5 and N6 proceeds in parallel after that.
3. **Merge order** follows what each PR's acceptance tests exercise:

   | Step | PRs | Why |
   |---|---|---|
   | 1 | N3a | Seams, the data directory, the `[tool.frappe-nix]` and profile schemas, the two built-in profiles. |
   | 2 | N1 and N3, in either order | N1 needs only N3a's loaders and the resolved-config reader. N3 is the sync engine and the profile resolver everything renders through. |
   | 3 | N2 and N5, after N3 | N2's managed `scripts/vite-register.mjs` and N5's README blocks and `marketplace.json` fragment render through N3's engine. |
   | 4 | N4, after N1, N3 and N5 | `selftest-ci` runs `frappe-nix sync --check` (N3), `frappe-test --ci` (N1) and `frappe-nix listing check` (N5). |
   | 5 | N6 | frappe-nix's own `pr-policy.yml` runs `frappe-nix policy` (N4). N6's PR may merge earlier if its policy job is temporarily non-required. |
   | 6 | tag `v1.0.0` | N6's first release PR is merged only after N1–N5 are in, so `v1.0.0` is the first complete platform and `release-1` exists. |

   - The **frappe-nix `main` ruleset** (`repo-policy/self/rulesets/main.json`) is applied with `frappe-nix repo apply` only after N4 merges, because it uses N4's tool.
   - **P, the Avunu org profile** (`Avunu/frappe-standards-profile`, §8.6), is created once N3 has merged, because `frappe-nix profile validate` (N3) checks it. Its `v1.0.0` tag is made after frappe-nix `v1.0.0`, against which its CI validates it.
   - **App migrations** in the Avunu fleet (PR A, PR B) start only after frappe-nix `v1.0.0` and P's `v1.0.0` are tagged (S32, S38). Apps outside Avunu opt in whenever they choose, with any profile.

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
│          `packages`/`apps` merged with `import ./lib/standards/outputs.nix`;
│          `checks` merged with `import ./tests/standards { … }`
│     nobody else edits flake.nix
├── flake.lock                                 [N3a for the new inputs; afterwards dependabot]
├── py/frappe_nix_tools/                       Python project `frappe-nix-tools`, import name `frappe_nix_tools`,
│   │                                          console script `frappe-nix` (S10, S33); self-contained
│   ├── pyproject.toml                         [N3a]  deps: jinja2>=3.1,<4; tomlkit>=0.13 (no upper cap: the
│   │                                                  locked nixpkgs ships 0.15.0); packaging>=24.
│   │                                                  [project.scripts] frappe-nix = "frappe_nix_tools.cli:main";
│   │                                                  flit's include ships frappe_nix_tools/data/**
│   ├── frappe_nix_tools/__init__.py           [N3a]  release-please version block (N6 adds it to extra-files)
│   ├── frappe_nix_tools/cli.py                [N3a]  `frappe-nix <command>`; imports every module in commands/;
│   │                                                  `repo` is a command group (apply, audit, rollout)
│   ├── frappe_nix_tools/common/               [N3a]  repo.py, flakelock.py, pyproject.py ([tool.frappe-nix] loader),
│   │                                                  config.py (profile resolution and the merge, §8.4),
│   │                                                  known_apps.py, gh.py (thin `gh api` wrapper), report.py,
│   │                                                  pins.py (fetch a flake-locked GitHub tree into .dev-dist/pins/
│   │                                                  and verify its narHash), nar.py
│   ├── frappe_nix_tools/commands/paths.py     [N3a]  `frappe-nix data-path <rel>` (importlib.resources over
│   │                                                  frappe_nix_tools/data), `frappe-nix pin-path <input>` for any locked
│   │                                                  GitHub input: frappe-nix's (frappe-semgrep-rules, marketplace,
│   │                                                  pilot), the app's own (frappe, erpnext, …) or `standards-profile`
│   ├── frappe_nix_tools/commands/config.py    [N3a]  `frappe-nix config <key>` (prints a resolved value, §8.4)
│   ├── frappe_nix_tools/data/                 everything sync, listing and the hooks read at run time (S10):
│   │   ├── profiles/minimal.toml              [N3a creates both in full from §8.5; then N3; others ask N3]
│   │   ├── profiles/recommended@1.0.toml      [N3a, then N3]  frozen once v1.0.0 is tagged; a later MINOR that
│   │   │                                              changes `recommended` adds recommended@1.<minor>.toml (S42)
│   │   ├── tsconfig/base.json                 [N3]   the inline TypeScript preset (`typescript.preset = "inline"`, §2.9)
│   │   ├── schema/tool-frappe-nix.schema.json [N3a; N3 extends it, others ask N3]
│   │   ├── schema/profile.schema.json         [N3a; N3 extends it, others ask N3]
│   │   ├── known-apps.json                    [N3a]  (frappe, erpnext, hrms, payments only; §2.1)
│   │   ├── manifest.d/core.json               [N3]
│   │   ├── manifest.d/assets.json             [N2]   (scripts/vite-register.mjs)
│   │   ├── manifest.d/ci.json                 [N4]
│   │   ├── manifest.d/marketplace.json        [N5]
│   │   ├── templates/**                       [N3]   except templates/.github/** [N4], templates/marketplace/** [N5],
│   │   │                                              templates/scripts/vite-register.mjs [N2]
│   │   ├── readme/*.md.j2                     [N5]
│   │   ├── node-locks/version-16/{frappe/ui,erpnext/banking,hrms/frontend,hrms/roster}/{yarn.lock,source.json} [N3]
│   │   ├── semgrep/test-correctness.yml       [N4]   (carbon_frappe's semgrep/test-correctness.yml)
│   │   ├── ci/nightly.sh                      [N4]   (§4.6)
│   │   └── shots/shots.d.ts                   [N5]   (copy of lib/shots/shots.d.ts; a nix check asserts equality)
│   ├── frappe_nix_tools/commands/sync.py      [N3]   `frappe-nix sync --write|--check`
│   ├── frappe_nix_tools/commands/profile.py   [N3]   `frappe-nix profile show|validate` (§5.13)
│   ├── frappe_nix_tools/commands/compat.py    [N3]
│   ├── frappe_nix_tools/commands/unchecked_js.py [N3] `frappe-nix unchecked-js --stale` (§2.9)
│   ├── frappe_nix_tools/commands/testmap.py   [N1]   plus frappe_nix_tools/bench/{testmap_probe,composition}.py [N1]
│   ├── frappe_nix_tools/commands/policy.py    [N4]
│   ├── frappe_nix_tools/commands/ratchet.py   [N4]
│   ├── frappe_nix_tools/commands/minibench.py [N4]
│   ├── frappe_nix_tools/commands/gate.py      [N4]   `frappe-nix gate lint|typecheck|marketplace` (S43, §4.2)
│   ├── frappe_nix_tools/commands/repo/{apply,audit,rollout,doctor}.py [N4]  `frappe-nix repo apply|audit|rollout|doctor`
│   ├── frappe_nix_tools/commands/listing.py   [N5]   plus frappe_nix_tools/listing/** [N5]
│   ├── frappe_nix_tools/commands/icon.py      [N5]   plus frappe_nix_tools/icon/** [N5]
│   └── tests/test_<module>.py                 [owner of <module>]
├── lib/standards/
│   ├── package.nix                            [N3a]  python314 build of py/frappe_nix_tools against the locked nixpkgs
│   │                                                  (pythonRuntimeDepsCheck stays on; a nix check builds it)
│   ├── outputs.nix                            [N3a]  reads lib/standards/tools/*.nix → {packages, apps}
│   ├── shell.nix                              [N3a]  app-mode dev-shell packages and enterShell snippet; adds
│   │                                                  `frappe-nix` only to opted-in apps' shells; exports the
│   │                                                  opt-in test (a line match, never fromTOML; S35) as
│   │                                                  `optedIn` for modules/devenv.nix
│   └── tools/
│       ├── frappe-nix.nix                     [N3a]  `frappe-nix` with git and gh on its PATH (the `repo` group needs gh)
│       └── frappe-listing.nix, frappe-icon.nix, frappe-shots.nix          [N5]
├── lib/scripts.nix  (shared)                  N3a: one line merging lib/scripts.d/*.nix; nobody else
├── lib/scripts.d/
│   ├── frappe-test.nix, frappe-rename-app.nix [N1]
│   └── frappe-demo.nix, frappe-shots.nix      [N5]
├── lib/sh/frappe-test.sh                      [N1]
├── lib/sh/app-sync.sh                         [N3]
├── lib/sh/{main.sh,app-init.sh}               [N3]   (new flags: --sync, --check, --standards, --format, --only)
├── lib/init.nix                               [N3]   (adds app-sync.sh and the frappe-nix-tools runtime input)
├── lib/rename/frappe_rename_app.py            [N1]   (ported from the Phase 0 rename-mechanics prototype)
├── lib/demo/frappe_demo.py                    [N5]
├── lib/shots/{cdp.ts,runner.ts,diff.ts,shots.d.ts,package.json,yarn.lock} [N5]
├── lib/js/esbuild-preload.js                  [N2]   (requires lib/js/vite-register.cjs)
├── lib/js/vite-register.cjs                   [N2]   the one registration implementation (S30); the managed
│                                                      scripts/vite-register.mjs is a build-time copy of it
├── lib/node-targets.nix, lib/node-locks.nix   [N2]   (the `excludeNodeTargets` option, §5.11)
├── lib/frappe-workspace.py                    [N1]   (`coverage` in the generated root's dev group; `ruff`,
│                                                      `pre-commit` and `semgrep` dropped only from an opted-in
│                                                      app-mode root whose tools/pyproject.toml pins them, S1)
├── templates/bench/pyproject.toml             [N1]   adds `coverage` and `unittest-xml-reporting` only; ruff,
│                                                      pre-commit and semgrep stay (S1, S35)
├── modules/devenv.nix  (shared)
│     N3a: app-mode `apps.frappe-init`; import of lib/standards/shell.nix
│     N1:  port offset salt (opted-in apps, or `ports.worktreeSalt = true`) and FRAPPE_NIX_PORT_OFFSET;
│          FRAPPE_NIX_CI in enterShell; nixfmt/statix/deadnix in an opted-in app-mode shell; frappe-nix.renamedApps
├── modules/nixos.nix                          [N1]   (services.frappe.sites.<site>.renamedApps)
├── templates/app/
│   └── flake.nix, .envrc, .gitignore          unchanged from frappe-nix `main` (S35). `frappe-init --app` without
│                                                      `--standards` copies exactly this, as today. N3 MUST NOT change
│                                                      these files; the opted-in forms are rendered by the engine from
│                                                      data/templates/{flake.nix.j2,envrc.j2,gitignore.block} (§2.5, §2.6)
├── repo-policy/                               vendor-neutral defaults, parameterised by the resolved config (§4.8, §4.9)
│   ├── repo-settings.json                     [N4]
│   ├── rulesets/{integration,integration-provisional,release-branch,tags}.json   [N4]
│   └── self/{repo-settings.json,rulesets/{main,release,tags}.json} [N6]   (frappe-nix's own repository)
├── .github/
│   ├── workflows/app-lint.yml, app-typecheck.yml, app-test.yml, app-marketplace.yml,
│   │   app-pr-policy.yml, app-release.yml, app-deps.yml, app-nightly.yml   [N4]   reusable (S39)
│   ├── workflows/fleet-audit.yml              [N4]   reusable (`workflow_call` only): the scorecard an org
│   │                                                  schedules from its profile repository (§5.7)
│   ├── workflows/selftest-runtime.yml         [N1]
│   ├── workflows/selftest-assets.yml          [N2]   (stock-bench Vite registration, §7)
│   ├── workflows/selftest-scaffold.yml        [N3]
│   ├── workflows/selftest-ci.yml              [N4]
│   ├── workflows/selftest-product.yml         [N5]
│   ├── workflows/check.yml  (shared)
│   │     N3a: the "offline checks" step builds `.#checks.x86_64-linux.standards-all` in addition to its list
│   │     N6:  SHA-pin actions/checkout; add a `vm-tests` boolean dispatch input (default false) and
│   │          gate the vm-tests job on `inputs.vm-tests == true` instead of on any dispatch
│   ├── workflows/pr-policy.yml                [N6]   frappe-nix's own, non-reusable `pr-policy` job (§4.10)
│   ├── workflows/release.yml                  [N6]
│   ├── workflows/dependabot-auto-merge.yml    [N6]   (SHA pin; skip rules unchanged)
│   └── dependabot.yml                         [N6]
├── tests/standards/default.nix                [N3a]  reads tests/standards/*.nix; exposes `standards-all` (linkFarm)
├── tests/standards/vendor-neutral.nix         [N3a]  the S37 check, with tests/standards/vendor-denylist.txt [N3a]
│                                                      (the denylist names org values only, never the codename; §7 N3a)
├── tests/standards/<area>.nix                 [owner of <area>] (sync, profiles, compat, ports, preload-vite,
│                                                       node-targets-docs-site, policy, ratchet, listing, icon, …)
├── tests/fixtures/standards-app/              [N3a: app sources; N3: the managed files sync renders, committed]
├── tests/fixtures/profiles/                   [N3]   an org-profile fixture (`example-org`, fictitious values, §7 N3);
│                                                      [N4] `docs-compat`, which sets the docs tool's own identifiers (§7 N4)
├── tests/fixtures/spa-app/                    [N2]   (root-level Vite config, a `portal/` SPA with no package.json,
│                                                      `[[tool.frappe-nix.typescript.spa]]` entries)
├── tests/fixtures/rename-esign/               [N1]   (hooks naming `esign.desk.bundle.js`, the matching files)
└── docs/app-standards/                        docs section "App standards and quality gates"
    ├── README.md (index, links to all pages)  [N3a]
    └── <page>.md                              [N3a creates one-line stubs; each owner fills its page:
                                                profiles.md N3, managed-files.md N3, testing.md N1, assets.md N2,
                                                ci.md N4, github.md N4, marketplace.md N5, icons.md N5,
                                                screenshots.md N5, versioning.md N6, rename.md N1]
```

Outside frappe-nix:

```
Avunu/frappe-standards-profile/                [P; owned and released by Avunu, not by any N-PR]
├── profile.toml                               the Avunu org profile (§8.6)
├── templates/                                 optional template overrides (§8.1)
├── fleet.json                                 the Avunu fleet that `frappe-nix repo … --all` iterates (§4.9)
├── resolved.snapshot.json                     `frappe-nix profile show --format json` of the fixture app (§6.4)
├── repo-policy/                               P's own settings and rulesets: main.json, release.json (`refs/heads/v*`),
│                                              tags.json, applied with `frappe-nix repo apply --policy-dir` (§5.6)
├── .github/workflows/fleet-audit.yml          caller of frappe-nix's reusable fleet-audit.yml, nightly (§5.7)
├── .github/workflows/validate.yml             on every PR: `frappe-nix profile validate profile.toml`, the snapshot
│                                              comparison, and `docusystem doctor` on a synced copy of the fixture app
├── .github/workflows/release.yml              release-please, then a REST fast-forward of `v1` (§6.4)
└── release-please config, CHANGELOG.md        tags vX.Y.Z and a moving `v1` branch (§6.4)
```

### 1.3 What each PR delivers

- **N3a, hook points.** It delivers:
  - the seams listed above;
  - `frappe-nix --version` and the command dispatcher;
  - the `py/frappe_nix_tools/frappe_nix_tools/data/` layout, `frappe-nix data-path`, and `data/known-apps.json`;
  - the two built-in profiles in full (§8.5), `schema/profile.schema.json`, and the resolver `common/config.py` with `frappe-nix config` reading resolved values, so every later PR reads module switches the same way (§8.4);
  - the `standards-vendor-neutral` nix check and its denylist (S37);
  - the fixture app sources in `tests/fixtures/standards-app/`: an app `standards_fixture` with one DocType, one whitelisted function with a test, one deliberately untested whitelisted function listed in `[[tool.frappe-nix.untested]]`, `doc_events` on ToDo, one scheduler job, `extend_doctype_class` on ToDo, `demo.py`, `marketplace/listing.toml`, the icon pair and `marketplace/screenshots.ts`;
  - the new flake inputs;
  - the docs stubs, under `docs/app-standards/`.

  It has no behaviour of its own.
- **N1, runtime.** It delivers:
  - `frappe-test`;
  - coverage scoping;
  - `coverage` in the bench root dev group;
  - the testmap and composition checks;
  - `FRAPPE_NIX_CI`;
  - nixfmt, statix and deadnix in an opted-in app-mode shell;
  - the worktree port salt and `FRAPPE_NIX_PORT_OFFSET`, with the documentation corrections;
  - `frappe-rename-app` (code and site halves) and the `renamedApps` and `replacedApps` NixOS and devenv options;
  - the `shell-checks` stage of `frappe-test --ci`;
  - ruff, pre-commit and semgrep dropped from the generated root's dev group of an opted-in app whose `tools/pyproject.toml` pins their replacements, and from no other root (S1);
  - `selftest-runtime.yml`.

  N1 MAY split the rename work into a follow-up PR, N1b.
- **N2, assets.** It delivers:
  - Vite manifest registration as one module, `lib/js/vite-register.cjs`, run by `esbuild-preload.js` (with an unconditional `child_process` wrap) and shipped to apps as the managed `scripts/vite-register.mjs` (S30), plus its `manifest.d/assets.json` fragment and the `frappe-nix compat` rule C8;
  - the `excludeNodeTargets` option in `node-targets.nix` and `node-locks.nix` (default empty; an opted-in app's `flake.nix` sets `[ "docs-site" ]` while the `docs-site` module is on, §5.11);
  - the `tests/fixtures/spa-app/` builtBench check;
  - `docs/app-standards/assets.md`, which covers the `<name>.bundle.[hash]` naming convention, `bundled_asset()` in www templates and `[tool.bench.assets]` for pilot.
- **N3, scaffold.** It delivers:
  - the sync engine, including the two-phase bootstrap (S32), the `retire` list (S31), module gating and retraction (S36, §3.3 step 6), and template overrides (§8.1);
  - org profile resolution: the `standards-profile` flake input in phase A, `pin-path` of it in no-Nix CI, `frappe-nix profile show|validate` (§5.13), and `docs/app-standards/profiles.md`;
  - every template in `manifest.d/core.json`;
  - `frappe-init --sync`, `--check` and `--standards <profile>`; `templates/app/` left unchanged, and `cmd_app_init` delegating to `frappe-nix sync --write` only for an opted-in app (S35);
  - `frappe-nix compat`;
  - node-lock seeding;
  - the committed managed files of the fixture app;
  - `selftest-scaffold.yml`.
- **N4, CI.** It delivers:
  - the eight reusable workflows (four gates, plus pr-policy, release, deps and nightly; S39) and the reusable `fleet-audit.yml`;
  - `frappe-nix gate lint|typecheck|marketplace`, the host-agnostic gate steps the reusable workflows call (S43);
  - the caller templates (`manifest.d/ci.json`), each gated by its module: `ci.yml`, `pr-policy.yml`, `release.yml`, `deps.yml`, `nightly.yml`, `assets.yml`, `dependabot.yml` and `zizmor.yml`;
  - `frappe-nix policy`, `frappe-nix ratchet`, `frappe-nix minibench`, `frappe-nix repo apply`, `frappe-nix repo audit`, `frappe-nix repo rollout` and `frappe-nix repo doctor`;
  - `repo-policy/*.json` (rulesets and repo settings; the fleet list moved to the org profile repository, §8.6);
  - `selftest-ci.yml`.
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
  - `repo-policy/self/*.json`;
  - frappe-nix's dependabot commit prefixes (§6.3).
- **P, the Avunu org profile** (`Avunu/frappe-standards-profile`; owned by Avunu, outside frappe-nix's PR set). It delivers:
  - `profile.toml` as in §8.6, extending the frozen snapshot `recommended@1.0` and setting every value Avunu relies on explicitly;
  - any template overrides Avunu wants (none are required);
  - `fleet.json`, the 12-app fleet list that 1.1 kept in frappe-nix, each entry with `profile` (§4.9);
  - a scheduled caller of frappe-nix's reusable `fleet-audit.yml`, holding the `FRAPPE_NIX_AUDIT_TOKEN` secret (§5.7);
  - a `validate.yml` that runs `frappe-nix profile validate` from the frappe-nix release it targets, compares `frappe-nix profile show --format json` on a copy of the fixture app with the committed `resolved.snapshot.json` (so a frappe-nix release that changes any Avunu value fails P's CI), and runs `docusystem doctor` on that synced copy;
  - release-please releases (tags `vX.Y.Z`, moving branch `v1`) with a `release.yml` that runs release-please and then fast-forwards `v1` by REST PATCH, with GitHub Actions (integration 15368) as the only bypass actor on `v*` branches (§6.4);
  - its own `repo-policy/` (`main.json`, `release.json` on `refs/heads/v*`, `tags.json` on `refs/tags/v*`, as frappe-nix's `repo-policy/self/` with the `v*` ref instead of `release-*`), applied with `frappe-nix repo apply --policy-dir repo-policy --repo Avunu/frappe-standards-profile` once N4 has merged.

  P is small and has no frappe-nix code. Nothing in frappe-nix depends on it: frappe-nix's own tests use the fictitious `tests/fixtures/profiles/example-org` profile.

### 1.4 Seam interfaces (N3a)

What N3a's seams accept, so each later PR adds files without editing the seam.

| Seam | A contribution is | Signature |
|---|---|---|
| `py/frappe_nix_tools/frappe_nix_tools/commands/<name>.py` | a module (or a subpackage, for a command group such as `repo`); `cli.py` imports every one there, in name order | `register(subparsers)`, adding one or more subcommands with `set_defaults(func=run)`, where `run(args) -> int` is the exit code. Raise `frappe_nix_tools.common.report.FrappeNixError` (`ConfigError` → 2, `EnvError` → 3) for a one-line failure. |
| `py/frappe_nix_tools/frappe_nix_tools/data/**` | a file | Read with `frappe_nix_tools.common.data_path(rel)` (a `Path`), or printed by `frappe-nix data-path <rel>`. |
| `frappe_nix_tools.common.config` | (N3a; every consumer reads through it) | `resolve(pyproject_path, *, lock_path=None, profile_dir=None) -> Resolved` and `resolve_doc(doc, *, root, …)`, with `Resolved.cfg`, `.modules`, `.profile`, `.sources` (the layer that set each key, for `--explain`), `.repo_host`, `.notices` and `.get(dotted_key)`. No `[tool.frappe-nix]` raises `NotOptedIn` (exit 2). Schemas are validated by `frappe_nix_tools.common.schema` (the keywords the shipped schemas use; `x-app-only` marks app-only module parameters). |
| `lib/standards/tools/<name>.nix` | a file; becomes `packages.<name>`, `apps.<name>`, a dev-shell package in an opted-in app, and `.#<name>` in an opted-in app's flake. A name the flakes already use (`default`, `frappe-init`, `frappe-nix-tools`, `backup-fetch`, `relock`) fails evaluation. | `{ pkgs, lib, frappeNixTools, ... }: <derivation with meta.mainProgram>`. It must propagate nothing (no `propagatedBuildInputs`, no Python package such as `frappeNixTools` itself): the dev shell would put a propagated Python's site-packages on `PYTHONPATH`, ahead of the bench venv's. Wrap a Python program's `bin/` instead, as `tools/frappe-nix.nix` does; `standards-app-flake` fails otherwise. |
| `lib/scripts.d/<name>.nix` | a file; merged over `lib/scripts.nix`'s scripts in name order. Redefining any script fails evaluation. | `{ lib, pkgs, appMode, lockDir, pythonBin, benchBin, atBench, atRepo, siteFlag, … }: { <script> = { exec; description; }; }` (every `lib/scripts.nix` argument and snippet: also `offlineMigrateEnv`, `workspaceBin`, `registerWorkspaceMember`, `refreshNodeModules[Soft]`, `regenNodeLocks[Soft]`, `syncRegistry`; take `...`; see `lib/scripts.d/README.md`) |
| `tests/standards/<area>.nix` | a file; its checks join the flake's `checks` and `standards-all`. Every check is named `standards-<name>` (no frappe-nix check is), so none can replace another. Two areas defining the same check, a name without the prefix, or `standards-all` fail evaluation. | `{ pkgs, lib, self, inputs, frappeNixTools, ... }: { standards-<check> = <derivation>; }` |
| `lib/standards/shell.nix` | (N3a only) | `{ pkgs, pyproject ? null }: { optedIn, packages, apps, enterShell, devenvModule }`; `modules/devenv.nix` passes the app's `pyproject.toml` and, in app mode, adds `apps` to the flake and imports `devenvModule` (`packages` and `enterShell`) into the shell. All of them are empty unless `optedIn` (S35). `optedIn` is for the other opt-in-gated parts of `modules/devenv.nix` (N1's nixfmt/statix/deadnix and port salt). |

`frappe-nix pin-path <input>` reads `--lock`, by default the nearest `flake.lock` at or above the current directory without leaving the git work tree (so an app in a subdirectory, like frappe-nix's `tests/fixtures/standards-app`, reads its own), falling back to the work tree root's. It prints the Nix store path when the tree the lock's `narHash` implies is already in the store, and otherwise fetches it into `.dev-dist/pins/<name>-<rev>/` beside that lock: a `github` node from codeload, a `gitlab` node from the GitLab API archive (`host` defaults to `gitlab.com`), a `git` node by `git clone --filter=blob:none` and a checkout of the locked rev. `FRAPPE_NIX_FETCH_TOKEN`, when set, is sent as `Authorization: Bearer` (for git, through `http.extraHeader`); without it a 401, 403 or 404 exits 3 with the hint to set it. A tree already in `.dev-dist/pins` is reused only after its NAR hash is recomputed and matches the lock; otherwise it is refetched (no stamp file is trusted, since `.dev-dist` sits in the app checkout, where a PR could commit any tree under that name). A locked `owner`, `repo` or `host` that is not a name, a git `url` that is not an `https`, `http`, `ssh` or `file` URL with a path, or a `rev` that is not a 40-hex SHA, exits 2 before anything is fetched or written. frappe-nix's own pins (`frappe-semgrep-rules`, `marketplace`, `pilot`) are read only through the lock's `frappe-nix` node (the root's inputs only in frappe-nix's own lock, which has no `frappe-nix` input), so a root input of the same name never shadows them, and each must lock `github:frappe/semgrep-rules`, `frappe/marketplace` or `frappe/pilot` respectively (case-insensitively); any other repository exits 2. `standards-profile` is read only from the root's inputs. Any other input is the root's own, else frappe-nix's. `FRAPPE_NIX_PIN_URL` (with `{host}`, `{owner}`, `{repo}`, `{rev}`) replaces a tarball URL, for mirrors and tests. `frappe-nix config <key>` reads `--pyproject`, by default the nearest `pyproject.toml` found the same way, resolves it (§8.4), takes dotted keys (`tests.setup`, `modules.ssort`, `profile.name`), prints a list one item per line, a boolean as `true`/`false`, and a table as one line of JSON (`--json` prints any value as JSON); on an app without `[tool.frappe-nix]` it exits 2 with the opt-in hint.

---

## 2. What an app gets: the managed-file contract

This section applies to opted-in apps only (S35). What an app gets is decided by its resolved configuration, so §8 (profiles and modules) is the companion to read first: every file below names the module that enables it and the profile values it reads. Values shown are the `recommended` ones unless a section says otherwise.

### 2.1 `[tool.frappe-nix]`: the app's parameters

`[tool.frappe-nix]` lives in the app's `pyproject.toml` and is **app-owned**: sync creates it once (only on opt-in, S35), never rewrites it, and validates it against `frappe_nix_tools/data/schema/tool-frappe-nix.schema.json`. Unknown keys are errors (exit 2).

The table holds two kinds of key:

- **App keys**, which only an app can set: the top-level keys below and the `[[…]]` lists. A profile that sets one is exit 2.
- **Module tables** (`[tool.frappe-nix.<module>]`) and `[tool.frappe-nix.org]`, which have the same shape as in a profile (§8.1). The app's values are the last layer of the merge (§8.4): an app switches a module on or off, or changes one parameter, by writing just that key.

```toml
[tool.frappe-nix]
schema = 1                                   # required
profile = "recommended"                      # optional; "minimal" when absent (S35). A built-in name ("minimal",
                                             # "recommended", or a frozen snapshot "recommended@1.0", S42), an org
                                             # profile flake URL ("github:<owner>/<repo>[/<ref>]", "gitlab:…",
                                             # "git+https://…"), or an in-repo profile "./<dir>" (§8.3)
frappe-major = 16                            # required. Drives every version-<major> string, range and branch
siblings = ["erpnext", "hrms"]               # bench apps besides frappe, in install order. Each is a key of
                                             # known-apps.json (or of a profile's or this table's [known-apps]),
                                             # "<owner>/<repo>" for any app on GitHub, or an object (below).
                                             # Default when created: hooks.required_apps, in order
# siblings = ["erpnext", { repo = "example/shared_lib", branch = "main", range = ">=2.0.0,<3.0.0" }]
#                                            # object form: any of repo, flake-url (gitlab:/git+https:), branch,
#                                            # range, desk_global; unset fields come from the matching known-apps rule
repo = "example/my_app"                      # optional; "<owner>/<repo>" (or "<host>/<group>/…/<repo>" off GitHub).
                                             # Default: from origin (§2.2)
integration-branch = "develop"               # optional; any branch name. Default: "develop" under
                                             # releases.branching = "develop+version", "main" under "main+tags",
                                             # whether or not releases is on. `--standards` writes origin's default
                                             # branch here when it differs from that default (S41)
retire-keep = []                             # paths exempt from every retire rule (§2.4.1)
site = "carbon-theme.localhost"              # optional; default "<App-hyphen>.localhost"
nightly-suites = []                          # shell commands nightly runs inside the dev shell with the site up,
                                             # e.g. ["node scripts/test-tables.ts"]; env FRAPPE_SITE_URL,
                                             # FRAPPE_ADMIN_PASSWORD and FRAPPE_NIX_ARTIFACT_DIR are set (§4.6)
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

[[tool.frappe-nix.untested]]                 # module tests: testmap exemptions; shrink-only (S24)
target = "standards_fixture.api.legacy"      # dotted path of a target (§5.1.1)
reason = "Removed in 16.2; kept for old kiosk bundles"   # ≥ 10 characters

[[tool.frappe-nix.coverage-omit]]            # module tests: extra coverage omit globs; shrink-only (R7), amber in A8
glob = "timeclock/www/job_timeclock_legacy/*"
reason = "Legacy kiosk page, replaced by job_timeclock in 16.3"     # ≥ 10 characters

[[tool.frappe-nix.unchecked-js]]             # module typescript (check-js on): desk/web JS files excluded from
path = "esign/public/js/controls/upload.js"  # strict checkJs; shrink-only (R6), amber in A7. A path, not a glob;
reason = "Pre-typing legacy control; JSDoc pass tracked in #41"     # it must be a tracked .js file; ≥ 10 characters

[[tool.frappe-nix.override-doctype-class]]   # module metadata: A.3 allow-list; every override_doctype_class
doctype = "Version"                          # hook needs an entry
reason = "Must replace, not extend: …"       # ≥ 20 characters

# Module tables. Every key is optional here; the profile supplies the rest (§8.5). Shown: the app-only
# parameters each module has, plus two examples of overriding a profile value.

[tool.frappe-nix.ssort]
enable = true                                # example: switch on a module the profile leaves off

[tool.frappe-nix.org]
publisher = "Example Apps Ltd"               # example: an org value for this app only (S37)

[tool.frappe-nix.dev-shell]                  # app-only flake parameters (§2.5); custom inputs go in its local region
systems = ["x86_64-linux", "aarch64-linux", "aarch64-darwin", "x86_64-darwin"]   # flake systems
extra-substituters = []                      # appended to nixConfig.extra-substituters
extra-trusted-public-keys = []               # appended to nixConfig.extra-trusted-public-keys
frappe-nix-url = ""                          # "" = github:Avunu/frappe-nix/release-<N>. Any flake URL (a fork, a
                                             # mirror, a tag or a rev); see §2.5 for what CI then needs

[tool.frappe-nix.known-apps."example/shared_lib"]   # optional; same shape as a profile's [known-apps] (§8.1)
repo = "example/shared_lib"
branch = "main"
range = ">=2.0.0,<3.0.0"

[tool.frappe-nix.tests]
setup = []                                   # steps before the app's tests. Each is "module:<dotted test module>"
                                             # (bench run-tests --module) or "execute:<dotted callable>".
                                             # Default when empty: "module:erpnext.tests.bootstrap_test_data" if
                                             # erpnext is a sibling, else "execute:frappe.utils.install.complete_setup_wizard"
js-coverage-min = 50                         # node:test line-coverage minimum; only used when test/unit/ exists

[tool.frappe-nix.releases]
bootstrap-sha = "3c33bb4b…"                  # optional; rendered into release-please-config.json

[tool.frappe-nix.pilot-assets]
enable = false                               # true → managed .github/workflows/assets.yml and the release dispatch

[tool.frappe-nix.test-utils]
track-overrides = false                      # true → the test_utils track_overrides hook (needs HASH:/REPO: annotations)

[tool.frappe-nix.typescript]                 # app-only parameters (the profile sets enable, strict-extras, check-js)
browser = true                               # false: no tsconfig.browser.json even when public/**/*.ts is tracked
browser-include = []                         # replaces the default browser include (§2.9)
web-include = []                             # globs of JS under the package that run on web pages, not the desk
                                             # (e.g. "esign/public/js/web/**"): moved from desk to web in tsc and oxlint
paths = {}                                   # compilerOptions.paths, in tsconfig.base.json
exclude = []                                 # appended to every project's exclude. Only non-source paths: an entry
                                             # matching a tracked .js/.ts/.vue file under the package is exit 2
                                             # (use unchecked-js or an spa entry)
audit-consumer = false                       # true: typecheck runs `frappe-types audit-consumer --strict` (§4.2)

[[tool.frappe-nix.typescript.spa]]           # a Vue/Vite SPA that lives at the root or inside the package and has no
root = "portal"                              # package.json of its own. "." for the repo root (timeclock)
include = ["portal/src/**"]                  # its sources; removed from the browser, desk and web projects
tsconfig = "portal/tsconfig.json"            # app-owned; MAY extend another SPA's app-owned config (portal's
                                             # "../tsconfig.json" when an SPA owns the root), never a managed file
check = "vue-tsc --noEmit -p portal/tsconfig.json"   # run by `typecheck` and chained into scripts.check

[tool.frappe-nix.js.oxlint]
ignore = []                                  # appended to ignorePatterns
globals = {}                                 # merged into the desk-JS override's globals
overrides = []                               # appended; each {files=[…], rules={…}, globals={…}}.
                                             # While js.locked-rules is true it MUST NOT name the locked rules
                                             # (typescript/no-explicit-any, typescript/ban-ts-comment,
                                             # typescript/consistent-type-imports) or change categories (exit 2)

[tool.frappe-nix.js.oxfmt]
ignore = []                                  # appended to ignorePatterns

[tool.frappe-nix.stylelint]
globs = []                                   # default: discovered "<app>/public/**/*.scss" when any exist
```

A `[[…]]` list belongs to the module named in its comment. While that module is off, the list is ignored and not validated, and its ratchet doesn't run.

How the two SPA layouts in the fleet are declared:

```toml
# erpnext_taskview: desk Vue in public/js built by the root vite.config.ts, plus portal/ (no package.json)
[[tool.frappe-nix.typescript.spa]]
root = "."
include = ["erpnext_taskview/public/js/**"]
tsconfig = "tsconfig.json"                   # app-owned, so the managed solution is tsconfig.solution.json
check = "vue-tsc --noEmit -p tsconfig.json"
[[tool.frappe-nix.typescript.spa]]
root = "portal"
include = ["portal/src/**"]
tsconfig = "portal/tsconfig.json"            # keeps "extends": "../tsconfig.json"
check = "vue-tsc --noEmit -p portal/tsconfig.json"

# timeclock: vite.config.ts and tsconfig.json at the root, sources in the package
[[tool.frappe-nix.typescript.spa]]
root = "."
include = ["timeclock/public/js/timeclock/**", "timeclock/public/js/job_timeclock/**"]
tsconfig = "tsconfig.json"
check = "vue-tsc --noEmit -p tsconfig.json"
```

`frappe_nix_tools/data/known-apps.json` (N3a) holds the template for each known sibling. `{n}` is the major and `{n1}` is the major plus one. It names only Frappe's own apps and one generic rule for any GitHub-hosted app; a profile's `[known-apps]` table, and then the app's `[tool.frappe-nix.known-apps]`, add or override entries with the same shape (§8.1, §8.4). The generic rule assumes the Frappe convention (`version-{n}` branches, major-tracking versions). A sibling on another convention (a `main`-branch, semver shared library) is declared with the object form in `siblings` or a `known-apps` entry, and every check that compares a sibling's branch or range (C3, C4, L3) uses that resolved value.

```json
{
  "frappe":   { "repo": "frappe/frappe",   "branch": "version-{n}", "range": ">={n}.0.0,<{n1}.0.0", "desk_global": null },
  "erpnext":  { "repo": "frappe/erpnext",  "branch": "version-{n}", "range": ">={n}.0.0,<{n1}.0.0", "desk_global": "erpnext" },
  "hrms":     { "repo": "frappe/hrms",     "branch": "version-{n}", "range": ">={n}.0.0,<{n1}.0.0", "desk_global": "hrms" },
  "payments": { "repo": "frappe/payments", "branch": "version-{n}", "range": ">=0.0.1,<1.0.0",       "desk_global": null },
  "*/*":      { "repo": "{owner}/{name}",  "branch": "version-{n}", "range": ">={n}.0.0,<{n1}.0.0", "desk_global": null }
}
```

For an `<owner>/<repo>` sibling, the app name is the repo name. The flake input name is the app name. The flake URL is `github:<owner>/<repo>/<branch>`, or the object's `flake-url` (`gitlab:<group>/<repo>/<branch>`, `git+https://<host>/<path>?ref=<branch>`) for a sibling hosted elsewhere. The `required_apps` spelling is `"<owner>/<repo>"` (D6). The `frappe-dependencies` key is the bare app name. A bare name that is in none of `known-apps.json`, the profile's `[known-apps]` and the app's `[tool.frappe-nix.known-apps]` is exit 2 ("unknown sibling; write it as <owner>/<repo>"). The Avunu profile adds no entries: its siblings are written `Avunu/<repo>` and use the generic rule.

### 2.2 Discovered facts (never declared)

Sync computes these from `git ls-files` (tracked files only), so the result is the same on every machine. `--check` recomputes them, so adding a first `.ts` file, for example, shows up as drift until sync is run.

| Fact | Rule |
|---|---|
| `spa_globs` | The union of every `[[tool.frappe-nix.typescript.spa]].include`, plus `<root>/**` for each SPA whose root isn't `.` |
| `desk_js` | Tracked `<app>/**/*.js` excluding `*.bundle.js`, `**/public/dist/**`, `**/node_modules/**`, `<app>/www/**`, `**/web_form/**`, `<app>/templates/**`, every nested frontend, `spa_globs`, `typescript.web-include` and `cfg.generated` |
| `web_js` | Tracked `<app>/www/**/*.js`, `<app>/**/web_form/**/*.js`, `<app>/templates/**/*.js` and `typescript.web-include`, excluding `spa_globs` and `cfg.generated` |
| `browser_ts` | `typescript.browser` is not `false`, and either a non-empty `typescript.browser-include` or tracked `<app>/public/**/*.ts` (not `public/dist`) that remain after removing `spa_globs` and `cfg.generated` |
| `vite` | A tracked `vite.config.*` or `vite.*.config.*` at the root or in a nested frontend |
| `solution` | `"tsconfig.json"`, or `"tsconfig.solution.json"` when any SPA's `tsconfig` is `tsconfig.json` |
| `scripts_ts` | Tracked `scripts/**/*.ts`, `marketplace/**/*.ts` or `ci/**/*.ts` |
| `test_ts` | Tracked `test/**/*.ts` |
| `unit_tests` | Tracked `test/unit/**/*.test.ts` |
| `scss` | `stylelint.globs`, or `["<app>/public/**/*.scss"]` when any such file is tracked |
| `nested_frontends` | Top-level directories other than `node_modules`, `.frappe-nix`, `nix` and (while the `docs-site` module is on) `docs-site` that contain a tracked `package.json`. A frontend without its own `package.json`, such as taskview's `portal/`, is declared as an SPA instead. |
| `docs_site` | A tracked `docs-site/package.json` |
| `gitmodules` | A tracked `.gitmodules` |
| `has_listing` | A tracked `marketplace/listing.toml` |
| `has_shots` | A tracked `marketplace/screenshots.ts` |
| `app_type` | `listing.toml` `type`; `"extension"` when there is no listing |
| `repo` | `[tool.frappe-nix].repo` when set; else `origin`'s URL normalised to its path (`<owner>/<repo>` on GitHub; `<group>/<subgroup>/…/<repo>` elsewhere, with `repo_host` set to the host); else `<org.github-owner>/<app>` when the resolved `org.github-owner` is set; else exit 2 asking for `repo`. Only the modules that render a repository URL (`ci`, `listing`, `readme`, `releases`) need it; for the others an unknown repo is not an error. On a non-GitHub `repo` the GitHub-only modules resolve to off (§8.2), and an app table that explicitly enables one is exit 2 naming it. |
| `frappe_nix` | `{rev, version, major, owner, name}`. `rev` is `flake.lock` → `nodes[root.inputs["frappe-nix"]].locked.rev`, and `owner`/`name` that node's `locked.owner`/`locked.repo` (`Avunu`/`frappe-nix` unless `dev-shell.frappe-nix-url` names a fork), read **after** sync's phase A (§3.3), so a missing flake or a `main`-locked frappe-nix is fixed before anything uses it. `version` is the running `frappe_nix_tools.__version__`. `--check` fails with exit 3 if the running frappe-nix-tools was not built from `rev` and the environment variable `FRAPPE_NIX_ALLOW_SKEW` is unset (§3.7). |

### 2.3 Template context

Every Jinja template (`StrictUndefined`; files end in `.j2`) receives this context:

```
app, app_hyphen, dist (pyproject [project].name), repo, title, tagline (listing → hooks app_title/app_description),
frappe = {major, next, branch: "version-<major>", preset: "version-<major>", range},
siblings = [{name, input, flake_url, branch, range, desk_global, required: bool}],   # required = in hooks.required_apps
site, cfg (= the resolved configuration, §8.4: app keys plus every module table, defaults applied),
modules (= {<module>: bool}, the resolved `enable` of each module after the dependency rules of §8.2),
org (= cfg.org, with copyright-holder defaulted to publisher), profile = {name, source, rev},
branches = {integration: <integration-branch>, release: "version-<major>" | null},   # S41; release from releases.branching
gates = [enabled gate names, in the order pr-policy, lint, typecheck, test, marketplace],   # §4.2
discover (§2.2), frappe_nix = {rev, version, major, owner, name}, floors (§2.4), app_type
```

`profile.source` is `"builtin"` or the org profile's flake URL, and `profile.rev` its locked revision (empty for a built-in). No template renders either into a file, so a profile release changes only the files whose content changes (§3.6). `branches` is computed even when `releases` is off, because dependabot's `target-branch` and the CI triggers use it: `branches.integration` is always `[tool.frappe-nix] integration-branch` (§2.1), and `branches.release` is `version-<major>` only while `releases` is on with `develop+version`, else null.

### 2.4 Inventory

Every file below is listed in a `manifest.d/*.json` fragment. Each entry has the form `{path, template, strategy, module, when, uses, floors?, overridable?}`:

- `module` is the module (§8.2) that enables the entry. The entry is live only when `modules[<module>]` is true **and** `when` is true. `module` may be a list, meaning any of them (the shared infrastructure files: `.pre-commit-config.yaml` and `tools/`).
- `when` is a Python expression over the context (for example `"discover.scss"`), `"True"` when the module alone decides.
- `uses` lists the profile values (`org.*` and module parameters) the template reads. It is documentation the engine checks: a template that reads a `cfg`/`org` key missing from `uses` fails the N3 nix check `standards-manifest`, and a live entry whose `uses` names an empty org value is exit 2 naming it (S37).
- `overridable: true` lets an org profile replace the template (§8.1).

Files whose entry isn't live MUST NOT exist. Sync deletes them (and retracts merged keys) as §3.3 step 6 says; otherwise `--check` reports exit 1 "file should not exist".

**Merged files.** `pyproject.toml` and `package.json` are touched by several modules. Each managed key or table belongs to one module and is managed only while that module is on (§2.8, §2.12). The file itself is "live" while any of its key groups is.

| Path | Strategy | Module (when) | Profile values used | Owner |
|---|---|---|---|---|
| `flake.nix` | whole + local region `inputs` (§2.5) | `dev-shell` | `dev-shell.systems`, `dev-shell.extra-substituters`, `dev-shell.extra-trusted-public-keys`, `dev-shell.frappe-nix-url` (the `standards-profile` input comes from `profile`, §8.3) | N3 |
| `.envrc` | whole + local region `envrc` | `dev-shell` | none | N3 |
| `.gitignore` | blocks (`# >>> frappe-nix >>>` block, legacy markers kept) | `dev-shell` | none | N3 |
| `.editorconfig` | whole + local region `editorconfig` | `editorconfig` | `editorconfig.indent`, `editorconfig.line-length` | N3 |
| `.pre-commit-config.yaml` | whole + local region `repos`; third-party revs are floors | any hook module: `python-lint`, `ssort`, `js`, `stylelint`, `workflow-lint`, `shell-lint`, `hygiene`, `metadata` (compat hook), `commits`, `test-utils` | each hook's module switch; `hygiene.max-file-kb`; `test-utils.hooks`, `test-utils.track-overrides` | N3 |
| `committed.toml` | whole | `commits` | `commits.allowed-types`, `commits.subject-length`, `releases.version-scheme` (comment only) | N3 |
| `tools/pyproject.toml` | whole; version floors checked in `tools/uv.lock` | any module with a tool pinned there: `python-lint`, `python-types`, `semgrep`, `workflow-lint`, `shell-lint`, `commits`, or `.pre-commit-config.yaml` live (prek) | the dependency list is the union of the enabled modules' tools | N3 |
| `tools/uv.lock` | seed (`uv lock --project tools`) | as `tools/pyproject.toml` | none | N3 |
| `pyproject.toml` | toml-merge (§2.12) | key groups of `metadata`, `python-lint`, `python-types`, `tests`, `test-utils` | `metadata.build-backend`, `python-lint.*` (select, ignore, line-length, indent-style, quote-style, typing-modules), `python-types.error-on-warning`, `tests.coverage.initial-floor` | N3 |
| `<app>/__init__.py` | blocks (`x-release-please` block, §2.13) | `releases` | none | N3 |
| `package.json` | json-merge (§2.8) | key groups of `metadata`, `js`, `stylelint`, `typescript`, `tests`, `python-lint`, `python-types`, `vite-register` | `org.license`, `org.publisher` (only when set), `metadata.package-type`, `metadata.package-manager`, `metadata.node-engine`, `typescript.preset`, `tests.js-coverage-min` | N3 |
| `yarn.lock` | seed (`yarn install`) | as `package.json` | none | N3 |
| `.oxlintrc.json` | whole (JSON, no header) | `js` (tool `oxc`) | `js.oxlint.*`, `js.locked-rules` | N3 |
| `.oxfmtrc.jsonc` | whole | `js` (tool `oxc`) | `js.oxfmt.*`, `js.format-width`, `js.format-tabs` | N3 |
| `.stylelintrc.json` | json-merge | `stylelint` (`discover.scss`) | none | N3 |
| `tsconfig.json` or `tsconfig.solution.json` (`discover.solution`) | whole | `typescript` (any managed TS project) | none | N3 |
| `tsconfig.base.json` | whole | `typescript` (`browser_ts or scripts_ts or test_ts`) | `typescript.strict-extras`, `typescript.preset` | N3 |
| `tsconfig.browser.json` | whole | `typescript` (`browser_ts`) | none | N3 |
| `tsconfig.scripts.json` | whole | `typescript` (`scripts_ts`) | none | N3 |
| `tsconfig.test.json` | whole | `typescript` (`test_ts`) | none | N3 |
| `tsconfig.desk.json` | whole | `typescript` (`typescript.check-js and desk_js`) | none | N3 |
| `tsconfig.web.json` | whole | `typescript` (`typescript.check-js and web_js`) | none | N3 |
| `release-please-config.json` | whole (JSON, no header) | `releases` | `releases.tool`, `releases.tag-prefix`; README.md as an extra file when `readme` is on under `main+tags` (§2.19) | N3 |
| `.release-please-manifest.json` | seed `{".": "<__version__>"}`; when seeding, sync also sets `package.json` `version` to `__version__` (§2.16) | `releases` | none | N3 |
| `scripts/vite-register.mjs` | whole (`ts` header) | `vite-register` (`discover.vite`) | none | N2 |
| `.git-blame-ignore-revs` | seed (header only) and validated (§2.20) | `hygiene` | none | N3 |
| `nix/node-locks/<key>/{yarn.lock,source.json}` | seed from `frappe_nix_tools/data/node-locks/version-<major>/` | `dev-shell` (sibling present and no lock) | none | N3 |
| `.github/workflows/ci.yml` | whole (frappe-nix SHA sync-owned); one caller job per enabled gate (S39) | `ci` | `ci.test-timeout-minutes`, `ci.schedules.ci`; the gate list | N4 |
| `.github/workflows/pr-policy.yml` | whole | `ci` and `commits` | `releases.version-scheme` | N4 |
| `.github/workflows/release.yml` | whole | `ci` and `releases` | `releases.branching`, `releases.tag-prefix`, `ci.schedules.release`, `listing.publish`, `listing.registry-fork`, `pilot-assets.enable` | N4 |
| `.github/workflows/deps.yml` | whole | `ci` and `dependabot` | `dependabot.auto-merge`, `ci.schedules.deps-sweep`, `ci.schedules.deps-relock`, `repo-policy.merge-methods` | N4 |
| `.github/workflows/nightly.yml` | whole | `ci` (`ci.nightly`) | `ci.schedules.nightly`; the nightly job switches (§4.6) | N4 |
| `.github/workflows/assets.yml` | whole; the pilot SHA is a floor (preserved) | `pilot-assets` | none | N4 |
| `.github/dependabot.yml` | whole + local region `updates` | `dependabot` | `dependabot.ecosystems`, `dependabot.cadence`, `dependabot.cooldown-days`, `docs-site.action-patterns`, `docs-site.cooldown-exclude-actions`, `docs-site.package-names` | N4 |
| `.github/zizmor.yml` | whole | `workflow-lint` | none | N4 |
| `marketplace/shots.d.ts` | whole | `screenshots` (`discover.has_shots`) | none | N5 |
| `marketplace/semgrep-baseline.json` | seed `{"schema":1,"findings":[]}` and validated (§5.2) | `listing` | none | N5 |
| `marketplace/listing.toml` | seed from `templates/marketplace/listing.toml.j2` (overridable) and validated by frappe-listing | `listing` (sync `--init-listing` only) | `org.website-url`, `org.docs-url` | N5 |
| `README.md` | blocks (`<!-- frappe-nix:begin <name> -->`, §2.19); block templates overridable | `readme` (`discover.has_listing`) | `org.license`, `org.copyright-holder`, `org.email`, `org.support-url`, `org.dev-docs-url`, `org.repo-url`, `org.readme.badges`, `org.readme.links`, `readme.install-ref` | N5 |
| `<app>/desktop_icon/<app>.json` | generated by `frappe-icon build --write-fixture`; freshness checked by `frappe-icon check` | `icons` (`app_type == "application"`) | none | N5 |
| each `[[extra-files]]` entry of the org profile | `whole` or `seed` (§8.1) | the module the entry names | as the entry declares | P (the profile) |

These are **not managed**, but are checked or tolerated:

- `docs/` and `docs-site/` are owned by the app's docs tool (docusystem in the Avunu fleet). Its workflows (docusystem's `docs.yml` and `docs-publish.yml`) are left alone, but zizmor and actionlint lint them when `workflow-lint` is on.
- `nix/uv.lock` and `nix/node-locks/**` belong to relock.
- `CHANGELOG.md` belongs to release-please.
- `semgrep/*.yml` is the app's own extra rules, which `lint` runs.
- `nix/local.nix` is the app's flake-parts module.

#### 2.4.1 Retired files (S31)

`manifest.d/core.json` carries a `retire` list (N3 owns it; N4 adds the workflow rules). Each rule belongs to the module that replaces **the function** it retires, and applies only while that module is on: an app that doesn't enable `releases` keeps its own release workflow, one without `dependabot` keeps its own auto-merger, and one without `ci` keeps every workflow. An org profile adds rules of the same shape with `[[retire]]` (§8.1), and an app exempts any path from every rule with `[tool.frappe-nix] retire-keep`. Sync deletes every tracked match of an applying rule. `--check` reports each one as exit 1 with the problem `legacy file`. Audit row A9 evaluates the same list, through `frappe-nix sync --check --format json`, so the two can't diverge.

| Rule | Module | Matches |
|---|---|---|
| Legacy release workflow | `releases` (with `ci`) | Any tracked `.github/workflows/*.y*ml` that isn't a managed caller and isn't a docs tool's `docs*.yml`, and whose text contains `release-please-action`. In the Avunu fleet: carbon's `release-please.yml`. |
| Legacy auto-merge workflow | `dependabot` (with `ci`) | The same set of files, whose text contains `gh pr merge --auto` or `dependabot/fetch-metadata`. In the Avunu fleet: carbon's and taskview's `dependabot-auto-merge.yml`. |
| Legacy JS config | `js` (tool `oxc`) | `.oxfmtrc.json` (the managed file is `.jsonc`), `.eslintrc*`, `eslint.config.*`, `.prettierrc*`, `prettier.config.*`. |
| Legacy Python config | `python-lint` | `.flake8`, `setup.cfg` with only a `[flake8]` section. |
| Legacy packaging | `metadata` (with `build-backend = "flit"`) | `MANIFEST.in`, `requirements.txt` (under the rule Appendix I records for N3). |
| Legacy Nix | `dev-shell` | `nix/node-offline-hashes.json` (frappe-nix throws on its option). |
| Legacy asset registration | `vite-register` | `update-assets.mjs`. |

Name-only rules are not built in: a file name says nothing about what a workflow does, so `check.yml` and `version-branch-guard.yml`, whose jobs moved to `ci.yml` in the Avunu fleet, are retired by the Avunu profile's `[[retire]]` entries (§8.6), not by frappe-nix. A file that matches but has app-specific content worth keeping is still deleted unless it is in `retire-keep`; the PR diff shows it. Retiring is a one-way step, so the adoption PR (Avunu fleet: PR A) is the PR that carries these deletions.

### 2.5 `flake.nix` (whole)

Module `dev-shell` (every opted-in app, including `minimal`). Parameters (app-only, `[tool.frappe-nix.dev-shell]`): `systems`, `extra-substituters`, `extra-trusted-public-keys`, `frappe-nix-url`. `py/frappe_nix_tools/frappe_nix_tools/data/templates/flake.nix.j2`. The output MUST be byte-stable under `nixfmt` (an acceptance test checks this). An app that has not opted in keeps the `flake.nix` that `templates/app/` gave it, untouched (S35).

Where an app's own Nix goes:

- **Extra inputs** go in the local region `inputs`, inside the `inputs` attribute set. Its content is kept verbatim; a name that collides with a managed input (`frappe-nix`, `nixpkgs`, `frappe`, `standards-profile`, a sibling) is exit 2.
- **Outputs** (packages, overlays, `nixosModules`, checks, extra dev-shell settings) go in `nix/local.nix`, a flake-parts module imported below; it can define `perSystem` and `flake.*` outputs and reads the extra inputs through `inputs`.
- **Systems and binary caches** are the parameters above.
- **The frappe-nix source.** `frappe-nix-url` replaces `github:Avunu/frappe-nix/release-<N>` with any flake URL: a tag (`…/v1.2.3`), a rev, a fork or a mirror. Phase A then re-locks only when the locked node's `original` differs from that URL (§3.3), so a pin is never undone. A GitHub fork is supported everywhere: the caller workflows' `uses:` and the no-Nix install read the locked `owner`/`repo` (§2.2 `frappe_nix`, §3.7, §4.1). A non-GitHub URL (`git+https:`) works for the dev shell and sync, and is exit 2 while `ci` is on, because a GitHub `uses:` can't name it. The skew rule (§3.7) compares the locked rev with the rev the running frappe-nix-tools was built from, so any pin works as long as the two agree.

**First opt-in.** When an app opts in (`--standards`) and its existing `flake.nix` has no managed header, sync compares it with what `templates/app/flake.nix` renders for the app. If they differ, sync exits 2, prints the diff of what would be replaced, and names the three places above; `--force` replaces it anyway. The same rule applies to `.envrc`. (Avunu fleet: PR A passes `--force` where an app's flake predates the template, after its diff is reviewed.)

```nix
# frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`); edit only inside the frappe-nix:local region.
# App-specific Nix goes in nix/local.nix, a flake-parts module imported below when it exists.
{
  description = "{{ app }}: {{ title }}";

  inputs = {
    frappe-nix.url = "{{ cfg['dev-shell']['frappe-nix-url'] or 'github:Avunu/frappe-nix/release-' ~ frappe_nix.major }}";
    nixpkgs.follows = "frappe-nix/nixpkgs";
    frappe = {
      url = "github:frappe/frappe/{{ frappe.branch }}";
      flake = false;
    };
{%- if profile.source != "builtin" %}
    standards-profile = {
      url = "{{ profile.source }}";
      flake = false;
    };
{%- endif %}
{%- for s in siblings %}
    {{ s.input }} = {
      url = "{{ s.flake_url }}";
      flake = false;
    };
{%- endfor %}
    # frappe-nix:local-begin inputs
    # frappe-nix:local-end inputs
  };

  nixConfig = {
    extra-substituters = [ "https://devenv.cachix.org"{% for u in cfg['dev-shell']['extra-substituters'] %} "{{ u }}"{% endfor %} ];
    extra-trusted-public-keys = [
      "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
{%- for k in cfg['dev-shell']['extra-trusted-public-keys'] %}
      "{{ k }}"
{%- endfor %}
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
{%- for sys in cfg['dev-shell'].systems | sort %}
          "{{ sys }}"
{%- endfor %}
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
{%- if modules["docs-site"] %}
                excludeNodeTargets = [ "docs-site" ];   # §5.11
{%- endif %}
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

`standards-profile` is the org profile's input (S38, §8.3). Nothing in the app's Nix evaluation reads it; it is in `flake.nix` so that `flake.lock` pins the profile revision the rendered files came from. Sync writes this file and locks it in phase A (§3.3), before anything reads `flake.lock`. `nix` is required for `--sync`; `--check` never runs it and reports missing lock nodes as exit 1.

### 2.6 `.envrc` (whole) and the `.gitignore` block

Module `dev-shell`. Profile values: none. These are the opted-in forms, rendered from `data/templates/envrc.j2` and `data/templates/gitignore.block`; `templates/app/.envrc` and `templates/app/.gitignore` stay as they are in `main` for apps that have not opted in.

`.envrc` (local region `envrc`, for `dotenv`, exports and other direnv lines; it is sourced after `use flake`):

```
# frappe-nix:managed — generated by frappe-nix; edit only inside the frappe-nix:local region.
use flake . --no-pure-eval
# frappe-nix:local-begin envrc
# frappe-nix:local-end envrc
```

The `.gitignore` managed block (between the existing `# >>> frappe-nix >>>` markers) is the `templates/app/.gitignore` body plus:

```
/.dev-dist/
*.tsbuildinfo
.ruff_cache/
__pycache__/
```

### 2.7 `.editorconfig` (whole + local region)

Module `editorconfig`. Profile values: `editorconfig.indent` (`"tab"`, Frappe's convention, in `recommended`; or `"space"`, which renders `indent_style = space` with `indent_size = 4`) and `editorconfig.line-length` (110). This fixes two bugs in carbon_frappe's copy: YAML and Nix can't use tabs.

```ini
# frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`); edit only inside the frappe-nix:local region.
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true

[*.{py,js,mjs,cjs,ts,mts,vue,css,scss,html,jinja,toml,json,jsonc,sh}]
indent_style = {{ cfg.editorconfig.indent }}
indent_size = 4
max_line_length = {{ cfg.editorconfig["line-length"] }}

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

# frappe-nix:local-begin editorconfig
# frappe-nix:local-end editorconfig
```

### 2.8 `package.json` (json-merge)

Live while any module with a key group below is on. Profile values: `org.license`, `org.publisher`, `metadata.package-type`, `metadata.package-manager`, `metadata.node-engine`, `typescript.preset`, `tests.js-coverage-min`. No module needs another module's key group: `js`, `typescript`, `stylelint` and `vite-register` need only that `package.json` exists, which any of them makes true.

Sync creates `package.json` if it's missing, as `{"name": "<App-hyphen>", "version": "<__version__ or 0.1.0>", "private": true, "description": <tagline>}`, plus `"license": <org.license>` and `"author": <org.publisher>` when those org values are set. After that, only the keys below are managed, each only while its module is on. Every other key is app-owned (for example `dependencies`, `build`, `codegen`, `ci:lint`, `ci:typecheck`). `version` is owned by release-please (module `releases`), except once: when sync seeds `.release-please-manifest.json`, it first sets `version` to `__version__` (taskview has 1.0.0 against 0.0.1, timeclock 0.0.0 against 0.0.1), because `__version__` is what the registry reads.

| Key | Module | Rule |
|---|---|---|
| `private` | `metadata` | exactly `true` |
| `type` | `metadata` | exactly `metadata.package-type` (`"module"` in `recommended`); app-owned when that is `""` (CommonJS config files keep working) |
| `license` | `metadata` | exactly `org.license` when it is set; app-owned when it is empty (then L2 only checks that it equals `hooks.app_license`) |
| `author` | `metadata` | exactly `org.publisher` when it is set; app-owned otherwise |
| `engines.node` | `metadata` | exactly `metadata.node-engine` (`">=24"`); app-owned when `""` |
| `packageManager` | `metadata` | exactly `metadata.package-manager` (`"yarn@1.22.22"`); app-owned when `""`. The CI gates install with yarn classic (`yarn install --frozen-lockfile`) whatever this says, so an app on another package manager keeps its own `ci:*` scripts for anything that needs it |
| `frappe` | `metadata` | exactly `{"major": "<major>", "branch": "version-<major>"}` (string major, as carbon_frappe has it; the Frappe branch, not the app's release branch) |
| `scripts.format` | `js` | `"oxfmt"` |
| `scripts.format:check` | `js` | `"oxfmt --check"` |
| `scripts.lint` | `js` (tool `oxc`) | `"oxlint"`. With `js` off (`tool = "none"`, the route for teams on eslint, prettier or biome) `lint` is app-owned and sync never writes it |
| `scripts.lint:css` | `stylelint` (`discover.scss`) | `stylelint "<glob>"` for each scss glob, joined by ` && ` |
| `scripts.typecheck` | `typescript` | `"tsc --build <discover.solution>"` when the solution is rendered, followed by ` && <check>` for each `[[tool.frappe-nix.typescript.spa]]` in order; just the SPA checks joined by ` && ` when there is no solution; absent when there is neither. TS 7's `tsc` can't check `.vue` imports, so SPAs keep `vue-tsc` (a `devDependencies` key the app owns) |
| `scripts.build` | `vite-register` | App-owned, but when `discover.vite`: it MUST end with ` && node scripts/vite-register.mjs` (sync appends it if missing, exit 1 under `--check`). This is `frappe-nix compat` C8. |
| `scripts.test:unit` | `tests` (`tests.js-unit`) | `"node --test --experimental-test-coverage --test-coverage-include='<app>/public/js/**' --test-coverage-lines=<tests.js-coverage-min> 'test/unit/**/*.test.ts'"`; iff `discover.unit_tests` |
| `scripts.lint:py` | `python-lint` | `"uv run --frozen --project tools ruff check . && uv run --frozen --project tools ruff format --check ."` |
| `scripts.typecheck:py` | `python-types` | `"uv run --frozen --project tools ty check --python \"${FRAPPE_BENCH_ROOT:-.frappe-nix/bench}/env\""` |
| `scripts.check` | `js` (tool `oxc`) | the parts joined by ` && `, in this order: `yarn -s format:check`, `yarn -s lint`, `yarn -s lint:css`, `yarn -s typecheck`, `yarn -s test:unit`, each only if that script is present, whoever owns it. With `js` off, `check` is app-owned, so an adopter's own lint and format steps never fall out of it |
| `devDependencies.<pkg>` | per package | **floor** (S9). The app's range MUST be a caret or exact range whose minimum is ≥ the floor; otherwise sync sets `^<floor>`. Floors: `oxlint` 1.87.0 and `oxfmt` 0.72.0 (`js`); `typescript` 7.0.2, `frappe-types` 16.5.0 (the minor that ships `tsconfig/*` presets, `frappe-types/web` and `gen-doctypes`; only with `typescript.preset = "frappe-types"`, §2.9) and `@types/node` 26.6.4 (`typescript`, when any TS or desk project exists); `stylelint` 17.16.0 and `stylelint-config-standard-scss` 17.0.0 (`stylelint`, when `discover.scss`). |

When a module turns off, sync removes its keys whose values still equal what it would render (a toolchain `devDependencies` entry counts as equal when its range is `^<floor>` or higher), and leaves any other value as app-owned, with a warning (§3.3 step 6).

Forbidden (exit 2), each only while the named module is on:

- (`metadata`) a `build` script when none of these holds: `discover.vite`, a nested frontend, an SPA entry, or `[tool.frappe-nix] build = true` (carbon sets it for its patch-assets, audits and ai-chat build). frappe's esbuild runs every app's `build`, so a stray one runs on every bench. Conversely, `build = true` without a `build` script is exit 2.
- (`js`, tool `oxc`) `eslint*`, `prettier*`, `@typescript-eslint/*` or `eslint-config-*` in any dependency map;
- (any) `scripts` keys starting with `frappe-nix:`, which are reserved.

### 2.9 TypeScript configs (whole, JSONC with header)

Module `typescript`. Profile values: `typescript.strict-extras` (the three extra compiler options in `tsconfig.base.json`; `true` in `recommended`), `typescript.check-js` (whether the desk and web projects exist at all: `false` in `recommended`, `true` in the Avunu profile) and `typescript.preset` (below). With `check-js` off, desk and web JS is still linted by oxlint when `js` is on, but not type-checked, and `[[tool.frappe-nix.unchecked-js]]`, R6 and A7's amber are inactive.

Every file starts with `// frappe-nix:managed — generated by frappe-nix (\`frappe-init --sync\`); do not edit. Customise via [tool.frappe-nix.typescript].`

**The preset dependency.** With `typescript.preset = "frappe-types"` (the `recommended` value), the compiler presets and the Frappe type declarations come from the npm package `frappe-types` (≥ 16.5.0; MIT; source `github.com/Avunu/frappe-types`, maintained by the frappe-nix authors): `frappe-types/tsconfig/base.json`, `frappe-types/tsconfig/desk-js.json`, `frappe-types/global` and `frappe-types/web`. 16.5.0, the first release with the presets and `frappe-types/web`, is on npm (published 2026-10-07). It is a third-party open-source dependency like `oxlint`, not an org value: nothing of Avunu's is rendered into the app, and dependabot moves it in the `toolchain` group. With `typescript.preset = "inline"`, `tsconfig.base.json` carries the compiler options itself (from frappe-nix-tools' `data/tsconfig/base.json`, a copy of the preset's options) instead of `extends`, the `frappe-types` devDependency is not managed, and the `frappe-types/*` entries are dropped from `types`. `check-js`, `audit-consumer` and the gen-doctypes freshness step need the Frappe declarations, so each of them with `preset = "inline"` is exit 2.

**The solution file** is named `discover.solution`: `tsconfig.json`, or `tsconfig.solution.json` when an SPA owns the root `tsconfig.json` (taskview, timeclock). Sync never writes over an SPA's config. It lists only the projects that exist, in the order browser, scripts, test, desk, web:

```jsonc
{
	"files": [],
	"references": [{ "path": "./tsconfig.browser.json" }, { "path": "./tsconfig.scripts.json" }, { "path": "./tsconfig.test.json" }, { "path": "./tsconfig.desk.json" }, { "path": "./tsconfig.web.json" }]
}
```

`tsconfig.base.json` holds the app-level strictness the frappe-types preset leaves out (the `compilerOptions` keys below other than `paths` are omitted when `typescript.strict-extras` is false). With `preset = "inline"`, `extends` is absent and the preset's options are rendered into `compilerOptions` first:

```jsonc
{
	"extends": "frappe-types/tsconfig/base.json",
	"compilerOptions": {
		"noUnusedLocals": true,
		"noUnusedParameters": true,
		"noImplicitReturns": true,
		"paths": { /* [tool.frappe-nix.typescript].paths; key omitted when empty */ }
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

`tsconfig.desk.json` checks uncompiled desk JS with JSDoc (D5), only when `typescript.check-js` is true:

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

**Migrating to strict checkJs** (when an app turns `typescript.check-js` on). The frappe-types track measured esign at 274 errors, timeclock at about 238 (plus 4,056 in a committed legacy bundle), jailbreak 59, postgrid 36 and jwt_auth 6. The adoption PR (Avunu fleet: PR B) lists each file it can't fix yet in `[[tool.frappe-nix.unchecked-js]]` with a reason. That list is rendered into the desk and web excludes and ratchets like `untested`: R6 lets it only shrink. An entry naming a file that is no longer tracked is stale (`frappe-nix compat` C9, exit 1). An entry whose file no longer has any error is also stale: the `typecheck` job runs `frappe-nix unchecked-js --stale` (N3), which runs `tsc -p` once over a generated temporary project that includes every listed file and fails naming each listed file with zero diagnostics. Audit row A7 is amber while the list is non-empty. `// @ts-nocheck` stays forbidden (`typescript/ban-ts-comment`). A committed build output such as `timeclock/www/job_timeclock_legacy/index.js` belongs in `cfg.generated` or should be deleted, not listed as unchecked.

**SPAs** (`[[tool.frappe-nix.typescript.spa]]`) keep their own app-owned tsconfig. Their globs are removed from the browser, desk and web projects and from the desk-globals override; `typecheck` runs each `check`. Nested frontends with a `package.json` work as before: `typecheck` runs their own `typecheck` script.

- `types/doctypes.d.ts`, if the app has it, is the output of `frappe-types gen-doctypes`. It is committed and checked for freshness in `typecheck` (§4.2).
- **App symbols on the frappe namespace.** An app declares the members it adds to `frappe` (taskview's `frappe.views.TasksView`, esign's `frappe.ui.form.ControlFontSelect` and `frappe.esign_context`, jailbreak's `frappe.ui.merge_records`, timeclock's `frappe.ui.form.show_workday_bulk_add`) in exactly one file, `types/<app>.augment.d.ts`, as `declare global { namespace frappe.<sub> { … } }` augmentations. Each member carries a `// app-owned: <reason>` comment on the line before it. App-owned, never managed.
- **Hand-written frappe globals** are an `frappe-nix compat` C7 failure (exit 1), which is plan §6 row "no hand-written frappe globals". C7 matches redeclarations only: `declare (var|let|const) frappe`, `interface Window {` containing a `frappe` member, `declare namespace frappe` anywhere, and any `namespace frappe` augmentation outside `types/<app>.augment.d.ts` or without its `// app-owned:` comments. taskview's `public/js/types/frappe.d.ts` and timeclock's `public/js/timeclock/env.d.ts` are deleted in their PR B, and their app-owned members move to the augment file.

### 2.10 `.oxlintrc.json` (whole JSON, no comments) and `.oxfmtrc.jsonc` (whole)

Module `js` with `js.tool = "oxc"` (`"none"` renders neither file and no `format`/`lint` scripts). Profile values: `js.oxlint.categories` (the `categories` object below is the `recommended` value), `js.locked-rules` (when true, the three `typescript/*` rules below are `"error"` and an app override may not touch them; when false they are ordinary rules an app may override), `js.format-width` (110) and `js.format-tabs` (true), plus the app's `js.oxlint` and `js.oxfmt` lists.

`.oxlintrc.json`. The desk and web overrides are generated from the same file sets tsc uses (§2.2), not from fixed globs:

- `<desk-files>` is `discover.desk_js` rendered as a sorted list of paths (oxlint matches literal paths as globs). A new desk file is therefore drift until sync runs, which is the same rule tsconfig already follows.
- `<web-files>` is `discover.web_js`, likewise.
- Files in `spa_globs` get neither override; their globals come from their own imports.

```json
{
	"$schema": "./node_modules/oxlint/configuration_schema.json",
	"plugins": ["typescript", "unicorn", "oxc", "import"],
	"categories": { "correctness": "error", "suspicious": "error", "perf": "warn", "pedantic": "off" },   // js.oxlint.categories
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
	                   /* + cfg.generated + [tool.frappe-nix.js.oxlint].ignore */],
	"overrides": [
		{ "files": ["scripts/**", "marketplace/**", "ci/**", "test/**"], "env": { "node": true },
		  "rules": { "no-console": "off", "no-await-in-loop": "off", "import/no-unassigned-import": "off" } },
		{ "files": ["<app>/public/js/*.bundle.ts", "<app>/public/js/*.bundle.js"], "rules": { "import/no-unassigned-import": "off" } },
		{ "files": <desk-files>, "globals": { "frappe": "readonly", "__": "readonly", "cur_frm": "readonly",
		  "cur_list": "readonly", "locals": "readonly", "$": "readonly", "jQuery": "readonly", "moment": "readonly"
		  /* + "<desk_global>": "readonly" for each sibling with one, + [tool.frappe-nix.js.oxlint].globals */ } },
		{ "files": <web-files>, "globals": { "frappe": "readonly", "__": "readonly", "$": "readonly",
		  "jQuery": "readonly" /* + [tool.frappe-nix.js.oxlint].globals */ } }
		/* + [tool.frappe-nix.js.oxlint].overrides */
	]
}
```

An override whose file list is empty is omitted.

`.oxfmtrc.jsonc`:

```jsonc
// frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`); do not edit. Customise via [tool.frappe-nix.js.oxfmt].
// Tabs and 110 columns are frappe's convention. Anything .gitignore'd is skipped without being listed.
{
	"$schema": "./node_modules/oxfmt/configuration_schema.json",
	"useTabs": true,      // js.format-tabs
	"printWidth": 110,    // js.format-width
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
		/* + cfg.generated + [tool.frappe-nix.js.oxfmt].ignore */
	]
}
```

Every `whole`-strategy text file that oxfmt formats (YAML, TOML, Markdown, JSONC) MUST already be in oxfmt's output form, whether or not the app enables `js`. N3 and N4 acceptance tests run `oxfmt --check` over a freshly rendered fixture. An app whose own formatter (prettier or biome, added through the `repos` local region or outside prek) reformats these files is not in drift: while `js.tool` isn't `"oxc"`, `--check` compares YAML, JSON, JSONC and TOML `whole` files semantically, and sync leaves a file alone when it is semantically equal to the rendered one (§3.2).

### 2.11 `.stylelintrc.json` (json-merge, only when `discover.scss`)

Module `stylelint`. Profile values: none. Managed keys:

- `extends`: exactly `["stylelint-config-standard-scss"]`;
- `ignoreFiles`: MUST contain `"<app>/public/dist/**"`.

Any other key (`rules`, more `ignoreFiles` entries) is app-owned.

### 2.12 `pyproject.toml` (toml-merge)

The engine is `tomlkit`, which preserves comments and order. Each managed group belongs to one module and is managed only while that module is on; when the module turns off, sync removes the group's keys that still hold the rendered values (§3.3 step 6). Groups: `[project]` `requires-python`/`dynamic`, `[build-system]` (only with `metadata.build-backend = "flit"`; with `"any"` it is app-owned, so setuptools, hatchling and poetry-core work) and `[tool.bench.frappe-dependencies]` → `metadata`; `[tool.ruff*]` → `python-lint`; `[tool.ty*]` → `python-types`; `[tool.coverage*]` → `tests` (`tests.coverage.enable`); `[tool.vulture]` and `[tool.test_utils.*]` (Appendix I, N3) → `test-utils`. Profile values: `python-lint.line-length`, `.select`, `.ignore`, `.typing-modules`, `.quote-style`, `.indent-style`; `python-types.error-on-warning`; `tests.coverage.initial-floor`.

**Managed exact.** Sync sets these and `--check` compares their values. Shown with the `recommended` values.

```toml
[project]                                  # metadata
requires-python = ">=3.14"                 # from the frappe-nix preset for version-<major>
dynamic = ["version"]                      # must contain "version"

[build-system]                             # metadata.build-backend = "flit" only
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[tool.bench.frappe-dependencies]           # exactly {"frappe"} ∪ hooks.required_apps (bare names); values from known-apps.json
frappe = ">=16.0.0,<17.0.0"
erpnext = ">=16.0.0,<17.0.0"

[tool.ruff]                                # python-lint
line-length = 110                          # python-lint.line-length
target-version = "py314"

[tool.ruff.lint]
select = ["F", "E", "W", "I", "UP", "B", "RUF", "SIM", "C4", "PIE", "PERF", "T20"]   # python-lint.select
ignore = ["E501", "W191"]                  # python-lint.ignore
typing-modules = ["frappe.types.DF"]       # python-lint.typing-modules

[tool.ruff.format]
quote-style = "double"                     # python-lint.quote-style
indent-style = "tab"                       # python-lint.indent-style
docstring-code-format = true

[tool.ty.environment]                      # python-types
python-version = "3.14"

[tool.ty.src]
include = ["<app>"]

[tool.ty.terminal]
error-on-warning = false                   # python-types.error-on-warning (the Avunu profile sets true)

[tool.coverage.run]                        # tests
omit = ["*/tests/*", "*/test_*.py", "*/patches/*"]   # + each [[tool.frappe-nix.coverage-omit]].glob, in order.
                                                    # Patches match testmap's own **/patches/** exclusion: a fresh
                                                    # install marks them complete without running them.

[tool.coverage.report]
show_missing = true
skip_covered = true
precision = 1
exclude_also = ["if TYPE_CHECKING:", "raise NotImplementedError", "@(abc\\.)?abstractmethod"]
```

**App-owned, but validated.** Sync never touches these. Each rule applies while the module in brackets is on.

| Key | Validation |
|---|---|
| `[project]` `name`, `authors`, `description`, `readme`, `license`, `dependencies` | [`metadata`] `dependencies` MUST NOT name `frappe`, `erpnext`, `hrms` or `payments` (exit 1). When `org.license` is set, `license` (if present) MUST equal it. |
| `tool.coverage.report.fail_under` | [`tests`] Required; a number from 0 to 100, seeded as `tests.coverage.initial-floor` when missing. Ratcheted (S24). |
| `tool.ruff.extend-exclude` | [`python-lint`] Allowed. `frappe-nix repo audit` reports a non-empty list as amber. |
| `tool.ruff.lint.per-file-ignores` | [`python-lint`] Allowed, except that F401 and E402 MUST NOT appear for a glob that matches `<app>/**` or `**` (exit 2) |
| `tool.ty.src.exclude` | [`python-types`] Allowed; reported as amber in audit |
| `[dependency-groups]` | App-owned. The app's test-only packages go here (`responses`, …). |
| `[tool.bench.assets]` | App-owned. Required when the `pilot-assets` module is on: `build_dir`, `out_dir` and `index_html_path` (frappe-listing validates them). |
| `[tool.frappe-nix]` | §2.1 |

**Forbidden** (exit 2), while the module in brackets is on:

- [`python-lint`] `tool.ruff.lint.extend-select`, `extend-ignore`, `ignore` beyond `python-lint.ignore`, `unfixable`, `tool.ruff.lint.isort` (the app changes the rule set through `[tool.frappe-nix.python-lint]`, not around it);
- [`python-types`] `tool.ty.rules` with any value other than `"error"`, and `tool.ty.overrides`;
- [`tests`] `tool.coverage.run.source` and `tool.coverage.run.relative_files`, which belong to frappe-test;
- [`metadata` with `build-backend = "flit"`] `[tool.poetry]` (D6 uses flit);
- [`metadata` with `build-backend = "flit"`] `requirements.txt` existing anywhere tracked (retired under the Appendix I rule).

### 2.13 `<app>/__init__.py` (blocks)

Module `releases` (the block is what release-please's generic updater rewrites). Profile values: none. The side-effect rule below (L5) belongs to `listing`.

```python
# x-release-please-start-version
__version__ = "16.1.0"
# x-release-please-end
```

- Only comment lines are allowed outside the block, such as test_utils' copyright stamp and its blank line. A docstring and any statement count as side effects (marketplace rule): exit 1.
- The version value is app-owned (release-please writes it). Sync only converts carbon_frappe's trailing `# x-release-please-version` form into the block form and keeps the value.
- Whitelisted functions or any other code in `__init__.py` (jailbreak's `assert_capability` and `check_capability`) move to `<app>/api.py` in the adoption PR (Avunu fleet: PR B). The old dotted paths stay reachable through an `override_whitelisted_methods` shim in `hooks.py` for one minor release, as for renames (§5.10).
- `frappe-nix compat` (C5, module `releases`) requires: `__version__` = `package.json` `version` = manifest `"."`. The registry parses the line `__version__ = "…"` (add_release.py `dynamic_version`), so nothing may follow the closing quote.

### 2.14 `.pre-commit-config.yaml` (whole + local region `repos`; third-party revs are floors)

Live while any hook module is on (§2.4). This is the rendered form with every module on (the Avunu profile); `{% if %}` marks the parts that depend on discovered facts. Each hook, or each remote `repo:` block, is additionally wrapped in `{% if modules[<module>] %}` as this table gives, and a remote block whose hooks are all off is omitted with its `rev:`:

| Hooks | Module | Profile values |
|---|---|---|
| `pre-commit-hooks` block (`check-merge-conflict` … `check-added-large-files`) | `hygiene` | `hygiene.max-file-kb` (1024) |
| `ssort` block | `ssort` | none |
| `ruff-isort`, `ruff-check`, `ruff-format` | `python-lint` | none (the rules live in `pyproject.toml`, §2.12) |
| `oxfmt`, `oxlint` | `js` (tool `oxc`) | none |
| `stylelint` | `stylelint` | none |
| `zizmor`, `actionlint` | `workflow-lint` | none |
| `shellcheck` | `shell-lint` | none |
| `frappe-nix-compat` | `metadata` (C2–C4, C6, C7), `releases` (C1, C5), `vite-register` (C8), `tests`/`typescript` (C9) — one hook that runs the rules of whichever of these is on | none |
| `committed`, `frappe-nix-commit-msg` | `commits` (the second hook's major rule only under `releases.version-scheme = "frappe-major"`) | none |
| agritheory/test_utils block | `test-utils` | `test-utils.hooks` (the list of hook ids rendered; the default is every hook shown), `test-utils.track-overrides` |

`default_install_hook_types` lists `commit-msg` only when `commits` is on.

```yaml
# frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`); edit only inside the frappe-nix:local region.
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
        args: [--maxkb={{ cfg.hygiene["max-file-kb"] }}]
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
      - id: frappe-nix-compat
        name: frappe-nix compat (versions, ranges, majors)
        language: system
        entry: sh -c 'if command -v frappe-nix >/dev/null; then exec frappe-nix compat; fi; echo "frappe-nix-compat skipped (frappe-nix not on PATH; CI runs it)" >&2'
        pass_filenames: false
        always_run: true
        verbose: true
      - id: committed
        name: committed (conventional commit)
        language: system
        entry: uv run --frozen --project tools committed --commit-file
        stages: [commit-msg]
      - id: frappe-nix-commit-msg
        name: no major bump through a commit message
        language: system
        entry: sh -c 'if command -v frappe-nix >/dev/null; then exec frappe-nix policy --commit-msg-file "$1"; fi; echo "frappe-nix-commit-msg skipped (frappe-nix not on PATH; pr-policy runs it)" >&2' --
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
        exclude: ^(docs/|docs-site/|marketplace/shots\.d\.ts$|types/doctypes\.d\.ts$|scripts/vite-register\.mjs$|tsconfig[^/]*\.json${% for g in cfg.generated %}|{{ g | glob_to_regex }}{% endfor %})
      # Bench-aware: silent no-ops outside a bench. CI runs them in a mini-bench (`frappe-nix minibench`).
      - id: validate_customizations
        stages: [manual]
      - id: clean_customized_doctypes
        stages: [manual]
      # Nightly only (jscpd through npx, needs the network).
      - id: check_code_duplication
        stages: [manual]
{% if cfg["test-utils"]["track-overrides"] %}
      - id: track_overrides
        args: [--app, {{ app }}, --base-branch, {{ frappe.branch }}]
{% endif %}

  # frappe-nix:local-begin repos
  # frappe-nix:local-end repos
```

**Generated files.** `cfg.generated` reaches every tool that would otherwise rewrite or stamp a generated file: the global `exclude` above (so ruff, ssort and test_utils skip it), `validate_copyright`'s exclude, and the oxfmt and oxlint `ignorePatterns`. `glob_to_regex` is a fixed filter in the engine (`**` → `.*`, `*` → `[^/]*`, anchored at the end with `$`). The managed files whose first line is the managed header are excluded from `validate_copyright` by name, so sync and the stamp never rewrite each other.

**zizmor scope** (`workflow-lint`). zizmor audits `.github/workflows` and `.github/dependabot.yml`. Every rendered dependabot entry therefore carries a `cooldown` (§2.17), which zizmor's dependabot-cooldown audit requires. docusystem's `docs*.yml` are audited too.

**Floor semantics (S9).** The renderer emits `rev: {{ rev(url, floor) }}`. When the existing file has a rev for the same `repo:` URL that compares ≥ the floor, that rev is kept. Revs compare with `packaging.version` after stripping a leading `v`. The floors are `v6.0.0`, `0.17.0` and `v1.30.1`.

`frappe-nix` is on PATH in an opted-in app's dev shell (N3a `shell.nix`) and in CI (§4.1). A commit made outside the dev shell (a GUI git client, an editor's commit button) skips the two `frappe-nix` hooks with a warning instead of failing: CI's `lint` (compat, through the same hook with `frappe-nix` installed) and `pr-policy` (the commit-message rules) still enforce them. The `uv run`, `yarn` and `committed` hooks need `uv` and `node` on PATH, as any prek setup does.

### 2.15 `committed.toml` and `tools/pyproject.toml` (whole)

`committed.toml` (module `commits`; profile values `commits.allowed-types` and `commits.subject-length`, shown with the `recommended` values, which are frappe's commitlint types) is carbon_frappe's file with the managed header. The second comment paragraph depends on `releases.version-scheme`:

```toml
# frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`); do not edit.
{% if cfg.releases["version-scheme"] == "frappe-major" %}
# Conventional commits. `fix:` → patch, `feat:` → minor. There is no way to bump the major: it is the
# Frappe major, and `frappe-nix policy` refuses `!` and BREAKING CHANGE (S12).
{% else %}
# Conventional commits. `fix:` → patch, `feat:` → minor, `!` or BREAKING CHANGE → major.
{% endif %}
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

`tools/pyproject.toml` (live while any module with a tool here is on, §2.4). `dependencies` is the union of the enabled modules' tools, sorted: `prek` whenever `.pre-commit-config.yaml` is live, `ruff` (`python-lint`), `ty` (`python-types`), `semgrep` (`semgrep`), `zizmor` and `actionlint-py` (`workflow-lint`), `shellcheck-py` (`shell-lint`), `committed` (`commits`). Shown with all of them:

```toml
# frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`); do not edit.
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

**Lock floors (S9).** `--check` reads `tools/uv.lock` and requires, for each tool that is in `dependencies`:

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

Module `releases` (`releases.tool = "release-please"`, the only tool in v1). Profile values: `releases.version-scheme` (only in the bullets below) and `releases.tag-prefix` (`"v"` or `""`; `include-v-in-tag` is `true` exactly when it is `"v"`, and the tag ruleset, assets trigger, guard and audit rows use the same prefix). The branch release-please targets is `branches.integration`, passed by `app-release.yml` (§4.4), not written here. When `readme` is on under `main+tags`, `extra-files` also lists `{ "type": "generic", "path": "README.md" }`, so the release PR moves the installation line's version (§2.19).

```json
{
	"$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
	"include-v-in-tag": true,
	"include-component-in-tag": false,
	"separate-pull-requests": true,
	"bootstrap-sha": "<[tool.frappe-nix.releases].bootstrap-sha; key omitted when unset>",
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
- Under `version-scheme = "frappe-major"`: for the first release, the adoption PR's (Avunu fleet: PR B's) last commit ends with the footer `Release-As: <major>.0.0`, and `npx release-please release-pr --dry-run --target-branch <that PR's branch>` must propose `<major>.0.0` before merging. The PR is then merged with `gh pr merge --squash --body-file <f>` whose last paragraph is that same footer, so the squash commit on the integration branch carries it as a trailing footer (S11). If the release PR still proposes another version, C1 fails it. `frappe-nix policy --pr` still checks the major in any `Release-As` it finds in that PR's commits.
- Under `version-scheme = "semver"` nothing special happens: release-please proposes the next version from the commits, and C1 does not apply.

### 2.17 `.github/dependabot.yml` (whole + local region `updates`)

Module `dependabot`. Profile values: `dependabot.ecosystems` (one switch per ecosystem below), `dependabot.cadence` (`"weekly"` in `recommended`, `"daily"` in the Avunu profile) and `dependabot.cadence-overrides` (per ecosystem), `dependabot.cooldown-days` (3), and, when `docs-site` is on, `docs-site.action-patterns` (the `docs-actions` group and auto-merge patterns), `docs-site.cooldown-exclude-actions` (the github-actions entry's cooldown `exclude`, exact names) and `docs-site.package-names` (the `/docs-site` npm entry's cooldown `exclude`). All three are empty in `recommended`; the Avunu profile sets `["Avunu/docusystem*"]`, `["Avunu/docusystem"]` and `["@avunu/docusystem"]`. The group takes glob patterns, while a docs tool's own check may expect an exact name in the cooldown exclude (docusystem's `doctor` tests `excludes.includes("Avunu/docusystem")`), so the two are separate parameters. Each entry is rendered only when its ecosystem switch is on (and, as before, its `{% if %}` fact holds). `{{ cadence(<ecosystem>) }}` is the override for that ecosystem, else `dependabot.cadence`; a `weekly` cadence renders `day: monday`.

```yaml
# frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`); edit only inside the frappe-nix:local region.
# Every entry targets {{ branches.integration }} and commits as chore(deps)/chore(deps-dev), so no bump cuts a release.
{% if modules.ci %}
# deps.yml decides what merges automatically (§4.5).
{% endif %}
version: 2
updates:
{% if cfg.dependabot.ecosystems["github-actions"] %}
  # One entry for /, which is also the entry a docs tool scaffolds. Separate groups,
  # so a docs or pilot bump never holds up the rest.
  - package-ecosystem: github-actions
    directory: /
    target-branch: {{ branches.integration }}
    schedule: { interval: {{ cadence("github-actions") }} }
    cooldown: { default-days: {{ cfg.dependabot["cooldown-days"] }}{% if docs_cooldown_exclude %}, exclude: {{ docs_cooldown_exclude | tojson }}{% endif %} }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    # frappe-nix's reusable workflows move only through `frappe-nix repo rollout` and `frappe-init --sync` (S5)
    ignore:
      - dependency-name: {{ frappe_nix.owner }}/{{ frappe_nix.name }}
    groups:
{% if docs_actions %}
      docs-actions:             # needs-review: a merge publishes the docs site with the new workflow code
        patterns: {{ docs_actions | tojson }}
{% endif %}
{% if modules["pilot-assets"] %}
      pilot:                    # needs-review: builds release assets
        patterns: ["frappe/pilot*"]
{% endif %}
      actions:
        patterns: ["*"]
        exclude-patterns: {{ (docs_actions + pilot_patterns) | tojson }}   # key omitted when empty
{% endif %}
{% if cfg.dependabot.ecosystems.npm and package_json_live %}
  - package-ecosystem: npm
    directory: /
    target-branch: {{ branches.integration }}
    schedule: { interval: {{ cadence("npm") }} }
    cooldown: { default-days: {{ cfg.dependabot["cooldown-days"] }} }
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
    target-branch: {{ branches.integration }}
    schedule: { interval: {{ cadence("npm") }} }
    cooldown: { default-days: {{ cfg.dependabot["cooldown-days"] }} }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    groups:
      {{ d }}: { patterns: ["*"], update-types: [minor, patch] }
{% endfor %}
{% if discover.docs_site and modules["docs-site"] %}
  # docs-site/: a merge publishes the site, so deps.yml never merges these automatically
  - package-ecosystem: npm
    directory: /docs-site
    target-branch: {{ branches.integration }}
    schedule: { interval: weekly }
    cooldown: { default-days: 7{% if docs_packages %}, exclude: {{ docs_packages | tojson }}{% endif %} }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    groups:
      docs-site-packages: { patterns: ["*"] }
{% endif %}
{% endif %}
{% if cfg.dependabot.ecosystems.uv and tools_live %}
  - package-ecosystem: uv
    directory: /tools
    target-branch: {{ branches.integration }}
    schedule: { interval: {{ cadence("uv") }} }
    cooldown: { default-days: {{ cfg.dependabot["cooldown-days"] }} }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    groups:
      tools: { patterns: ["*"], update-types: [minor, patch] }
{% endif %}
{% if cfg.dependabot.ecosystems["pre-commit"] and precommit_has_remote_hooks %}
  - package-ecosystem: pre-commit
    directory: /
    target-branch: {{ branches.integration }}
    schedule: { interval: {{ cadence("pre-commit") }} }
    cooldown: { default-days: {{ cfg.dependabot["cooldown-days"] }} }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    groups:
      hooks:
        patterns: ["*"]
        exclude-patterns: ["*agritheory/test_utils*"]
      test-utils:               # rendered only when test-utils is on
        patterns: ["*agritheory/test_utils*"]
{% endif %}
{% if cfg.dependabot.ecosystems.nix %}
  - package-ecosystem: nix
    directory: /
    target-branch: {{ branches.integration }}
    schedule: { interval: {{ cadence("nix") }} }
    cooldown: { default-days: {{ cfg.dependabot["cooldown-days"] }} }
    open-pull-requests-limit: 5
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
    # frappe-nix and the org profile move only through `frappe-nix repo rollout` (S5, S38): their bump can rewrite
    # workflow files, which GITHUB_TOKEN can't push. Dependabot moves frappe and the siblings, which never touch one.
    ignore:
      - dependency-name: frappe-nix
{% if profile.source != "builtin" %}
      - dependency-name: standards-profile
{% endif %}
    groups:
      flake-inputs: { patterns: ["*"] }
{% endif %}
{% if discover.gitmodules and cfg.dependabot.ecosystems.gitsubmodule %}
  - package-ecosystem: gitsubmodule
    directory: /
    target-branch: {{ branches.integration }}
    schedule: { interval: {{ cadence("gitsubmodule") }} }
    cooldown: { default-days: {{ cfg.dependabot["cooldown-days"] }} }
    commit-message: { prefix: chore, include: scope }
    labels: [dependencies]
{% endif %}
  # frappe-nix:local-begin updates
  # frappe-nix:local-end updates
```

`docs_actions`, `docs_cooldown_exclude` and `docs_packages` are `docs-site.action-patterns`, `docs-site.cooldown-exclude-actions` and `docs-site.package-names` when `docs-site` is on, else empty lists; `pilot_patterns` is `["frappe/pilot*"]` when `pilot-assets` is on. `package_json_live`, `tools_live` and `precommit_has_remote_hooks` say whether those files are live and whether the pre-commit config has a remote `repo:` block. The github-actions ignore names the repository the app locks frappe-nix from (`Avunu/frappe-nix`, or a fork set through `dev-shell.frappe-nix-url`), read from `flake.lock`; frappe-nix's own repository is the one org name a built-in template may contain (S37).

The rendered output is expanded to block style by oxfmt's rules; the template stores the oxfmt-formatted result. Major npm and uv updates arrive as individual PRs, because the groups only take minor and patch updates. `deps.yml` labels them `needs-review`.

- **Docs-tool compatibility.** For docusystem (Avunu profile), the `/docs-site` npm entry and the github-actions entry for `/` are the entries `docusystem init` scaffolds, with the cooldown exclusions its `doctor` checks (`@avunu/docusystem` and `Avunu/docusystem`, which come from the profile's `docs-site` values). `docusystem upgrade` only adds an entry when none exists for that ecosystem and directory, so on a synced repo it writes nothing. The acceptance test is in N4 (§7), run with the `docs-compat` fixture profile, whose three `docs-site` values are exactly the Avunu profile's; P's own CI runs `docusystem doctor` against the real Avunu profile (§1.3 P).
- **Dependabot ignores for nix.** If the nix ecosystem doesn't honour `ignore` for a flake input, `deps.yml` still never auto-merges a PR that moves the `frappe-nix` or `standards-profile` lock node: `relock-plan` detects it, and the auto-merge job comments `<input> moves through frappe-nix repo rollout` and closes the PR.

### 2.18 `.github/zizmor.yml` (whole)

Module `workflow-lint`. Profile values: none.

```yaml
# frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`); do not edit.
# Every `uses:` is hash-pinned with a version comment. dependabot moves third-party pins;
# `frappe-init --sync` moves Avunu/frappe-nix's.
rules:
  unpinned-uses:
    config:
      policies:
        "*": hash-pin
```

### 2.19 README generated blocks (N5; `blocks`, only when `discover.has_listing`)

Module `readme` (needs `listing`; off in `recommended`). Profile values: `org.license`, `org.copyright-holder`, `org.email`, `org.support-url`, `org.dev-docs-url`, `org.repo-url`, `org.readme.badges`, `org.readme.links`, `readme.install-ref`. Each block template is overridable by an org profile (§8.1). A block that needs an empty org value is exit 2 naming the key (S37), except `org.email` and `org.support-url`, of which the `support` block needs one.

Blocks are delimited by `<!-- frappe-nix:begin <name> -->` and `<!-- frappe-nix:end <name> -->`. They are rendered by `frappe-listing readme --write`, which sync calls (§3.5), from `frappe_nix_tools/data/readme/<name>.md.j2` or the profile's override. The required blocks, in this order, are `header`, `compatibility`, `installation`, `support`, `development` and `license`, plus the hand-written sections in between, as D15 sets out. Missing markers are an error: `frappe-listing readme --check` exits 2 and names the missing block.

- `header`: a centred `<div>` containing:
  - the logo `<img src="<app>/public/images/<app>-logo.svg" width="80">` (only when `icons` is on);
  - the H1 title, and the tagline;
  - badges: CI (`actions/workflows/ci.yml/badge.svg?branch=<branches.integration>`, when `ci` is on), latest release (shields `github/v/release`, when `releases` is on and the repo is on GitHub), licence (`org.license`), and `Frappe v<major>`; then each `org.readme.badges` entry `{label, image, link}` in order;
  - a hero `<picture>` with a dark `<source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/<hero>-dark.webp">` and `<img src="docs/screenshots/<hero>-light.webp" alt="<alt>">`. `<hero>` is the shot with `readme: "hero"`. The `<picture>` is omitted when there are no shots or `screenshots` is off.
- `compatibility`: a table with `Frappe <major> → <branches.release or branches.integration>` and each required app with its range.
- `installation`: Frappe Cloud marketplace text (only when `listing.publish` is on) plus
  ```
  bench get-app <repo URL> --branch <ref>
  bench --site <site> install-app <app>
  ```
  `<repo URL>` is `org.repo-url` with `{repo}` substituted (default `https://github.com/{repo}`; a GitLab or self-hosted organisation sets its own template). `<ref>` is `readme.install-ref` when set; otherwise `branches.release` under `develop+version`, and under `main+tags` the latest released tag, `<tag-prefix><__version__>`, never `main` (which holds unreleased code). The `main+tags` form wraps the code block in `<!-- x-release-please-start-version -->` and `<!-- x-release-please-end -->`, and `README.md` is a release-please extra file (§2.16), so the release PR that bumps `__version__` moves this line in the same commit and the block never drifts.
- `support`: `org.support-url` (default: the repo's issues page), `org.email` when set, and each `org.readme.links` entry.
- `development`: `nix develop` (or `direnv allow`), `prek install` (when `.pre-commit-config.yaml` is live), `frappe-test` (when `tests` is on), and a link to `org.dev-docs-url` (S26).
- `license`: `<org.license>, Copyright (c) <first-commit-year>–present <org.copyright-holder> → license.txt`. `<first-commit-year>` is the year of `git log --reverse --format=%cs | head -1`, which never changes. Nothing in a rendered block depends on today's date (§3.4).

Avunu profile example: `MIT, Copyright (c) 2024–present Avunu LLC → license.txt`, support `mail@avu.nu`.

### 2.20 `.git-blame-ignore-revs` (seed + validated)

Module `hygiene`. Profile values: none.

Seed:

```
# Mass reformats (style: PRs), one squash SHA per line with its subject as a comment.
# GitHub reads this file; locally the dev shell sets blame.ignoreRevsFile.
```

Validation (`frappe-nix compat`):

- every non-comment line is `<40-hex>  # <subject>` (two spaces before `#`);
- every SHA is reachable from `HEAD`. This is checked only when the history is available (`lint` has `fetch-depth: 0`).

In the Avunu fleet, PR B adds PR A's squash SHA. The helper `frappe-nix compat --add-blame-ignore <sha>` appends the line with the commit subject.

The dev shell's enterShell (N3a `shell.nix`) runs `git config blame.ignoreRevsFile .git-blame-ignore-revs` when the file exists and the setting differs.

### 2.21 Marketplace files (N5)

Module `listing` (off in `recommended`), and `screenshots`, `demo` and `icons` for the files those modules name. Profile values: `org.website-url`, `org.docs-url` (the expected URL patterns, L12, and the seed's values).

**`marketplace/listing.toml`** is app-owned and validated by frappe-listing (§5.2). The example is an Avunu fleet app:

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

Expected URL patterns are the profile's templates, with `{app}` and `{app_hyphen}` substituted:

- `website`: `org.website-url` (Avunu profile: `https://avunu.net/open-source/{app}/`);
- `documentation`: `org.docs-url` (Avunu profile: `https://{app_hyphen}.avunu.net/`).

A value that differs is a warning, not an error (docs-and-urls track). With an empty template there is no expected pattern and L12 is skipped. `sync --init-listing` seeds both URLs from the templates, or leaves them as `""` (which L1 then reports) when the templates are empty.

**`marketplace/screenshots.ts`** (module `screenshots`) is app-owned. **`marketplace/shots.d.ts`** is managed: it is the `ShotSpec` types (§5.5), rendered from `frappe_nix_tools/data/shots/shots.d.ts`, the packaged copy of `lib/shots/shots.d.ts`. The spec file is written as

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

**`<app>/demo.py`** (module `demo`) is app-owned: `setup(ctx)` (§5.4). **Icons** (module `icons`) are app-owned sources: `<app>/public/images/<app>-symbolic.svg`, and `<app>-logo.svg`, which `frappe-icon tile` generates and which is committed.

---

## 3. The managed-file mechanism (N3)

### 3.1 Where the data lives

- **Everything lives inside the package** (S10): templates in `py/frappe_nix_tools/frappe_nix_tools/data/templates/**`, with `.j2` meaning Jinja, and **manifest fragments** in `py/frappe_nix_tools/frappe_nix_tools/data/manifest.d/*.json`. The engine reads the union of all fragments through `importlib.resources`, so a `uv tool install` from the git subdirectory has the same data as the Nix build. The built-in profiles live beside them in `data/profiles/`. `templates/app/` (`flake.nix`, `.envrc`, `.gitignore`) is left exactly as in `main`: `frappe-init --app` copies it for an app that has not opted in, and the engine never reads it.
- **Org profile data** (S38) comes from the locked `standards-profile` tree, or from the app's own tree for an in-repo profile (`profile = "./<dir>"`): `profile.toml`, and the optional `templates/` overrides and `[[extra-files]]` templates (§8.1). For an entry with `overridable: true`, the engine renders `<profile tree>/templates/<template>` when that file exists, else the built-in template. An override of a non-overridable template is exit 2 naming it.
- A path listed in more than one fragment is a frappe-nix build error. `tests/standards/sync.nix` checks for it.
- A fragment entry looks like this:

```json
{ "path": ".github/workflows/ci.yml", "template": ".github/workflows/ci.yml.j2", "strategy": "whole",
  "module": "ci", "when": "True", "uses": ["ci.test-timeout-minutes"], "overridable": false,
  "local_regions": [], "floors": {}, "header": "yaml" }
```

`header` is one of `yaml|toml|shell|ini|nix` (`#`), `jsonc|ts` (`//`), or `none` (plain JSON). The header is the first line or lines of the rendered output, and it is part of the byte comparison.

### 3.2 Strategies

| Strategy | Sync writes | Check compares | Local changes |
|---|---|---|---|
| `whole` | The full rendered file, unless the current file is already equal to it under the comparison in the next column | Bytes, after carrying local regions and floors (below) over from the current file. Exception: while `js.tool` isn't `"oxc"`, a YAML, JSON, JSONC or TOML file is compared as parsed data plus its first-line header, so a formatter the app chose (prettier, biome) may restyle it without drift | Only in `local_regions` and through `[tool.frappe-nix.*]` parameters |
| `toml-merge` | Sets managed keys with tomlkit, creating missing tables after the last existing `[tool.*]` table, and leaves every other key byte-for-byte | Parsed values of the managed keys; validation rules for the app-owned keys; forbidden keys | Every key that isn't managed |
| `json-merge` | Sets managed keys and raises floors, keeping key order (new keys appended to the object) and indentation (tabs) | Parsed values; floors as "minimum of range ≥ floor" | Every key that isn't managed |
| `seed` | Only when the file is missing | Presence plus the file's own validation rules | All of it |
| `blocks` | Only the content between markers | Bytes of the block content | Everything outside the markers |

**Local regions** (only in YAML, INI-style, shell-style and Nix files, all of which use `#` comments) look like this:

```
# frappe-nix:local-begin <name>
…kept verbatim…
# frappe-nix:local-end <name>
```

- The names and their order are fixed by the template.
- A missing region is treated as empty and recreated.
- An unknown region, a duplicate region or an unbalanced marker is a configuration error (exit 2).
- After merging, the result MUST still parse (YAML; Nix through `nix-instantiate --parse` when Nix is available) and MUST NOT redefine a managed hook `id`, an `updates` entry with the same ecosystem and directory, an EditorConfig section that's already managed, or a managed flake input. Any of these is exit 2.

**Floors** are expressed in the template as `{{ rev("<repo-url>", "<floor>") }}` and `{{ pin("<owner>/<repo>/<path>", "<sha>", "<version>") }}`, the latter for third-party `uses:` in caller files:

- **pre-commit revs:** the existing value is kept if its version is ≥ the floor.
- **third-party SHA pins** (pilot in `assets.yml`): the existing SHA line is always kept once present, because dependabot owns it.
- **frappe-nix's own `uses:`** (`<frappe_nix.owner>/<frappe_nix.name>/.github/workflows/app-*.yml`) is never a floor. It is always `@<frappe_nix.rev> # v<frappe_nix.version>`.

**Formatting.** After writing, sync runs `yarn -s oxfmt <written files>` when `js.tool = "oxc"` and `node_modules/.bin/oxfmt` exists, and `uv run --frozen --project tools ruff format <app>/__init__.py` when `python-lint` is on. `--check` compares `toml-merge` and `json-merge` files semantically, so formatting changes made by oxfmt are never drift.

### 3.3 `frappe-init --sync` and `--check`: the CLI

```
frappe-init --sync  [--standards <profile>] [--force] [--dry-run] [--skip-lock] [--only <path>[,<path>…]]
                    [--init-listing] [--frappe-version version-16] [--profile-path <dir>]
frappe-init --check [--format text|json|github] [--only …] [--expect-rev <sha>] [--profile-path <dir>]
frappe-init --app   [--standards <profile>] …                      (the existing app-mode flags)
```

- Both imply `--app` mode. Both run in the current directory, which MUST be a git work tree whose `pyproject.toml` `[project].name` names a package containing `hooks.py`.
- **Opt-in (S35).** `--sync` and `--check` on an app without `[tool.frappe-nix]` exit 2 with `this app has not opted in to frappe-nix app standards; run frappe-init --sync --standards minimal|recommended|github:<owner>/<repo>`, and write nothing. `--standards <profile>` creates the table with `profile = "<profile>"` (step 1) and is otherwise ignored when the table exists and names the same profile; naming a different one is exit 2 (edit `profile` instead). `frappe-init --app` without `--standards`, on an app without the table, does exactly what it does in `main`: it copies `templates/app/` and stops. With `--standards`, or on an app that has the table, it copies `templates/app/` with `install_template keep` and then runs `frappe-nix sync --write`.
- `--profile-path <dir>` reads the org profile from a local directory instead of the locked input, for profile authors. It is never rendered into `flake.nix`, it prints a warning on every run, and `--check` with it exits 3 when `CI` is set.
- `frappe-init` sources `lib/sh/app-sync.sh`, which `exec`s `frappe-nix sync --write|--check …` from `frappe-nix-tools` in its `runtimeInputs`.
- **Bootstrap** (an app with no flake, or one that locks frappe-nix `main`), from outside any dev shell:

  ```
  nix run github:Avunu/frappe-nix/release-1#frappe-init -- --app --sync --frappe-version version-16
  ```

  with `--standards <profile>` added the first time. This is the single documented entry point for an adoption PR (Avunu fleet: PR A). It needs only `nix` and `git`. `release-1` exists from `v1.0.0` on, so migrations start after N6 tags it (S32).
- Later runs inside an app: `nix run .#frappe-init -- --sync` is preferred. It uses the frappe-nix from `flake.lock`, and the app-mode flake exposes `apps.frappe-init` (N3a).
- A new app: `frappe-init --app` alone gives today's three files; `frappe-init --app --standards recommended` gives those, then everything the `recommended` profile renders (N3).

`--sync` runs in **two phases** (S32). Each step is a no-op when there is nothing to do.

**Phase A: the flake.**

1. Load `[tool.frappe-nix]`, or create it when `--standards` is given (otherwise exit 2, above). When creating it:
   - `profile` comes from `--standards`;
   - `frappe-major` comes from `--frappe-version`, else the existing `flake.nix` `frappeVersion`, else it is an error;
   - `siblings` comes from `hooks.required_apps`, with the existing flake siblings appended (an existing sibling whose flake URL the generic rule wouldn't produce is written in the object form, so its branch is kept);
   - `integration-branch` is written only when `git symbolic-ref --short refs/remotes/origin/HEAD` names a branch other than the branching model's default.
   Then resolve the profile far enough to know its source (§8.3): a built-in name or an in-repo `./<dir>` needs nothing more; a flake URL is the `standards-profile` input.
2. Render and write `flake.nix` and `.envrc` only. On first opt-in, an existing unmanaged `flake.nix` or `.envrc` that differs from what `templates/app/` renders stops here with exit 2 unless `--force` is given (§2.5).
3. If `flake.lock` is missing, its input set differs from `flake.nix` (this covers adding, removing or re-pointing `standards-profile`), or its `frappe-nix` node's `original` doesn't match the rendered URL (`release-<N>` for the running frappe-nix-tools' major, or `dev-shell.frappe-nix-url` when set): `nix flake lock` (moving frappe-nix in the last case). A pinned tag, rev, fork or mirror in `frappe-nix-url` is therefore kept on every run. The lock now holds a `release-1` commit and, for an org profile, a `standards-profile` commit. Then read `profile.toml` from the locked `standards-profile` tree and finish resolving the configuration (§8.4); a resolution error is exit 2 before phase B writes anything. For frappe-nix's own self-tests only, `FRAPPE_NIX_URL_OVERRIDE` (e.g. `path:$GITHUB_WORKSPACE`) is passed as `--override-input frappe-nix <url>` to the lock step and every later `nix` call; it is never rendered into `flake.nix`, and sync refuses it unless `FRAPPE_NIX_ALLOW_SKEW=1`.
4. Re-read `frappe_nix.rev` from the new lock. If it differs from the running frappe-nix-tools' own rev (a bootstrap from a newer or older release), sync re-executes itself as `nix run --no-pure-eval .#frappe-init -- --sync <same args>` once, so phase B always renders with the frappe-nix-tools that the lock pins. The environment variable `FRAPPE_NIX_SYNC_REEXEC=1` prevents a second re-exec.

**Phase B: everything else.** If `yarn` or `uv` is missing from PATH, sync re-enters through `FRAPPE_NIX_CI=1 nix develop --no-pure-eval -c frappe-nix sync --write --phase b <same args>` once, so the lock steps below always run.

5. Render every other live entry (module on and `when` true, §2.4), and apply its strategy. When this turns a module on that needs a repository setting (`releases`, `dependabot` with `auto-merge`), sync prints `run frappe-nix repo doctor` (§5.6).
6. **Retract** what isn't live. This is how turning a module off removes its files (S36):
   - a `whole` file is deleted when it still opens with its managed header and its local regions are empty (Appendix I, N3); with a non-empty local region it is left, and sync exits 2 naming the region, so local content is never lost silently;
   - a `seed` file is left (it is app content once seeded) and reported;
   - a `blocks` file has its managed blocks and markers removed;
   - in a merged file, each managed key of an off module is removed when its value still equals the rendered one, and is otherwise left as app-owned with a warning (§2.8, §2.12);
   - a caller workflow of an off gate is a `whole` file and is deleted like any other, which removes the gate's job (S39).
7. Delete every match of an applying `retire` rule (§2.4.1), except paths in `retire-keep`.
8. If `tools/pyproject.toml` changed, `tools/uv.lock` is missing, or a lock floor isn't met: `uv lock --project tools [--upgrade-package …]`.
9. If a managed `package.json` key changed or `yarn.lock` is missing: `yarn install --non-interactive`.
10. Seed the missing `nix/node-locks/<key>` for each present sibling, where key ∈ {`frappe/ui`, `erpnext/banking`, `hrms/frontend`, `hrms/roster`}.
11. If the `frappe-nix` or a sibling lock node changed since the last relock, and `--skip-lock` isn't given: `nix run --no-pure-eval .#relock`. The last relocked state is recorded in `nix/uv.lock`'s header comment, which relock writes.
12. If `readme` is on and `discover.has_listing`: `frappe-nix listing readme --write`.
13. `git add -A` every path written or deleted. Nothing is committed.

`--dry-run` prints the unified diff of steps 2, 5, 6 and 7 and the commands the other steps would run, and writes nothing. A dry run on an unbootstrapped app renders phase B against the rev phase A *would* lock, resolved with `git ls-remote https://github.com/Avunu/frappe-nix refs/heads/release-1`.

`--check` performs steps 1, 2, 5, 6 and 7 in memory, then validates (each item only while its module is on):

- the lock floors (step 8);
- that `flake.lock` contains a node for every input in `flake.nix`, and that the `standards-profile` node is locked from the URL `profile` names;
- that the version skew is zero (§3.7);
- the README blocks (`frappe-nix listing readme --check`, when installed);
- `.git-blame-ignore-revs`;
- the validation rules for app-owned keys (§2.12).

It never runs `nix`, `uv` or `yarn`, so it works in no-Nix CI. It reads an org profile through `pin-path standards-profile` (narHash-verified, §5.2 "Pins").

**Exit codes**, which both modes share:

| Code | Meaning |
|---|---|
| 0 | Clean: nothing to write, or everything written. |
| 1 | Drift: a managed file is missing, differs, or should not exist; a retired file is tracked; or a lock floor isn't met. `--check` only. |
| 2 | Invalid configuration that sync can't fix: not opted in; a `[tool.frappe-nix]` or profile schema error; an unknown or unresolvable profile; a module dependency that isn't met (§8.2); an org value a live module needs that is empty; an override of a non-overridable template; an unknown sibling; a forbidden key; a malformed local region; a retracted file with local content; a missing README marker; an unmanaged `flake.nix`/`.envrc` that would be replaced on first opt-in without `--force`; or an app table that explicitly enables a GitHub-only module (S43) on a repository hosted elsewhere. |
| 3 | Environment error: not an app, not a git repo, `flake.lock` unreadable, the org profile tree can't be fetched or fails its narHash, a profile whose `requires-frappe-nix` excludes the running version, version skew. |

When several apply, the highest code wins.

**Output.**

- `text`: one block per file with `path`, `strategy`, the problem, and a unified diff (`--- current` / `+++ rendered`). The last line is `frappe-nix: N file(s) drifted — run \`frappe-init --sync\`` or `frappe-nix: clean`.
- `json`: `{"status":"clean|drift|invalid|error","frappe_nix":{"rev","version"},"profile":{"name","source","rev"},"modules":{"<module>":bool},"files":[{"path","strategy","module","problem","diff"}]}`. `frappe-nix repo audit` consumes it.
- `github`: the text form plus `::error file=<path>::<problem>` annotations.

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
| Which modules are on, and their parameters | The resolved configuration (§8.4): built-in profile < org profile < `[tool.frappe-nix.<module>]` |
| Organisation values | `org.*` from the same merge (S37) |
| App name | `[project].name` |
| Frappe major and branch | `[tool.frappe-nix].frappe-major` |
| Siblings and their order | `[tool.frappe-nix].siblings` (+ `known-apps.json`) |
| Nested frontend dirs | Discovered (§2.2) |
| SPAs without a `package.json` | `[[tool.frappe-nix.typescript.spa]]` |
| Generated files | `[tool.frappe-nix].generated` |
| Strict-checkJs and coverage exemptions | `[[tool.frappe-nix.unchecked-js]]`, `[[tool.frappe-nix.coverage-omit]]` (shrink-only) |
| Dev-shell freshness checks | `[tool.frappe-nix].shell-checks` |
| SCSS globs | `[tool.frappe-nix.stylelint].globs`, or discovered |
| Unit-test paths | Discovered `test/unit/**/*.test.ts`; minimum from `tests.js-coverage-min` |
| Python test setup | `[tool.frappe-nix.tests].setup` |
| Coverage minimum | `[tool.coverage.report].fail_under` (app-owned value, managed table) |
| pilot assets | `pilot-assets.enable` + `[tool.bench.assets]` |
| Integration and release branches | `releases.branching` → `branches` (§2.3, S41); the release branch is `version-<frappe-major>` |
| Site name | `[tool.frappe-nix].site` |

### 3.6 Headers

| Kind | Header |
|---|---|
| `yaml`, `toml`, `ini`, `shell`, `nix` | `# frappe-nix:managed — generated by frappe-nix (\`frappe-init --sync\`); do not edit[; edit only inside the frappe-nix:local region(s)].` |
| `jsonc`, `ts` | The same text after `// `. |
| `none` | No header, because the format allows no comments. Listed in the manifest and in `docs/app-standards/managed-files.md`. |

The header names no version and no profile, so a frappe-nix or profile release only touches files whose content actually changed.

### 3.7 Version skew

Let `rev` be the `frappe-nix` rev in `flake.lock`. All of the following MUST hold:

- Every `<frappe_nix.owner>/<frappe_nix.name>/.github/workflows/app-*.yml@<sha>` in the caller workflows has `<sha> == rev` and the comment `# v<frappe_nix_tools.__version__>`.
- The running `frappe-nix` was built from `rev`. Nix knows this directly, because the dev shell's `frappe-nix` comes from the locked frappe-nix. In CI the install step records the rev, and `lint` passes it as `--expect-rev "$FRAPPE_NIX_EXPECT_REV"` (§4.1). `frappe-nix sync --check` is the same as `frappe-init --check`, and both accept `--expect-rev`.

If any of these fails, `--check` exits 3. The exceptions, the only places that set `FRAPPE_NIX_ALLOW_SKEW=1`:

- `frappe-nix repo audit --against latest` and the nightly `drift` job;
- frappe-nix's own self-tests: when an `app-*.yml` gets a non-empty `frappe-nix-override`, the install step exports `FRAPPE_NIX_ALLOW_SKEW=1` and `lint` omits `--expect-rev`. A commit can't contain its own SHA, so the fixture's rendered callers can never match the commit under test.

---

## 4. CI: the reusable workflows and their callers (N4)

Everything in this section exists in an app only while the `ci` module is on (S36), and each workflow and job only while its own module is on:

| Gate or workflow | Context (required when listed in §4.8) | Present when |
|---|---|---|
| `lint` (`app-lint.yml`) | `ci / lint` | `ci`. It always runs `frappe-nix sync --check`, the managed-file gate, so it exists for every app with CI; its other steps follow their modules. |
| `typecheck` (`app-typecheck.yml`) | `ci / typecheck` | `ci` and (`typescript`, or `tests.js-unit` with `discover.unit_tests`) |
| `test` (`app-test.yml`) | `ci / test` | `ci` and any of `tests`, `python-types`, `nix-lint`, or a non-empty `shell-checks` (all need the dev shell) |
| `marketplace` (`app-marketplace.yml`) | `ci / marketplace` | `ci` and `listing` |
| `pr-policy` (`app-pr-policy.yml`, own caller) | `ci / pr-policy` | `ci` and `commits` |
| `release.yml` → `app-release.yml` | none | `ci` and `releases` |
| `deps.yml` → `app-deps.yml` | none | `ci` and `dependabot` |
| `nightly.yml` → `app-nightly.yml` | none | `ci` and `ci.nightly` |
| `assets.yml` → pilot | none | `pilot-assets` (which needs `ci` and `releases`) |

The `gates` context list (§2.3) is the first five rows' gates that are present. **The gate logic is a command, not YAML (S43).** `frappe-nix gate lint|typecheck|marketplace` runs that gate's steps in order, each only when its module is on (it resolves the configuration itself), and exits non-zero when any step failed, after running all of them. The `test` gate is `frappe-test --ci`, which reads the same switches (§5.1). A reusable workflow therefore only checks out, installs node, uv and frappe-nix-tools, runs the gate command, and uploads its report; any other CI (GitLab CI, Woodpecker, a laptop) runs the same command after the same setup, which `docs/app-standards/ci.md` lists. Workflow-level steps that do need a module switch (the node cache, the frappe `node_modules` install) read it from `frappe-nix config --github-output modules` (step id `cfg`). The gate workflows take no input per module, and a parameter change never needs a caller change; only turning a whole gate or workflow on or off changes the rendered callers. (`app-nightly.yml` is the exception: its optional jobs take `run-*` inputs, §4.6.)

`<integration>` below is `branches.integration` (any branch name, S41), passed to each reusable workflow as the input `integration-branch` by the rendered caller; `<release>` is `branches.release`, empty under `main+tags`.

**Private sources (S43).** Every reusable workflow accepts the optional secret `FETCH_TOKEN`, which callers pass from `secrets.FRAPPE_NIX_FETCH_TOKEN`. When it is non-empty, the install step exports it as `FRAPPE_NIX_FETCH_TOKEN`, which `pin-path` and `minibench` use for authenticated fetches of a private org profile, frappe fork or sibling (§5.2 "Pins"), and the Nix setup adds `access-tokens = github.com=<token>` to the job's `nix.conf`. It needs only Contents read on those repositories. For Dependabot runs it must also be a Dependabot secret.

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
- **Install frappe-nix-tools.** `lint`, `typecheck`, `marketplace` and `pr-policy` all use the same step. Resolving the configuration afterwards fetches an org profile with `pin-path standards-profile`, so these jobs need no extra checkout:

```yaml
- name: Install frappe-nix-tools at the frappe-nix revision flake.lock pins
  env:
    OVERRIDE: ${{ inputs.frappe-nix-override }}
  run: |
    set -euo pipefail
    node="$(jq -c '.nodes[.nodes.root.inputs["frappe-nix"]].locked' flake.lock)"
    rev="$(jq -r .rev <<<"$node")"; src="$(jq -r '"\(.owner)/\(.repo)"' <<<"$node")"   # Avunu/frappe-nix or a fork
    if [ -n "$OVERRIDE" ]; then
      uv tool install --python 3.14 "$GITHUB_WORKSPACE/$OVERRIDE/py/frappe_nix_tools"
      echo "FRAPPE_NIX_ALLOW_SKEW=1" >> "$GITHUB_ENV"      # self-test only (§3.7)
      echo "FRAPPE_NIX_EXPECT_REV=" >> "$GITHUB_ENV"
    else
      uv tool install --python 3.14 "frappe-nix-tools @ git+https://github.com/${src}@${rev}#subdirectory=py/frappe_nix_tools"
      echo "FRAPPE_NIX_EXPECT_REV=$rev" >> "$GITHUB_ENV"
    fi
```

`lint` passes `--expect-rev "$FRAPPE_NIX_EXPECT_REV"` only when the variable is non-empty. The package is self-contained (S10), so this install has every template and data file.

- **Nix setup.** The `test`, `relock-plan` and nightly Nix jobs all use the same steps, in this order:
  1. Free disk: `sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /opt/hostedtoolcache/CodeQL`.
  2. nix-installer.
  3. Cache selection (S15). Let `trusted` be `github.ref == format('refs/heads/{0}', inputs.integration-branch) && (github.event_name == 'push' || github.event_name == 'schedule')`. A step maps `TOKEN` in its `env:` to `${{ (trusted) && secrets.NIX_CACHE_TOKEN || secrets.NIX_CACHE_READ_TOKEN }}`; nothing else ever sees the write token.
     - `inputs.nix-cache == ''` → **no cache action**. (magic-nix-cache is throttled in every bench-sized job and evicts the node_modules caches; frappe-nix's own self-tests may still use it for their small closures.)
     - `cachix:<name>` → `cachix/cachix-action` with `name: <name>`, `authToken` from that step's mapped token, and `skipPush: ${{ !(trusted) }}`.
     - `attic:<endpoint>/<cache>` → `nix profile install --inputs-from . nixpkgs#attic-client` (pinned by the app's `flake.lock`), then always `attic login ci <endpoint> ${TOKEN:+"$TOKEN"}` (a public cache needs no token but still needs the server configured), then `attic use ci:<cache>`. A final step `if: success() && <trusted>` runs `attic push ci:<cache> ./result .dev-dist/devshell-profile` with the write token.
  4. Every `nix develop` uses `--profile .dev-dist/devshell-profile`, so the dev-shell closure can be pushed.

  Org setup: `FRAPPE_NIX_CACHE` is an org variable; `FRAPPE_NIX_CACHE_TOKEN` (write) is an org **Actions** secret only; `FRAPPE_NIX_CACHE_READ_TOKEN` (pull-only, or unset for a public cache) is an org Actions secret **and** an org Dependabot secret. That is the whole drop-in. Dependabot runs, which execute bumped third-party code, never hold a credential that can write the cache.
- **Self-test only.** The input `frappe-nix-override` (a path relative to the workspace) adds `--override-input frappe-nix "path:$GITHUB_WORKSPACE/<override>"` to every `nix` call, installs frappe-nix-tools from that path, and allows skew (§3.7).

### 4.2 The gate workflows: `app-lint.yml`, `app-typecheck.yml`, `app-test.yml`, `app-marketplace.yml`

1.1's single `app-ci.yml` is split so that a caller includes only the gates the app enables (S39). Each file holds exactly one job, named as its gate, and takes the subset of these inputs it uses:

```yaml
on:
  workflow_call:
    inputs:
      app:                  { type: string, required: true }
      app-root:             { type: string, default: "." }
      integration-branch:   { type: string, default: "develop" }   # branches.integration (S41)
      nix-cache:            { type: string, default: "" }      # app-test.yml: "", "cachix:<name>", "attic:<endpoint>/<cache>"
      test-timeout-minutes: { type: number, default: 75 }      # app-test.yml
      frappe-nix-override:  { type: string, default: "" }      # frappe-nix self-test only
    secrets:
      NIX_CACHE_TOKEN:      { required: false }                # app-test.yml; write; used only on trusted runs (S15)
      NIX_CACHE_READ_TOKEN: { required: false }                # app-test.yml
      FETCH_TOKEN:          { required: false }                # every gate; private profile, frappe or siblings (§4)
    outputs:
      coverage: { value: "${{ jobs.test.outputs.coverage }}" }  # app-test.yml only, e.g. "63.2"
permissions: {}
```

Every gate job carries `if: ${{ !startsWith(github.ref, 'refs/heads/version-') }}` (a no-op under `main+tags`). The 1.1 input `run-test` is gone: frappe-nix's self-test simply doesn't call `app-test.yml` on PRs (§7 N4).

**Context names (S3, S39).** In the rendered `ci.yml` the caller job ids are `lint`, `typecheck`, `test` and `marketplace`, and every one has `name: ci`, so the check runs are `ci / lint`, … as in 1.1. N4's `selftest-ci` asserts these exact check-run names on its first run. If GitHub were to report the caller job id instead of its name, the fallback is a single reusable `app-ci.yml` with one boolean input per gate and job-level `if: inputs.<gate>`, called from one caller job `ci`; a disabled gate then shows as a skipped job, which is acceptable only because §4.8 never lists a disabled gate. N4 records which form shipped in `docs/app-standards/ci.md`.

- **Dispatch mode** is when `github.event_name == 'workflow_dispatch'`. In that mode, the first step of every gate job looks up the open PR whose `headRefName == github.ref_name`. If one exists, it asserts `headRefOid == github.sha` and fails with `::error::stale dispatch` otherwise. Every gate job therefore has `pull-requests: read` (with explicit permissions, an unlisted scope is `none`, and the lookup would fail with "Resource not accessible by integration").
- **Ratchet base** (`BASE`, S24).
  - `pull_request`: `git merge-base "$BASE_SHA" HEAD`, with `BASE_SHA` = `github.event.pull_request.base.sha` passed through `env:`.
  - dispatch: `git merge-base "origin/$INTEGRATION" HEAD`.
  - push to the integration branch, and `schedule`: the ratchet is skipped.

Steps marked [*module*] run only when that module is on. Steps that need `package.json` or `tools/` run only when those files are live. In `lint`, `typecheck` and `marketplace`, the steps from "install frappe-nix-tools" onwards (excluding uploads) are what `frappe-nix gate <job>` runs, with the ratchet base passed as `--base "$BASE"` and dispatch mode detected from `GITHUB_EVENT_NAME`; the workflow itself runs only the setup steps before it, the single `frappe-nix gate <job> --format github` step, and the upload.

| Job (`name:`) | `runs-on` / timeout | Permissions | Steps |
|---|---|---|---|
| `lint` | ubuntu-latest / 20 | `contents: read`, `pull-requests: read` | <ol><li>dispatch guard (above)</li><li>checkout, `fetch-depth: 0`</li><li>setup-node (`node-version-file: ${{ inputs.app-root }}/package.json`)</li><li>`actions/cache` of `node_modules`, keyed on `yarn.lock`</li><li>`yarn install --frozen-lockfile --non-interactive`</li><li>setup-uv, with `cache-dependency-glob: ${{ inputs.app-root }}/tools/uv.lock`</li><li>`uv lock --check --project tools && uv sync --frozen --project tools`</li><li>install frappe-nix-tools; the `cfg` step</li><li>`frappe-nix sync --check --format github`, plus `--expect-rev "$FRAPPE_NIX_EXPECT_REV"` when that is non-empty</li><li>when `.pre-commit-config.yaml` is live: `uv run --frozen --project tools prek run --all-files --show-diff-on-failure`</li><li>[*test-utils*, with either hook in `test-utils.hooks`] `frappe-nix minibench --mode customizations --dest .dev-dist/minibench` and then, from the worktree it creates, `prek run --hook-stage manual validate_customizations --all-files --show-diff-on-failure` and the same for `clean_customized_doctypes`. minibench exits 1 if `sites/`, `env/` or `apps/` is missing (the guard).</li><li>[*semgrep*] semgrep (below)</li><li>`frappe-nix ratchet --base "$BASE"`, unless the ratchet is skipped for this event; it runs only the ratchets whose modules are on (§5.9)</li><li>`yarn -s ci:lint` if `package.json` has that script</li></ol> |
| `typecheck` | ubuntu-latest / 20 | `contents: read`, `pull-requests: read` | <ol><li>dispatch guard</li><li>checkout</li><li>node</li><li>cache</li><li>`yarn install --frozen-lockfile`</li><li>setup-uv; install frappe-nix-tools</li><li>`export FRAPPE_PATH="$(frappe-nix pin-path frappe)"` into `$GITHUB_ENV`: the app's locked frappe tree, fetched and narHash-verified (carbon's `compile` and `audit:drift` read it)</li><li>[*typescript*] if `[tool.frappe-nix] frappe-node-modules = true`: `yarn --cwd "$FRAPPE_PATH" install --frozen-lockfile --ignore-scripts --non-interactive`, cached on frappe's `yarn.lock` hash</li><li>[*typescript*] if the `typecheck` script exists: `yarn -s typecheck` (the managed solution, then each SPA's `check`)</li><li>[*typescript* with `check-js`] if `[[tool.frappe-nix.unchecked-js]]` is non-empty: `frappe-nix unchecked-js --stale` (§2.9)</li><li>[*tests* with `js-unit`] if `test:unit` exists: assert at least one `test/unit/**/*.test.ts`, then `yarn -s test:unit`</li><li>for each nested frontend whose `package.json` has a `typecheck` script: `yarn --cwd <d> install --frozen-lockfile && yarn --cwd <d> typecheck`</li><li>[*typescript*] if `types/doctypes.d.ts` is tracked: `frappe-nix minibench --mode doctypes --dest .dev-dist/minibench`, then `yarn -s frappe-types gen-doctypes --bench .dev-dist/minibench --app <app> --include-siblings <csv> --out types/doctypes.d.ts --check`. gen-doctypes merges the app's Custom Field definitions (`custom/*.json` and `fixtures/*.json`, which minibench copies) into the core doctypes they extend.</li><li>[*typescript*] if `[tool.frappe-nix.typescript] audit-consumer = true`: `yarn -s frappe-types audit-consumer --app . --strict --types 'types/*.d.ts'`. Otherwise, when the subcommand exists, it runs without `--strict` and its summary is a `::notice::`. The default flips to on in a later frappe-nix MINOR, once frappe-types ships `--types` and the string-literal fix; today `--strict` fails 9 of 12 apps, partly on false positives.</li><li>`yarn -s ci:typecheck` if present (it sees `FRAPPE_PATH`)</li></ol> |
| `test` | ubuntu-latest / `inputs.test-timeout-minutes` | `contents: read`, `pull-requests: read` | <ol><li>dispatch guard</li><li>checkout, `fetch-depth: 0`</li><li>Nix setup (§4.1)</li><li>**Clean bench build**, `nix build .#default -L --no-pure-eval` (also compiles every app's assets, so it exercises S30): always on push, schedule and dispatch; on `pull_request` only when `git diff --name-only "$(git merge-base "$BASE_SHA" HEAD)" HEAD` touches `package.json`, `yarn.lock`, `flake.nix`, `flake.lock`, `nix/**`, `vite*.config.*`, `scripts/vite-register.mjs`, `<app>/public/**`, a nested frontend, an SPA root or `<app>/hooks.py`. Every release PR is dispatched, so it always gets the build.</li><li>`FRAPPE_NIX_CI=1 nix develop --no-pure-eval --profile .dev-dist/devshell-profile -c frappe-test --ci` (the single shell entry; §5.1). `--ci` turns on exactly the stages whose modules are on: tests and coverage (`tests`), testmap and composition (`tests.testmap`, `tests.composition`), ty (`python-types`), nix-lint (`nix-lint`) and the `shell-checks` stage.</li><li>`if: failure()`: tail 200 lines of each `.frappe-nix/bench/logs/*.log` in a group</li><li>`if: always()`: upload `.dev-dist/test/` as the artifact `test-report`</li><li>cache push (§4.1, trusted runs only)</li><li>the output `coverage` is read from `.dev-dist/test/frappe-test-report.json` (empty when `tests` is off)</li></ol> |
| `marketplace` | ubuntu-latest / 30 | `contents: read`, `pull-requests: read` | <ol><li>dispatch guard</li><li>checkout</li><li>`sudo apt-get install -y --no-install-recommends pkg-config libmysqlclient-dev` (mysqlclient has no Linux wheel, and L9 installs frappe from source; registry-dryrun track)</li><li>setup-uv, `cache-dependency-glob: ${{ inputs.app-root }}/flake.lock`</li><li>install frappe-nix-tools</li><li>`EIO_BACKEND=posix frappe-nix listing check --format github` (§5.2: listing, hooks metadata against the org values, pyproject ranges, version, override allow-list, logo structure when `icons` is on, registry semgrep at the pinned marketplace rev with the baseline, pilot get-app validator at the pinned pilot rev with the dependency apps on the bench when `listing.getapp-check` is on)</li><li>upload `.dev-dist/marketplace/` as the artifact `marketplace-report`</li></ol>The pip/uv caches for L9's validation bench are kept under `actions/cache` keyed on the pinned frappe and pilot revs; `selftest-product` (N5) measures L9 and fails if a cold run exceeds 25 minutes, leaving 5 minutes of margin. |

There is no `version-guard` job. `version-*` only ever moves through the release workflow's REST PATCH, which raises no `push` event, so a push-triggered guard would never run. The fast-forward job asserts the D8 invariant itself right after the PATCH (§4.4), and audit row A2 checks it nightly. Under `main+tags` there is no release branch and nothing to guard.

**The semgrep step** in `lint` (module `semgrep`; `semgrep.frappe-rules` and `semgrep.test-correctness` switch the first two scans), with `RULES` set to the `frappe-semgrep-rules` store path read from the app's flake.lock (`frappe-nix pin-path frappe-semgrep-rules` fetches and verifies that tree, then prints its path):

```
RULES="$(frappe-nix pin-path frappe-semgrep-rules)"
uv run --frozen --project tools semgrep scan --error --metrics=off --config "$RULES/rules" <app>
uv run --frozen --project tools semgrep scan --error --metrics=off --include='**/test_*.py' \
  --config "$(frappe-nix data-path semgrep/test-correctness.yml)" <app>
if [ -d semgrep ]; then
  uv run --frozen --project tools semgrep scan --error --metrics=off --config semgrep <app>
fi
```

`r/python.lang.correctness`, which carbon_frappe uses, is dropped because it is unpinned.

### 4.3 `app-pr-policy.yml`: the job behind `ci / pr-policy`

Module `commits` (with `ci`). The major rule applies only under `releases.version-scheme = "frappe-major"`, and `frappe-nix policy` reads that from the resolved configuration.

```yaml
on:
  workflow_call:
    inputs:
      app:                 { type: string, required: true }
      integration-branch:  { type: string, default: "develop" }
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
3. install frappe-nix-tools;
4. `frappe-nix policy --pr`, with `env` `GH_TOKEN: ${{ github.token }}`, `INTEGRATION` (`inputs.integration-branch`), `EVENT_NAME`, `BASE_REF` (`github.event.pull_request.base.ref`), `HEAD_SHA` (`github.event.pull_request.head.sha || github.sha`), `PR_TITLE` and `REF_NAME`. On a dispatch, `BASE_REF` and `PR_TITLE` are empty; `frappe-nix policy` then takes them from the PR it looks up (§5.9).

What `frappe-nix policy --pr` checks is in §5.9.

### 4.4 `app-release.yml`: releases without an App

Module `releases` (with `ci`). Profile values: `releases.branching`, `listing.publish`, `listing.registry-fork`, `pilot-assets.enable`, all rendered into the caller's `with:` (§4.7).

```yaml
on:
  workflow_call:
    inputs:
      app:                { type: string, required: true }
      integration-branch: { type: string, default: "develop" }   # branches.integration
      release-branch:     { type: string, default: "" }          # branches.release: "version-16", or "" under main+tags
      publish:            { type: boolean, default: false }      # caller: listing.publish && vars.MARKETPLACE_PUBLISH == 'true'
      pilot-assets:       { type: boolean, default: false }
      registry-fork:      { type: string, default: "" }          # listing.registry-fork; required when publish is true
    secrets:
      REGISTRY_TOKEN: { required: false }
    outputs:
      released: { value: "${{ jobs.release-please.outputs.releases_created }}" }
      tag:      { value: "${{ jobs.release-please.outputs.tag_name }}" }
permissions: {}
```

| Job | `needs` / `if` | Permissions | What it does |
|---|---|---|---|
| `release-please` | `if: github.ref == format('refs/heads/{0}', inputs.integration-branch)` | `contents: write`, `pull-requests: write` | release-please-action with `config-file`, `manifest-file` and `target-branch: <integration>`. Outputs: `releases_created`, `sha`, `tag_name`. If the action fails because the repository doesn't let GitHub Actions create pull requests, the job adds `::error::` naming the setting and `frappe-nix repo doctor` (§5.6). |
| `dispatch-ci` | needs release-please; `if: always() && needs.release-please.result == 'success'` | `actions: write`, `checks: read`, `pull-requests: read` | Finds the open PR: `gh pr list --base <integration> --label 'autorelease: pending' --state open --json number,headRefName,headRefOid`. Accepts it only if `headRefName == "release-please--branches--<integration>--components--<package-name>"` or `"release-please--branches--<integration>"`. Then checks each required context on the head SHA independently (`gh api repos/$R/commits/$SHA/check-runs?check_name=<ctx>`): if `ci / lint` is absent, `gh workflow run ci.yml --ref "$head"`; if `ci / pr-policy` is absent and `pr-policy.yml` exists, `gh workflow run pr-policy.yml --ref "$head"`. A dispatch that failed or was cancelled by a concurrency group is therefore retried by the next run. Idempotent under the daily cron. |
| `fast-forward` | needs release-please; `if: needs.release-please.outputs.releases_created == 'true' && inputs.release-branch != ''` | `contents: write`, `actions: write` | `gh api repos/$R/git/ref/heads/$VB` exists → `gh api -X PATCH repos/$R/git/refs/heads/$VB -f sha=$SHA -F force=false`; otherwise `gh api -X POST repos/$R/git/refs -f ref=refs/heads/$VB -f sha=$SHA`, which happens on the first release. GitHub refuses anything that isn't a fast-forward. `$VB` is `inputs.release-branch`. `$SHA` is a commit already on the integration branch, so the PATCH never introduces a new workflow file (S29). **Guard (D8):** it then reads the ref back and the tag this run created, `needs.release-please.outputs.tag_name` (peeled), and fails with `::error::` unless they are equal. This holds under both version schemes: under `semver` the release branch `version-16` carries tags `v1.x`, `v2.x`, … and never a `v16.*` tag. Under `frappe-major` it also fails unless that tag starts with `<tag-prefix><major>.`. Then, if `inputs.pilot-assets`: `gh workflow run assets.yml --ref "$VB"` (S14). |
| `assets-on-tag` | needs release-please; `if: needs.release-please.outputs.releases_created == 'true' && inputs.release-branch == '' && inputs.pilot-assets` | `actions: write` | `main+tags` only: `gh workflow run assets.yml --ref "$TAG"`, so pilot names the release `assets-<tag>`. |
| `publish` | needs fast-forward (or release-please under `main+tags`); `if: inputs.publish` | `contents: write` (to edit the release notes) | `env: REGISTRY_TOKEN: ${{ secrets.REGISTRY_TOKEN }}`, `FORK: ${{ inputs.registry-fork }}`, `TAG`, `VB`. Fails with `::error::` naming the missing secret if `$REGISTRY_TOKEN` is empty, or the missing value if `$FORK` is. Otherwise: install frappe-nix-tools; `GH_TOKEN="$REGISTRY_TOKEN" frappe-nix listing registry --tag "$TAG" --branch "${VB:-$INTEGRATION}" --fork "$FORK"`. On success, `gh release edit "$TAG" --notes-file …`, with `GITHUB_TOKEN`, appends `Marketplace: <registry PR URL>`. |

- **Merging the release PR** is the "ship it" moment. In the Avunu fleet (plan §7 decision 6), a person or the main agent does it. GitHub then cuts the tag through release-please on the next run: either the `push` from a human merge, or the daily cron if the merge was done with `GITHUB_TOKEN`.
- **Concurrency** is set by the caller: `group: release`, `cancel-in-progress: false`.

### 4.5 `app-deps.yml`: relock, auto-merge, sweep

Module `dependabot` (with `ci`). Profile values: `dependabot.auto-merge` (the `auto-merge` input; `true` in both built-in profiles that enable dependabot), and `dependabot.ecosystems` (only ecosystems that are on can produce PRs). A job for an ecosystem that is off never triggers, because no such PR exists.

```yaml
on:
  workflow_call:
    inputs:
      app:                { type: string, required: true }
      integration-branch: { type: string, default: "develop" }
      nix-cache:          { type: string, default: "" }
      auto-merge:         { type: boolean, default: true }
      merge-method:       { type: string, default: "squash" }   # first of repo-policy.merge-methods
      relock-cron:        { type: string, default: "0 8 * * 1" } # ci.schedules.deps-relock; "" = no weekly relock
    secrets:
      NIX_CACHE_TOKEN:      { required: false }
      NIX_CACHE_READ_TOKEN: { required: false }
      FETCH_TOKEN:          { required: false }
permissions: {}
```

**Auto-merge safety.** Auto-merge is only safe when required checks protect the integration branch, and it only works when the repository allows it; `recommended` turns on `dependabot.auto-merge` but leaves `repo-policy` off, so neither can be assumed. Before any `gh pr merge --auto`, the job (`auto-merge`, `relock-push` or `relock-upgrade`) reads `GET /repos/{r}/rules/branches/<integration>` (readable with `contents: read`) and the classic protection summary, and requires a `required_status_checks` rule that lists every enabled gate's `ci / <gate>` context. If none does, or if `gh pr merge --auto` fails because auto-merge is disabled for the repository, the job labels the PR `needs-review`, writes a `::notice::` naming `frappe-nix repo doctor`, and succeeds. Every job that applies a label first runs `gh label create <name> --color <c> --description <d> --force` for it (idempotent; `pull-requests: write` suffices), so the `dependencies`, `needs-review` and `screenshots` labels exist without `repo-policy`.

Let `isDependabot` be `github.event_name == 'pull_request' && github.event.pull_request.user.login == 'dependabot[bot]'`. zizmor's bot-conditions rule requires reading the PR author rather than `github.actor`.

**What dependabot `nix` moves.** `frappe` and the siblings only (S5). Their bump changes `flake.lock`, `nix/uv.lock` and possibly `nix/node-locks/**`, never a workflow file, so `relock-push` can push it with `GITHUB_TOKEN`. frappe-nix and `standards-profile` move through `frappe-nix repo rollout` (§5.8) or a manual commit (S5, S38).

| Job | `if` | Permissions | Steps |
|---|---|---|---|
| `relock-plan` | `isDependabot && startsWith(github.head_ref, 'dependabot/nix/')` | `contents: read` | <ol><li>checkout the PR head SHA, `fetch-depth: 0`</li><li>Nix setup (read token only)</li><li>if the PR moves the `frappe-nix` or the `standards-profile` lock node (compare `flake.lock` against the merge-base): write `rollout-input-moved` to the job output and stop; the auto-merge job closes the PR (§2.17)</li><li>`nix run --no-pure-eval .#relock`</li><li>`FRAPPE_NIX_CI=1 nix develop --no-pure-eval -c frappe-init --sync --skip-lock`</li><li>if `pyproject.toml` changed: `nix run --no-pure-eval .#relock` again</li><li>`git add -A && git diff --cached --binary > relock.patch` (sync stages its writes, and new files such as fresh `nix/node-locks/**` are untracked, so the plain `git diff` would miss both)</li><li>if `relock.patch` names any path under `.github/workflows/`: fail with `::error::relock would change a workflow; run frappe-nix repo rollout` (S29)</li><li>output `empty=true\|false`; upload the patch as an artifact. This job runs PR code, so it has no write token and no secrets apart from the read-only cache token.</li></ol> |
| `relock-push` | needs relock-plan; `needs.relock-plan.outputs.empty == 'false'` | `contents: write`, `actions: write`, `pull-requests: write` | <ol><li>checkout the head ref, `persist-credentials: false`</li><li>download the patch; `git apply --index relock.patch`; nothing else runs</li><li>refuse if the index touches `.github/workflows/` (S29)</li><li>`git -c user.name=github-actions[bot] -c user.email=41898282+github-actions[bot]@users.noreply.github.com commit -m "chore(deps): relock and sync after flake bump [dependabot skip]"`</li><li>push to the explicit token remote (§4.1) `HEAD:$HEAD_REF`</li><li>`gh workflow run ci.yml --ref "$HEAD_REF" && gh workflow run pr-policy.yml --ref "$HEAD_REF"`</li><li>if `inputs.auto-merge`: `gh pr merge --auto --<merge-method> "$PR_URL"` under the auto-merge safety rule above, which is the only place a nix PR's auto-merge is enabled after a relock</li></ol>The push creates only 0-job `action_required` runs, so it can't loop. `[dependabot skip]` lets Dependabot keep rebasing; each rebase triggers relock again, and a new head SHA cancels any pending auto-merge only after the required checks re-run on it. |
| `auto-merge` | `isDependabot && inputs.auto-merge` | `contents: write`, `pull-requests: write` | `fetch-metadata`, then the decision table below. When the verdict is auto, `gh pr merge --auto --<merge-method> "$PR_URL"` under the auto-merge safety rule above; when it is review, `gh pr edit "$PR_URL" --add-label needs-review`. For `nix` this job never enables auto-merge itself: it `needs: relock-plan` and enables it only when `relock-plan` reported `empty == 'true'` (nothing to relock). Otherwise `relock-push` enables it after its push, so a stale `nix/uv.lock` can't merge on green checks that ran before the relock landed. When `relock-plan` reported `rollout-input-moved`, it comments and closes the PR. |
| `sweep` | `(github.event_name == 'schedule' && github.event.schedule != inputs.relock-cron) \|\| github.event_name == 'workflow_dispatch'` | `actions: write`, `checks: read`, `pull-requests: read` | For each open PR whose author is `app/dependabot` or `app/github-actions`, check each required context on its head SHA independently: dispatch `ci.yml` when `ci / lint` is absent, and `pr-policy.yml` when `ci / pr-policy` is absent. This is the fallback if a Dependabot-triggered job can't be granted `actions: write` (inferred, not proven), and it retries a dispatch that failed or was cancelled. |
| `relock-upgrade` | `(inputs.relock-cron != '' && github.event.schedule == inputs.relock-cron) \|\| (github.event_name == 'workflow_dispatch')` | `contents: write`, `pull-requests: write`, `actions: write` | <ol><li>checkout the integration branch, `persist-credentials: false`</li><li>Nix setup, with the read token only even though it is a schedule: it resolves new third-party packages</li><li>`nix run --no-pure-eval .#relock -- --upgrade`</li><li>if nothing changed, exit 0</li><li>otherwise commit `chore(deps): relock python` on branch `standards/relock-python`, refuse workflow paths (S29), and force-push to the token remote (§4.1)</li><li>open or update the PR with `GITHUB_TOKEN`</li><li>dispatch `ci.yml` and `pr-policy.yml` on it</li><li>if `inputs.auto-merge`: `gh pr merge --auto --<merge-method>`, under the auto-merge safety rule above</li></ol> |

**The PAT-free automation spike (N4, not blocking v1).** N4 tests, on a scratch repo, whether a `GITHUB_TOKEN` can land a commit that changes a workflow file through the Git Data API (`POST git/blobs`, `git/trees`, `git/commits`, then `PATCH git/refs/heads/<branch>` with `force=false`). If it can, a later frappe-nix MINOR adds an opt-in `frappe-nix-bump` job that does what `frappe-nix repo rollout` does. If it can't, `frappe-nix repo rollout` stays the only path and nothing changes. The result is written into `docs/app-standards/github.md`.

**Auto-merge decisions**, using `fetch-metadata`'s `package-ecosystem`, `update-type`, `dependency-group`, `dependency-names` and `directory`. The ecosystem strings are fetch-metadata v3's own values; N4 adds a unit test with recorded metadata for each row.

| `package-ecosystem` | Merged automatically | Labelled `needs-review` |
|---|---|---|
| `github_actions` | the `actions` group | the `docs-actions` group (`docs-site.action-patterns`; a merge publishes the docs site) and the `pilot` group (`frappe/pilot*`); `Avunu/frappe-nix` never arrives (ignored) |
| `npm_and_yarn` | minor and patch in `/` and nested frontends | `directory == /docs-site`; any `version-update:semver-major` |
| `uv` (`/tools`) | minor and patch | semver-major |
| `pre_commit` | `hooks` group | the `test-utils` group, i.e. anything touching `agritheory/test_utils` (S19, test-utils track) |
| `nix` | enabled by `relock-push` after its push, or by this job when there was nothing to relock | a PR that moves `frappe-nix` or `standards-profile` is closed instead (S5, S38) |
| `submodules` | always | — |

The required checks are the gate, and auto-merge waits for them on the newest head SHA.

### 4.6 `app-nightly.yml`

Module `ci` with `ci.nightly`. Each job has its own switch, rendered by the caller as a boolean input (nightly jobs are never required checks, so a skipped one is harmless): `integration` (`tests`), `shots` (`screenshots`), `links` (`listing`), `duplication` (`test-utils` with `check_code_duplication` in `test-utils.hooks`), `registry-refresh` (`listing.publish`); `drift` always runs.

```yaml
on:
  workflow_call:
    inputs:
      app:              { type: string, required: true }
      integration-branch: { type: string, default: "develop" }
      nix-cache:        { type: string, default: "" }
      publish:          { type: boolean, default: false }
      run-integration:  { type: boolean, default: true }
      run-shots:        { type: boolean, default: false }
      run-links:        { type: boolean, default: false }
      run-duplication:  { type: boolean, default: false }
    secrets:
      NIX_CACHE_TOKEN:      { required: false }
      NIX_CACHE_READ_TOKEN: { required: false }
      REGISTRY_TOKEN:       { required: false }
permissions: {}
```

| Job | Permissions | Steps |
|---|---|---|
| `integration` (`inputs.run-integration`) | `contents: read` | Nix setup, then **no** `FRAPPE_NIX_CI`, because node modules are needed for `bench build`. Then `nix develop --no-pure-eval -c bash -c 'bash "$(frappe-nix data-path ci/nightly.sh)"'` (the dev shell's `frappe-nix` resolves the path), running the script below. Every failure is kept, and the job fails if the tests, the build or any suite failed. Uploads `.dev-dist/`. |
| `shots` (`inputs.run-shots`) | `contents: write`, `pull-requests: write`, `actions: write` | Runs only if `marketplace/screenshots.ts` is tracked. Nix setup; `nix develop -c frappe-shots --update`. If `git status --porcelain docs/screenshots` is non-empty: commit `docs: refresh screenshots` on branch `standards/screenshots`, refuse workflow paths (S29), force-push to the token remote (§4.1), open or update a PR labelled `screenshots`, and dispatch `ci.yml` and `pr-policy.yml`. **Never** auto-merged. The job summary links the diff images artifact. |
| `links` (`inputs.run-links`) | `contents: read` | Install frappe-nix-tools; `frappe-nix listing check --links-only` (both URLs must return 200 over https). When `icons` is on, with Nix set up, also `nix run --no-pure-eval .#frappe-icon -- check` for the raster checks, so `resvg` comes from the app's locked frappe-nix, not the registry nixpkgs. |
| `duplication` (`inputs.run-duplication`) | `contents: read` | `uv run --frozen --project tools prek run --hook-stage manual check_code_duplication --all-files`. The `check_code_duplication` hook is in the managed test_utils block with `stages: [manual]` (§2.14). It runs jscpd through npx, needs the network, and is informational: `continue-on-error: true`. |
| `drift` | `contents: read`, `issues: write` | Installs the **latest** frappe-nix release's frappe-nix-tools and runs `FRAPPE_NIX_ALLOW_SKEW=1 frappe-nix sync --check --format json`. If the pinned frappe-nix is older than the latest release, it opens or updates the issue "frappe-nix vX.Y.Z available" with the drift summary and the command `frappe-nix repo rollout --to vX.Y.Z --repo <repo>`, and closes it when current. For an org profile it also compares the locked `standards-profile` rev with the head of the ref `profile` names (`git ls-remote`), and keeps a second issue "standards profile update available" with `frappe-nix repo rollout --profile-to latest --repo <repo>`. These issues are the reminders for the manual mover (S5, S38). |
| `registry-refresh` | `contents: read` | Runs only if `inputs.publish`. The step maps `REGISTRY_TOKEN: ${{ secrets.REGISTRY_TOKEN }}` into `env:` and exits 0 with a notice when it is empty; otherwise `GH_TOKEN="$REGISTRY_TOKEN" frappe-nix listing registry --refresh`, which rebuilds the open registry PR branch on the current upstream `main`, because registry CI requires it to be up to date. |

The nightly script (N4, `py/frappe_nix_tools/frappe_nix_tools/data/ci/nightly.sh`):

```bash
set -uo pipefail
rc=0
export FRAPPE_ADMIN_PASSWORD="${FRAPPE_TEST_ADMIN_PASSWORD:-admin}"
export FRAPPE_NIX_ARTIFACT_DIR="$PWD/.dev-dist/nightly"; mkdir -p "$FRAPPE_NIX_ARTIFACT_DIR"
frappe-test --ci --keep-up || rc=$?
bench build || rc=1
port="$(jq .webserver_port "$FRAPPE_BENCH_ROOT/sites/common_site_config.json")"
export FRAPPE_SITE_URL="http://127.0.0.1:${port}"
export CF_SITE_URL="$FRAPPE_SITE_URL" CF_SHOT_DIR="$FRAPPE_NIX_ARTIFACT_DIR"   # the names carbon's suites read
while IFS= read -r s; do
  bash -c "$s" || rc=1
done < <(frappe-nix config nightly-suites)
frappe-test --down
exit "$rc"
```

`frappe-nix config <key>` (N3a) prints a resolved configuration value (§8.4), one list item per line.

### 4.7 Caller files (rendered into each app; whole; N4)

Every caller file has the YAML header and `permissions: {}`. `<fn>` is `<frappe_nix.owner>/<frappe_nix.name>` (`Avunu/frappe-nix` unless the app locks a fork, §2.5), `<rev>` is `frappe_nix.rev`, `<v>` is `frappe_nix.version`, `<int>` is `branches.integration` and `<rel>` is `branches.release` (empty under `main+tags`). Each file and each job exists only as §4's table says.

`.github/workflows/ci.yml` (module `ci`). One caller job per enabled gate (S39); shown with all four:

```yaml
name: ci
on:
  push:
    branches: [<int>]
  pull_request:
{% if cfg.ci.schedules.ci %}
  schedule:
    - cron: "<ci.schedules.ci>"   # "0 6 * * *": integration branch, daily: GITHUB_TOKEN auto-merges raise no push
{% endif %}
  workflow_dispatch:
permissions: {}
concurrency:
  group: ci-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
{% if "lint" in gates %}
  lint:
    name: ci
    permissions:
      contents: read
      pull-requests: read
    uses: <fn>/.github/workflows/app-lint.yml@<rev> # v<v>
    with:
      app: <app>
      integration-branch: <int>
    secrets:
      FETCH_TOKEN: ${{ secrets.FRAPPE_NIX_FETCH_TOKEN }}
{% endif %}
{% if "typecheck" in gates %}
  typecheck:
    name: ci
    permissions:
      contents: read
      pull-requests: read
    uses: <fn>/.github/workflows/app-typecheck.yml@<rev> # v<v>
    with:
      app: <app>
      integration-branch: <int>
    secrets:
      FETCH_TOKEN: ${{ secrets.FRAPPE_NIX_FETCH_TOKEN }}
{% endif %}
{% if "test" in gates %}
  test:
    name: ci
    permissions:
      contents: read
      pull-requests: read
    uses: <fn>/.github/workflows/app-test.yml@<rev> # v<v>
    with:
      app: <app>
      integration-branch: <int>
      nix-cache: ${{ vars.FRAPPE_NIX_CACHE || '' }}
      test-timeout-minutes: <ci.test-timeout-minutes>
    secrets:
      NIX_CACHE_TOKEN: ${{ secrets.FRAPPE_NIX_CACHE_TOKEN }}
      NIX_CACHE_READ_TOKEN: ${{ secrets.FRAPPE_NIX_CACHE_READ_TOKEN }}
      FETCH_TOKEN: ${{ secrets.FRAPPE_NIX_FETCH_TOKEN }}
{% endif %}
{% if "marketplace" in gates %}
  marketplace:
    name: ci
    permissions:
      contents: read
      pull-requests: read
    uses: <fn>/.github/workflows/app-marketplace.yml@<rev> # v<v>
    with:
      app: <app>
      integration-branch: <int>
    secrets:
      FETCH_TOKEN: ${{ secrets.FRAPPE_NIX_FETCH_TOKEN }}
{% endif %}
```

`.github/workflows/pr-policy.yml` (modules `ci` and `commits`):

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
    uses: <fn>/.github/workflows/app-pr-policy.yml@<rev> # v<v>
    with:
      app: <app>
      integration-branch: <int>
    secrets:
      FETCH_TOKEN: ${{ secrets.FRAPPE_NIX_FETCH_TOKEN }}
```

`.github/workflows/release.yml` (modules `ci` and `releases`):

```yaml
name: release
on:
  push:
    branches: [<int>]
{% if cfg.ci.schedules.release %}
  schedule:
    - cron: "<ci.schedules.release>"   # "30 5 * * *": auto-merged commits raise no push
{% endif %}
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
    uses: <fn>/.github/workflows/app-release.yml@<rev> # v<v>
    with:
      app: <app>
      integration-branch: <int>
      release-branch: <rel>                 # "" under main+tags
{% if cfg.listing.publish %}
      publish: ${{ vars.MARKETPLACE_PUBLISH == 'true' }}
      registry-fork: <listing.registry-fork>
{% endif %}
      pilot-assets: <pilot-assets.enable>
{% if cfg.listing.publish %}
    secrets:
      REGISTRY_TOKEN: ${{ secrets.REGISTRY_TOKEN }}
{% endif %}
```

`.github/workflows/deps.yml` (modules `ci` and `dependabot`):

```yaml
name: deps
on:
  pull_request:
{% if cfg.ci.schedules["deps-sweep"] or cfg.ci.schedules["deps-relock"] %}
  schedule:
{% if cfg.ci.schedules["deps-sweep"] %}
    - cron: "<ci.schedules.deps-sweep>"    # "0 9 * * *": re-dispatch CI for bot PRs
{% endif %}
{% if cfg.ci.schedules["deps-relock"] %}
    - cron: "<ci.schedules.deps-relock>"   # "0 8 * * 1": python relock; passed as relock-cron below
{% endif %}
{% endif %}
  workflow_dispatch:
permissions: {}
jobs:
  deps:
    permissions:
      contents: write
      pull-requests: write
      actions: write
      checks: read
    uses: <fn>/.github/workflows/app-deps.yml@<rev> # v<v>
    with:
      app: <app>
      integration-branch: <int>
      nix-cache: ${{ vars.FRAPPE_NIX_CACHE || '' }}
      auto-merge: <dependabot.auto-merge>
      merge-method: <repo-policy.merge-methods[0]>
      relock-cron: "<ci.schedules.deps-relock>"
    secrets:
      NIX_CACHE_TOKEN: ${{ secrets.FRAPPE_NIX_CACHE_TOKEN }}
      NIX_CACHE_READ_TOKEN: ${{ secrets.FRAPPE_NIX_CACHE_READ_TOKEN }}
      FETCH_TOKEN: ${{ secrets.FRAPPE_NIX_FETCH_TOKEN }}
```

`.github/workflows/nightly.yml` (module `ci` with `ci.nightly`):

```yaml
name: nightly
on:
  schedule:
    - cron: "<ci.schedules.nightly>"   # "0 7 * * *"
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
    uses: <fn>/.github/workflows/app-nightly.yml@<rev> # v<v>
    with:
      app: <app>
      integration-branch: <int>
      nix-cache: ${{ vars.FRAPPE_NIX_CACHE || '' }}
      run-integration: <modules.tests>
      run-shots: <modules.screenshots>
      run-links: <modules.listing>
      run-duplication: <test-utils on with check_code_duplication>
{% if cfg.listing.publish %}
      publish: ${{ vars.MARKETPLACE_PUBLISH == 'true' }}
{% endif %}
    secrets:
      NIX_CACHE_TOKEN: ${{ secrets.FRAPPE_NIX_CACHE_TOKEN }}
      NIX_CACHE_READ_TOKEN: ${{ secrets.FRAPPE_NIX_CACHE_READ_TOKEN }}
      FETCH_TOKEN: ${{ secrets.FRAPPE_NIX_FETCH_TOKEN }}
{% if cfg.listing.publish %}
      REGISTRY_TOKEN: ${{ secrets.REGISTRY_TOKEN }}
{% endif %}
```

`.github/workflows/assets.yml` (module `pilot-assets`). The pilot SHA is a floor and is moved by dependabot. Under `develop+version` it runs on the release branch; under `main+tags` the release workflow dispatches it on the tag.

```yaml
name: assets
on:
  workflow_dispatch:
permissions: {}
jobs:
  assets:
    if: {% if branches.release %}startsWith(github.ref, 'refs/heads/version-'){% else %}startsWith(github.ref, 'refs/tags/v'){% endif %}
    permissions:
      contents: write
    uses: frappe/pilot/.github/workflows/app-assets.yml@e2364936eb0a4f89a5309d22fb198056f14ff86a # develop
```

- zizmor MUST pass on every rendered caller, in every combination of enabled gates the N4 fixture matrix renders (§7), and on frappe-nix's own `app-*.yml`, with the shipped `zizmor.yml`. If zizmor's `excessive-permissions` audit flags a caller job's union of permissions, N4 adds a narrowly scoped `rules.excessive-permissions.ignore` for `ci.yml`, `release.yml`, `deps.yml` and `nightly.yml` to the template, with a comment that reusable callers must grant the union.
- A docs tool's own workflows (docusystem's `docs.yml` and `docs-publish.yml`) stay as that tool renders them.
- **Schedules are parameters** (`ci.schedules`, §8.2): `ci` (`"0 6 * * *"`), `release` (`"30 5 * * *"`), `deps-sweep` (`"0 9 * * *"`), `deps-relock` (`"0 8 * * 1"`) and `nightly` (`"0 7 * * *"`), each a cron string or `""` for none. The defaults suit a public repo (unlimited Actions minutes). A private repo that pays for minutes turns off what it doesn't need. The `ci` and `release` crons exist because a `GITHUB_TOKEN` auto-merge raises no push: with them off and auto-merge on, the integration branch's CI status and the release PR catch up only on the next human push, and `repo doctor` says so. `deps-sweep` is the fallback for dispatches Dependabot runs can't make (§4.5). In the Avunu fleet (plan §7 decision 2), a private repo (timeclock today) is made public **before** its PR A, which its `fleet.json` entry (`private: true`) enforces (§5.6).
- `ci.yml` doesn't run on `version-*`: that branch only moves by the release workflow's PATCH, which raises no event (§4.2).

### 4.8 Rulesets (`repo-policy/rulesets/*.json`, N4)

Module `repo-policy` (off in `recommended`, S40). Nothing here touches a repository unless someone with admin runs `frappe-nix repo apply` (§5.6) for an app whose resolved configuration has `repo-policy.enable = true`. Profile values: `repo-policy.required-approvals`, `repo-policy.require-code-owner-review`, `repo-policy.required-checks`, `repo-policy.release-bypass`, `repo-policy.merge-methods`, `repo-policy.require-thread-resolution`, `releases.branching`, `releases.tag-prefix`. The files are JSON with `{placeholders}` that `repo apply` fills from the app's resolved configuration; an org profile MAY override them (`overridable`, §8.1).

`integration.json` (1.1's `develop.json`):

```json
{
  "name": "{integration_branch}",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/{integration_branch}"], "exclude": [] } },
  "bypass_actors": [],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "allowed_merge_methods": "{merge_methods}",
        "required_approving_review_count": "{required_approvals}",
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": "{require_code_owner_review}",
        "require_last_push_approval": false,
        "required_review_thread_resolution": "{require_thread_resolution}" } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": "{required_checks}" } }
  ]
}
```

- `{required_checks}` is one `{"context": "ci / <gate>", "integration_id": 15368}` per gate, in the order `pr-policy`, `lint`, `typecheck`, `test`, `marketplace`. With `repo-policy.required-checks = "gates"` (both built-in profiles) the gates are exactly the app's enabled `gates` (§2.3), so a disabled gate is never required and an enabled one always is. An explicit list of gate names is allowed and MUST be a subset of `gates` (exit 2 otherwise). With no gates (`ci` off), the `required_status_checks` rule is omitted.
- `{required_approvals}` is `repo-policy.required-approvals` (`recommended`: 1; Avunu profile: 0, because its PRs are merged by the main agent or auto-merge after checks). With a value above 0, Dependabot auto-merge (§4.5) waits for an approval like any other PR.
- `{merge_methods}` is `repo-policy.merge-methods` (`["squash"]` in both built-in profiles; any of `squash`, `merge`, `rebase`), and `{require_thread_resolution}` is `repo-policy.require-thread-resolution` (true). Squash stays the default because S11's `Release-As` footer and the managed auto-merge (`--<first method>`) assume a single commit per PR; with another first method, `frappe-major` adoption sets the footer on the merge commit instead.
- `integration-provisional.json` is the same without the `required_status_checks` rule, and with the same `name`, so the full ruleset later replaces it in place.

`release-branch.json` (1.1's `version.json`; applied only under `develop+version`):

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
  "name": "{tag_prefix}* tags",
  "target": "tag",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/tags/{tag_prefix}*"], "exclude": [] } },
  "bypass_actors": [],
  "rules": [
    { "type": "deletion" },
    { "type": "update", "parameters": { "update_allows_fetch_and_merge": false } },
    { "type": "non_fast_forward" }
  ]
}
```

Creating tags isn't restricted, because release-please creates them through the Releases API with `GITHUB_TOKEN`. `{tag_prefix}` is `releases.tag-prefix` (`"v"`); with `""` the pattern is `refs/tags/[0-9]*`. `assets-version-16` matches neither.

`repo-policy.release-bypass` selects the `bypass_actors` of `release-branch.json`: `"github-actions"` (the above, both built-in profiles) or `"user:<id>"`. If the API rejects `Integration 15368` with a 422, `frappe-nix repo apply` stops for that repo and prints the two fallbacks, in order:

1. `"user:<id>"`: `bypass_actors: [{"actor_type": "User", "actor_id": <machine user id>, "bypass_mode": "always"}]` (the `User` bypass actor type, GA 2026-05-07, github-capabilities track). The fast-forward step then uses that machine user's token (`VERSION_BRANCH_TOKEN`) instead of `GITHUB_TOKEN`.
2. A `DeployKey` bypass, through an org template override of `release-branch.json`. Unusable in the Avunu org today: it has deploy keys disabled (`deploy_keys_enabled_for_repositories: false`), and only an org owner can enable them.

Both need the user (Q2).

### 4.9 Repo settings (`repo-policy/repo-settings.json`, N4) and the fleet list

```json
{
  "repository": {
    "default_branch": "{integration_branch}",          // only with repo-policy.manage-default-branch
    "allow_squash_merge": "{squash in merge_methods}",
    "allow_merge_commit": "{merge in merge_methods}",
    "allow_rebase_merge": "{rebase in merge_methods}",
    "allow_auto_merge": true,
    "allow_update_branch": true,
    "delete_branch_on_merge": "{delete_branch_on_merge}",
    "squash_merge_commit_title": "PR_TITLE",
    "squash_merge_commit_message": "COMMIT_MESSAGES",
    "has_wiki": false,                                   // these two only with repo-policy.manage-features
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

- `{documentation}` is the listing's `documentation` URL when `listing` is on, else the fleet entry's `documentation`, else the field is left unchanged.
- `security_and_analysis` follows `repo-policy.secret-scanning` (true in both built-in profiles); `labels` lists only the labels of enabled modules (`screenshots` needs `screenshots`; the other two need `dependabot`).
- `can_approve_pull_request_reviews: true` is required so `GITHUB_TOKEN` can open PRs (release-please, relock-upgrade, shots).
- The keys marked in comments are parameters (§8.2), with the values shown as their defaults: `repo-policy.merge-methods`, `repo-policy.delete-branch-on-merge` (true), `repo-policy.manage-default-branch` (true; false leaves `default_branch` alone) and `repo-policy.manage-features` (true; false leaves `has_wiki` and `has_projects` alone). The comments are not part of the JSON file.
- Without `repo-policy`, nobody applies these settings. `frappe-nix repo doctor` (§5.6) reports which of them the enabled modules need.

**The fleet list** is not part of frappe-nix (S40). `frappe-nix repo apply|audit|rollout --all` read it from `--fleet <file>`, a local path (the Avunu fleet's is `fleet.json` in a checkout of P); without `--fleet`, `--all` is exit 2. Its schema (`frappe_nix_tools/data/schema/fleet.schema.json`, N4):

```json
{ "schema": 1, "profile": "github:<owner>/<profile repo>/v1", "apps": [
  { "repo": "<owner>/<repo>", "app": "<app>", "target_app": "<app after a rename>", "private": false, "list": true,
    "documentation": "https://…", "rename_mode": "rename", "profile": "…", "integration_branch": "…" }
] }
```

The top-level `profile` (and an entry's own `profile`, which wins) is the profile `repo apply` resolves for a repository that has no `[tool.frappe-nix]` yet (§5.6); `integration_branch` overrides the branch that profile implies for it.

`target_app` is the post-rename package name, and a repo rename updates `repo`. `rename_mode` is `"rename"` (the default, `frappe-rename-app`) or `"replace"` (Avunu fleet: jailbreak → data_steward, §5.10). `private` gates `frappe-nix repo apply --phase full` (§5.6). The Avunu fleet's file is `Avunu/frappe-standards-profile/fleet.json`, with one entry per app for all 12 apps, for example:

```json
{ "repo": "Avunu/carbon_frappe", "app": "carbon_frappe", "target_app": "carbon_theme", "private": false, "list": true,
  "documentation": "https://carbon-theme.avunu.net/" }
```

### 4.10 frappe-nix's own settings (N6)

- `repo-policy/self/repo-settings.json` is the same as §4.9, except `default_branch: "main"` and no labels.
- `repo-policy/self/rulesets/main.json` is the integration ruleset with the ref `refs/heads/main`, 0 required approvals, and the required contexts `lint` and `flake` (frappe-nix's own `check.yml` jobs) and `pr-policy`. None is a reusable-workflow job, so the names are bare.
- **`pr-policy`** lives in its own `.github/workflows/pr-policy.yml` (N6), not in `check.yml`, for the reason S4 gives: it runs on `pull_request` types `opened`, `edited`, `synchronize` and `reopened`, plus `workflow_dispatch`, and re-running `check.yml` on every retitle would rebuild `flake`. Its one job is named `pr-policy` and isn't reusable. It installs frappe-nix-tools from the checkout (`uv tool install ./py/frappe_nix_tools`), because frappe-nix's own `flake.lock` has no `frappe-nix` node, and runs `frappe-nix policy --pr --major-free` (no `Release-As` major rule; frappe-nix has its own semver, §6.3).
- **`check.yml`** gains a boolean `workflow_dispatch` input `vm-tests` (default `false`), and its `vm-tests` job runs only when `inputs.vm-tests == true`. frappe-nix's release flow dispatches `check.yml` on its release PR without it, so a release dispatch never starts the NixOS VM tests, which a hosted runner can't carry.
- `repo-policy/self/rulesets/release.json` is `release-branch.json` with the ref `refs/heads/release-*`.
- `repo-policy/self/rulesets/tags.json` is `tags.json`.

The selftest workflows are not required checks, because they are path-filtered and expensive.

---

## 5. Tool CLIs

Every Nix-side tool is reachable in two ways:

- as a dev-shell command in app mode (`lib/scripts.d/*.nix` or `lib/standards/shell.nix`);
- as a flake app (`nix run github:Avunu/frappe-nix#<tool>`, or `.#<tool>` in an app, through `lib/standards/outputs.nix`).

The Python-side commands are `frappe-nix <cmd>` (one console script, S33). The Nix wrappers are `frappe-nix` itself (with `git` and `gh` on PATH, which the `repo` group needs), `frappe-listing` (= `frappe-nix listing`) and `frappe-icon` (= `frappe-nix icon`, with `resvg` on PATH). The 1.1 stand-alone apply, audit and rollout wrappers are the subcommands `frappe-nix repo apply|audit|rollout`.

**Org values (S37).** Every tool below that needs an organisation value reads it from the app's resolved configuration (`org.*` and module parameters, §8.4), never from a constant. Where a tool needs one that is empty, it exits 2 naming the key (for example `org.brand.tile-color is empty; set it in your profile or [tool.frappe-nix.org.brand]`). A tool's own module must be on for it to run in CI; run by hand on an app with the module off, it prints a notice and exits 0, except `frappe-test`, which always runs the stages it is asked for.

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

`--ci` turns on, from the resolved configuration, exactly the stages whose modules are on: stages 3–4 (`tests`, coverage only with `tests.coverage.enable`), 5 (`tests.testmap`), 6 (`tests.composition`), 7 (`python-types`, as `--ty`), 8 (`nix-lint`, as `--nix-lint`) and 8b (`--shell-checks`, when `shell-checks` is non-empty), plus `--junit $OUT/junit.xml`, the step summary and `frappe-test-report.json`. With `tests` off it skips stages 1–6 entirely (no bench is started) and runs only 7, 8 and 8b. A skipped stage is `"skipped"` in the report. frappe-test itself is a dev-shell command for every app-mode user; in an app without `[tool.frappe-nix]` it reads no configuration and `--ci` uses the `recommended` values (stage 7 only when `tools/pyproject.toml` exists, Appendix I).

**Stages.** frappe-test runs these in order. Every stage after 3 runs even when an earlier one failed.

1. **Up.**
   - If no process-compose is listening on `$PC_SOCKET_PATH`: `DEVENV_IN_DIRENV_SHELL=true PC_TUI_ENABLED=0 devenv up -D`, with a trap that runs `process-compose -U -u "$PC_SOCKET_PATH" down` unless `--keep-up` is given.
   - Wait at most 300 s for `mariadb-admin --socket "$FRAPPE_DB_SOCKET" ping`, then for an HTTP answer on `127.0.0.1:$(jq .webserver_port sites/common_site_config.json)`.
   - Failure → exit 10.
2. **Site.**
   - Unless `--reuse-site` is given and the site exists: `printf '\n' | provision-site "$ADMIN_PW"`.
   - Then `bench --site S set-config allow_tests true`.
   - Then each `[tool.frappe-nix.tests].setup` step, or the default (§2.1):
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
   - **Upward ratchet (S24, plan decision 5).** Let `target` be `tests.coverage.target` (80 in both built-in profiles) and `margin` be `tests.coverage.raise-margin` (2.0). If `fail_under < target` and `total ≥ fail_under + margin`, the stage fails with verdict 2 and the message `coverage is <total>; raise [tool.coverage.report] fail_under to <min(target, floor(total) - 1)>`. After that raise, `total - fail_under < margin`, so the rule settles. `raise-margin = 0` turns the upward ratchet off. The PR that adds the tests carries the raise.
   - The gate is skipped when any filter (`--module`, `--doctype`, `--test`) is given.
   - **Scoping.** coverage stores real paths. `--source` is the repo's package dir, so `.frappe-nix/bench/apps/*`, which holds real directories under the repo root but outside `<app>/`, is never measured.
   - **Acceptance.** No key of `coverage.json` `files` may contain `/.frappe-nix/`.
5. **Testmap** (§5.1.1) → verdict 3. Skipped with filters.
6. **Composition** (§5.1.2) → verdict 4.
7. **ty** (`--ty`). From the repo root: `uv run --frozen --project tools ty check --python "$FRAPPE_BENCH_ROOT/env" --output-format concise`. Records the diagnostic count and the `ty: ignore` count. → verdict 5.
8. **nix-lint** (`--nix-lint`). `nixfmt --check flake.nix $(git ls-files 'nix/*.nix')`, `statix check .` and `deadnix --fail --exclude .frappe-nix .`. The binaries are pinned through frappe-nix's nixpkgs, are in frappe-test's own `runtimeInputs` (so the stage works in any app-mode shell), and are added to an opted-in app's shell by N1 for interactive use. → verdict 6.
   - **8b. Shell checks** (`--shell-checks`). For each `[tool.frappe-nix] shell-checks` command, in order, from the repo root: run it (`bash -c`), then `git status --porcelain` must be empty, so a generator whose output is committed (timeclock's pydantic2ts `generate-types`, carbon's `codegen`) proves its output fresh. Each failure is listed with the diff. → verdict 7. Node-based checks here may need node_modules, which CI mode doesn't install; such a command installs them itself (`yarn install --frozen-lockfile &&` …), or the check moves to `ci:typecheck`, which runs in the no-Nix typecheck job with node_modules and `FRAPPE_PATH`.
9. **Report.** Write `$OUT/frappe-test-report.json` (below) and `$OUT/summary.md`, and append the summary to `$GITHUB_STEP_SUMMARY`.

**Exit status** is the verdict of the **first failing stage**, in the order 1 (tests), 2, 3, 4, 5, 6, 7. It is 10 for an environment failure in stage 1 or 2, and 0 otherwise.

`frappe-test-report.json`:

```json
{ "schema": 1, "app": "carbon_frappe", "frappe_nix": {"rev": "…", "version": "1.0.0"}, "git_sha": "…",
  "tests": {"ran": 39, "failures": 0, "errors": 0, "skipped": 0},
  "coverage": {"percent": 63.2, "fail_under": 60.0, "modules": {"carbon_frappe/api.py": 81.0}},
  "testmap": {"targets": 14, "untested": [], "exempt": 1, "stale_exemptions": []},
  "composition": {"ok": true, "doctypes": {"ToDo": {"layers": ["…"], "app_first_ok": true}}},
  "ty": {"diagnostics": 0, "ignores": 3}, "nix_lint": "ok", "exit": 0 }
```

`--down` stops a bench that `--keep-up` left running, and exits.

#### 5.1.1 Testmap: the whitelist and hook coverage algorithm (N1, `frappe-nix testmap`; `tests.testmap`)

frappe-test invokes it with the bench interpreter, so the evaluated hooks are visible:

```
$FRAPPE_BENCH_ROOT/env/bin/python <store>/frappe_nix_tools/bench/testmap_probe.py --site S --app A \
    --coverage-json $OUT/coverage.json --pyproject $DEVENV_ROOT/pyproject.toml --out $OUT/testmap.json
```

The probe runs `frappe.init(site)` and `frappe.connect()` (the site exists by stage 5), then `hooks = frappe.get_hooks(app_name=A)`. Because the hooks are evaluated, conditional hooks are included. Connecting matters because some modules query the database when imported (timeclock's `utilities.py` calls `frappe.get_all` at module level). A target whose module fails to import is reported as untested, with the exception text in its `error` field; the probe itself never crashes on it.

**Targets.** Each target is a dotted path to a Python function or method:

- **T1:** every `FunctionDef` or `AsyncFunctionDef` under `<app>/`, excluding `**/tests/**`, `**/test_*.py` and `**/patches/**`, that is decorated with `frappe.whitelist`, `frappe.whitelist(...)`, `whitelist` or `whitelist(...)` (where `whitelist` is imported from `frappe`). Methods are named `module.Class.method`.
- **T2:** every string in `hooks["scheduler_events"]`, for all keys including `cron`, that starts with `A.`.
- **T3:** every handler in `hooks["doc_events"][*][*]`, string or list, that starts with `A.`.
- **T4:** for each `extend_doctype_class` or `override_doctype_class` value that starts with `A.`: import the class, and take every function defined in its own `__dict__`. Dunders are skipped except `__init__`, and names starting with `_` are skipped.
- **T5:** values of `override_whitelisted_methods`, `permission_query_conditions` and `has_permission` that start with `A.`.
- **T6:** every string, or string in a list, that starts with `A.` under these callable-valued hooks: `auth_hooks`, `before_request`, `after_request`, `on_session_creation`, `on_login`, `on_logout`, `boot_session`, `jinja.methods`, `jinja.filters`, `additional_timeline_content` (every list in the dict), `website_context` values that are dotted callables or `/api/method/A.…` URLs (the path after `/api/method/`), `after_install`, `after_sync`, `after_migrate`, `before_uninstall`, `before_tests`. This covers jwt_auth's `validate_auth`, `handle_redirects` and `on_logout`, carbon's `after_request` injector, and esign's jinja and timeline methods. The install and migrate kinds (`after_install`, `after_sync`, `after_migrate`, `before_uninstall`) run before coverage starts, so a test must call them directly or they need a `[[tool.frappe-nix.untested]]` entry with a reason.

**Body lines.** Resolve each target, `inspect.unwrap` it, and call `inspect.getsourcelines`. The body lines are the executable lines after the `def` line, its decorators and its docstring, taken from coverage's analysis of that file (`coverage.json` `files[realpath].executed_lines` ∪ `missing_lines`).

**Tested** means at least one body line is in `executed_lines`.

**Exemptions.** A target listed in `[[tool.frappe-nix.untested]]` is exempt. An exemption whose target is now tested, or is no longer a target, is **stale** and counts as a failure: the list only shrinks.

**Output.** `testmap.json` holds `{targets:[{path, kind, file, line, tested, exempt, error?}], untested:[…], stale_exemptions:[…]}`, and a markdown table goes to the summary. The verdict is 3 when `untested` or `stale_exemptions` is non-empty.

#### 5.1.2 Composition check (A.3; `tests.composition`)

`<store>/frappe_nix_tools/bench/composition.py --site S --app A` connects to the site. Then:

1. Collect every doctype that A names in `extend_doctype_class` or `override_doctype_class`, read from the merged hooks of all installed apps.
2. For each, after clearing frappe's controller cache (`frappe.controllers = {}` plus `frappe.clear_cache()`), call `frappe.get_controller(dt)`. Assert that no `TypeError` is raised and that A's class is in `__mro__`.
3. Repeat with `frappe.get_installed_apps` monkeypatched to return the real order with A moved directly after `frappe`. This is the A.0 #4 failure mode.

The verdict is 4 on any failure, and the message names the doctype and the order.

### 5.2 `frappe-listing` (N5; `frappe-nix listing`)

Module `listing` (`readme` for the `readme` subcommand). Profile values: `org.publisher`, `org.email`, `org.license` (L2), `org.website-url`, `org.docs-url` (L12), `listing.registry-fork`, `listing.registry-upstream`, `listing.getapp-check`.

```
frappe-listing check    [--release --tag vX.Y.Z] [--links-only] [--no-getapp] [--format text|json|github]
frappe-listing registry [--tag vX.Y.Z | --ref SHA] [--branch <release or integration branch>] [--onboard] [--refresh]
                        [--fork <listing.registry-fork>] [--upstream <listing.registry-upstream>] [--dry-run]
frappe-listing readme   --write | --check
```

**Pins.**

- The marketplace and pilot revisions are the app's `flake.lock` nodes reached as `nodes[nodes["frappe-nix"].inputs.marketplace]` and `….pilot`. They come from frappe-nix's flake inputs (S20).
- In Nix they are store paths. In no-Nix CI, `frappe-nix` fetches each locked tree into `.dev-dist/pins/<name>-<rev>/` and verifies it against the lock's `narHash`, using `nix-hash` semantics implemented in Python (`frappe_nix_tools.common.nar`). The fetch follows the lock node's `type`: `github` → `https://codeload.github.com/<owner>/<repo>/tar.gz/<rev>`; `gitlab` → `https://<host>/api/v4/projects/<url-encoded path>/repository/archive.tar.gz?sha=<rev>`; `git` → `git clone --filter=blob:none` of `url` and a checkout of `rev`. The same `pin-path` serves the marketplace and pilot pins, the app's frappe and siblings, and `standards-profile`.
- **Authentication.** When `FRAPPE_NIX_FETCH_TOKEN` is set (§4), the fetch sends it (`Authorization: Bearer` for codeload and the GitLab API, `http.extraHeader` for git), so a private org profile, frappe fork or sibling works. Without it, a 404 or 401 is exit 3 with the hint `set FRAPPE_NIX_FETCH_TOKEN (Contents read on <repo>)`.
- If the hash doesn't match, exit 3.

**`check` rules.** These rules apply whatever `listing.toml` says, so they are the registry-readiness checks of every app with `listing` on, listed or not; publishing is the separate switch `listing.publish`. L1, L2, L8, L10 and L12 apply only when `listing.toml` exists.

| Id | Rule | Severity |
|---|---|---|
| L1 | `listing.toml` passes its schema (§2.21): the enums, the tagline at 40–80 characters with no trailing `.`, the title at ≤ 40 characters, a non-empty `categories`, and `https` URLs | error |
| L2 | hooks.py, read by AST at top level, with any conditional assignments reported as warnings: `app_name == <app>`, `app_title == title`, `app_description == tagline`, `app_publisher == org.publisher`, `app_email == org.email`, `app_license == org.license` (each comparison only when that org value is set; otherwise the hook value MUST be non-empty, and `app_license` MUST equal `package.json` `license`). No `app_logo_url`. No `app_icon == "octicon octicon-file-directory"` and no `app_color == "grey"`. `add_to_apps_screen` present iff `apps_screen`, and if present its logo is `/assets/<app>/images/<app>-logo.svg`, with the route and `has_permission` taken from `[apps_screen_entry]`. | error |
| L3 | `[tool.bench.frappe-dependencies]`: the keys are exactly `{frappe} ∪ required_apps (bare)`; each value equals the sibling's resolved range (§2.1); each parses with `packaging.SpecifierSet`; for `frappe` and each sibling on the generic Frappe rule, the lower bound's major equals `frappe-major` (payments and object-form or `known-apps` siblings with their own range are exempt); `frappe` isn't in `required_apps`; `[project.dependencies]` names none of frappe, erpnext, hrms or payments | error |
| L4 | The version block is in block form. `__version__` = `package.json` = manifest. With `--release`: `__version__ == tag.lstrip("v")`, and the tag's commit is an ancestor of `origin/<branch>` | error |
| L5 | `__init__.py` has no side effects (§2.13). No tracked symlink points outside the repo. | error |
| L6 | Every `override_doctype_class` key in hooks has a `[[tool.frappe-nix.override-doctype-class]]` entry, and every entry has a hook | error |
| L7 | **Registry semgrep.** Import `validation/semgrep_check.py` from the pinned marketplace tree (`SemgrepValidator(repo_root, app)`, run as registry CI runs it, with `EIO_BACKEND=posix`). Each blocking finding is keyed `(rule, path, sha1(stripped first matched line))` (S21), and findings are counted per key. For every key, the number found must equal the baseline entry's `count` (absent = 0): more is a new finding, fewer means the count must be lowered (stale). Either is an error, so the baseline only shrinks. With `--release`, the baseline MUST be empty. Advisory findings go into the report only. | error |
| L8 | Logo structure: `frappe-nix icon check --structural` (§5.3); only when `icons` is on | error |
| L9 | **pilot get-app** (when `listing.getapp-check` is on). Build a temporary validation bench with `pilot.core.bench.Bench`. Python is `>=3.14,<3.15` from uv. `apps/frappe` and each dependency app are source trees at the app's `flake.lock` revisions, fetched like the pins; this mirrors marketplace#29. Install the app, and run every check in `pilot.core.app.validator.validator._all_checks()` individually, as scratchpad `p0-registry-dryrun/scripts/run_sim.py` does. Building frappe's `mysqlclient` needs `pkg-config` and the MySQL client headers on the host; the `marketplace` job installs them (§4.2), and in Nix they come from the dev shell. Every check must pass. `--no-getapp` skips this rule, for local use only. | error |
| L10 | With `--links-only` or `--release`: `website` and `documentation` return 200 over https after redirects, using GET with a 20 s timeout and 2 retries | error |
| L11 | With `--release`: every `<owner>/<repo>` entry in `required_apps` appears in the upstream `apps.json` on `main` | error |
| L12 | `website` and `documentation` follow the profile's URL templates (§2.21); skipped when a template is empty | warning |

**`check` exit codes:** 0 pass (warnings allowed); 1 any error; 2 a config or parse error in `listing.toml` or `pyproject.toml`; 3 an environment problem (network, a pin hash mismatch, the pilot bench couldn't be built).

The JSON report goes to `.dev-dist/marketplace/report.json`.

**`registry`** (needs `listing.publish` and a non-empty `listing.registry-fork`, else exit 2). The token is `GH_TOKEN`: in CI that is `REGISTRY_TOKEN`, and locally it is `gh auth token` (in the Avunu fleet, under plan decision 15, the user's own account). `<fork-branch>` below is `<lowercased fork owner>/<app>` (Avunu profile: `avunu/<app>`).

1. Resolve the commit to its full 40-character SHA, and check that it is reachable from `origin/<branch>`.
2. Run `check --release --tag`. Abort on failure.
3. Clone the upstream `main` shallowly into a temp dir, and create the branch `<fork-branch>` from it, so it is always up to date with `main`.
4. Collect the pending entries: the new entry, plus every entry in the fork branch's `apps/<app>.json` whose `commit` isn't in upstream's file.
5. For each pending entry, run the pinned `tools/add_release.py`, with `APP`, `BRANCH`, `COMMIT` and `CHANNEL=stable` set, `--app-dir` as a `git worktree` of that commit, and `--registry` as the clone.
6. With `--onboard`, or when the app isn't in upstream `apps.json`, append the index entry to `apps.json` and create `apps/<app>.json` with `{"name": app, "releases": []}` before step 5. The index entry is `{name, title, description: tagline, repo: "https://github.com/<repo>", logo_url: "https://raw.githubusercontent.com/<repo>/<branch>/<app>/public/images/<app>-logo.svg", website, documentation, category, categories, stars: <registry.stars or 0>, releases: "apps/<app>.json"}`. It keeps the file's existing indentation.
7. Commit with the message `<app>: <version>`, or `<app>: onboard and <version>`. Force-push to `<fork>:<fork-branch>`.
8. Run `gh pr create --repo <upstream> --base main --head <fork-owner>:<fork-branch>`, or `gh pr edit` when one is open. The title is `<app>: <newest version>`, and the body is generated: the pending versions, the local `check` summary, and the semgrep baseline state.
9. Print the PR URL. `--refresh` does steps 3–8 with no new entry, and only when the fork branch is behind upstream `main`. `--dry-run` stops before the push and prints the diff.

**Exit codes:** 0 PR opened or updated (or nothing to do); 1 the gate failed; 3 a git, gh or network error.

**`readme`.** It renders the §2.19 blocks from `frappe_nix_tools/data/readme/*.md.j2`. Its exit codes match `sync`'s (0, 1 for drift, 2 for a missing marker).

### 5.3 `frappe-icon` (N5; `frappe-nix icon`, with `resvg` from nixpkgs)

Module `icons` (off in `recommended`). Profile values: `org.brand.tile-color` (required; exit 2 when empty) and `org.brand.glyph-color` (default `#FFFFFF`).

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
  - the first child is a `<rect width=1024 height=1024 rx=ry=293>` (28.6%, ±1) with `fill="<org.brand.tile-color>"`;
  - the glyph group is all `<org.brand.glyph-color>`, centred to within ±4 units;
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

Module `demo` (off in `recommended`; `screenshots` needs it). Profile values: `demo.company-name`, `demo.company-abbr`, `demo.country`, `demo.currency`, `demo.timezone`, `demo.language`, `demo.date`, `demo.seed`, `demo.erpnext-demo`.

```
frappe-demo [--site S] [--fresh] [--erpnext-demo | --no-erpnext-demo] [--date YYYY-MM-DD] [--seed N] [--no-up]
```

The defaults come from `marketplace/screenshots.ts`'s `demo` block when it exists, otherwise from the `demo` module parameters (`recommended` values shown):

| Option | Default |
|---|---|
| date | `demo.date`, `2026-01-15` |
| seed | `demo.seed`, `1` |
| ERPNext demo data | `demo.erpnext-demo`, off |

1. Bring the bench up as frappe-test stage 1 does, unless `--no-up`. With `--fresh`, or when the site is missing, run `provision-site`.
2. Run the setup wizard with fixed arguments:
   ```
   language <demo.language>, country <demo.country>, currency <demo.currency>, timezone <demo.timezone>,
   company_name <demo.company-name>, company_abbr <demo.company-abbr>, fy_start_date <year>-01-01,
   fy_end_date <year>-12-31, chart_of_accounts Standard, full_name "Demo User", email demo@example.com,
   password <admin pw>
   ```
   `recommended`: English, United States, USD, America/New_York, "Demo Company", "DC". The Avunu profile sets "Avunu Demo", "AD".
   The company and fiscal-year keys are passed only when erpnext is installed.
3. When ERPNext demo data is on and erpnext is installed: `erpnext.setup.demo.setup_demo_data()`.
4. If `<app>.demo` is importable, call `setup(ctx)`, where

   ```python
   class DemoContext(TypedDict):
       today: datetime.date        # the --date value; never date.today()
       seed: int
       company: str | None         # demo.company-name when erpnext is installed
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

Module `screenshots` (off in `recommended`; needs `demo`). Profile values: `screenshots.timezone` and `screenshots.locale`, the defaults for the spec fields of the same names.

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
	alt: string;                  // required; used by the README and the org's website
	waitFor?: string;             // CSS selector
	waitForFn?: string;           // JS expression, truthy when ready
	actions?: Action[];
	clip?: string;                // selector; capture its bounding box
	fullPage?: boolean;
	mask?: string[];              // painted #8C8C8C
	hide?: string[];              // visibility: hidden
	themes?: Theme[];
	viewport?: Viewport;
	featured?: boolean;           // an org website may show featured shots
	readme?: "hero" | "feature";  // at most one "hero"
	threshold?: number;           // pixelmatch per-pixel threshold, default 0.1
	maxDiffRatio?: number;        // allowed share of differing pixels, default 0.001
}
export interface VideoFlow { name: string; route: string; steps: Action[]; seconds?: number }
export interface ShotSpec {
	viewport?: Viewport;          // default { width: 1440, height: 900, deviceScaleFactor: 2 }
	themes?: Theme[];             // default ["light", "dark"]
	timezone?: string;            // default screenshots.timezone ("America/New_York" in recommended)
	locale?: string;              // default screenshots.locale ("en-US" in recommended)
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
5. Write `docs/screenshots/manifest.json` (committed): `[{name, theme, alt, featured, readme, width, height, sha256}]`, sorted. The README and any org website read it.

**Exit codes:** 0 no differences, or updated; 1 differences in `--check`; 2 a spec error (validation against the types runs at runtime); 3 environment; 4 a capture error (navigation, timeout or JS error).

With `--video <name>`, it records the flow with `Page.startScreencast` and encodes `.dev-dist/shots/<name>.mp4` with ffmpeg. Never committed.

### 5.6 `frappe-nix repo apply` (N4)

```
frappe-nix repo apply <owner/repo>… | --all --fleet <file>  [--phase provisional|full] [--profile <ref>]
                      [--rename-default-branch] [--self | --policy-dir <dir>] [--dry-run]
                      [--settings-only | --rulesets-only]
frappe-nix repo doctor [<owner/repo>] [--format text|json]
```

It needs `gh` authenticated with `repo` scope, and admin on the target. It never needs `admin:org`. Module `repo-policy`. For each target it makes a shallow clone of the repository's **default** branch and resolves the configuration (§8.4, fetching an org profile like `--check` does):

- **An opted-in repo** (its default branch has `[tool.frappe-nix]`): the app's own configuration. If the default branch isn't yet `branches.integration`, the clone is retried on `branches.integration` when that exists.
- **A repo that hasn't opted in yet** (the Avunu fleet before PR A, plan Phase 4 step 1): the profile named by `--profile <ref>`, else the fleet entry's `profile`, else the fleet file's top-level `profile`, resolved with an empty app layer (`frappe-major` is not needed for the settings and the provisional ruleset). Only `--phase provisional` is allowed (settings, labels and `integration-provisional.json`); `--phase full` needs the gates, so it is exit 2 for that repo until the table exists. Without any profile it is skipped with a notice.

A repo whose resolved `repo-policy.enable` is false is skipped with a notice (exit 0 for it), so the tool never applies policy an app or its profile didn't opt into. When `branches.integration` doesn't exist on the remote, the settings step would point `default_branch` at nothing: with `--rename-default-branch` it first runs `POST /repos/{r}/branches/<default>/rename` with `new_name=<integration>` (GitHub retargets open PRs and redirects the old name); without it, that repo fails with the command to run. `--policy-dir <dir>` reads settings and rulesets from a local directory (P applies its own `repo-policy/` this way, §1.3); `--self` is shorthand for frappe-nix's `repo-policy/self/`.

1. Read `repo-policy/repo-settings.json` and `repo-policy/rulesets/*.json`, or the org profile's overrides of them (§8.1), and the fleet entry when `--all`. With `--self`, it reads `repo-policy/self/*` and targets frappe-nix's own repository; with `--policy-dir`, that directory.
2. `PATCH /repos/{r}` with the settings, after substituting `{integration_branch}` and `{documentation}`.
3. `PUT /repos/{r}/actions/permissions/workflow`.
4. `PUT vulnerability-alerts`, and `PUT automated-security-fixes`.
5. Create or update each label.
6. Rulesets, matched by `name`: `GET /repos/{r}/rulesets`, then for each wanted ruleset `PUT …/rulesets/{id}` if it differs, or `POST` if it is missing.
   - provisional = `integration-provisional.json`;
   - full = `integration.json` (with the app's `{required_checks}`, §4.8) + `release-branch.json` (only under `develop+version`) + `tags.json` (only when `releases` is on).
   - Unknown rulesets are listed, never deleted.
7. **Private repos.** Rulesets work on private repositories on GitHub Team and Enterprise plans and return 403 on the Free plan. `repo apply` therefore tries the rulesets API on a private repo and fails that repo (exit 1) only on an actual 403, with the message that its plan doesn't support rulesets on private repositories, so the required checks would be missing. A fleet entry with `private: true` is refused for `--phase full` before any call (exit 1 for that repo): that is how an organisation records "make it public first" (Avunu fleet, plan §7 decision 2: timeclock goes public before its PR A). Private repos also pay for scheduled minutes, which `ci.schedules` controls (§4.7).

`--dry-run` prints a JSON diff per repo and changes nothing.

**Exit codes:** 0 applied or already matching; 1 some repo failed (the rest continue); 2 config error.

**`frappe-nix repo doctor`** is read-only and needs no admin. For the app in the cwd (or `<owner/repo>`), it lists every repository setting the enabled modules rely on, each as `ok`, `missing` or `unknown` (not readable without admin), with where to change it:

- `releases`: Actions may create pull requests (`can_approve_pull_request_reviews`), needed by release-please; under `develop+version`, the fast-forward needs GitHub Actions to be allowed to move `version-*` (a ruleset bypass, or no ruleset on it);
- `dependabot` with `auto-merge`: `allow_auto_merge`, and a ruleset or branch protection on `branches.integration` that requires every enabled gate's context (otherwise deps.yml labels instead of merging, §4.5);
- `ci` with `ci.schedules.ci` or `release` off while auto-merge is on: the catch-up caveat of §4.7;
- `screenshots`, `dependabot`: their labels (deps.yml and nightly create them on first use);
- `listing.publish`: the `REGISTRY_TOKEN` secret and the registry fork;
- any private source: `FRAPPE_NIX_FETCH_TOKEN` (Actions, and Dependabot when `dependabot` is on).

Sync prints `run frappe-nix repo doctor` whenever it turns on a module that needs one of these (§3.3 step 5). Exit 0 when nothing is `missing`, else 1.

### 5.7 `frappe-nix repo audit` (N4): the plan's §6 scorecard, mapped to checks

```
frappe-nix repo audit [--repo R… | --all --fleet <file>] [--format md|json] [--out PATH]
                      [--issue --issue-repo <owner/repo>] [--against pinned|latest]
```

For each repo, the audit makes a shallow clone of the integration branch (resolved as `repo apply` does, §5.6), installs frappe-nix-tools at the repo's `flake.lock` rev into a temp venv (or the latest release's, with `--against latest`), resolves the app's configuration, and evaluates the rows below. **A row whose module is off is `n/a`** (the module is in brackets); a profile that turns modules off therefore never turns the scorecard red. `develop` and `version-<major>` below are `branches.integration` and `branches.release`.

| Row | Plan §6 item | Concrete check | Data source |
|---|---|---|---|
| A1 | [`repo-policy`] default branch is the integration branch | `repo.default_branch == branches.integration` | REST |
| A2 | [`releases`, `develop+version` only] `version-<major>` exists and equals the newest release tag | ref SHA == the peeled SHA of the newest tag: under `frappe-major` the newest `<tag-prefix><major>.*` tag; under `semver` the newest `<tag-prefix>*` tag by version order (v1 maintains one release branch, §6.3) | REST |
| A3 | [`repo-policy`] settings and rulesets match `repo-policy/` as rendered for this app | Field-by-field diff of the settings; normalised ruleset diff, by name (private repo → n/a) | REST |
| A4 | managed files present and `--check` clean at the pinned version | `frappe-nix sync --check --format json` → `status == clean` | clone |
| A5 | [`ci`] the enabled gates are green on the integration branch's HEAD, and nightly is green | The latest completed `ci.yml` run on the integration branch (push or schedule) concluded `success` with every gate job present successful; the latest `pr-policy` on any merged PR is green (when `commits` is on); the latest `nightly.yml` run concluded `success` (when `ci.nightly` is on) | REST runs |
| A6 | [`python-types`] ty: 0 diagnostics, and the ignore count ≤ baseline | The `test-report` artifact of the run in A5 → `ty.diagnostics == 0`. The ignore count isn't higher than in the report 30 days earlier, and it is enforced per PR by the ratchet. | artifact |
| A7 | [`typescript`] tsc clean on every project; no hand-written frappe globals | `typecheck` job success (A5), plus `frappe-nix compat` C7 (redeclarations only; augmentations confined to `types/<app>.augment.d.ts`). The augment file's member count is reported. Amber while `[[tool.frappe-nix.unchecked-js]]` is non-empty (with `check-js` on). | clone |
| A8 | [`tests`] coverage ≥ minimum, rising towards the target; testmap passing | report: `coverage.percent ≥ fail_under` and `testmap.untested == []`. Amber when `fail_under < tests.coverage.target` or `[[tool.frappe-nix.coverage-omit]]` is non-empty. | artifact |
| A9 | no legacy tooling | No file matches an applying `retire` rule (§2.4.1), read from the same `sync --check --format json` as A4 so the lists can't diverge; no tracked `**/public/dist/**`; no `pyupgrade` or `codesorter` in the pre-commit config; ruff and ssort managed when their modules are on (A4). Amber when `tool.ruff.extend-exclude` is non-empty. | clone |
| A10 | [`dependabot`] every enabled ecosystem configured; the last nix bump merged automatically with relock; frappe-nix and the org profile current | A4 covers the config. The newest merged PR with head `dependabot/nix/*` (frappe and siblings): merged through auto-merge, and it contains a commit starting `chore(deps): relock and sync` unless its relock was empty. The pinned frappe-nix is the latest `v1.*` release, or one released less than 14 days ago; otherwise amber, naming the `frappe-nix repo rollout` command (S5). The same 14-day rule applies to the locked `standards-profile` rev against its ref's head (S38). | REST |
| A11 | [`releases`] at least one release cut by release-please | A release tag exists (`<tag-prefix><major>.*` under `frappe-major`, any `<tag-prefix>*` version tag under `semver`), and its GitHub release was authored by `github-actions[bot]` | REST |
| A12 | [`readme`] README follows D15 and its blocks are in sync | `frappe-nix listing readme --check` == 0, and the README contains the `org.dev-docs-url` link (S26) | clone |
| A13 | [`icons`] icons pass; hooks wired by type; no `app_logo_url` or scaffold `app_icon` | `frappe-nix icon check --structural` == 0, and L2 | clone |
| A14 | [`listing`] `frappe-listing check` clean; semgrep baseline empty; links 200 | `frappe-nix listing check --links-only` and `check` (without `--no-getapp`) == 0, and the baseline's `findings == []` | clone |
| A15 | [`screenshots`] screenshot spec runs in CI; committed shots match the latest nightly | The latest nightly `shots` job succeeded, and there is no open `screenshots` PR older than 7 days | REST |
| A16 | [`listing.publish`] registry index merged; newest registry release == latest tag | The upstream `apps.json` on `main` has the app, and `apps/<app>.json` `releases[0].version == latest tag` | raw.githubusercontent |
| A17 | [`docs-site`] docs site (docs-and-urls track) | `GET /repos/{r}/pages`: `cname` set and `https_enforced: true`; `DOCS_SITE_ENABLED == "true"` | REST |

- Every row is `green`, `amber`, `red`, `n/a` or `unknown`. `unknown` means the data couldn't be read, for example because the token lacks admin. For a fleet entry with `list: false`, A16 is `n/a` and A14 evaluates `check` and the baseline but not the links (an unlisted app has no `listing.toml`); the registry-readiness checks still apply to it (§8.6).
- `--issue` opens or updates one issue per app in `--issue-repo` (required with `--issue`) titled `scorecard: <repo>`. The Avunu caller passes `Avunu/frappe-standards-profile`.
- **Scheduling.** frappe-nix ships `.github/workflows/fleet-audit.yml` as a reusable workflow only (`workflow_call`; inputs `fleet` (a path in the caller's checkout), `issue-repo`, `frappe-nix-version`; secret `FRAPPE_NIX_AUDIT_TOKEN`). An organisation schedules it from its own profile repository (P for Avunu, nightly). `FRAPPE_NIX_AUDIT_TOKEN` is a read-only fine-grained PAT with Administration and Contents read on the fleet's repos. Without that token, the settings and ruleset rows are `unknown`.

**Exit codes:** 0 everything green or n/a; 1 any amber or red; 2 error.

### 5.8 `frappe-nix repo rollout` (N4)

```
frappe-nix repo rollout [--to vX.Y.Z] [--profile-to <ref>|latest] [--repo R… | --all --fleet <file>]
                        [--no-auto-merge] [--dry-run]
```

This is **the** path by which a frappe-nix release, or an org profile release, reaches apps with CI callers (S5, S38). At least one of `--to` and `--profile-to` is required. It runs locally, needs Nix, and uses the user's `gh` token, which must have the `workflow` scope (`gh auth refresh -s workflow`), because the commit rewrites the caller workflows' `uses:` SHAs. For each repo, in a temp clone of the integration branch:

```
nix flake update frappe-nix                     # with --to
nix flake update standards-profile             # with --profile-to (after setting the ref in `profile` when it changes)
nix run --no-pure-eval .#relock && nix run --no-pure-eval .#frappe-init -- --sync
```

A repo whose `profile` is a built-in is skipped by `--profile-to`. `frappe-init --sync` runs from the newly locked frappe-nix (phase A re-execs if needed, §3.3). Rollout then commits `chore(deps): frappe-nix vX.Y.Z` (or `chore(deps): standards profile <short rev>`, or both in one subject), pushes `standards/frappe-nix-vX.Y.Z` (or `standards/profile-<short rev>`), opens the PR with the user's token, and, unless `--no-auto-merge`, runs `gh pr merge --auto` with the first of `repo-policy.merge-methods` (squash by default). A user-token push triggers CI normally, and the user-token merge raises a normal push event. If a stricter rule in the new version fails the app, the PR waits for a fix pushed to its branch.

In the Avunu fleet the main agent runs `frappe-nix repo rollout --to vX.Y.Z --all --fleet <P>/fleet.json` after each frappe-nix release, and `--profile-to latest` after each P release; the nightly `drift` issues (§4.6) and audit row A10 are the reminders. An app without CI callers needs none of this: `nix flake update frappe-nix && frappe-init --sync` in any commit is enough.

### 5.9 `frappe-nix policy`, `frappe-nix ratchet`, `frappe-nix compat`, `frappe-nix minibench`

Module `commits` for `policy`; each `compat` and `ratchet` rule names its module, and a rule whose module is off is skipped. `minibench` serves `test-utils` and `typescript`.

**`frappe-nix policy --commit-msg-file F`** is the commit-msg hook. Under `releases.version-scheme = "frappe-major"` it rejects:

- a subject matching `^[a-z]+(\([^)]*\))?!:`;
- a `BREAKING[ -]CHANGE:` footer;
- a `Release-As: X.y.z` whose major isn't `frappe-major`.

Under `"semver"` (or with `releases` off) it rejects nothing beyond what `committed` checks, and the hook is not rendered.

**`frappe-nix policy --pr`** runs in CI. When `EVENT_NAME == workflow_dispatch`, it finds the PR with `gh pr list --head $REF_NAME --state open --json number,title,baseRefName,headRefOid` and asserts that `headRefOid == HEAD_SHA`. It then takes the title and base from that result instead of `PR_TITLE` and `BASE_REF`, which are empty on a dispatch; if either is missing from the result, it fails rather than check an empty value. If there is no PR, it passes, for example on a branch dispatched by the sweep after the PR closed. It then checks:

1. The base is allowed: `base == $INTEGRATION` (`branches.integration`); or `base` matches a pattern in `commits.pr-allowed-bases` (fnmatch, default `[]`; e.g. `["version-*"]` for maintenance PRs); or, while `commits.allow-stacked` is true (`recommended`; false in the Avunu profile, plan §5 "no stacked PRs"), `base` is the head branch of another open PR in the same repository, so a stacked PR passes and is re-checked when GitHub retargets it after its parent merges. Otherwise the error message is `retarget: gh pr edit <n> --base <integration>`.
2. The title passes `committed --commit-file <(title)` and the subject rules above that apply.
3. Every commit in `git rev-list --no-merges base..head` passes `committed <sha>` and the rules above that apply, including the `Release-As` major rule under `frappe-major`.
4. On a `release-please--*` branch: `frappe-nix compat`. Under `frappe-major`, C1 then requires the proposed `package.json` version's major to equal `frappe-major`.

Exit 0 or 1. `--major-free` (frappe-nix itself, §4.10, which has no `[tool.frappe-nix]`) drops the `Release-As` major rule and check 4, and takes the integration branch from `--base-branch` (default `main`).

**`frappe-nix compat`** is the prek hook (C1–C9):

| Id | Rule |
|---|---|
| C1 | [`releases`, `frappe-major` scheme] The `package.json` version's major is ≤ `frappe-major`. It MUST equal `frappe-major` once any `v<major>.*` tag exists, or on a `release-please--*` branch. |
| C2 | [`metadata`] `package.json` `frappe` = `{major, branch}`. |
| C3 | [`metadata`] `flake.nix` `frappeVersion` and the `flake.lock` `frappe` node's `original.ref` are `version-<major>`; each sibling's `original.ref` is that sibling's resolved branch (§2.1: `version-<major>` under the generic rule, else the object's or `known-apps` entry's `branch`). |
| C4 | [`metadata`] The L3 range rules (shared code). |
| C5 | [`releases`] The version block holds, and `__version__` = `package.json` = manifest. |
| C6 | [`metadata`] `hooks.required_apps` ⊆ `siblings`; `frappe` isn't in `required_apps`. |
| C7 | [`typescript`; the blame-ignore part `hygiene`] No hand-written frappe globals (§2.9): no `declare (var\|let\|const) frappe`, no `interface Window {` with a `frappe` member, no `declare namespace frappe`, and `namespace frappe` augmentations only inside `declare global` in `types/<app>.augment.d.ts`, each member preceded by `// app-owned: <reason>`. Also: `.git-blame-ignore-revs` is valid (§2.20). |
| C8 | [`vite-register`] When `discover.vite`: `scripts.build` ends with `node scripts/vite-register.mjs` (S30). |
| C9 | [`typescript` with `check-js` for unchecked-js; `tests` for coverage-omit] Every `[[tool.frappe-nix.unchecked-js]]` path and every `[[tool.frappe-nix.coverage-omit]]` glob matches at least one tracked file; reasons are ≥ 10 characters. |

Exit 0 or 1, or 2 when a file can't be parsed.

**`frappe-nix ratchet --base <sha>`** applies only if the base's `pyproject.toml` has `[tool.frappe-nix]` (S24).

| Id | Rule |
|---|---|
| R1 | [`tests.coverage`] `fail_under(head) ≥ fail_under(base)` |
| R2 | [`python-types`] The count of `ty: ignore` comments in head ≤ the count in base. Every `ty: ignore` matches `#\s*ty:\s*ignore\[[a-z0-9-]+(,\s*[a-z0-9-]+)*\]\s+#\s+\S.{9,}`, so it names a rule and gives a reason of at least 10 characters. |
| R3 | [`listing`] For every semgrep baseline key, `count(head) ≤ count(base)`, and head has no key that base lacks |
| R4 | [`tests.testmap`] The `[[tool.frappe-nix.untested]]` targets in head ⊆ those in base |
| R6 | [`typescript.check-js`] The `[[tool.frappe-nix.unchecked-js]]` paths in head ⊆ those in base (a renamed file counts as new) |
| R7 | [`tests.coverage`] The `[[tool.frappe-nix.coverage-omit]]` globs in head ⊆ those in base |
| R5 | [`semgrep` or `listing`] Every `nosemgrep` comment names a rule (`# nosemgrep: <rule-id>`), and the same or the previous line carries a justification comment. A format rule, not a count. |

A module switched on in head but off in base has no base value: its ratchets start from head. A module switched off in head drops its ratchets. Exit 0 or 1, listing each violation.

**`frappe-nix minibench --mode customizations|doctypes --dest D`** builds a fake bench from the app's `flake.lock`. For frappe and each sibling: `git clone --filter=blob:none --no-checkout --sparse <clone URL>` (from the lock node: `https://github.com/<owner>/<repo>`, `https://<host>/<path>` for `gitlab`, or the `git` node's `url`; authenticated with `FRAPPE_NIX_FETCH_TOKEN` when set, §5.2), check out the locked rev, and set the sparse patterns (non-cone): `/*/modules.txt`, `/*/hooks.py`, `/pyproject.toml`, `/*/*/doctype/**/*.json`, `/*/*/custom/*.json`, `/*/fixtures/*.json`. Then:

- write `D/sites/apps.txt` and `D/sites/apps.json`, listing frappe, the siblings and the app;
- `mkdir D/env`;
- in `customizations` mode, `git worktree add --detach D/apps/<app> HEAD`; in `doctypes` mode, a sparse copy of the app's `/<app>/*/doctype/**/*.json`, `/<app>/*/custom/*.json` and `/<app>/fixtures/*.json`, so gen-doctypes sees the app's Custom Fields on core doctypes (postgrid's address and notification, jailbreak's version, automated_subscriptions' sales_invoice, timeclock's fixtures) and merges them into `FrappeDocTypes`.

The guard: exit 1 unless `D/sites`, `D/env` and `D/apps/frappe/frappe/modules.txt` exist. In customizations mode, the caller then runs the manual-stage hooks from `D/apps/<app>`, and `--show-diff-on-failure` reports what they rewrote.

### 5.10 `frappe-rename-app` (N1; dev-shell script and NixOS step)

This follows the rename-mechanics track. The **code half** is for PR 0, and the **site half** runs before migrate.

**Code half:**

```
frappe-rename-app code --from OLD --to NEW [--fleet <file> | --profile <ref> | --no-replace-check] [--dry-run]
```

0. Refuse (exit 1) when the pair is a replace pair. Replace pairs come from the app's resolved configuration, `[[replace-apps]]` of its org profile (§8.1), which needs no flag, and from a `"rename_mode": "replace"` entry of the fleet file given with `--fleet <file>`. frappe-nix has no built-in pairs (S37); the Avunu profile declares jailbreak → data_steward (§8.6), so running the code half in a jailbreak checkout synced with that profile refuses with or without `--fleet`. Because the Avunu fleet's renames (PR 0) land before the app opts in, an app **without** `[tool.frappe-nix]` must name where its replace pairs come from: `--fleet <file>`, `--profile <ref>` (resolved as `repo apply` does, §5.6), or an explicit `--no-replace-check`; with none of them, `code` exits 2 before touching the tree. A replace goes through the replace path below instead.
1. `git mv OLD NEW`. Every module folder and every `modules.txt` line stays the same. Then `git mv` every tracked file under `NEW/` whose basename starts with `OLD.` to the same name with `NEW.` (esign's `esign.desk.bundle.js` and `esign.control.bundle.js`, timeclock's `timeclock.app.bundle.js`, their `.css`/`.scss` twins), so the bundle names in hooks and the files agree.
2. Rewrite the tracked text files with the boundary regex `(?<![\w./])OLD(?=[.\/])`, covering imports, dotted paths in `hooks.py`, `patches.txt` (line by line), JS method strings, `/assets/OLD/` and `OLD/templates/…`. A match inside a string that is a bare filename (`OLD.<rest>` with no `/` and no further dotted module segment that resolves) is rewritten only if step 1 renamed a file with that basename; otherwise it is left alone and listed in the report. Also rewrite `pyproject` `[project].name`, `package.json` `name`, `app_name`, flit's include, the CI `--app` and `release-please` `package-name`.
3. Bump `modified` in every standard JSON whose content changed (`import_file.py:150`).
4. Append an `override_whitelisted_methods` shim, mapping each old whitelisted dotted path to its new one, between `# frappe-nix:rename-shim-begin OLD` and `# frappe-nix:rename-shim-end` in `hooks.py`. Keep it for at least one minor release.
5. Print what it deliberately left alone: custom fieldnames, DocType names, Communication types, CSS classes, localStorage keys, and module names. Module names are kept (Q5).

Exit 0, or 1 when the tree is dirty or the pair is a replace.

**Replace path** (generic; the Avunu fleet's case is jailbreak → data_steward, A.2). data_steward is a new app with its own module (`Data Steward`) and doctypes (`Data Steward Settings`), so it never collides with jailbreak's `Jailbreak` Module Def or `Jailbreak Settings` while both are installed; Q5's keep-the-module-names rule doesn't apply to it. Its `after_install` copies the Jailbreak Settings values across, mapping each capability, and is idempotent. Wiring:

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

**Caller 1: the app's own build** (every bench, including Frappe Cloud and pilot's get-app bench). `scripts/vite-register.mjs` is a managed copy of the module with a small entry point, and `scripts.build` ends with `node scripts/vite-register.mjs` (C8). frappe's esbuild.js writes `assets.json` first and then runs each app's `yarn build`, with cwd `<bench>/apps/<app>`, so the registration lands after esbuild's own write. The entry point resolves `sitesDir` as `$FRAPPE_BENCH_ROOT/sites` when set, else `path.resolve(process.env.PWD ?? process.cwd(), "../../sites")` (the logical path, since `apps/<app>` may be a symlink), else the realpath of `../../sites`, and uses the first one containing `apps.txt`. When none does (a standalone `yarn build` outside a bench), it prints `vite-register: no bench found; skipped` and exits 0.

**Caller 2: `lib/js/esbuild-preload.js`** (Nix builds, where the app dir is a store path). The `child_process` wrapper returned to esbuild.js becomes unconditional. Only the carry-on behaviour stays behind `FRAPPE_NIX_KEEP_GOING`. After every `execSync` whose command matches `/^yarn( run)? build\b/` and which succeeds, with cwd `<bench>/apps/<x>` and `<x> != "frappe"`, it calls `register` with `sitesDir` = `$FRAPPE_BENCH_ROOT/sites` when set, else three levels above esbuild.js's `parent.filename`.

frappe's `--using-cached` glob uses the same `<name>.bundle.<ext>` keys, so pilot's prebuilt assets resolve identically.

**Conventions for apps** (`docs/app-standards/assets.md`):

- A Vite entry is emitted as `<name>.bundle.[hash].js` and `.css` under `public/dist/`, with `build.manifest: true`.
- Vite sources MUST NOT be named `*.bundle.*` under `public/`, or esbuild compiles them a second time; taskview moves them to `src/*.entry.ts`.
- `www` templates use `{{ bundled_asset('<name>.bundle.js') }}` instead of fixed paths, because production serves `/assets` with a one-year cache.
- `update-assets.mjs` is deleted (it is on the retire list, §2.4.1); `scripts/vite-register.mjs` replaces it.
- `[tool.bench.assets]`, for pilot, is declared only by apps with the `pilot-assets` module on.

**`lib/node-targets.nix` and `lib/node-locks.nix`.** A new option, `frappe-nix.app.excludeNodeTargets` (a list of top-level directory names, default `[]`), removes directories from node-target discovery; both discovery implementations honour it. Its default changes nothing for any app, so a non-opted-in app with a nested frontend named `docs-site` keeps being built (S35). An opted-in app's managed `flake.nix` sets `[ "docs-site" ]` while the `docs-site` module is on (§2.5), because a docs tool's site (docusystem's, in the Avunu fleet) is built by its own workflow, never by the bench; an app adds other names through `nix/local.nix`.

### 5.12 CI mode and the worktree port salt (N1, `modules/devenv.nix`)

These are dev-shell behaviours, not standards modules. `FRAPPE_NIX_CI` applies to every app-mode user and is inert unless set; the port salt applies only to opted-in apps unless an app turns it on (S35).

**`FRAPPE_NIX_CI=1`** is read when enterShell runs, not at evaluation, so one shell derivation serves both modes. When it is set, enterShell:

- skips `frappe-nix-node-verify` and `frappe-nix-node-modules` for every app;
- skips `frappe-nix-apps-report`, which only runs in bench mode anyway;
- replaces the banner with the single line `frappe-nix: CI mode (<bench>, site <site>)`;
- exports `DEVENV_IN_DIRENV_SHELL=true` and `PC_TUI_ENABLED=0`.

Everything else, including app materialisation, the env symlink and bench patches, runs unchanged. `frappe-test`, ty and `bench --site … run-tests` don't need node_modules. `bench build` does, and so do the shots, so the nightly `integration` and `shots` jobs don't set the flag.

**Ports.** A new option `frappe-nix.ports.worktreeSalt` defaults to the opt-in test of `lib/standards/shell.nix` (true with `[tool.frappe-nix]`, false without), so a non-opted-in app's linked worktrees keep today's ports; any app may set it either way. In app mode, define `seed`:

- `cfg.benchName + "@" + builtins.getEnv "PWD"` when `ports.worktreeSalt` is true and `PWD/.git` is a regular file, which means a linked worktree;
- otherwise `cfg.benchName`, which keeps today's ports.

The new option `frappe-nix.ports.offset` defaults to `portOffsetFor seed`. The environment variable `FRAPPE_NIX_PORT_OFFSET`, read with `builtins.getEnv` (evaluation is already impure), overrides it when it is a number from 0 to 899.

`webBase`, `dbBase` and the Mailpit SMTP, HTTP and POP3 defaults (devenv.nix:752, :770 and :816) all derive from `ports.offset`. The `frappe:config` task fails before start-up when nginx's or Mailpit's port is already bound, naming the port and suggesting `FRAPPE_NIX_PORT_OFFSET`.

Bench mode is unchanged. Its `common_site_config.json` is committed, so a port derived from the path would dirty the tree.

### 5.13 `frappe-nix profile` (N3)

```
frappe-nix profile show [--explain] [--format toml|json]      # the resolved configuration of the app in the cwd
frappe-nix profile validate <profile.toml> [--templates <dir>] # an org profile, before it is published
frappe-nix profile list                                        # the built-in profiles, with their descriptions
```

- `show` prints the resolved configuration (§8.4) and `modules`. `--explain` adds, per value, the layer that set it. It exits 2 on any resolution error, with the same message sync would give.
- `validate` checks a profile file on its own: the profile schema; `extends` names a built-in; no app-only key; every module's needs are met **under the profile's own switches** (an app can still break them, which its sync reports); `requires-frappe-nix` admits the running version; each file in `--templates` (default: `templates/` beside the file) overrides an `overridable` template or is named by an `[[extra-files]]` entry, and renders against the fixture contexts of §7 N3 without an undefined variable; `[[extra-files]]` paths collide with no manifest path. Exit 0, 2 (invalid) or 3 (environment).
- `list` prints `minimal` and every `recommended@<minor>` snapshot, marking the one plain `recommended` resolves to (S42). For an app on the floating `recommended`, `sync` (and so `repo rollout`) prints a warning, exit 0, whenever the snapshot `recommended` resolves to differs from the one it resolved to at `HEAD` (whose frappe-nix version sync reads from the `# vX.Y.Z` comment of `HEAD`'s callers or `HEAD`'s `flake.lock` rev): it lists each value that changed and suggests pinning `recommended@<old minor>`. The bump PR therefore says in its own log which defaults moved.
- `frappe-nix config <key> [--json]` (N3a) prints one resolved value, a list one item per line; `frappe-nix config --github-output modules` appends `<module>=true|false` for every module to `$GITHUB_OUTPUT` (the CI `cfg` step, §4).

Exit codes as sync's (§3.3).

---

## 6. Versioning and compatibility

### 6.1 How an app pins frappe-nix

- `flake.nix`: `frappe-nix.url = "github:Avunu/frappe-nix/release-1"`, unless the app pins another source with `dev-shell.frappe-nix-url` (§2.5).
- `flake.lock` records the commit, which is always a `v1.*` tag commit for the default URL, because `release-1` only fast-forwards to tags.
- The caller workflows use `@<that commit> # v<version>`, which sync enforces (§3.7).
- The no-Nix jobs install `frappe-nix-tools` at the same commit (§4.1).
- An app with an org profile also locks `standards-profile` (S38), so its rendered files come from exactly one profile revision too.

The result is one frappe-nix version, and one profile revision, per app commit, everywhere.

### 6.2 How a release propagates

1. A frappe-nix PR merges to `main`. frappe-nix's release-please opens or updates its release PR. Its `dispatch-ci` dispatches `check.yml` (with `vm-tests` false) and `pr-policy.yml` on that PR. A person or the main agent merges it.
2. release-please tags `vX.Y.Z`. frappe-nix's `release.yml` fast-forwards `release-1` to the tag (REST; the `release-*` ruleset's only bypass is integration 15368).
3. Whoever maintains the apps moves them (Avunu fleet: the main agent runs `frappe-nix repo rollout --to vX.Y.Z --all --fleet …`, §5.8), with the user's `gh` token. For each app it updates the `frappe-nix` lock node, relocks, runs `frappe-init --sync` (which rewrites the caller SHAs and every changed managed file), and opens `chore(deps): frappe-nix vX.Y.Z` with auto-merge on. Until it does, each app's nightly `drift` job keeps an issue open and audit row A10 turns amber after 14 days.
4. Auto-merge happens once `ci / *` is green. If a stricter rule in the new version fails the app, the PR waits, and a person or agent pushes the fix to the PR branch.

Dependabot `nix` never moves frappe-nix or `standards-profile` (S5, S38). It moves frappe and the siblings at `dependabot.cadence`; `deps.yml` relocks those PRs with `GITHUB_TOKEN`, which works because they change no workflow file. An app without `ci` or `dependabot` simply runs `nix flake update frappe-nix && frappe-init --sync` when it wants a new release.

### 6.3 frappe-nix's own versioning (N6)

- **Release.** `release-please-config.json`:
  - `release-type: simple`, which uses `version.txt`;
  - `include-v-in-tag: true` and `target-branch: main`;
  - extra-files: `py/frappe_nix_tools/frappe_nix_tools/__init__.py` (generic block) and `py/frappe_nix_tools/pyproject.toml` (`type: toml`, `jsonpath: $.project.version`).
- **First release.** The first release is `v1.0.0`, via `Release-As: 1.0.0` in N6's commit.
- **Commit types for dependabot in frappe-nix:**
  - `fix(deps)` for `nix` (nixpkgs, devenv, and the marketplace, pilot and semgrep-rules pins), for `github-actions` (the action SHAs inside the `app-*.yml` files), and for `pre-commit`, so each bump that changes what apps run ships as a patch release;
  - `chore(deps)` for `uv /dev` and `npm /docs-site`.
- **Semver contract:**
  - **MAJOR:** a removed or renamed reusable-workflow input, secret, output or job name; changed required-check contexts; a new `[tool.frappe-nix] schema` or profile `schema`; a removed or renamed module or module parameter; a changed meaning of an existing parameter value; a strategy change that can't be applied by `--sync` alone; a removed tool or CLI flag; turning a module **on** in `minimal`; any change to an already released `recommended@<minor>` snapshot other than one that accepts strictly more code (S42); or a change of the schema default of `releases.branching`, `releases.version-scheme`, `releases.tag-prefix` or `integration-branch`, which would move every repository's branches, tags and rulesets. A new major gets a new `release-<N>` branch, and apps move by editing the flake URL in a deliberate PR.
  - **MINOR:** new managed content or checks that `--sync` applies, even if they make previously passing code fail (it then waits in the bump PR); a new module (off in both built-in profiles and every existing snapshot); a new `recommended@<minor>` snapshot, which may turn modules on or change defaults and which plain `recommended` then means; new parameters with defaults (a new parameter's default reproduces the old behaviour); new optional inputs with defaults; new tools or flags. An app on a pinned snapshot, or an org profile extending one, therefore sees only new checks of modules it already has on, never new modules or new defaults.
  - **PATCH:** fixes and pin bumps.
- **Compatibility guarantees within v1:**
  - `app-*.yml` at any `v1.y` accepts a caller rendered by any `v1.x`, because inputs are only ever added, with defaults;
  - `[tool.frappe-nix] schema = 1` keys, profile `schema = 1` keys and module names are never removed;
  - deprecated keys keep working for one minor and `--check` warns about them, with exit 0;
  - `minimal` renders the same file set in every `v1.x`, and a released `recommended@<minor>` resolves to the same values in every later `v1.x` (§7 N3);
  - an app that has not opted in is never touched by any `v1.x` (S35).
- **Frappe major bump (v17).** Set `frappe-major = 17`, run `--sync` (the flake inputs, ranges, `package.json` stanza and release branch all follow), and, under `version-scheme = "frappe-major"`, land a `Release-As: 17.0.0` commit. Keeping `version-16` maintained alongside it is out of scope for v1 of this spec. Reserved key: `[tool.frappe-nix] maintenance-branches`.

### 6.4 Org profile versioning

- An org profile repository SHOULD release like frappe-nix: tags `vX.Y.Z` and a moving branch `v<major>` fast-forwarded to each tag. Apps then write `profile = "github:<owner>/<repo>/v1"`, and the lock pins a tag commit. (A profile pinned to a branch that moves on every commit works, but every commit then becomes a rollout.)
- Its own semver: MAJOR for a change that makes `frappe-nix profile validate` or an app's `--check` fail without an app edit (a module turned on that needs app config, a stricter value); MINOR for new modules turned on or changed parameters that `--sync` applies; PATCH for org-value fixes (a URL, an e-mail).
- `requires-frappe-nix` (§8.1) declares the frappe-nix versions it is written for. Sync refuses a profile whose range excludes the running frappe-nix-tools (exit 3), so an app that moves frappe-nix past a profile's range must move the profile in the same PR.
- A frappe-nix MINOR never breaks an org profile that validated against an earlier `v1.x`: new keys get defaults and no key is removed (§6.3).
- P (Avunu) follows this; its CI validates `profile.toml` against the frappe-nix release named in its `requires-frappe-nix` lower bound and against the latest one, and compares the resolved configuration of the fixture app with the committed `resolved.snapshot.json`, so any frappe-nix release that would change an Avunu value fails P's CI before it can reach an app.
- **P's release and protection.** P's `release.yml` runs release-please on `main` (tags `vX.Y.Z`) and then moves `v1` with a REST PATCH (`force=false`) to the tag commit, exactly as frappe-nix's N6 flow moves `release-1`. P's rulesets (`repo-policy/` in P, applied with `frappe-nix repo apply --policy-dir`): `main` requires a PR and P's `validate` check; `refs/heads/v*` allows only GitHub Actions (integration 15368) to update, with no deletion and no force-push; `refs/tags/v*` forbids deletion and update. A push to `v1` reaches every app only through a reviewed `frappe-nix repo rollout --profile-to` PR (S38), and these rules keep anything but the release workflow from moving `v1`.

---

## 7. Acceptance tests per PR

Each PR's own CI MUST prove these. A "nix check" is under `tests/standards/` and runs in `check.yml` through `standards-all`. A "selftest" is the PR's own `selftest-*.yml` workflow, path-filtered to its files plus `workflow_dispatch`. Tests that need an org profile use the fictitious `tests/fixtures/profiles/example-org` (publisher "Example Org", `example.org` URLs, tile `#336699`), never Avunu's values. The cross-cutting tests 1.2 adds are marked **[1.2]**: default app mode unchanged without opt-in (N1, N2, N3), `minimal` renders nothing extra (N3), toggling a module off removes its files and jobs (N3, N4), no vendor strings in frappe-nix (N3a), and the review-round cases of Appendix R2.

**N3a: hook points**
- `nix flake check --no-build` passes, and `nix build .#frappe-nix-tools` builds against the locked nixpkgs (tomlkit 0.15.0) with `pythonRuntimeDepsCheck` on. `frappe-nix --version` prints `0.0.0`, and `frappe-nix nonexistent` exits 2 listing the commands.
- **Self-contained package:** in a job with no frappe-nix checkout, `uv tool install "frappe-nix-tools @ git+file://$PWD#subdirectory=py/frappe_nix_tools"` from a clean clone, then `frappe-nix data-path known-apps.json` and `frappe-nix data-path schema/tool-frappe-nix.schema.json` print existing files. (N3 extends this to `frappe-nix sync --check` on the fixture.)
- `.#checks.x86_64-linux.standards-all` builds.
- **Profiles:** `data/profiles/minimal.toml` and `recommended@1.0.toml` are byte-identical to §8.5 (modulo the header comment) and validate against `schema/profile.schema.json`; every module named in §8.2 has a table in each; `minimal` enables only `dev-shell`; `profile = "recommended"` and `"recommended@1.0"` resolve identically, and `"recommended@9.9"` is exit 2. From N6's first release on, a nix check compares every released snapshot file with its content at the tag that introduced it (S42). A pytest of `common/config.py` covers the merge order of §8.4 (a value set in all three layers resolves to the app's; a table merges key by key; an array replaces), an app-only key in a profile (exit 2), a profile-only key in the app table (exit 2), an unmet module dependency (exit 2 naming both modules), and `frappe-nix config modules.ssort` printing the resolved boolean.
- **[1.2] No vendor strings in frappe-nix** (`standards-vendor-neutral`, S37): the check scans `py/frappe_nix_tools/frappe_nix_tools/**` (all code and data, excluding `tests/` and fixtures), `lib/**`, `repo-policy/repo-settings.json`, `repo-policy/rulesets/**`, `templates/app/**`, and `.github/workflows/app-*.yml` and `fleet-audit.yml`, with the patterns in `tests/standards/vendor-denylist.txt`: case-insensitive `avunu` not followed by `/frappe-nix` or `/frappe-runtime`, `avu\.nu`, `#834AFF`, `Avunu LLC`, `mail@avu`, `carbon-theme` and `docusystem` (the docs tool is reached only through profile values). Planted variants each fail naming the file and line: `"author": "Avunu LLC"` in a template, `#834aff` in `recommended@1.0.toml`, a `REPLACE_PAIRS` entry naming `jailbreak` with an `avunu` comment in `lib/rename/`, and `registry-fork: { default: "Avunu/marketplace" }` in `app-release.yml`. `docs/**`, `tests/**` and `repo-policy/self/**` are out of scope; docs show Avunu values only inside blocks introduced by "Avunu profile example" (a doc lint in N3a's docs check). The `frappe-types` dependency's source URL is allowed in docs (§2.9).
- **Opt-in shell:** the app-mode shell of an app **without** `[tool.frappe-nix]` has neither `frappe-nix` nor nixfmt/statix/deadnix on PATH and doesn't set `blame.ignoreRevsFile`; with the table, it has them (string assertion on `lib/standards/shell.nix`'s output for both fixture pyprojects). **[1.2]** A third fixture pyproject that Nix's `fromTOML` rejects (a TOML datetime, `released = 1979-05-27T07:32:00Z`) and has no table evaluates the shell without error as not opted in; the same file with a `[tool.frappe-nix]` line is opted in; a line `# [tool.frappe-nix]` (a comment) is not.
- A test `lib/scripts.d/` file and a test `lib/standards/tools/` file (fixtures under `tests/standards/fixtures/`) are picked up by the loaders, proven by a nix eval check.
- The opted-in app-mode shell exposes `frappe-nix` and `frappe-init`, and the flake exposes `apps.frappe-init`. Proven with the fixture app's flake and `--override-input frappe-nix path:.`.
- The docs stubs pass the existing docs workflow's check, and the docs index is titled "App standards and quality gates".
- **[1.2] Naming** (a pre-merge check, deliberately **not** committed, so the tree never has to contain the word it looks for): the reviewer or merging agent runs `git grep -i -e "$TERM"` over the tree (excluding nothing), `git log -i --grep "$TERM" <base>..HEAD` and a search of the PR title and body, with `$TERM` taken from Appendix N of the internal copy of this spec; all find nothing. frappe-nix MAY add a CI step to its own `pr-policy.yml` that reads the terms from the Actions secret `FRAPPE_NIX_DENY_TERMS` (newline-separated; secrets are masked in logs and absent on fork PRs, where the step is skipped), but no committed file names them.

**N1: runtime**
- **Nix checks:**
  - Port offset: the primary checkout's offset is unchanged from today; in an opted-in app, a linked worktree (`.git` is a file) gets a different one; **[1.2]** in a non-opted-in app it keeps today's offset unless `ports.worktreeSalt = true`; `FRAPPE_NIX_PORT_OFFSET=123` sets all of web, db and the three Mailpit ports from 123.
  - `coverage` is importable from the generated root's dev env (`python -c 'import coverage'` over `devPythonEnv`).
  - `FRAPPE_NIX_CI=1` enterShell renders without the node-verify and node-modules calls (string assertion on the rendered enterShell).
  - The generated bench root's dev group has `coverage`. **[1.2]** For an opted-in fixture whose `tools/pyproject.toml` lists `ruff`, `semgrep` and `prek`, the generated root has none of `ruff`, `pre-commit` or `semgrep`, also after `ensure-root` runs on a root that had them; with `tools/pyproject.toml` listing only `ruff`, only `ruff` is dropped. A non-opted-in app-mode root and an existing bench-mode root (fixture copies of `templates/bench/pyproject.toml` with the three entries) keep all three after `ensure-root`, byte-identical, and `templates/bench/pyproject.toml` still lists all three.
- **`selftest-runtime.yml`** on the fixture app:
  - `FRAPPE_NIX_CI=1 nix develop --override-input frappe-nix path:$GITHUB_WORKSPACE -c frappe-test --ci` exits 0, and the log contains no `Installing node_modules`.
  - `coverage.json` has no key containing `/.frappe-nix/`, and contains `standards_fixture/api.py`.
  - A variant with `fail_under = 100` exits 2, and a variant with `fail_under` 5 points below the measured total exits 2 with the `raise … fail_under` message.
  - A variant with the exemption removed exits 3 and names `standards_fixture.api.legacy`.
  - A variant whose extension class subclasses another app's extension (the A.0 #4 shape) exits 4.
  - `frappe-test-report.json` validates against its schema.
  - The default setup works with erpnext as a sibling (`module:erpnext.tests.bootstrap_test_data`), in a variant with the erpnext sibling.
  - The fixture's `after_request` hook (T6) is a target and is reported tested; a variant whose module calls `frappe.get_all` at import time is probed without crashing, and a variant whose module raises on import reports that target untested with the error.
  - A variant with `shell-checks = ["touch stray.txt"]` exits 7 and names the untracked file.
  - **Module switches:** a variant with `[tool.frappe-nix.tests] enable = false` runs `frappe-test --ci` without starting the bench (no process-compose socket appears) and reports stages 1–6 as `skipped`; a variant with `python-types` off reports stage 7 `skipped`; a variant with `tests.coverage.target = 60` and `fail_under = 55` passes the upward ratchet when the total is 59, and fails it at 57 with `raise-margin = 2` turned to the message naming 60 as the cap.
- **`frappe-rename-app`:**
  - On a scratch site, the fixture renamed to `standards_fixture2` migrates cleanly. Data, the Patch Log and the Scheduled Job Type `name` and `stopped` values are kept.
  - A second run prints `nothing to do`.
  - `code --dry-run` shows the expected diff.
  - On `tests/fixtures/rename-esign`, `code --from esign --to esign_webforms` renames `esign.desk.bundle.js` and `esign.control.bundle.js` to `esign_webforms.*`, and every `app_include_js` entry in the rewritten `hooks.py` names an existing file. A bare `esign.<x>` string with no matching file is left alone and reported.
  - `code --from jailbreak --to data_steward --fleet <fixture fleet with that replace entry>` exits 1 (replace pair); in a fixture app synced with a profile whose `[[replace-apps]]` has the pair, it exits 1 without `--fleet`; in a non-opted-in fixture without `--fleet`, `--profile` or `--no-replace-check` it exits 2 and changes nothing. The `replacedApps` NixOS check installs a stand-in new app, uninstalls the old one inside the snapshot, and is a no-op on a second run.
- **Docs:** `docs/development/README.md:36` and `docs/reference/dev-shell-options.md:12` no longer claim the allocator walks forward.

**N2: assets**
- **Nix check** `tests/esbuild-preload.js`, extended:
  - a fixture app's `public/dist/.vite/manifest.json` with `js/foo.bundle.AbC123.js` and `css/foo.bundle.XyZ.css` registers `foo.bundle.js` and `foo.bundle.css` in `assets.json` under `/assets/<app>/dist/…`;
  - a non-matching `index-AbC.js` is ignored;
  - registration happens without `FRAPPE_NIX_KEEP_GOING`;
  - frappe's own keys aren't touched.
- **Nix check:** the node-targets fixture with a top-level `docs-site/package.json` yields no `docs-site` target, in either the Nix discovery or `node-locks.sh`, when `excludeNodeTargets = [ "docs-site" ]`; **[1.2]** with the default `[]` it yields one, exactly as on `main`.
- **Nix check:** `tests/fixtures/spa-app/`, whose `build` script writes a hashed bundle and a manifest with no network, built as a builtBench, has `assets.json` mapping `spa.bundle.js` to the hashed file.
- **Stock bench (no preload).** In `selftest-assets.yml` (N2): a plain frappe bench made with `bench init` from the pinned frappe, no `NODE_OPTIONS`, `bench get-app` of the spa-app fixture, `bench build --app spa_app`; `sites/assets/assets.json` maps `spa.bundle.js` and `spa.bundle.css` to the hashed files. A second `bench build` under frappe-nix's preload leaves `assets.json` byte-identical (idempotent). With `--hard-link`, `sites/assets/spa_app/dist/` contains the hashed bundle.
- **Nix check:** `lib/js/vite-register.cjs` and the packaged `scripts/vite-register.mjs` template contain the same function body (a string comparison after stripping the entry point).

**N3: scaffold**
- **[1.2] Default app mode unchanged without opt-in** (nix check `standards-optin`):
  - `templates/app/` is byte-identical to frappe-nix `main` at 9e63c96 (`flake.nix`, `.envrc`, `.gitignore`);
  - `frappe-init --app --frappe-version version-16` in a fixture app without `[tool.frappe-nix]` writes exactly `flake.nix`, `.envrc` and `.gitignore`, byte-identical to what `main`'s `frappe-init` writes for the same app, and leaves `pyproject.toml` untouched;
  - `frappe-init --sync` and `--check` on that app exit 2 with the opt-in hint and write nothing (`git status --porcelain` empty).
- **[1.2] `minimal` renders nothing extra:** `frappe-init --sync --standards minimal` on the same app (offline, Appendix I) changes exactly `flake.nix`, `.envrc`, the `.gitignore` block and `pyproject.toml` (the new `[tool.frappe-nix]` table, two keys plus `schema`; no other key of `pyproject.toml` changes), plus `flake.lock`; `--check` then exits 0. The manifest's live entries for `minimal` are exactly the three `dev-shell` paths (plus node-lock seeds when siblings exist).
- **[1.2] Toggling a module off removes its files** (nix check `standards-toggle`), on a fixture synced with `recommended`:
  - setting `[tool.frappe-nix.js] tool = "none"` and re-syncing deletes `.oxlintrc.json` and `.oxfmtrc.jsonc`, removes the `oxfmt`/`oxlint` hooks, the `format`, `format:check`, `lint` and `check` scripts (each still equal to its rendered value) and the `oxlint`/`oxfmt` devDependencies, and `--check` exits 0;
  - setting `[tool.frappe-nix.ci] enable = false` deletes every `.github/workflows/*.yml` sync rendered and leaves an app-owned `.github/workflows/custom.yml`; `dependabot.yml` stays (its module is separate);
  - setting `[tool.frappe-nix.typescript] enable = false` removes the `typecheck` caller job from `ci.yml` (the other jobs are byte-identical) and deletes every `tsconfig*.json` sync wrote;
  - turning a module off whose file has a non-empty local region (`.pre-commit-config.yaml` with content in `repos`, after turning off every hook module) exits 2 naming the region and deletes nothing;
  - a managed `pyproject.toml` key the app changed (`[tool.ruff] line-length = 120`) is left in place with a warning when `python-lint` turns off;
  - turning the module back on restores the files byte-identically;
  - **[1.2] js off keeps the adopter's scripts:** with `js.tool = "none"`, `stylelint` on and an existing `"lint": "eslint . && stylelint 'x/**/*.scss'"` and `"check": "yarn lint && vitest"`, sync leaves `lint` and `check` untouched, writes `lint:css`, deletes no `.eslintrc*`, and `--check` exits 0;
  - **[1.2] third-party formatter:** with `js.tool = "none"`, reformatting the rendered `.pre-commit-config.yaml`, `.github/dependabot.yml`, `ci.yml` and `release-please-config.json` with prettier's defaults (2-space, quote and wrap changes) gives `--check` exit 0 and a following `--sync` writes nothing; changing a value in any of them is still exit 1;
  - **[1.2] metadata parameters:** with `metadata.build-backend = "any"`, `package-type = ""` and `package-manager = ""`, a setuptools `[build-system]`, a `[tool.poetry]` table, `requirements.txt`, `MANIFEST.in`, `"type": "commonjs"` and `"packageManager": "pnpm@9"` all survive sync and `--check` exits 0, while `typescript`, `js` and `releases` stay on;
  - **[1.2] retire rules by function:** with `ci` on and `releases` off, a tracked `.github/workflows/release.yml` containing `release-please-action` survives; with `dependabot` off, one containing `dependabot/fetch-metadata` survives; a tracked `.github/workflows/check.yml` survives under `recommended` and is retired under the `example-org` profile's `[[retire]]` entry; a path in `retire-keep` survives every rule;
  - **[1.2] the app's own flake:** on first opt-in, a fixture `flake.nix` with an extra input and an `x86_64-linux`-only `systems` exits 2 with the diff and writes nothing; with `--force` it is replaced; content in the `inputs` and `envrc` local regions survives later syncs; a local input named `frappe` is exit 2; `dev-shell.systems = ["x86_64-linux"]` renders one system; `dev-shell.frappe-nix-url = "github:Avunu/frappe-nix/v1.0.0"` is locked once and not re-locked by a second sync;
  - **[1.2] siblings off the Frappe convention:** an object-form sibling `{ repo = "example/shared_lib", branch = "main", range = ">=2.0.0,<3.0.0" }` renders `github:example/shared_lib/main` and that range, and C3, C4 and L3 pass with `original.ref = "main"`;
  - **[1.2] TypeScript preset:** `typescript.preset = "inline"` renders `tsconfig.base.json` without `extends`, no `frappe-types` devDependency and no `frappe-types/*` types, `tsc --build` passes on the fixture's browser project, and `check-js = true` with it is exit 2.
- **Profiles** (nix check `standards-profiles`):
  - `recommended` renders the file set §8.5 lists for the fixture, and `--check` exits 0;
  - the `example-org` fixture, used with `--profile-path`, renders its org values: `package.json` `author`, the README `license` block, L2's expected publisher; its template override of `readme/support.md.j2` is used; its `[[extra-files]]` entry (`SECURITY.md`, module `hygiene`) is rendered, and deleted when `hygiene` is turned off;
  - an override of a non-overridable template (`.github/workflows/ci.yml.j2`) is exit 2;
  - `readme` on with an empty `org.license` is exit 2 naming `org.license`; `releases` on with `commits` off is exit 2 naming both;
  - `profile = "github:example/profile"` adds the `standards-profile` input to `flake.nix` (phase A, with `FRAPPE_NIX_URL_OVERRIDE`-style override for the test) and the dependabot `nix` ignore entry; switching back to `"recommended"` removes both;
  - `--check` in a no-Nix job reads the org profile through `pin-path standards-profile`, and a tampered tree fails its narHash with exit 3;
  - a profile with `requires-frappe-nix = ">=9"` is exit 3;
  - `frappe-nix profile validate tests/fixtures/profiles/example-org/profile.toml` exits 0, and exits 2 on a copy with an unknown module;
  - `main+tags`: with `releases.branching = "main+tags"`, every rendered `target-branch`, `branches:` trigger and `integration-branch` input is `main`, `release.yml` passes `release-branch: ""`, and the README installation block names `v<__version__>` inside the release-please version markers, with `README.md` in `extra-files`;
  - **[1.2] integration branch:** `integration-branch = "trunk"` with `releases` off renders `trunk` in every `target-branch`, trigger and input; `--standards` on a fixture whose `origin/HEAD` is `master` writes `integration-branch = "master"`;
  - **[1.2] snapshots:** a fixture pinned to `recommended@1.0` resolves identically under a test copy of the package that adds a `recommended@1.1` snapshot turning `ssort` on, while a fixture on plain `recommended` turns it on and sync prints the changed-defaults warning naming `ssort.enable`;
  - **[1.2] in-repo profile:** `profile = "./.standards-profile"` (a tracked directory with `profile.toml` extending `recommended` and a `templates/readme/support.md.j2` override) renders its org values and override with no `standards-profile` flake input, and `--check` in a no-Nix job reads it from the checkout;
  - **[1.2] other hosts:** a profile `gitlab:example/profile` and an object-form sibling with `flake-url = "git+https://git.example.org/libs/shared_lib?ref=main"` render the expected flake inputs; `pin-path` fetches both from a local test server (GitLab archive API and smart-HTTP git) and verifies their narHash; with the server requiring a bearer token, the fetch fails with exit 3 and the `FRAPPE_NIX_FETCH_TOKEN` hint, and passes with the variable set.
- **Nix check `standards-manifest`:** every manifest entry names a module that exists; every `cfg`/`org` key a template reads is in its `uses`; every `uses` key exists in the profile schema.
- **Nix check `standards-sync`,** on a copy of the fixture app (synced with the `example-org` profile, which turns every module on):
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
  - with an SPA entry whose `tsconfig` is `tsconfig.json`, the solution is rendered as `tsconfig.solution.json`, the app's `tsconfig.json` is untouched, and `scripts.typecheck` is `tsc --build tsconfig.solution.json && vue-tsc …`; with `typescript.browser = false` no browser project exists;
  - a `web-include` glob moves a file from `tsconfig.desk.json` and the oxlint desk override to `tsconfig.web.json` and the web override;
  - an `unchecked-js` entry appears in the desk exclude; one naming an untracked file is C9 exit 1; `typescript.exclude` naming a tracked `.js` under the package is exit 2;
  - a `generated` glob appears in the prek exclude, the `validate_copyright` exclude and the oxfmt and oxlint ignorePatterns, and `prek run validate_copyright --all-files` followed by `--check` exits 0 (no stamp/sync fight on `marketplace/shots.d.ts`);
  - a fixture with package.json `1.0.0` and `__version__ = "0.0.1"` and no manifest gets package.json `0.0.1` and manifest `0.0.1` on its first sync, and C5 passes;
  - the README `license` block renders identically with the system clock set to 31 December and to 1 January.
- **Nix check:** no path appears in two manifest fragments; every template renders for the fixture contexts {plain, erpnext+hrms siblings, scss, nested frontend, SPA at root, SPA in `portal/` without package.json, docs-site, pilot-assets, Vite} × {`minimal`, `recommended`, `example-org`}.
- **Nix check:** `frappe-init --app --standards recommended` in a fixture produces `templates/app/`'s three files, then what `frappe-nix sync --write` renders for `recommended`, and nothing else.
- **`selftest-scaffold.yml`,** on the rendered fixture:
  - `nixfmt --check flake.nix`, `oxfmt --check`, `ruff format --check`, `actionlint`, `zizmor` and `uv lock --check --project tools` all pass;
  - after `yarn install`, `prek run --all-files` passes; this needs the network for the remote hooks;
  - `tsc --build` passes against the published `frappe-types` 16.5.0 (on npm since 2026-10-07);
  - `nix flake lock && nix eval .#packages.x86_64-linux.default.drvPath` succeeds;
  - `frappe-init --check` on a read-only clone of carbon_frappe (which has no `[tool.frappe-nix]` at its pinned commit) exits 2 with the opt-in hint and no crash; with `--standards` given through a temporary copy that adds `[tool.frappe-nix] profile = "recommended"`, it reports drift and no crash.
  - **Bootstrap:** on a copy of the fixture with no `flake.nix` and no `flake.lock` (the postgrid and jwt_auth shape), and on one whose lock pins frappe-nix `main` (the carbon, taskview and timeclock shape), one `frappe-init --sync --standards recommended --frappe-version version-16` run with `FRAPPE_NIX_URL_OVERRIDE=path:$GITHUB_WORKSPACE FRAPPE_NIX_ALLOW_SKEW=1` and **without yarn or uv on PATH** leaves a tree on which `--check` exits 0 immediately: phase A locked, phase B re-entered the dev shell, `yarn.lock` and `tools/uv.lock` exist.
  - **No-Nix install:** `uv tool install "frappe-nix-tools @ git+file://…#subdirectory=py/frappe_nix_tools"` in a job without the frappe-nix checkout on disk, then `frappe-nix sync --check` on the rendered fixture, exits 0 (`FRAPPE_NIX_ALLOW_SKEW=1`).
- **Nix check:** with the erpnext and hrms siblings, node-lock seeding writes `nix/node-locks/{frappe/ui,erpnext/banking,hrms/frontend,hrms/roster}` into an empty fixture and leaves existing ones alone.

**N4: CI**
- `actionlint` and `zizmor` (strict `hash-pin`) pass on frappe-nix's `.github/workflows/*.yml`, on every rendered caller for the fixture under `recommended`, `example-org` (everything on) and a gate matrix (each of `typescript`, `tests`, `listing`, `commits`, `releases`, `dependabot` off in turn), and on the rendered `.github/dependabot.yml` (every entry has a `cooldown`). A planted dependabot entry without `cooldown` makes the zizmor hook fail. actionlint accepts per-job `defaults.run.working-directory: ${{ inputs.app-root }}` and finds no job-level `if:` reading `secrets`.
- **[1.2] Callers contain only enabled gates:** with every gate on, `ci.yml` has the jobs `lint`, `typecheck`, `test`, `marketplace`, each `name: ci`; turning `typescript` off removes exactly the `typecheck` job; turning `listing` off removes `marketplace`; `commits` off removes `pr-policy.yml`; `releases` off removes `release.yml`; `dependabot` off removes `deps.yml` and `dependabot.yml`. The unit test compares each rendered file against its all-on form with the job's block removed.
- **Required checks follow the gates:** `frappe-nix repo apply --dry-run` against a mocked API renders `integration.json` with exactly the contexts of the app's enabled gates (`ci / lint` and `ci / test` for a fixture with only those on; all five with everything on), `required_approving_review_count` from `repo-policy.required-approvals`, and no `release-branch.json` under `main+tags`; for an app with `repo-policy.enable = false` it makes no API call and exits 0 with a notice.
- **docs-site compatibility:** on the rendered fixture with `docs-site/` and the `docs-compat` fixture profile, whose `docs-site` values are exactly the Avunu profile's (`action-patterns = ["Avunu/docusystem*"]`, `cooldown-exclude-actions = ["Avunu/docusystem"]`, `package-names = ["@avunu/docusystem"]`; the docs tool's own identifiers, not org values), `docusystem doctor` reports no dependabot warning and `docusystem upgrade --dry-run` plans no change to `.github/dependabot.yml`. A variant whose cooldown exclude is the glob `Avunu/docusystem*` makes `doctor` warn, which proves the test sees the difference.
- **Python tests** (`py/frappe_nix_tools/tests`, run as a nix check with pytest):
  - **policy:** under `frappe-major`, rejects `feat!: x`, a `BREAKING CHANGE:` footer, and `Release-As: 17.0.0` when the major is 16; accepts `chore(deps): bump …` and `chore(develop): release 16.1.0`; under `semver` accepts `feat!: x`; with `main+tags`, a PR based on `develop` fails check 1 naming `main`; **[1.2]** a PR based on another open PR's head passes check 1 with `allow-stacked = true` and fails with false; a PR based on `version-16` passes with `pr-allowed-bases = ["version-*"]`;
  - **ratchet:** R1–R7 positive and negative cases, inactive when the base lacks `[tool.frappe-nix]`, and each rule skipped when its module is off;
  - **minibench:** the layout guard;
  - **ruleset and settings JSON:** validated against a vendored schema of GitHub's ruleset API;
  - **apply:** `--dry-run` produces no PUTs for an already-matching mocked API; `--phase full` on a fleet entry with `private: true` exits 1; `--all` without `--fleet` exits 2; **[1.2]** on a private repo without a fleet entry, a mocked 201 from the rulesets API applies them and a mocked 403 fails that repo with the plan message; a not-yet-opted-in fleet entry (no `[tool.frappe-nix]` on its default branch `main`) with the fleet's `profile` set gets the settings, labels and `integration-provisional.json` for `develop` with `--phase provisional --rename-default-branch` (the mocked rename call is made first), and `--phase full` on it exits 2; `--policy-dir` reads a P-style directory with a `refs/heads/v*` release ruleset;
  - **auto-merge decisions:** recorded fetch-metadata v3 outputs for each ecosystem row (`github_actions` with the `docs-actions`, `pilot` and `actions` groups, `npm_and_yarn`, `uv`, `pre_commit`, `nix`, `submodules`) give the table's verdicts, and a `nix` PR never gets `--auto` from the auto-merge job when relock has a patch; **[1.2]** with a mocked branch-rules response that lacks a `required_status_checks` rule, or a mocked `gh pr merge --auto` failure "auto-merge is not allowed", the verdict auto becomes the `needs-review` label and a notice, with exit 0; the label is created first when missing;
  - **[1.2] release guard under semver:** the fast-forward step in `--dry-run` with `branching = "develop+version"`, `version-scheme = "semver"`, `version-16` and release-please's `tag_name = "v1.3.0"` passes the guard (and fails it when the ref read back differs from `v1.3.0`); under `frappe-major` a `tag_name` of `v1.3.0` fails it; audit rows A2 and A11 are green on a mocked repo with only `v1.*` tags under `semver`;
  - **[1.2] gate commands:** `frappe-nix gate lint`, `gate typecheck` and `gate marketplace` run on the fixture outside GitHub Actions (no `GITHUB_*` variables) with the same results as the workflows, and each step of a module that is off is reported `skipped`;
  - **[1.2] doctor:** against mocked API responses, `frappe-nix repo doctor` reports `missing` for disabled auto-merge with `dependabot.auto-merge` on and for disallowed Actions PR creation with `releases` on, `unknown` for settings needing admin, and exit 0 when everything is `ok`;
  - **relock patch:** a fixture where sync writes a new untracked `nix/node-locks/x/yarn.lock` and stages a change produces a patch that `git apply --index` reproduces exactly; a patch touching `.github/workflows/` is refused.
  - **nightly script:** with a stubbed `frappe-test` that exits 1 and a suite that exits 0, `nightly.sh` exits non-zero; with all stubs passing it exits 0;
  - **relock-plan:** a PR moving the `standards-profile` lock node is reported `rollout-input-moved`, like one moving `frappe-nix`.
- **[1.2] schedules:** with `ci.schedules = { ci = "", release = "", deps-sweep = "", deps-relock = "", nightly = "" }`, `ci.yml`, `release.yml` and `deps.yml` render without a `schedule:` key, `nightly.yml` keeps only `workflow_dispatch`, `deps.yml` passes `relock-cron: ""`, and actionlint and zizmor still pass.
- **`selftest-ci.yml`:**
  - calls `./.github/workflows/app-lint.yml`, `app-typecheck.yml` and `app-marketplace.yml` with `app-root: tests/fixtures/standards-app` and `frappe-nix-override: .` (which allows skew, §3.7) from caller jobs with `name: ci`, and `app-test.yml` only on `schedule` (weekly) and `workflow_dispatch`; `lint`, `typecheck` and `marketplace` succeed on every PR touching `.github/`, `py/frappe_nix_tools/` or `templates/`;
  - **[1.2] context names:** a step queries the run's check runs and asserts the names `ci / lint`, `ci / typecheck` and `ci / marketplace` (S39); if it fails, N4 ships the §4.2 fallback instead;
  - on a dispatch, every gate job's dispatch guard succeeds (it has `pull-requests: read`), and `app-pr-policy.yml` passes with no PR found;
  - in the minibench job, a planted `owner: someone@example.com` in a fixture `custom/*.json` makes `validate_customizations` fail.
- **`frappe-nix repo audit --repo <a public repo synced with the fixture profile> --format json`** runs end to end with `GITHUB_TOKEN`, where rows needing admin are `unknown`, and produces all 17 rows; rows of modules the app has off are `n/a`.
- **Reusable `fleet-audit.yml`:** actionlint and zizmor pass on it, and it has no `schedule` trigger (only `workflow_call`).
- **Spike (recorded, not gating):** on a scratch repo, whether `GITHUB_TOKEN` can land a workflow-file change through the Git Data API (§4.5). The result goes into `docs/app-standards/github.md`.

**N5: product tools**
- **`frappe-listing`:**
  - `check --no-getapp` passes on the fixture with the `example-org` profile;
  - **org values:** L2 expects `app_publisher == "Example Org"` from that profile; with `org.publisher` empty it only requires a non-empty `app_publisher`; L12 is skipped with empty URL templates; `registry --dry-run` uses `<listing.registry-fork>` and the branch `<fork owner>/<app>`, and exits 2 when the fork is empty;
  - negative fixtures each fail with their rule id: the tagline is 85 characters (L1); the `-dev` range (L3); `frappe` in `required_apps` (L3); `__version__` with a trailing comment (L4); a print in `__init__` (L5); an override with no allow-list entry (L6); a stale baseline entry (L7); three identical findings against a baseline `count` of 2 (L7, new finding) and one against a `count` of 2 (L7, stale count).
- **`selftest-product.yml`:**
  - `check` with L9 passes on the fixture with the pinned pilot, on `ubuntu-latest` with only the `marketplace` job's apt packages added, and a cold run takes under 25 minutes;
  - `registry --dry-run --onboard` produces an `apps/<app>.json` entry byte-identical to running the pinned `tools/add_release.py` directly.
- **`frappe-icon`:**
  - check passes on the good fixture pair rendered with `org.brand.tile-color = "#336699"`, and fails one by one on the bad fixtures: `<text>`, a non-square viewBox, a stroke, outside the safe area, a stale tile, and a tile whose fill is not the profile colour; with the colour empty, `icon check` exits 2 naming `org.brand.tile-color`;
  - `build` output hashes are stable across two runs.
- **`frappe-demo`:** run twice on one site, a record count per doctype is unchanged the second time; with erpnext installed, the company is `demo.company-name`.
- **`frappe-shots`:**
  - two runs from two fresh sites in the same job are pixel-identical (`maxDiffRatio 0`);
  - `--check` against the committed fixture shots exits 0;
  - a 1-pixel CSS change in a variant exits 1 and writes a diff PNG.
- **`readme --check`** passes on the fixture, and removing a marker exits 2. **[1.2]** With `org.repo-url = "https://git.example.org/{repo}"` the installation line uses that host; under `main+tags` it names `v<__version__>`, and after `npx release-please release-pr --dry-run` bumps `__version__` and `README.md` together, `readme --check` still passes. The rendered `license` block names `org.copyright-holder` and `org.license`, and the `development` block links `org.dev-docs-url` (the built-in default when the profile leaves it unset).

**N6: frappe-nix self-release**
- `npx release-please release-pr --dry-run --repo-url Avunu/frappe-nix --target-branch main` proposes `1.0.0` from `Release-As`, and bumps `version.txt` and both frappe-nix-tools version locations.
- Every `uses:` in frappe-nix's own workflows is SHA-pinned (zizmor).
- `.github/workflows/pr-policy.yml` has one non-reusable job named `pr-policy` (`committed` plus the no-`!` rule, `--major-free`) that installs frappe-nix-tools from the checkout, fails a `feat!:` title in a test PR, and re-runs on a retitle without re-running `check.yml`.
- A `workflow_dispatch` of `check.yml` without inputs (what the release flow does) skips `vm-tests`; with `vm-tests: true` it runs it.
- The `release-1` fast-forward step is exercised in `--dry-run` mode, printing the REST calls.
- `repo-policy/self/rulesets/*.json` pass the N4 schema test.

---

## 8. Profiles

A profile decides which modules an opted-in app gets and with which parameters, and supplies the organisation's values (S34, S37). This section is the contract for profile files, for how an app selects one, and for how the layers merge. Every module, parameter and org value named in §2–§5 is defined here.

### 8.1 The profile file

A profile is one TOML file. A built-in profile is `frappe_nix_tools/data/profiles/<name>.toml`. An org profile is `profile.toml` at the root of its own repository, optionally with `templates/` beside it. The schema is `frappe_nix_tools/data/schema/profile.schema.json` (N3a; N3 extends it). Unknown keys are exit 2.

```toml
schema = 1                                  # required
name = "example-org"                        # required; ^[a-z0-9][a-z0-9-]*$
description = "Example Org's app standards" # optional
extends = "recommended@1.0"                 # org profiles only, required there: "minimal", "recommended" or a
                                            # frozen snapshot "recommended@<minor>" (S42; recommended for org
                                            # profiles). Built-in profiles have no `extends`. Chains are not allowed.
requires-frappe-nix = ">=1.2,<2"            # optional; PEP 440 specifier against frappe_nix_tools.__version__ (§6.4)

[org]                                       # organisation values (S37). Every string defaults to "" (unset).
publisher = ""                              # hooks.app_publisher (L2), package.json author (§2.8)
email = ""                                  # hooks.app_email (L2), README support block (§2.19)
copyright-holder = ""                       # README licence line; "" means: use publisher
license = ""                                # SPDX id: package.json license, hooks.app_license, README licence line
github-owner = ""                           # default owner for `repo` when origin is unset (§2.2)
website-url = ""                            # template with {app}, {app_hyphen}: listing website and L12 (§2.21)
docs-url = ""                               # template with {app}, {app_hyphen}: listing documentation and L12
dev-docs-url = "https://github.com/Avunu/frappe-nix/tree/main/docs/app-standards"   # README development link (S26)
support-url = ""                            # README support link; "" means: https://github.com/<repo>/issues
repo-url = "https://github.com/{repo}"      # template with {repo}: the clone URL in README installation (§2.19)

[org.brand]
tile-color = ""                             # "#RRGGBB"; the icon tile fill (§5.3); required by `icons`
glyph-color = "#FFFFFF"                     # the icon tile glyph
palette = {}                                # name → "#RRGGBB"; passed to templates as org.brand.palette, for overrides

[[org.readme.badges]]                       # optional; extra README header badges, in order (§2.19)
label = "Docs"
image = "https://img.shields.io/badge/docs-online-blue"
link = "https://docs.example.org/"

[[org.readme.links]]                        # optional; extra links in the README support block
label = "Community forum"
url = "https://forum.example.org/"

[known-apps."example/shared_lib"]           # optional; sibling templates added to or overriding known-apps.json
repo = "example/shared_lib"
branch = "version-{n}"
range = ">={n}.0.0,<{n1}.0.0"
desk_global = "shared_lib"

[<module>]                                  # one table per module (§8.2): `enable` plus its parameters
enable = true

[[retire]]                                  # optional; retire rules added to the built-in ones (§2.4.1)
paths = [".github/workflows/check.yml"]     # globs over tracked files
contains = []                               # optional; regexes, any of which the file's text must match
module = "ci"                               # applies only while this module is on

[[replace-apps]]                            # optional; app pairs frappe-rename-app must replace, not rename (§5.10)
from = "old_app"
to = "new_app"

[[extra-files]]                             # optional; files the org adds to every app that uses the profile
path = "SECURITY.md"                        # must not be a path any manifest fragment manages (exit 2)
template = "SECURITY.md.j2"                 # under the profile's templates/
strategy = "seed"                           # "whole" (managed, with header) or "seed" (written once)
module = "hygiene"                          # live only while this module is on
when = "True"                               # optional; a Python expression over the context (§2.4)
header = "none"                             # for whole: the §3.6 header kind
```

**Template overrides.** A file `templates/<template path>` in the org profile's directory (its repository, or the in-repo profile directory, §8.3) replaces the built-in template with the same path, for manifest entries marked `overridable: true` only:

- the README block templates `readme/<block>.md.j2` (§2.19);
- the listing seed `marketplace/listing.toml.j2` (§2.21);
- the repo policy JSON `repo-policy/repo-settings.json` and `repo-policy/rulesets/*.json` (§4.8, §4.9), read by `frappe-nix repo apply`.

An override gets the same context (§2.3) and the same `StrictUndefined` rules. Everything else (tool configs, merged keys, caller workflows) is a contract with a reusable workflow or a check, so it changes through parameters, never through an override; an override of any other path is exit 2. A managed file rendered from an override keeps frappe-nix's header.

**App-only and profile-only keys.** The app keys of §2.1 (top-level `[tool.frappe-nix]` keys and the `[[…]]` lists) and the app-only module parameters (`typescript.browser`, `browser-include`, `web-include`, `paths`, `exclude`, `spa`; `tests.setup`; `releases.bootstrap-sha`; `js.oxlint.ignore`, `globals`, `overrides`; `js.oxfmt.ignore`; `stylelint.globs`) are refused in a profile (exit 2). `schema`, `name`, `description`, `extends`, `requires-frappe-nix`, `retire`, `replace-apps` and `extra-files` are refused in `[tool.frappe-nix]`; a single app that needs them uses an in-repo profile (§8.3). `[known-apps]` is allowed in both: the app's `[tool.frappe-nix.known-apps]` is the last layer, so an app without an org profile can describe a sibling that doesn't follow the Frappe convention.

### 8.2 Modules

Every module has `enable` (boolean). A module is effectively on when its own `enable` is true and every module in its "Needs" column is on; a module whose needs aren't met while its own `enable` is true is exit 2 naming both, never switched off silently (S36). Modules marked "CI only" act only inside CI jobs; without `ci` they have no effect, which is not an error. For a module with a `tool` parameter, `tool = "none"` is the same as `enable = false`.

| Module | What it controls | Parameters (type, `recommended` value) | Needs | `minimal` | `recommended` | Avunu |
|---|---|---|---|---|---|---|
| `dev-shell` | `flake.nix`, `.envrc`, the `.gitignore` block, node-lock seeds (§2.5, §2.6) | app-only: `systems` (list, the four systems); `extra-substituters`, `extra-trusted-public-keys` (lists, []); `frappe-nix-url` (string, "") | — | on (required: `enable = false` is exit 2) | on | on |
| `editorconfig` | `.editorconfig` (§2.7) | `indent` ("tab"\|"space", "tab"); `line-length` (int, 110) | — | off | on | on |
| `metadata` | `pyproject.toml` build system, `requires-python`, frappe-dependencies; `package.json` base keys; C2–C4, C6; the override allow-list | `build-backend` ("flit"\|"any", "flit"); `package-type` ("module"\|"", "module"); `package-manager` (string\|"", "yarn@1.22.22"); `node-engine` (string\|"", ">=24"). `"any"`/`""` leaves that key app-owned (§2.8, §2.12) | — | off | on | on |
| `python-lint` | ruff hooks, `[tool.ruff*]`, `lint:py` (§2.12, §2.14) | `tool` ("ruff"\|"none", "ruff"); `select` (list, carbon's 12 codes, S27); `ignore` (list, `["E501","W191"]`); `line-length` (int, 110); `indent-style` ("tab"\|"space", "tab"); `quote-style` ("double"\|"single", "double"); `typing-modules` (list, `["frappe.types.DF"]`) | — | off | on | on |
| `ssort` | the ssort hook (S2) | none | — | off | off | on |
| `python-types` | ty: `[tool.ty*]`, `typecheck:py`, frappe-test stage 7, R2 | `tool` ("ty"\|"none", "ty"); `error-on-warning` (bool, false) | — | off | on | on (`error-on-warning = true`) |
| `js` | oxlint and oxfmt: configs, hooks, `format`/`lint` scripts, devDependencies (§2.10) | `tool` ("oxc"\|"none", "oxc"); `locked-rules` (bool, true); `format-width` (int, 110); `format-tabs` (bool, true); `oxlint.categories` (table, `{correctness="error", suspicious="error", perf="warn", pedantic="off"}`) | — | off | on | on |
| `typescript` | `tsconfig*.json`, `typecheck` script and job, C7, C9 (§2.9) | `preset` ("frappe-types"\|"inline", "frappe-types"); `strict-extras` (bool, true); `check-js` (bool, false); `audit-consumer` (bool, false) | — | off | on | on (`check-js = true`) |
| `stylelint` | `.stylelintrc.json`, its hook and `lint:css` script, when SCSS exists (§2.11) | none | — | off | on | on |
| `tests` | frappe-test gate (`ci / test`), coverage tables, testmap, composition, JS unit tests, R1/R4/R7 (§5.1) | `coverage.enable` (bool, true); `coverage.target` (number, 80); `coverage.raise-margin` (number, 2.0; 0 turns the upward ratchet off); `coverage.initial-floor` (number, 0); `testmap` (bool, true); `composition` (bool, true); `js-unit` (bool, true); `js-coverage-min` (int, 50) | — | off | on | on |
| `semgrep` | the semgrep scans in `lint` (§4.2); CI only | `frappe-rules` (bool, true); `test-correctness` (bool, true) | — | off | on | on |
| `workflow-lint` | zizmor and actionlint hooks, `.github/zizmor.yml` | none | — | off | on | on |
| `shell-lint` | the shellcheck hook | none | — | off | on | on |
| `nix-lint` | frappe-test stage 8; nixfmt, statix, deadnix in the shell | none | — | off | on | on |
| `hygiene` | pre-commit-hooks basics, `.git-blame-ignore-revs` (§2.20) | `max-file-kb` (int, 1024) | — | off | on | on |
| `commits` | `committed.toml`, commit-msg hooks, `pr-policy` (§2.15, §4.3) | `tool` ("committed", "committed"); `allowed-types` (list, frappe's commitlint types); `subject-length` (int, 100); `allow-stacked` (bool, true); `pr-allowed-bases` (list of globs, []) (§5.9) | — | off | on | on (`allow-stacked = false`) |
| `releases` | release-please config and manifest, the version block, `release.yml`, C1, C5 (§2.13, §2.16, §4.4) | `tool` ("release-please"\|"none", "release-please"); `branching` ("develop+version"\|"main+tags", "develop+version"); `version-scheme` ("semver"\|"frappe-major", "semver"); `tag-prefix` ("v"\|"", "v") | `commits` | off | on | on (`version-scheme = "frappe-major"`) |
| `dependabot` | `.github/dependabot.yml`, `deps.yml` (§2.17, §4.5) | `cadence` ("daily"\|"weekly"\|"monthly", "weekly"); `cadence-overrides` (table ecosystem → cadence, `{}`); `cooldown-days` (int, 3); `auto-merge` (bool, true; acts only where required checks protect the integration branch, §4.5); `ecosystems` (table of bools: `github-actions`, `npm`, `uv`, `pre-commit`, `nix`, `gitsubmodule`; all true) | — | off | on | on (`cadence = "daily"`, overrides below) |
| `ci` | the caller workflows (§4.7) and so every gate job | `provider` ("github", "github"; other values reserved); `nightly` (bool, true); `test-timeout-minutes` (int, 75); `schedules` (table of cron strings, `""` = none: `ci` "0 6 * * *", `release` "30 5 * * *", `deps-sweep` "0 9 * * *", `deps-relock` "0 8 * * 1", `nightly` "0 7 * * *") | — | off | on | on |
| `repo-policy` | rulesets and repo settings applied by `frappe-nix repo apply`; audit A1, A3 (§4.8, §4.9) | `required-approvals` (int, 1); `require-code-owner-review` (bool, false); `required-checks` ("gates" or a list of gate names, "gates"); `release-bypass` ("github-actions" or "user:<id>", "github-actions"); `secret-scanning` (bool, true); `merge-methods` (list of "squash"\|"merge"\|"rebase", ["squash"]); `require-thread-resolution` (bool, true); `delete-branch-on-merge` (bool, true); `manage-default-branch` (bool, true); `manage-features` (bool, true) | — | off | off | on (`required-approvals = 0`) |
| `vite-register` | `scripts/vite-register.mjs` and C8, when Vite exists (§5.11) | none | — | off | on | on |
| `pilot-assets` | `assets.yml` and its dispatch | none | `ci`, `releases` | off | off | off (apps turn it on) |
| `listing` | registry readiness: marketplace files, `ci / marketplace`, L1–L12, R3; and registry publishing when `publish` is on (§2.21, §5.2). An app that isn't listed keeps it on with `publish = false` and no `listing.toml` | `publish` (bool, false); `registry-upstream` (string, "frappe/marketplace"); `registry-fork` (string, ""; required when `publish`); `getapp-check` (bool, true) | `metadata` | off | off | on (`publish = true`, fork "Avunu/marketplace") |
| `readme` | README generated blocks (§2.19) | `install-ref` (string, "" = automatic) | `listing` | off | off | on |
| `icons` | icon checks, desktop-icon fixture (§5.3) | none (`org.brand`) | — | off | off | on |
| `screenshots` | `marketplace/shots.d.ts`, frappe-shots, nightly `shots` (§5.5) | `timezone` (string, "America/New_York"); `locale` (string, "en-US") | `demo` | off | off | on |
| `demo` | frappe-demo (§5.4) | `company-name` ("Demo Company"); `company-abbr` ("DC"); `country` ("United States"); `currency` ("USD"); `timezone` ("America/New_York"); `language` ("English"); `date` ("2026-01-15"); `seed` (1); `erpnext-demo` (false) | — | off | off | on (company "Avunu Demo", "AD") |
| `test-utils` | the agritheory/test_utils hooks (S19), minibench customizations, nightly duplication | `hooks` (list of hook ids, all eight of §2.14); `track-overrides` (bool, false) | — | off | off | on |
| `docs-site` | dependabot entries and groups for a docs site (§2.17), and the `docs-site` node-target exclusion (§5.11) | `action-patterns` (list, []); `cooldown-exclude-actions` (list, []); `package-names` (list, []) | `dependabot` | off | off | on (docusystem's patterns) |

Notes:

- **GitHub-only modules (S43).** `ci`, `releases` (release-please through the workflows), `dependabot`, `repo-policy`, `pilot-assets`, `docs-site` (its dependabot entries) and `listing.publish` (the registry is a GitHub repository); the `commits` module's PR check needs `ci`. Every other module, the gate commands (`frappe-nix gate …`, `frappe-test --ci`) and the commit-msg hooks work on any host. On a repository whose `repo` isn't on GitHub, the GitHub-only modules resolve to off (and modules that need only them follow), with one notice listing them, so `recommended` works unchanged; an app whose own `[tool.frappe-nix]` sets `enable = true` on one of them gets exit 2 naming it, as §3.3 lists.
- **Shared infrastructure** is not a module. `.pre-commit-config.yaml` is live while any hook module is on, `tools/` while any module with a tool there is on, `package.json` while any module with a key group is on (§2.4).
- **Gates** are derived, not configured: §4's table says which gate exists for which modules, and `gates` (§2.3) lists them. Turning a gate off means turning off its module.
- `recommended` is chosen to be useful for any organisation with no further edits: Frappe's own conventions (tabs, 110 columns, frappe's commit types, `version-<major>` branches) where Frappe has one, the strict tools on, and everything that needs an org value, admin rights or extra infrastructure (repo policy, listing, readme, icons, screenshots, demo, test_utils, docs site, pilot assets) off. Choices that are taste rather than safety (`ssort`, strict `check-js`, ty `error-on-warning`, `frappe-major` versioning, daily cadence, zero required approvals) are off or neutral in `recommended` and turned on by the Avunu profile.

### 8.3 Selecting and pinning a profile

`[tool.frappe-nix] profile` takes one of:

| Value | Meaning | Where it is read from |
|---|---|---|
| absent | `"minimal"` (S35) | the package |
| `"minimal"`, `"recommended"` | a built-in profile; `recommended` is the newest snapshot | the package, so it moves with frappe-nix |
| `"recommended@<minor>"` | a frozen `recommended` snapshot (S42) | the package; identical in every later `v1.x` |
| `"github:<owner>/<repo>[/<ref>]"`, `"gitlab:<group>/<repo>[/<ref>]"`, `"git+https://<host>/<path>?ref=<ref>"` | an org profile | the flake input `standards-profile` |
| `"./<dir>"` | an in-repo profile: `<dir>/profile.toml` (with `extends`) and optional `<dir>/templates/`, tracked in the app | the app's own checkout, so it is versioned with the app and needs no input, no lock node and no rollout |

**Pinning.** For an org profile, sync's phase A (§3.3 steps 1–3) renders the input into `flake.nix` (§2.5):

```nix
standards-profile = {
  url = "github:Avunu/frappe-standards-profile/v1";
  flake = false;
};
```

and locks it, so `flake.lock` records the exact commit and its `narHash`. Every later step reads `profile.toml` (and `templates/`) from that locked tree: from the store when Nix is available, and through `frappe-nix pin-path standards-profile` in no-Nix CI, which fetches `https://codeload.github.com/<owner>/<repo>/tar.gz/<rev>` and verifies the `narHash` like every other pin (§5.2). `pin-path` accepts `standards-profile` only as a root input of the app's own lock, and fetches `github`, `gitlab` and `git` lock nodes (§5.2), with `FRAPPE_NIX_FETCH_TOKEN` for a private profile repository. Changing `profile` (to another org profile, a ref, or a built-in) re-points or removes the input in phase A, and the lock follows (Appendix I, N3: an input whose URL changed is stale).

**Updating.** Dependabot never moves `standards-profile` (S38, §2.17). It moves when someone commits

```
nix flake update standards-profile && nix run --no-pure-eval .#frappe-init -- --sync
```

with a token that may change workflow files (S5), or runs `frappe-nix repo rollout --profile-to <ref>|latest` over a fleet (§5.8). The nightly `drift` job and audit row A10 report a profile that is behind its ref (§4.6, §5.7). Moving to a new profile major means editing the ref in `profile` (`…/v1` → `…/v2`), like a frappe-nix major.

**Authoring.** A profile author works against a local checkout with `frappe-init --sync --profile-path ../my-profile` on a test app (never rendered, never in CI; §3.3) and validates the file with `frappe-nix profile validate` (§5.13), which the profile repository's own CI should run. A solo developer or a single-app agency doesn't need a repository at all: the in-repo form `profile = "./.standards-profile"` gives the full profile (template overrides, `[[extra-files]]`, `[[retire]]`, `[known-apps]`, repo-policy overrides) and works in CI, because it is part of the checkout.

### 8.4 Resolution and merge order

`common/config.py` (N3a) resolves an app's configuration the same way for every consumer: sync, `--check`, `frappe-nix config`, frappe-test, the CI `cfg` step, `repo apply` and `repo audit`.

1. Read `[tool.frappe-nix]`. No table: not opted in (S35); the caller decides what that means (sync exits 2, the dev shell adds nothing).
2. Load the layers, lowest first:
   1. the built-in profile: `profile` itself when it is a built-in name or snapshot, else the org profile's `extends` (`recommended` resolving to the newest snapshot, S42);
   2. the org profile's `profile.toml`, when there is one (from the locked input, or from `./<dir>` for an in-repo profile);
   3. the app's `[tool.frappe-nix]`.
3. Validate each layer against its schema on its own: profile schema for 1 and 2 (with the profile-only keys), app schema for 3 (with the app-only keys) (§8.1).
4. **Merge** 1 ← 2 ← 3. Tables merge key by key, recursively. Scalars and arrays are replaced whole by the higher layer (an app that sets `python-lint.ignore` gives the complete list). `[known-apps]` merges by key across all three layers. `[[extra-files]]`, `[[retire]]` and `[[replace-apps]]` come only from the org profile. `org.copyright-holder` falls back to `org.publisher` after the merge.
5. Apply defaults from the schema for anything still unset, then apply the module rules of §8.2 (needs, `tool = "none"`), computing `modules`.
6. Check that every org value a live entry `uses` is non-empty (S37), and `requires-frappe-nix` (exit 3 when it excludes the running version).

The result is the `cfg` of §2.3. `frappe-nix profile show --explain` prints every resolved value with the layer that set it (`builtin:recommended`, `org:github:Avunu/frappe-standards-profile@<rev>`, `app`, `default`), which is how a developer answers "why is this gate on".

### 8.5 The built-in profiles

Both ship in `frappe_nix_tools/data/profiles/`, as `minimal.toml` and `recommended@1.0.toml` (the first `recommended` snapshot, S42). They are given here in full; N3a creates them byte-for-byte (apart from the comment header) and the N3a nix check compares them. A later MINOR that changes `recommended` adds a new snapshot file and never edits this one.

`minimal.toml`, the dev shell and nothing else:

```toml
schema = 1
name = "minimal"
description = "The frappe-nix dev shell only: flake.nix, .envrc and the .gitignore block."

[org]
dev-docs-url = "https://github.com/Avunu/frappe-nix/tree/main/docs/app-standards"

[dev-shell]
enable = true

[editorconfig]
enable = false
[metadata]
enable = false
[python-lint]
enable = false
[ssort]
enable = false
[python-types]
enable = false
[js]
enable = false
[typescript]
enable = false
[stylelint]
enable = false
[tests]
enable = false
[semgrep]
enable = false
[workflow-lint]
enable = false
[shell-lint]
enable = false
[nix-lint]
enable = false
[hygiene]
enable = false
[commits]
enable = false
[releases]
enable = false
[dependabot]
enable = false
[ci]
enable = false
[repo-policy]
enable = false
[vite-register]
enable = false
[pilot-assets]
enable = false
[listing]
enable = false
[readme]
enable = false
[icons]
enable = false
[screenshots]
enable = false
[demo]
enable = false
[test-utils]
enable = false
[docs-site]
enable = false
```

`minimal` sets no parameters: a module turned on by an app on top of `minimal` gets the schema defaults, which are the `recommended` values.

`recommended@1.0.toml`, vendor-neutral defaults:

```toml
schema = 1
name = "recommended"
description = "Vendor-neutral app standards: lint, format, types, tests, conventional commits, releases, dependabot and CI. Nothing that needs an organisation's values or repo admin."

[org]
publisher = ""
email = ""
copyright-holder = ""
license = ""
github-owner = ""
website-url = ""
docs-url = ""
dev-docs-url = "https://github.com/Avunu/frappe-nix/tree/main/docs/app-standards"
support-url = ""
repo-url = "https://github.com/{repo}"

[org.brand]
tile-color = ""
glyph-color = "#FFFFFF"
palette = {}

[dev-shell]
enable = true

[editorconfig]
enable = true
indent = "tab"
line-length = 110

[metadata]
enable = true
build-backend = "flit"
package-type = "module"
package-manager = "yarn@1.22.22"
node-engine = ">=24"

[python-lint]
enable = true
tool = "ruff"
select = ["F", "E", "W", "I", "UP", "B", "RUF", "SIM", "C4", "PIE", "PERF", "T20"]
ignore = ["E501", "W191"]
line-length = 110
indent-style = "tab"
quote-style = "double"
typing-modules = ["frappe.types.DF"]

[ssort]
enable = false

[python-types]
enable = true
tool = "ty"
error-on-warning = false

[js]
enable = true
tool = "oxc"
locked-rules = true
format-width = 110
format-tabs = true

[js.oxlint.categories]
correctness = "error"
suspicious = "error"
perf = "warn"
pedantic = "off"

[typescript]
enable = true
preset = "frappe-types"
strict-extras = true
check-js = false
audit-consumer = false

[stylelint]
enable = true

[tests]
enable = true
testmap = true
composition = true
js-unit = true
js-coverage-min = 50

[tests.coverage]
enable = true
target = 80
raise-margin = 2.0
initial-floor = 0

[semgrep]
enable = true
frappe-rules = true
test-correctness = true

[workflow-lint]
enable = true

[shell-lint]
enable = true

[nix-lint]
enable = true

[hygiene]
enable = true
max-file-kb = 1024

[commits]
enable = true
tool = "committed"
allowed-types = ["build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test", "deprecate"]
subject-length = 100
allow-stacked = true
pr-allowed-bases = []

[releases]
enable = true
tool = "release-please"
branching = "develop+version"
version-scheme = "semver"
tag-prefix = "v"

[dependabot]
enable = true
cadence = "weekly"
cadence-overrides = {}
cooldown-days = 3
auto-merge = true

[dependabot.ecosystems]
github-actions = true
npm = true
uv = true
pre-commit = true
nix = true
gitsubmodule = true

[ci]
enable = true
provider = "github"
nightly = true
test-timeout-minutes = 75

[ci.schedules]
ci = "0 6 * * *"
release = "30 5 * * *"
deps-sweep = "0 9 * * *"
deps-relock = "0 8 * * 1"
nightly = "0 7 * * *"

[repo-policy]
enable = false
required-approvals = 1
require-code-owner-review = false
required-checks = "gates"
release-bypass = "github-actions"
secret-scanning = true
merge-methods = ["squash"]
require-thread-resolution = true
delete-branch-on-merge = true
manage-default-branch = true
manage-features = true

[vite-register]
enable = true

[pilot-assets]
enable = false

[listing]
enable = false
publish = false
registry-upstream = "frappe/marketplace"
registry-fork = ""
getapp-check = true

[readme]
enable = false
install-ref = ""

[icons]
enable = false

[screenshots]
enable = false
timezone = "America/New_York"
locale = "en-US"

[demo]
enable = false
company-name = "Demo Company"
company-abbr = "DC"
country = "United States"
currency = "USD"
timezone = "America/New_York"
language = "English"
date = "2026-01-15"
seed = 1
erpnext-demo = false

[test-utils]
enable = false
hooks = ["validate_frappe_project", "validate_patches", "static_analysis", "validate_doctype_python_types",
         "validate_copyright", "validate_customizations", "clean_customized_doctypes", "check_code_duplication"]
track-overrides = false

[docs-site]
enable = false
action-patterns = []
cooldown-exclude-actions = []
package-names = []
```

**What `recommended` renders** for a plain app (no TypeScript, no SCSS, no Vite, no siblings): `flake.nix`, `.envrc`, the `.gitignore` block, `.editorconfig`, `.pre-commit-config.yaml` (hygiene, ruff, oxc, zizmor, actionlint, shellcheck, compat, committed hooks), `committed.toml`, `tools/pyproject.toml` and `tools/uv.lock`, the managed `pyproject.toml` groups, the `<app>/__init__.py` version block, `package.json` and `yarn.lock`, `.oxlintrc.json`, `.oxfmtrc.jsonc`, `release-please-config.json` and its manifest, `.git-blame-ignore-revs`, `.github/workflows/{ci,pr-policy,release,deps,nightly}.yml`, `.github/dependabot.yml` and `.github/zizmor.yml`. Its `ci.yml` has the `lint` and `test` jobs; `typecheck` appears with the first TypeScript file. With `repo-policy` turned on, it requires `ci / pr-policy`, `ci / lint` and `ci / test`. No file names an organisation. Without `repo-policy`, `dependabot.auto-merge` acts only once the integration branch requires those checks (§4.5), and `frappe-nix repo doctor` lists the two repository settings to turn on by hand (Actions may create pull requests; allow auto-merge).

### 8.6 Example org profile: Avunu (worked example)

This profile lives in `Avunu/frappe-standards-profile/profile.toml` (item P), **not** in frappe-nix. It is the reference for what an org profile looks like, and it is how the Avunu fleet gets exactly the behaviour 1.1 specified: an app synced with it renders what 1.1 describes, apart from the S33 renames, the S39 caller split and the review changes of Appendix R2. 1.1 fixed these values inside frappe-nix; here the profile owns them, so it **extends the frozen snapshot `recommended@1.0` and sets explicitly every value the fleet relies on**, including those that equal today's `recommended`. A later frappe-nix MINOR that changes `recommended`, or a flipped Q9 default, therefore can't move an Avunu app. P's CI proves it: it compares the resolved configuration of a copy of the frappe-nix fixture app with the committed `resolved.snapshot.json` (§6.4), and runs `docusystem doctor` on it.

```toml
schema = 1
name = "avunu"
description = "Avunu's app standards: every gate on, Frappe-major versioning, develop + version-<major>, Marketplace publishing."
extends = "recommended@1.0"
requires-frappe-nix = ">=1.0,<2"

[org]
publisher = "Avunu LLC"
email = "mail@avu.nu"
copyright-holder = "Avunu LLC"
license = "MIT"
github-owner = "Avunu"
website-url = "https://avunu.net/open-source/{app}/"
docs-url = "https://{app_hyphen}.avunu.net/"
dev-docs-url = "https://frappe-nix.avunu.net/docs/app-standards/"
repo-url = "https://github.com/{repo}"

[org.brand]
tile-color = "#834AFF"
glyph-color = "#FFFFFF"

[editorconfig]
enable = true
indent = "tab"
line-length = 110

[metadata]
enable = true
build-backend = "flit"
package-type = "module"
package-manager = "yarn@1.22.22"
node-engine = ">=24"

[python-lint]
enable = true
tool = "ruff"
select = ["F", "E", "W", "I", "UP", "B", "RUF", "SIM", "C4", "PIE", "PERF", "T20"]
ignore = ["E501", "W191"]
line-length = 110
indent-style = "tab"
quote-style = "double"
typing-modules = ["frappe.types.DF"]

[ssort]
enable = true

[python-types]
enable = true
tool = "ty"
error-on-warning = true

[js]
enable = true
tool = "oxc"
locked-rules = true
format-width = 110
format-tabs = true

[js.oxlint.categories]
correctness = "error"
suspicious = "error"
perf = "warn"
pedantic = "off"

[typescript]
enable = true
preset = "frappe-types"
strict-extras = true
check-js = true
audit-consumer = false

[stylelint]
enable = true

[tests]
enable = true
testmap = true
composition = true
js-unit = true
js-coverage-min = 50

[tests.coverage]
enable = true
target = 80
raise-margin = 2.0
initial-floor = 0

[semgrep]
enable = true
frappe-rules = true
test-correctness = true

[workflow-lint]
enable = true

[shell-lint]
enable = true

[nix-lint]
enable = true

[hygiene]
enable = true
max-file-kb = 1024

[commits]
enable = true
tool = "committed"
allowed-types = ["build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test", "deprecate"]
subject-length = 100
allow-stacked = false                       # plan §5: every PR targets develop, no stacked PRs
pr-allowed-bases = []

[releases]
enable = true
tool = "release-please"
branching = "develop+version"
version-scheme = "frappe-major"
tag-prefix = "v"

[dependabot]
enable = true
cadence = "daily"
cadence-overrides = { uv = "weekly", pre-commit = "weekly", nix = "weekly", gitsubmodule = "weekly" }
cooldown-days = 3
auto-merge = true

[dependabot.ecosystems]
github-actions = true
npm = true
uv = true
pre-commit = true
nix = true
gitsubmodule = true

[ci]
enable = true
nightly = true
test-timeout-minutes = 75

[ci.schedules]
ci = "0 6 * * *"
release = "30 5 * * *"
deps-sweep = "0 9 * * *"
deps-relock = "0 8 * * 1"
nightly = "0 7 * * *"

[repo-policy]
enable = true
required-approvals = 0
require-code-owner-review = false
required-checks = "gates"
release-bypass = "github-actions"
secret-scanning = true
merge-methods = ["squash"]
require-thread-resolution = true
delete-branch-on-merge = true
manage-default-branch = true
manage-features = true

[vite-register]
enable = true

[pilot-assets]
enable = false                              # apps with prebuilt assets turn it on

[listing]
enable = true                               # registry readiness for every app, listed or not
publish = true
registry-upstream = "frappe/marketplace"
registry-fork = "Avunu/marketplace"
getapp-check = true

[readme]
enable = true
install-ref = ""

[icons]
enable = true

[screenshots]
enable = true
timezone = "America/New_York"
locale = "en-US"

[demo]
enable = true
company-name = "Avunu Demo"
company-abbr = "AD"
country = "United States"
currency = "USD"
timezone = "America/New_York"
language = "English"
date = "2026-01-15"
seed = 1
erpnext-demo = false

[test-utils]
enable = true
hooks = ["validate_frappe_project", "validate_patches", "static_analysis", "validate_doctype_python_types",
         "validate_copyright", "validate_customizations", "clean_customized_doctypes", "check_code_duplication"]
track-overrides = false

[docs-site]
enable = true
action-patterns = ["Avunu/docusystem*"]     # the docs-actions group and its needs-review rule
cooldown-exclude-actions = ["Avunu/docusystem"]   # exact name, as `docusystem doctor` checks it
package-names = ["@avunu/docusystem"]

[[retire]]                                  # the fleet's old workflows whose jobs moved to ci.yml (§2.4.1)
paths = [".github/workflows/check.yml", ".github/workflows/version-branch-guard.yml"]
module = "ci"

[[replace-apps]]                            # A.2: data_steward replaces jailbreak (§5.10)
from = "jailbreak"
to = "data_steward"
```

An Avunu app then needs only:

```toml
[tool.frappe-nix]
schema = 1
profile = "github:Avunu/frappe-standards-profile/v1"
frappe-major = 16
siblings = ["erpnext"]
```

plus its app keys, and per-app switches where the fleet differs:

- **An app that isn't listed** (`list: false` in P's `fleet.json`) sets `[tool.frappe-nix.listing] publish = false` and has no `marketplace/listing.toml`. It keeps `listing` on, so, as in 1.1, it still has the `ci / marketplace` gate and its required check, L3–L7 and L9, the semgrep baseline, and R3. Without `listing.toml`, L1, L2, L8, L10, L12, the README blocks and the nightly `links` job have nothing to check, and `release.yml` publishes nothing.
- **An app with prebuilt assets** sets `[tool.frappe-nix.pilot-assets] enable = true`.

**Another organisation** copies this file, changes `[org]` and the switches, publishes it as `github:<owner>/<repo>`, and points its apps at it. A team that wants no org profile writes `profile = "recommended"` and sets a few `[tool.frappe-nix.org]` values in each app.

---

## 9. Migration of in-flight PRs #58, #59 and #60

The three open PRs implement 1.1 under the internal codename. Before any of them merges, each MUST be brought to 1.2 as listed here. Appendix N gives the full old → new table; this section says what each PR does with it, and what it restructures. The frappe[dev]/pypika relock failure on #59 is being fixed separately by the user and is not part of this migration.

**For all three PRs:**

- **Text.** PR title and body, every commit message (subject and body), every file name and every file's content are free of the codename (S33). The conventional-commit scope becomes `standards` (`feat(standards): …`). Titles:
  - N3a: `feat(standards): add the hook points the app standards build on (N3a)`;
  - N3: `feat(standards): add the managed-file engine, frappe-init --sync/--check and frappe-nix compat (N3)`;
  - N1: `feat(standards): add frappe-test, CI mode, the worktree port salt and frappe-rename-app (N1)`.
- **Why a message rewrite is not enough.** The codename is in every commit's **tree** on the three branches, not only in messages: 201 to 271 distinct codename tokens per branch (`docs/<codename>/spec.md`, the Python project path, the config table, the environment variables, …). A message-only reword keeps those trees, and GitHub keeps them browsable from each PR's commit list and its "force-pushed" links. The PR pages also keep a public record that editing can't remove: title-change events with the old title, the body's edit history, the force-push links to the old commits, and existing review comments.
- **History: fresh branches, new PRs.** Each branch is rebuilt as new commits of its 1.2 content, with no ancestor that contains the codename: start from `main` (N3a) or from the rebuilt N3a branch (N3, N1), apply the PR's final tree with the Appendix N path renames and content replacements, then the 1.2 restructuring below, and commit in a few logical commits with neutral messages. (`git filter-repo` with the Appendix N path and content replacements over the old commits is an acceptable alternative when per-commit history is worth keeping; the result must pass the naming check of §7 N3a on every commit, `git log -p`.) The rebuilt branches are pushed as **new** branches `standards/n3a-hook-points`, `standards/n3-scaffold` and `standards/n1-runtime`, and opened as **new** PRs with the titles above, stacked as before (N3 and N1 based on N3a's branch). #59 and #60 are not rebased or renamed in place.
- **The old PRs.** #58, #59 and #60 are closed with the neutral comment `Superseded by #<new>`, after their titles are edited to the neutral ones above (which hides the codename from the current page but not from the timeline), and their head branches are deleted. Their pages, timelines and the commits reachable from `refs/pull/<n>/head` stay public unless GitHub Support is asked to remove the PRs' refs and cached views (Q8). New branches use the `standards/` prefix.
- **The spec.** The committed 1.1 spec moves to `docs/app-standards/spec.md` with this 1.2 text, without Appendix N, plus the PR's own Appendix I rows with the renames applied.
- **Mechanical renames**, by Appendix N: paths, the package, the CLI, the table, markers, environment variables, nix check names, fixture names.

**#58 (N3a), additionally:**

- Move the Python project to `py/frappe_nix_tools/` (distribution `frappe-nix-tools`, import `frappe_nix_tools`, console script `frappe-nix`); the Nix seam directory to `lib/standards/`, its tool file becoming `tools/frappe-nix.nix` (git and gh on PATH); the nix tests to `tests/standards/` with the `standards-` check prefix rule; the fixture app to `tests/fixtures/standards-app/` with the app `standards_fixture`; the docs to `docs/app-standards/`, index titled "App standards and quality gates", plus a `profiles.md` stub (every old path is in Appendix N).
- Flake outputs: `packages.frappe-nix-tools`, `apps.frappe-nix`, `checks.standards-all`; `check.yml` builds `standards-all`; `modules/devenv.nix`, `ty.toml` and `dev/env.nix` follow the new paths.
- Schema: `schema/tool-frappe-nix.schema.json` for `[tool.frappe-nix]`, with `profile`, `repo` and the module tables of §2.1; its `$id` is a URL under `github.com/Avunu/frappe-nix` (not an org website). Siblings accept any `<owner>/<repo>`, not only `Avunu/…`.
- **New:** `data/profiles/minimal.toml` and `data/profiles/recommended@1.0.toml` (§8.5, S42), `schema/profile.schema.json` (with `[[retire]]`, `[[replace-apps]]`, `org.repo-url` and the review-round parameters of §8.2), `common/config.py` (§8.4) behind `frappe-nix config` (snapshots, in-repo `./<dir>` profiles, the app's `[known-apps]` layer, GitHub-only modules off on other hosts), `known-apps.json` with the generic `*/*` rule instead of `Avunu/*`, `tests/standards/vendor-neutral.nix` with its denylist over the wider S37 scope (Python code, `lib/**`, the reusable workflows), `pin-path` for `github`, `gitlab` and `git` lock nodes with `FRAPPE_NIX_FETCH_TOKEN`, and `lib/standards/shell.nix` adding `frappe-nix` and `blame.ignoreRevsFile` only for opted-in apps, decided by a line match, never `fromTOML` (S35).
- The schema's `siblings` accepts the object form, `[tool.frappe-nix]` gains `integration-branch`, `retire-keep` and `[tool.frappe-nix.dev-shell]`, and `profile` accepts snapshots, other flake hosts and `./<dir>` (§2.1).
- The naming check of §7 N3a is a pre-merge check; no committed file contains the codename, including the denylist.
- The fixture app uses fictitious values (publisher "Example Org", `example.org` URLs) in `hooks.py`, `license.txt`, `pyproject.toml`, `marketplace/listing.toml` and its logo tile, and adds `profile = "recommended"` to its `[tool.frappe-nix]`; `tests/fixtures/profiles/example-org/` is N3's.
- Environment variables in code and docs: the pin URL, debug, allow-skew, expect-rev, artifact-dir and three cache variables become `FRAPPE_NIX_PIN_URL`, `FRAPPE_NIX_DEBUG`, `FRAPPE_NIX_ALLOW_SKEW`, `FRAPPE_NIX_EXPECT_REV`, `FRAPPE_NIX_ARTIFACT_DIR`, `FRAPPE_NIX_CACHE`, `FRAPPE_NIX_CACHE_TOKEN` and `FRAPPE_NIX_CACHE_READ_TOKEN`. The CLI description and `--help` texts link `docs/app-standards`.

**#59 (N3), additionally:**

- Move the scaffold package to `frappe_nix_tools/scaffold/` and rename every template, marker and header (`frappe-nix:managed`, `frappe-nix:local-begin|end`, README `frappe-nix:begin|end`), the solution file `tsconfig.solution.json`, the hook ids `frappe-nix-compat` and `frappe-nix-commit-msg`, and `scripts/vite-register.mjs`; the sync variables become `FRAPPE_NIX_OFFLINE`, `FRAPPE_NIX_URL_OVERRIDE`, `FRAPPE_NIX_SYNC_REEXEC`, `FRAPPE_NIX_SYNC_REENTERED`, `FRAPPE_NIX_SYNC_RESULT`, `FRAPPE_NIX_SYNC_LOCK_CHANGED` and `FRAPPE_NIX_LOCKED_REV` (Appendix N); the CI temp dir is `$RUNNER_TEMP/frappe-nix`.
- **Restore the default app mode (S35):** put `templates/app/flake.nix` back and leave `templates/app/` byte-identical to `main`; `cmd_app_init` keeps `main`'s behaviour without `--standards`, and calls `frappe-nix sync --write` only with `--standards` or an existing table; `--sync`/`--check` without the table exit 2 with the hint; add `--standards` and `--profile-path` to `lib/sh/main.sh` and `app-sync.sh`. The engine's `flake.nix.j2`, `envrc.j2` and `gitignore.block` stay as the opted-in forms.
- **Module gating:** every manifest entry gets `module`, `uses` and `overridable` (§2.4, §3.1); live = module on and `when` true; retraction of whole files, blocks and merged keys when a module turns off (§3.3 step 6); retire rules carry their module (§2.4.1); `package.json` and `pyproject.toml` key groups per module (§2.8, §2.12); the pre-commit template wraps each hook in its module (§2.14); `tools/pyproject.toml` lists only enabled tools (§2.15); `committed.toml`'s comment and the commit-msg hook follow `version-scheme`.
- **Org values out of code:** `scaffold/package_json.py` stops writing `"author": "Avunu LLC"` and `"license": "MIT"` and uses `org.publisher` and `org.license` when set; test helpers use the example-org values; `lib/sh/main.sh`'s help link points at `docs/app-standards`.
- **Review-round changes (Appendix R2):** `flake.nix` and `.envrc` get the `inputs` and `envrc` local regions, the `dev-shell` parameters and the first-opt-in refusal with `--force`; phase A re-locks frappe-nix only when the locked `original` differs from the rendered URL; `metadata`'s four parameters, and the module needs of §8.2 without `metadata` for `js`, `typescript`, `stylelint`, `vite-register` and `releases`; `scripts.lint` and `scripts.check` owned by `js` only, `lint:css` for stylelint; semantic comparison of YAML/JSON/JSONC/TOML whole files while `js.tool` isn't `oxc`; retire rules by function, profile `[[retire]]` and `retire-keep`; sibling objects and the resolved-branch C3; `integration-branch`; `typescript.preset`; `releases.tag-prefix`; the `frappe-nix` hooks that skip outside the dev shell; `excludeNodeTargets` in the flake template; caller `uses:` and dependabot ignores from the locked frappe-nix owner; the changed-defaults warning for floating `recommended`.
- **Profiles:** phase A adds, re-points or removes the `standards-profile` input and reads the locked profile; template overrides and `[[extra-files]]` (§8.1); `frappe-nix profile show|validate` (§5.13); `branches` and `gates` in the context; `editorconfig`, `python-lint`, `python-types`, `typescript` (`strict-extras`, `check-js`) and `js` parameters rendered from `cfg`.
- Its acceptance tests gain the [1.2] cases of §7 N3, and `selftest-scaffold` runs with the `example-org` profile (every module on) as well as `recommended`.

**#60 (N1), additionally:**

- Move `bench/`, `commands/testmap.py` and `commands/test_report.py` into `frappe_nix_tools/`, and rename the report `frappe-test-report.json` and its schema `schema/frappe-test-report.schema.json`, the rename shim markers `# frappe-nix:rename-shim-begin|end`, the nix checks (`standards-ports`, `standards-ci-mode`, `standards-frappe-test`, `standards-coverage-env`, `standards-bench-dev-group`, `standards-rename-code`, `standards-rename-devenv`, `standards-rename-nixos`), the fixtures `standards_fixture2`, `standards-fixture.localhost` and `FixtureToDo`, and the selftest's scratch path (`/standards-n1/`).
- **frappe-test reads module switches** from `frappe-nix config` (§5.1): `--ci` enables exactly the stages of enabled modules, skips stages 1–6 with `tests` off, and takes `tests.coverage.target` and `raise-margin` for the upward ratchet instead of the constants 80 and 2.0.
- **No built-in replace pair:** remove `REPLACE_PAIRS = {"jailbreak": "data_steward"}` from `lib/rename/frappe_rename_app.py` and the matching comment in `lib/scripts.d/frappe-rename-app.nix`; replace pairs come from the resolved profile's `[[replace-apps]]` and from `--fleet <file>`, and a non-opted-in app must pass `--fleet`, `--profile` or `--no-replace-check` (§5.10).
- `lib/standards/shell.nix`'s opt-in rule also gates nixfmt, statix and deadnix (S35); `FRAPPE_NIX_CI` stays unconditional, and the port salt becomes `ports.worktreeSalt`, defaulting to the opt-in test (§5.12).
- **Bench dev group (S1):** `lib/frappe-workspace.py` drops `ruff`, `pre-commit` and `semgrep` only from an opted-in app-mode root whose `tools/pyproject.toml` pins their replacements, and never from other roots; restore `pre-commit>=4.5.1`, `ruff>=0.15.0` and `semgrep` in `templates/bench/pyproject.toml` (keeping the PR's `coverage` and `unittest-xml-reporting` additions) and drop its comment saying an app pins them; the `standards-bench-dev-group` check covers both directions (§7 N1).

**Not affected:** the frappe-nix code outside these three PRs; N2, N4, N5 and N6, which haven't started and are written against 1.2 directly.

---

## 10. Open questions that need the user

Everything else in this spec is decided. These need the user, and none blocks writing the code. Q1–Q6 are from 1.1 (Q3 and Q4 reworded for the profile repository); Q7–Q9 are new, and were narrowed by the review round (Appendix R2). The user has decided Q7, Q8 and Q9; each decision is recorded under its question.

1. **Q1. Binary cache.** Attic (host plus S3) or Cachix (plan and token)? Then set the org variable `FRAPPE_NIX_CACHE`, the write secret `FRAPPE_NIX_CACHE_TOKEN` (Actions store only), and, for a private cache, the pull-only `FRAPPE_NIX_CACHE_READ_TOKEN` (Actions and Dependabot stores).
   - Until then, `ci / test` takes about 26–28 minutes (ci-timing track), against a 15-minute target.
   - The CI design doesn't change when the cache is added.
2. **Q2. The `version-*` bypass actor.** This spec uses GitHub Actions (integration 15368); only applying a ruleset proves it's accepted. If it returns 422: may we create a machine user for a `User` bypass (`repo-policy.release-bypass = "user:<id>"` in the Avunu profile, its token stored as `VERSION_BRANCH_TOKEN`), or would you rather enable deploy keys for the org? Also confirm that any workflow in the repo with `contents: write` being able to move `version-*` is acceptable.
3. **Q3. Registry credential.** `REGISTRY_TOKEN`, a classic PAT with `public_repo` from your account (plan decision 15), has to be added as a repo secret on each listed app before Phase 6, along with the `Avunu/marketplace` fork named in the Avunu profile. Alternatively, publishing stays local: `frappe-listing registry` uses your `gh` login.
4. **Q4. `FRAPPE_NIX_AUDIT_TOKEN`.** A read-only fine-grained PAT (Administration and Contents read, on the 13 repos), stored as a secret of `Avunu/frappe-standards-profile`, whose scheduled caller runs the scorecard (§5.7). Without it, those rows are `unknown`.
5. **Q5. Module names under in-place renames.** The validated zero-data-move path keeps the old module names, such as "Carbon Frappe", "Frappe UI Editor Integration", "JWT Auth" and "Mercury Integration". Is keeping marks like "Frappe" in module names acceptable? Renaming them needs a module-rename mode in `frappe-rename-app`, which isn't specified here. (data_steward is a fresh install and gets new module names regardless.)
6. **Q6. frappe/marketplace#29.** Until it merges, upstream registry CI fails ImportCheck for the 5 apps that import erpnext or hrms, even though our `marketplace` check (which mirrors #29) passes. Engage upstream, or wait?
7. **Q7. The one runtime change for apps that haven't opted in.** After this review, everything else waits for opt-in (the dev-group trim, the port salt and the `docs-site` exclusion now do too). What remains is the Vite registration in the Nix preload (§5.11): on a Nix-built bench it adds `assets.json` keys for hashed Vite bundles (`<name>.bundle.<hash>.js`) that 404 today, and a Vite key overwrites a same-named esbuild key. Keep it for every app-mode user (it fixes broken pages and touches no file), or gate it on opt-in like the rest?
   - **Decided (2026-10-07):** keep the Vite registration in the Nix preload for every app-mode user. It is a runtime fix for bundles that 404 today and touches no file, so it is not gated on opt-in (S35's one exception besides the inert `FRAPPE_NIX_CI`).
8. **Q8. Replacing the in-flight PRs.** The codename is in the trees of every commit on #58, #59 and #60, not only in their messages, so §9 rebuilds the three branches as fresh `standards/*` branches and opens new PRs, then closes #58–#60 with a neutral "Superseded by" comment. Reviewers lose the old PR threads (the new PRs link to them). The old PR pages, their title history and the commits behind `refs/pull/<n>/head` stay public unless GitHub Support removes them. Go ahead with the new PRs, and should we also ask GitHub Support to purge #58–#60?
   - **Decided (2026-10-07):** rebuild the three branches as fresh `standards/*` PRs, as §9 says, and close #58, #59 and #60 with a `Superseded by #<new>` comment and delete their branches.
9. **Q9. The `recommended` defaults.** Outside users will meet these first. The judgment calls are: ssort off; ty without `error-on-warning`; desk `check-js` off; `version-scheme = "semver"`; `develop+version` branching (Frappe's convention, but heavier than `main+tags` for a small app); Dependabot weekly with auto-merge (which now acts only where required checks protect the branch); `ci.nightly` on; repo policy off but, when turned on, 1 required approval; stacked PRs allowed. Confirm, or name the ones to flip. Avunu's own apps are unaffected either way: the Avunu profile extends the frozen `recommended@1.0` and sets each of these values explicitly (§8.6), and P's snapshot check fails if any of them would move.
   - **Decided (2026-10-07):** ship `recommended@1.0` exactly as §8.5 gives it; no default is flipped.

Not questions, but things the user does once: make timeclock public before its PR A (plan §7 decision 2); refresh the `gh` login with the `workflow` scope (`gh auth refresh -s workflow`) on the machine that runs `frappe-nix repo rollout`; create the public repository `Avunu/frappe-standards-profile` (P). The former Q6 of 1.0 (`validate_copyright` stamping) stays settled: D7 keeps it, and §2.14 excludes managed and generated files, so it no longer fights sync.

---

## Appendix C. Changes from 1.1 to 1.2

- **Naming (S33).** Every internal name replaced by a neutral one; Appendix N maps them for the implementers.
- **Opt-in (S35).** `templates/app/` and `frappe-init --app` stay as in `main`; `--standards <profile>` opts in; `--sync`/`--check` refuse without `[tool.frappe-nix]`; the dev shell adds tools only for opted-in apps.
- **Profiles (S34, S36–S38, §8).** New section: the profile file, 29 modules with parameters and needs, built-in `minimal` and `recommended` in full, the merge order, org profiles as a pinned `standards-profile` flake input, template overrides and extra files, `frappe-nix profile show|validate` (§5.13), and Avunu's profile as the worked example in its own repository (item P).
- **Org values out of frappe-nix (S37).** Publisher, e-mail, copyright holder, licence, brand colours, URL templates, GitHub owner, registry fork, README badges and links, the demo company, docs-tool patterns, the fleet list and the rename replace pair moved to profiles or the fleet file; `known-apps.json` has a generic `<owner>/<repo>` rule; a nix check keeps built-ins free of Avunu values.
- **§2.** `[tool.frappe-nix]` split into app keys and module tables; every inventory entry names its module and the profile values it uses; merged files have per-module key groups; retire rules are per module; dependabot, README, listing, icons, demo and shots read profile values.
- **§3.** `--standards`, `--profile-path`, profile resolution in phase A, retraction when a module turns off, new exit-2 cases.
- **§4.** One reusable workflow per gate, callers with one job per enabled gate (contexts unchanged), module-gated steps through a `cfg` step, the integration branch as an input, `main+tags` support, repo policy optional with required checks computed from the gates, the fleet list and the scorecard schedule moved to the org repository.
- **§5.** Tools read org values from the resolved configuration; `repo apply|audit|rollout` subcommands with `--fleet`, `--issue-repo` and `--profile-to`; audit rows are `n/a` when their module is off; policy, compat and ratchet rules are per module; frappe-test's stages and coverage target follow the configuration.
- **§6.** Profile changes in the semver contract, `minimal` frozen within v1, §6.4 org profile versioning.
- **§7.** New cross-cutting tests: default app mode unchanged, `minimal` renders nothing extra, toggling a module removes its files and jobs, no vendor strings in built-ins; profile, gate-matrix and org-value tests in N1, N3, N4 and N5.
- **§9.** Migration of #58, #59 and #60. **§10.** Q7–Q9 added; the open-questions section moved from §8 to §10.
- **Review round (Appendix R2).** Opt-in now also gates the bench dev-group trim, the port salt and the `docs-site` exclusion, and the opt-in test is a line match; the release guard and audit rows work under `semver`; `recommended` ships as frozen snapshots (S42); gates are `frappe-nix gate` commands and siblings/profiles accept other hosts and a fetch token (S43); parameters for the integration branch, metadata keys, the TypeScript preset, schedules, merge policy, tag prefix and docs-tool cooldown names; local regions in `flake.nix` and `.envrc`; retire rules by function; in-repo profiles; `repo doctor`; `repo apply` before opt-in; the Avunu profile sets every value explicitly; §9 rebuilds the branches and opens new PRs.

## Appendix I. Implementation deviations

The implementing PRs record their deviations from the spec here, one row each, as they did on their branches under 1.1 (N3a: 15 rows, N3: 41 rows, N1: 10 rows at the time of writing). When a PR replaces its copy of the spec with 1.2 (§9), it carries its rows over with the Appendix N renames applied. Rows that 1.2 already adopts in the main text keep their place here for the record: N3's `requirements.txt` retire rule, `[tool.vulture]` and `[tool.test_utils.*]` as `test-utils` keys, the `--offline` sync, the `patches_dir` fact, and N1's `--python` bench scripts and report `stages`.

| PR | Section | Change | Why |
|---|---|---|---|
| N3a | S20 | Full SHAs and URLs for the three inputs; `frappe-semgrep-rules` is locked at the `develop` head of 2026-10-06 (81a6e3d). | S20 gave no revision for `frappe-semgrep-rules`. The flake URLs name branches, not SHAs, so Dependabot `nix` can move the locks. |
| N3a | §1.4 | New: the seam interfaces, including `common/config.py`'s API and `common/schema.py`. | §1.2 names the seams but not what a contribution looks like. |
| N3a | §1.2 | `common/schema.py`, a validator for the keywords the shipped schemas use, joins `common/`. | The package carries no JSON Schema library (jinja2, tomlkit and packaging only), and the resolver must validate every layer (§8.4 step 3), so validation lives beside it rather than in N3's engine. |
| N3a | §1.2 | Each module is defined once, in `profile.schema.json`'s `$defs`; `tool-frappe-nix.schema.json` refers to it, and the annotation `"x-app-only": true` marks the app-only module parameters of §8.1, which a profile may not set. | One definition per module keeps the two schemas from drifting apart, and the `default`s in it are the values §8.4 step 5 applies (the `recommended` values; a package test compares them). |
| N3a | §1.2 | N3a commits the fixture's `flake.nix`, hand-rendered from §2.5 for an app with no siblings. N3 owns it from then on. | §7 N3a evaluates the fixture's flake, so it must exist before N3's renderer does. For no siblings, §2.5's template renders `siblings = [` and `];` on separate lines, which `nixfmt` collapses to `siblings = [ ];`: N3's template must special-case the empty list to be nixfmt-stable. |
| N3a | §1.2 | `ty.toml` and `dev/env.nix` gain `py/frappe_nix_tools` (owner N3a). | `ty` checks the whole tree and must resolve `frappe_nix_tools` imports. `tomlkit` is not in `dev/uv.lock`, so N3 adds it to `dev/pyproject.toml`'s dev group when its code first imports it. |
| N3a | §1.2 | `modules/devenv.nix` keeps `apps.relock` inside a `lib.mkMerge` with the app-mode `apps`, and `apps.frappe-init` (with every `lib/standards/tools` app) exists only in an opted-in app's flake. | Nix can't define `apps.relock` and `apps = mkIf …` side by side. Gating `apps.frappe-init` too keeps a non-opted-in app's flake outputs exactly `main`'s (S35); an app opts in by adding the table or with `nix run github:Avunu/frappe-nix#frappe-init -- --sync --standards <profile>`. |
| N3a | §1.2 | `lib/standards/shell.nix` takes `pyproject` (the app's `pyproject.toml`) and also returns `devenvModule`, which `modules/devenv.nix` imports into the app-mode shell: the fragment's `packages`, and its enterShell snippet as a definition of its own under `mkIf optedIn`. | The opt-in test needs the file. A separate definition keeps the enterShell of an app that has not opted in byte-identical to `main`'s, and gives `standards-app-shell` something it can find and evaluate without the whole shell. |
| N3a | §1.2, §7 N3a | `.github/workflows/selftest-package.yml` (N3a): from a clean clone, `uv tool install "frappe-nix-tools @ git+file://…#subdirectory=py/frappe_nix_tools"` and `frappe-nix data-path` / `config` on the fixture. Path-filtered to the package plus `workflow_dispatch`. | §7 N3a requires the no-checkout install to be proven by CI, and N3a owns no other workflow; `check.yml`'s N3a hunk is only the `standards-all` build. |
| N3a | §7 N3a | `standards-all` holds N3a's checks (`standards-cli`, `standards-loaders`, `standards-app-flake`, `standards-optin`, `standards-profiles`, `standards-vendor-neutral`, `standards-docs`). | Each proof is a nix check under `tests/standards/`, which runs through `standards-all`. |
| N3a | §7 N3a | The app-mode dev shell is proven by evaluating `modules/devenv.nix`'s wiring rather than by `nix develop`. `standards-app-shell` evaluates the fixture app's flake with flake-parts' `debug` on, once as it is and once over a source without `[tool.frappe-nix]`. It calls `modules/devenv.nix`'s own `devenv.shells.default` definition with that shell's config. It then asserts that the shell imports the fragment's devenv module, which carries `frappe-nix`, `frappe-init` and the blame setting only when opted in; that the shell's other packages are the same either way and never include a standards tool; and that the flake's apps differ by exactly the fragment's apps. `standards-optin` and `standards-app-flake` evaluate `lib/standards/shell.nix` itself. Of the opt-in shell assertions, the absence of nixfmt, statix and deadnix without the table is N3a's; their presence with it comes with N1's hunk. | The shell's full evaluation imports the generated bench workspace (IFD) and needs the fixture's `nix/uv.lock`, which only a relock produces; a package that needs it is skipped by name (`tryEval`). N3a adds only `frappe-nix`, `frappe-init` and the blame setting. |
| N3a | S35 | The opt-in line match also accepts leading whitespace and an array-of-tables header: the pattern is `[[:space:]]*\[{1,2}tool\.frappe-nix[].].*` per line. frappe-nix-tools applies the same match (`common/pyproject.py`), and `config.resolve` refuses (exit 2) a `pyproject.toml` on which it and `tomllib` disagree: a quoted key (`[tool."frappe-nix"]`), spaces inside the brackets, dotted keys or an inline table under `[tool]` (the table without the line), and a header line inside a multi-line string (the line without the table). The tools read the file's bytes with their line endings as Nix's `builtins.readFile` does, so CRLF opts in on both sides and a file whose lines end in a lone CR (one line to the shell) is a TOML error, exit 2. `tests/standards/fixtures/optin/` holds each spelling with the shell's verdict (`standards-optin` adds the CRLF and lone-CR variants), and `standards-cli` runs `frappe-nix config` on the same files. | TOML allows indented headers, and `[[tool.frappe-nix.untested]]` alone creates the `[tool.frappe-nix]` table, which the Python side (`tomllib`) then sees as opted in; the two tests must agree. Where TOML lets them differ, an error naming the fix is better than an app whose tools say opted in while its shell lacks them, or the reverse. |
| N3a | §7 N3a | The "Avunu profile example" doc lint (`standards-docs`) covers `docs/app-standards/*.md` except `spec.md`; an example block is a fenced code block whose opening fence follows a line containing "Avunu profile example". | The spec is the contract and names the Avunu profile as its worked example throughout (§8.6, §10); the lint keeps the other pages honest. |
| N3a | §8.1 | `schema` is accepted in `[tool.frappe-nix]`, where it is required, although §8.1's list of profile-only keys names it. | §2.1 requires `schema = 1` in the app table (its own schema version); only `name`, `description`, `extends`, `requires-frappe-nix`, `retire`, `replace-apps` and `extra-files` are refused there. |
| N3a | §8.4 | Step 6's check that every org value a live entry `uses` is set is N3's; `config.py` checks `requires-frappe-nix` (exit 3). | That check needs the manifest, which N3 brings. |
| N3a | §8.4 | For plain `recommended`, `profile.name` is the snapshot it resolves to (`recommended@1.0`), so `recommended` and `recommended@1.0` resolve identically, `profile` included. An app whose host can't be determined (no `repo`, no origin, no `org.github-owner`) counts as on GitHub. | §7 N3a asks for identical resolution. An unknown repository is not an error for modules that don't render one (§2.2), so it must not switch GitHub-only modules off either. |
| N3a | §8.4 | `frappe-nix config` takes `--json`, `--lock` and `--profile-path` besides `--pyproject` and `--github-output modules`; on an app without `[tool.frappe-nix]` it exits 2 with the opt-in hint. | `--lock` and `--profile-path` let a caller name the org profile's source the way sync does (§3.3); §8.4 step 1 leaves the not-opted-in meaning to the caller. |
| N3a | §1.4 | `pin-path` and `config` default to the nearest `flake.lock`/`pyproject.toml` within the git work tree, not the work tree root's. | §4.1 runs every step in `inputs.app-root`, and §7 N4's `selftest-ci` passes `app-root: tests/fixtures/standards-app`: the root's lock is frappe-nix's own, which has no `frappe` input. |
| N3a | §1.4 | `tests/standards` checks must be named `standards-<name>`; tool names `default`, `frappe-init`, `frappe-nix-tools`, `backup-fetch` and `relock` are refused. | flake.nix merges both seams with `//`, so a clash would otherwise replace frappe-nix's own output silently in one flake and fail with an option conflict in the other. A prefix keeps the guard in the seam instead of reindenting flake.nix's `checks`. Every check §7 names already has it. |
| N3a | §1.4 | A tool must propagate nothing; `packages.frappe-nix` is a wrapper of the package's `bin/frappe-nix` (with git and gh appended to `PATH`), not the Python package, which is `packages.frappe-nix-tools`. | The app-mode shell adds every tool to devenv's `packages`, which `mkShell` takes as `nativeBuildInputs`: a propagated python3 runs its setup hook and appends nixpkgs' `jinja2`, `markupsafe`, `packaging` and `tomlkit` to `PYTHONPATH`, which comes before the venv's site-packages, so a bench in the app's shell would import those instead of its `nix/uv.lock` versions. |
| N3a | §1.4 | `pin-path` rehashes a cached `.dev-dist/pins` tree before reusing it and writes no `.narHash` stamp. | The cache lives in the app checkout, so a PR could commit a weakened `frappe-semgrep-rules` tree with a matching stamp and the CI semgrep gate would run it. The trees are small and hashed once per job. |
| N3a | §1.4 | `pin-path` reads frappe-nix's three pins only through the `frappe-nix` node and refuses (exit 2) one that locks any repository but its own (`github:frappe/semgrep-rules`, `frappe/marketplace`, `frappe/pilot`). | A PR's `flake.lock` could otherwise retarget `frappe-semgrep-rules` at a repository holding `rules: []` with that repository's `narHash`, or add a root input of that name, and the narHash check would pass. Still accepted: a lock edit that moves one of them to another commit of its own repository. Such an edit is a visible `flake.lock` diff, and refusing it would need the installed tool to carry frappe-nix's lock, which Dependabot's lock-only bumps would then have to keep in step. |
| N3a | §2.2 | `common/repo.py` reads `origin` as `(host, path)` for any host (`parse_remote`, `origin_repo`); nothing in it assumes an owner. | §2.2's `repo` fact: `<owner>/<repo>` on GitHub, a group path elsewhere, with the host deciding the GitHub-only modules (S43). |
| N3a | §3.3 | Unexpected failures exit 3; `github` output fences the text in `::stop-commands::` and escapes the annotations. | Exit 1 is drift, so an uncaught exception in `--check` read as drift in CI. Paths come from `git ls-files` and diffs hold file contents, so unescaped they could break annotations or run workflow commands. |
| N3a | S43, §1.4 | `pin-path` sends `FRAPPE_NIX_FETCH_TOKEN` only over https, or plain http to a loopback address. It drops the token on a redirect to another scheme, host or port, and refuses a redirect from https to http. git gets the token as `http.extraHeader` through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>`, never through `git -c`. | urllib copies `Authorization` to wherever a redirect points, so a GitLab that redirects archives to object storage, or a mirror, would receive the token. On the command line the token is readable by every user of a shared runner. |
| N3a | S43, §5.2 | A `github` lock node whose `host` is not `github.com` (GitHub Enterprise) is fetched from `https://<host>/api/v3/repos/<owner>/<repo>/tarball/<rev>`, the host's REST API. frappe-nix's three pins must be on `github.com`. | Fetching it from codeload.github.com sent the GHE token to github.com and fetched a same-named public repository, if any. |
| N3a | S35 | Every app's `flake.lock` gains the three `flake = false` nodes of S20 on its next frappe-nix update, whether or not it opted in. S35 now says so. | S20 puts these inputs in every app's lock. Nothing else changes for an app that has not opted in: its dev shell, packages, apps and checks evaluate to the same derivations as on `main`. |
| N3a | §1.2 | This copy of the spec has a front-matter block for the docs site. | Every page under `docs/` carries one; `standards-docs` checks the section's pages for it. |

---

## Appendix R. Review log (spec 1.0 → 1.1)

Each review issue was checked against the sources named in its row. The log is kept as written for 1.1, except that names are given in their 1.2 form (S33, Appendix N) and section numbers are 1.1's (they are unchanged for §0–§7). "Fixed" means the spec now says what the row lists. Nothing was rejected outright; two issues were resolved differently from the reviewer's first suggestion, with the reason given.

| # | Sev. | Issue | Verified against | Verdict and resolution |
|---|---|---|---|---|
| 1 | blocker | relock-push can't push the caller-SHA rewrite of a frappe-nix bump with `GITHUB_TOKEN` | GitHub's documented `workflows`-permission refusal for GitHub App tokens; github-capabilities track (version-16 REST move across existing workflow commits) | **Fixed, via the reviewer's fallback rather than a PAT or floating refs.** S5, S29, §2.17, §4.5, §5.8, §6.2, A10: dependabot ignores frappe-nix in both ecosystems; `frappe-nix repo rollout` (user token with `workflow` scope, auto-merge on) is the only mover; relock/upgrade/shots refuse workflow paths; nightly `drift` and A10 remind. Rejected alternatives: (a) a fine-grained PAT with `workflows` on all app repos is a new long-lived write credential, against the no-App, minimum-credential stance; (b) `@release-1` callers would let the workflow code float ahead of the tools the app pins (`job_workflow_sha` ≠ flake.lock), breaking §6.1's one-version-per-commit and the zizmor `hash-pin` policy. The Git Data API path is an N4 spike that can automate this in a later MINOR (§4.5). |
| 2 | blocker | Vite bundles unregistered on stock benches | frappe `esbuild/esbuild.js` (writes `assets.json`, then runs app builds); taskview `hooks.py` `app_include_js`; taskview `update-assets.mjs` | **Fixed.** S30, §2.4, §2.8 `scripts.build`, C8, §5.11 (one module `lib/js/vite-register.cjs`, two callers, sites resolution, hard-link copy replacing `copyPortal`), N2 stock-bench acceptance test in `selftest-assets.yml`. |
| 3 | blocker | Root/in-package Vue SPAs inexpressible | timeclock root `vite.config.ts`/`tsconfig.json` and `public/js/{timeclock,job_timeclock}`; taskview `portal/tsconfig.json` extends `../tsconfig.json`, `scripts.typecheck` uses vue-tsc | **Fixed.** §2.1 `[[tool.frappe-nix.typescript.spa]]` and `typescript.browser`, with both fleet layouts written out; §2.2 `spa_globs`, `solution`; §2.8 `scripts.typecheck`; §2.9 solution renamed `tsconfig.solution.json` when an SPA owns the root, empty projects omitted (no TS18003); N3 tests. |
| 4 | major | Strict checkJs with no honest migration path | frappe-types track error counts | **Fixed.** `[[tool.frappe-nix.unchecked-js]]` (§2.1, §2.9), R6, C9, `frappe-nix unchecked-js --stale` in typecheck, amber A7; `typescript.exclude` limited to non-source paths (exit 2). |
| 5 | major | C7 forbids the augmentations apps need | frappe-types track (taskview, esign, jailbreak, timeclock symbols) | **Fixed.** C7 narrowed to redeclarations; augmentations only in `types/<app>.augment.d.ts` with `// app-owned:` per member (§2.9, §5.9); A7 counts members. |
| 6 | major | esign web scripts under `public/js` classed as desk; oxlint globs ≠ tsconfig sets | esign `public/js/web/*.js` (`frappe.web_form`, `frappe.ready`, `frappe.form_dirty`) | **Fixed.** `typescript.web-include` (§2.1, §2.2); tsconfig and oxlint overrides generated from the same file sets, with a web override (§2.9, §2.10). |
| 7 | major | No way to retire legacy files | carbon and taskview `.github/workflows`; timeclock `.oxfmtrc.json`, `MANIFEST.in`, `requirements.txt`; carbon `nix/node-offline-hashes.json`; frappe-nix `modules/devenv.nix:1858` throw | **Fixed.** S31, §2.4.1 retire list, sync step 7, exit 1 `legacy file`, A9 reads the same list. |
| 8 | major | Bootstrapping undefined | postgrid and jwt_auth have no flake; carbon/taskview/timeclock lock `main` | **Fixed.** S32, §3.3 two-phase sync with self re-exec and dev-shell re-entry, single bootstrap command, migrations after `v1.0.0` (§1.1); N3 bootstrap test. |
| 9 | major | Rename leaves bundle files with old names; jailbreak replace path missing | esign and timeclock `hooks.py`; jailbreak `__init__.py` whitelisted functions and JS callers; plan A.2 | **Fixed.** §5.10 step 0/1/2 (rename `OLD.*` files, bare-filename guard), replace path with `replacedApps`, `rename_mode` in apps.json, `__init__` functions to `api.py` (§2.13); N1 tests with an esign fixture. |
| 10 | major | Date-dependent README license block | §3.4 | **Fixed.** `<first-commit-year>–present` (§2.19); §3.4 forbids current-date inputs; N3 test across 31 Dec/1 Jan. |
| 11 | major | `validate_copyright` fights managed and generated files | §2.14, §2.21; carbon `public/js/generated/*`, timeclock `types.ts`, `components.d.ts` | **Fixed.** `[tool.frappe-nix] generated` fed to prek, validate_copyright, oxfmt, oxlint; managed headed files excluded by name (§2.14); N3 test. Q6 removed. |
| 12 | major | Set-keyed semgrep baseline hides identical findings | taskview `api.py`: 12 identical `frappe.db.commit()` | **Fixed.** Multiset with `count` (S21, §2.21, L7, R3); N5 tests. |
| 13 | major | Nightly script masks failures | §4.6 command (exit status of `--down`) | **Fixed.** Full script with `rc` accumulation, admin password, artifact dir and carbon's `CF_*` names (§4.6); N4 stub test. |
| 14 | major | Security hook callables not testmap targets | jwt_auth, carbon, esign, automated_subscriptions `hooks.py` | **Fixed.** T6 (§5.1.1), install/migrate kinds exemptable; N1 test. |
| 15 | major | Coverage omit not extensible | §2.12 | **Fixed.** `*/patches/*` in managed omit; `[[tool.frappe-nix.coverage-omit]]`, R7, C9, amber A8. |
| 16 | major | No CI hook for frappe-tree and shell-only checks | carbon `check.yml` lines 124–143 (`codegen`, `FRAPPE_PATH`, `compile`, `audit:drift`) | **Fixed.** typecheck exports `FRAPPE_PATH` via `frappe-nix pin-path frappe` (app inputs supported), opt-in `frappe-node-modules`; `shell-checks` run by `frappe-test --ci` stage 8b, verdict 7. |
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
| 27 | minor | "App-owned build step" undefined | carbon `package.json` `build` | **Fixed.** `[tool.frappe-nix] build = true` (§2.1, §2.8). |
| 28 | minor | Second ruff in the bench env | `lib/frappe-workspace.py:140-141` (`ruff>=0.15.0`, `pre-commit`, `semgrep`) | **Fixed.** N1 removes them and reconciles existing roots (S1, §1.2, §1.3); N1 test. |
| 29 | minor | Two evaluations per PR test run | ci-timing track | **Fixed differently.** A single evaluation for both `nix build` and `nix develop` isn't available (a `--profile` from `nix build` isn't a dev-shell env, and `--no-pure-eval` disables the eval cache). Instead the clean build runs on PRs only when build inputs change, and always on push, schedule and dispatch (S16, §4.2). |
| 30 | minor | Private timeclock gets no protection and burns minutes | github-capabilities track (403 on rulesets) | **Fixed.** `--phase full` requires `private: false` (§5.6); §4.7 note; decision 2 (public first). |
| 31 | blocker | Duplicate of #1 (relock-push vs workflow files) | as #1 | **Fixed** as #1. The reviewer's Git Data API primary is kept as the N4 spike; its fallback (dependabot ignores frappe-nix, rollout only) is the v1 design, since it works regardless of the spike's outcome. |
| 32 | blocker | Wheel from `#subdirectory=py/frappe_nix_tools` lacks templates and data | §1.2 tree, §3.1 | **Fixed.** S10: all data under `py/frappe_nix_tools/frappe_nix_tools/data/` read via `importlib.resources` (§1.2, §3.1); N3a and N3 no-checkout install tests. |
| 33 | major | Gate jobs lack `pull-requests: read` for the dispatch guard | §4.2 table | **Fixed.** Every gate job has it (§4.2); N4 dispatch test. |
| 34 | major | relock patch misses staged and untracked files | sync step 10 `git add`; `git diff --binary` | **Fixed.** `git add -A && git diff --cached --binary`; `git apply --index`; N4 test (§4.5). |
| 35 | major | nix auto-merge enabled before relock lands | §4.5 | **Fixed.** auto-merge job never enables it for nix unless the relock was empty; `relock-push` enables it after pushing (§4.5). |
| 36 | major | selftest-ci always skews; no way to skip `test` | §3.7, §4.2 | **Fixed.** Override sets `FRAPPE_NIX_ALLOW_SKEW=1` and drops `--expect-rev` (§3.7, §4.1); `run-test` input (§4.2); N4 test. |
| 37 | major | frappe-nix release dispatch runs VM tests; pr-policy inside check.yml | frappe-nix `check.yml:86-88` (`vm-tests` on any `workflow_dispatch`) | **Fixed.** `vm-tests` dispatch input; separate non-reusable `pr-policy.yml` installing from the checkout, `--major-free` (§1.2, §4.10, §6.2); N6 tests. |
| 38 | major | Cache write token exposed to untrusted runs | S15, §4.1 | **Fixed.** Write token only on develop push/schedule; separate read token in the Dependabot store; S15 text corrected (S15, §4.1, callers). |
| 39 | major | `frappe-init --app` would copy all of `templates/app` | frappe-nix `lib/sh/template.sh` `render_template`/`install_template` (cp -R, find -type f) | **Fixed.** `templates/app/` keeps only `.envrc` and `.gitignore`; `cmd_app_init` delegates to `frappe-nix sync --write` (§1.2, §1.3, §3.3); N3 test. |
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

## Appendix R2. Review log (spec 1.2)

Each issue was checked against this text, the binding requirements (codename-free public artifacts; nothing changes without opt-in; profile-driven; every gate toggleable; no org values in frappe-nix; Avunu's choices in its own profile; a vendor-neutral CI check) and the sources named. "Fixed" means the spec now says what the row lists. The user handles the #59 frappe[dev]/pypika relock failure separately; no issue in this round is about it.

| # | Sev. | Issue | Verified against | Verdict and resolution |
|---|---|---|---|---|
| 1 | blocker | Dev-group trim of ruff, pre-commit and semgrep reaches non-opted-in and bench-mode users | `lib/frappe-workspace.py:139-142` on `main`; `templates/bench/pyproject.toml:28-33`; #60's diff, which removes all three from the bench template | **Fixed.** S1, §1.2, §1.3 N1, §9 #60: `ensure-root` drops each only from an opted-in app-mode root whose tracked `tools/pyproject.toml` pins its replacement (`ruff`, `semgrep`, `prek`); bench mode and `templates/bench` keep them (only `coverage` and `unittest-xml-reporting` are added). [1.2] N1 test for both directions. |
| 2 | blocker | Release guard compares `version-<frappe major>` with a `v<frappe major>.*` tag under `semver` | §4.4, §5.7, §8.5 | **Fixed.** The guard compares with `needs.release-please.outputs.tag_name`, plus the major prefix check only under `frappe-major` (§4.4); A2 and A11 are scheme-aware (§5.7); N4 test develop+version × semver. |
| 3 | major | Managed `scripts.lint`/`scripts.check` overwrite eslint/biome users' scripts when `js` is off | §2.8 | **Fixed.** `lint` and `check` belong to `js` (oxc) only; stylelint writes `lint:css`; with `js` off both are app-owned; `check` chains whichever parts exist, whoever owns them (§2.8, §8.2). N3 test. |
| 4 | major | Third-party formatters fight byte-compared whole files | §2.10, §3.2, §2.14 | **Fixed via the reviewer's alternative.** While `js.tool` isn't `oxc`, YAML/JSON/JSONC/TOML whole files are compared as parsed data plus header, and sync doesn't rewrite a semantically equal file (§2.10, §3.2). A pre-commit global exclude was rejected: it would also hide the managed workflows from zizmor and actionlint. N3 test with prettier output. |
| 5 | major | `metadata` is monolithic and every JS/TS/release module needs it | §8.2, §2.8, §2.12, §2.4.1 | **Fixed.** `metadata.build-backend` (flit\|any), `package-type`, `package-manager`, `node-engine` (`""` = app-owned); `[tool.poetry]`, `requirements.txt` and `MANIFEST.in` rules apply only with flit; `js`, `typescript`, `stylelint`, `vite-register` and `releases` no longer need `metadata` (§8.2). The CI gates still install with yarn classic, stated in §2.8. N3 test. |
| 6 | major | `ci` retire rules delete adopters' own release and auto-merge workflows; `check.yml` name rule | §2.4.1 | **Fixed.** Content rules belong to `releases` and `dependabot`; the name-only rule moved to the Avunu profile's `[[retire]]` (§8.1, §8.6); app-level `retire-keep`. N3 test. |
| 7 | major | Whole `flake.nix`/`.envrc` loses custom content; phase A undoes frappe-nix pins | §2.5, §3.3 | **Fixed.** Local regions `inputs` and `envrc`; app-only `dev-shell.systems`, `extra-substituters`, `extra-trusted-public-keys`, `frappe-nix-url`; outputs stay in `nix/local.nix`; first opt-in refuses with a diff unless `--force`; phase A re-locks only when the locked `original` differs from the rendered URL; forks flow through to `uses:`, the CI install and the dependabot ignore (§2.2, §2.5, §3.3, §3.7, §4.1). N3 test. |
| 8 | major | Generic sibling rule forces `version-{n}`; C3 assumes it; `[known-apps]` refused in the app | §2.1, §5.9, §8.1 | **Fixed.** Object-form siblings (`repo`/`flake-url`, `branch`, `range`, `desk_global`); `[tool.frappe-nix.known-apps]` allowed as the last layer; C3, C4 and L3 use the resolved branch and range (§2.1, §5.2, §5.9, §8.1, §8.4). N3 test. |
| 9 | major | Integration branch limited to develop/main and tied to `releases`; pr-policy rejects stacked and maintenance PRs | S41, §2.3, §5.9 | **Fixed.** App key `integration-branch` (any name; `--standards` writes origin's default branch when it differs); `commits.allow-stacked` (true in `recommended`, false for Avunu per plan §5) and `commits.pr-allowed-bases` (S41, §2.1, §2.3, §5.9). The reviewer's "default: the repository's default branch" is applied at creation time only, because reading `origin/HEAD` at every sync would not be deterministic (§3.4). N4 test. |
| 10 | major | `recommended` auto-merge without repo policy fails or merges unprotected; labels and Actions PR creation missing | §4.5, §4.9, §8.5 | **Fixed.** Auto-merge acts only when the branch rules require every enabled gate's context, else labels `needs-review` with a notice; labels are created idempotently; release-please failure names the setting; new read-only `frappe-nix repo doctor`, which sync points to (§4.4, §4.5, §5.6, §8.5). `auto-merge` stays `true` in `recommended`, since the safety rule makes it inert until checks are required. N4 tests. |
| 11 | major | Fixed daily crons; private repos refused for rulesets | §4.7, §5.6 | **Fixed.** `ci.schedules` (one cron or `""` per schedule, the relock cron passed to `app-deps.yml`); `repo apply` tries rulesets on private repos and fails only on a real 403; the fleet's `private: true` stays Avunu's own guard (§4.5, §4.7, §5.6). N4 test. |
| 12 | major | GitLab and other hosts shut out | §4, §2.2, §5.2, §5.9, §8.3 | **Fixed in part.** S43: `frappe-nix gate lint\|typecheck\|marketplace` holds the gate logic; siblings and profiles accept `gitlab:` and `git+https:`; `pin-path` and `minibench` fetch those lock types; `repo` accepts subgroup paths; GitHub-only modules listed and resolved off on other hosts (§4, §5.2, §5.9, §8.2, §8.3). **Not done:** `ci.provider = "gitlab"` (rendered GitLab pipelines) stays reserved; a later MINOR can add it on top of the gate commands without breaking anything. |
| 13 | major | No token for private profile, siblings or frappe in no-Nix CI | §5.2, §5.9 | **Fixed.** `FRAPPE_NIX_FETCH_TOKEN` (secret `FETCH_TOKEN` on every reusable workflow; `access-tokens` for Nix jobs); `pin-path` and `minibench` authenticate; doctor lists it (§4, §4.7, §5.2, §5.6, §5.9). N3 test with a token-protected local server instead of a real private repository. |
| 14 | major | `recommended` TypeScript depends on an Avunu-published, unreleased `frappe-types` | §2.9, §2.8; `npm view frappe-types@16.5.0` | **Fixed in part; partly rejected.** Rejected: "unreleased" — 16.5.0 with `tsconfig/*` and `./web` exports is on npm (modified 2026-10-07), so `typescript` stays on and the tarball fallback is removed (§7 N3). Fixed: the dependency is documented (§2.9) and `typescript.preset = "inline"` removes it (with `check-js`, `audit-consumer` and gen-doctypes then exit 2, since they need Frappe's declarations). An MIT dependency on npm is a dependency, not an org value, so the vendor-neutral check is not extended to it. |
| 15 | major | MINOR changes to `recommended` reach adopters with no way to pin | §6.3, §8.3 | **Fixed.** S42: frozen `recommended@<minor>` snapshots, `recommended` = newest; changing a released snapshot, or the branching, version-scheme, tag-prefix or integration-branch default, is MAJOR; sync warns with the changed values for apps on the floating name (§5.13, §6.3, §8.3, §8.5). N3a and N3 tests. |
| 16 | minor | Port salt and docs-site exclusion reach non-opted-in apps | §5.11, §5.12, Q7 | **Fixed.** `ports.worktreeSalt` defaults to the opt-in test; `excludeNodeTargets` defaults to `[]`, and the managed flake sets `docs-site` only with the `docs-site` module (§5.11, §5.12, §2.5). Q7 narrowed to the Vite preload. N1 and N2 tests. |
| 17 | minor | `fromTOML` in every app-mode evaluation | S35; `nix eval` | **Fixed, differently.** Verified that Nix's `fromTOML` rejects TOML datetimes ("Dates and times are not supported") and that `builtins.tryEval` does **not** catch the parse error, so the suggested `tryEval` would not help. The opt-in test is a line match on the file's text, never a parse (S35). N3a fixture with an unparsable pyproject. The file is still read, so editing it still re-evaluates the shell; that is unavoidable for any opt-in test. |
| 18 | minor | Single-app teams need a published profile repository for overrides | §8.1, §8.3 | **Fixed.** In-repo profile `profile = "./<dir>"`, read from the checkout (works in CI, no input, no rollout) (S38, §3.1, §8.1, §8.3). N3 test. |
| 19 | minor | Review and merge policy fixed in JSON | §4.8, §4.9, §2.16 | **Fixed.** `repo-policy.merge-methods`, `require-thread-resolution`, `delete-branch-on-merge`, `manage-default-branch`, `manage-features`; `releases.tag-prefix` drives release-please and the tag ruleset (§2.16, §4.8, §4.9, §8.2). Squash stays the default, with the reason given in §4.8. |
| 20 | minor | Compat hook fails outside the dev shell; README install line host- and tag-blind | §2.14, §2.19 | **Fixed.** The two `frappe-nix` hooks skip with a warning when the command is missing (CI still enforces both); `org.repo-url` template; under `main+tags` the line names `v<__version__>` inside release-please markers with README.md as an extra file, so it never drifts; `readme.install-ref` overrides it (§2.14, §2.16, §2.19). N5 test. |
| 21 | major | Avunu profile inherits values from a moving `recommended` | §8.6, §10 Q9, §6.3 | **Fixed.** The Avunu profile extends `recommended@1.0` and sets every value the fleet relies on (branching, nightly, coverage, commit types, test-utils hooks, ruff, cooldown, demo and screenshot locale, schedules, merge policy, …); P's CI compares the resolved configuration with `resolved.snapshot.json`; Q9's sentence corrected; branching and version-scheme default changes are MAJOR (§1.3 P, §6.3, §6.4, §8.6, §10). |
| 22 | major | Unlisted Avunu apps lose the registry-readiness checks of 1.1 | §8.6, §5.2, §5.7 | **Fixed.** `listing` is registry readiness; publishing is `listing.publish`. Unlisted apps keep `listing` on with `publish = false` and no `listing.toml`, which keeps the gate, its required check, L3–L7, L9, the baseline and R3 (§5.2, §5.7, §8.2, §8.6). |
| 23 | major | `repo apply` can't run before the app opts in (plan Phase 4 step 1) | §5.6; plan Phase 4 step 1 | **Fixed.** Profile from `--profile` or the fleet (`profile`, per entry or top level), empty app layer, default-branch clone, `--phase provisional` only, `--rename-default-branch` for `main` → `develop` (§4.9, §5.6). N4 test. |
| 24 | major | Cooldown exclude rendered from glob patterns breaks `docusystem doctor` | docusystem `src/lib/dependabot.ts` `cooldownAdvice` (exact `excludes.includes("Avunu/docusystem")`) | **Fixed.** `docs-site.cooldown-exclude-actions` (Avunu: `["Avunu/docusystem"]`) renders the exclude; `action-patterns` keeps the group and auto-merge (§2.17, §8.2, §8.6). The N4 test uses a `docs-compat` fixture with exactly the Avunu values, plus a negative variant; P's CI runs doctor against the real profile. |
| 25 | major | A message-only rewrite leaves the codename in trees and on the PR pages | `git grep` of the three upstream branches (201–271 distinct tokens each) | **Fixed.** §9 rebuilds the branches as fresh commits (or `filter-repo` with tree replacements), pushes new `standards/*` branches, opens new PRs, retitles and closes #58–#60, and states what stays public without a GitHub Support purge; Q8 asks the user. |
| 26 | minor | Appendix N misses identifiers on the PR branches | `git grep` of the three branches | **Fixed.** Rows for the shell attribute set, the fixture HTTP header, the testmap probe module, the pyproject helpers, the type stubs directory, the ports file, the sync-result temp names, the dev-group check names and the schema `$id`s, with their new names `standardsShell`, `X-Standards-Fixture`, `testmap_probe.py`, `tool_frappe_nix`, …, and a catch-all rule (Appendix N). |
| 27 | minor | A committed naming check would contain the codename | §7 N3a | **Fixed.** The naming check is a pre-merge reviewer/agent check with the term taken from the internal copy of the spec; an optional CI step reads it from an Actions secret; nothing committed names it (§7 N3a, §1.2). |
| 28 | minor | Vendor-neutral check misses code, `lib/**` and reusable workflows | S37, §7 N3a; `git grep` of `main` (no Avunu values in `lib/`; `Avunu/frappe-runtime` only in `modules/`) | **Fixed.** Scope extended to the frappe-nix-tools code, `lib/**`, `app-*.yml` and `fleet-audit.yml`, with `Avunu/frappe-nix` and `Avunu/frappe-runtime` allowed; planted variants for each new area (S37, §7 N3a). |
| 29 | minor | Rename replace guard needs `--fleet` | §5.10 | **Fixed.** Profile key `[[replace-apps]]` (Avunu declares jailbreak → data_steward); because PR 0 runs before opt-in, a non-opted-in app must pass `--fleet`, `--profile` or `--no-replace-check` (exit 2 otherwise) (§5.10, §8.1, §8.6). N1 test. |
| 30 | minor | P has no release workflow or rulesets | §1.2, §1.3 P, §6.4 | **Fixed.** P's `release.yml` (release-please, REST fast-forward of `v1`), its `repo-policy/` (main, `refs/heads/v*`, `refs/tags/v*`) applied with `repo apply --policy-dir` (§1.2, §1.3, §5.6, §6.4). |
| 31 | minor | §2.12 shows the Avunu `error-on-warning = true` as the `recommended` value | §2.12, §8.5 | **Fixed.** Shows `false`, with the Avunu value in the comment. |

Nothing was rejected outright. Partly rejected: #14 (frappe-types 16.5.0 is released, so `typescript` stays on) and #12 (rendered GitLab pipelines deferred). Resolved differently from the suggestion: #4 (semantic compare instead of a pre-commit exclude), #9 (the default branch read once, at creation), #17 (a line match instead of `tryEval`, which doesn't catch TOML errors).
