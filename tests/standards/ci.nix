# N4's checks (docs/app-standards/spec.md §7, "N4: CI"), offline. The cases of policy, ratchet,
# minibench, the CI steps (auto-merge decisions, the release guard, relock), the repo group
# and the rendered callers are py/frappe_nix_tools/tests/test_*.py, which run in the package
# build every check here depends on. What needs GitHub or the network (the gates themselves,
# their check-run names, the mini-bench's sparse clones) is .github/workflows/selftest-ci.yml.
#
#   standards-ci-workflows  actionlint (with shellcheck) and zizmor (strict hash-pin, the
#                           zizmor.yml apps get) on every one of frappe-nix's
#                           .github/workflows (N6: every `uses:` SHA-pinned); the fixture's
#                           callers rendered under recommended, example-org and the gate matrix
#                           (tests/standards/ci_render.py) through both, and each rendered
#                           dependabot.yml through oxfmt --check; fleet-audit.yml is
#                           triggered by workflow_call only; and frappe-nix's own release
#                           wiring (§4.10, §7 N6): pr-policy.yml has one job, `pr-policy`, and
#                           re-runs on a retitle that check.yml ignores; check.yml's VM tests
#                           run on a dispatch only with `vm-tests: true`; app-deps.yml's
#                           relock-plan keeps `contents: read`, only restores the node_modules
#                           cache, and runs its continue-on-error doctypes steps before
#                           relock-patch (§7 N4 "relock-doctypes"); every job that mints a
#                           GitHub App token or reads its key runs on ubuntu-latest, written
#                           so; the split jobs (relock-plan/relock-push,
#                           relock-upgrade-plan/relock-upgrade-push, shots/shots-push) give the
#                           half that runs app or third-party code no write token and no secret
#                           but the read ones, and the hosted half runs no Nix or app code and
#                           checks the patch before it mints the token, with one script that is
#                           run here on accepted and refused patches (flake.lock among the
#                           refused); the push jobs build only on a plan commit the
#                           integration branch holds, with one script run here on an
#                           ancestor and a commit that is not; the hosted jobs compute
#                           whether a rollout input moved themselves, with one jq program
#                           run here against relock.moved_inputs; every job that can hold
#                           the App's key installs frappe-nix-tools hash-locked, with a
#                           pinned uv and no uv cache; the
#                           relock push jobs, and auto-merge on a nix PR with nothing to
#                           relock, run `frappe-nix ci verify-relock` (what the lock files say,
#                           ci/lockcheck.py) before the token is minted, hold the PR before a
#                           push unless it says ok, and enable auto-merge only on ok; every
#                           `gh pr merge --auto` names the head it was decided on
#                           (--head-sha); the weekly relock is checked against the plan's
#                           commit and the integration branch's head; a nix PR's new head
#                           starts with auto-merge off (disarm) and a head no job reached a
#                           verdict on is held (hold), both with GITHUB_TOKEN alone; the key
#                           jobs install frappe-nix-tools only from the frappe-nix-repo input
#                           (Avunu/frappe-nix unless a fork's caller names the fork);
#                           frappe-nix's own dependabot-auto-merge.yml merges
#                           only nix, uv and pre-commit bumps and holds the rest
#                           (github-actions above all); and no reusable workflow from
#                           elsewhere gets the key at a pin that runs it on a self-hosted
#                           runner (only python-devops-nix e76d5d8, #72, is named hosted),
#                           and frappe-nix's own call excludes github_actions and lists the
#                           key jobs' actions under review-dependencies; rollout.yml (§5.8)
#                           runs every job on ubuntu-latest with minimal permissions, its
#                           resolve and plan jobs hold no secret, environment or cache, its
#                           plan runs the app's code as an unprivileged user, and publish (in
#                           the frappe-nix-rollout environment) mints the App token only after
#                           the verify and re-derive steps, from the environment's own
#                           FRAPPE_NIX_ROLLOUT_* variable and secret, which no other job or
#                           workflow names; publish turns an earlier auto-merge off before it
#                           pushes, enables auto-merge only for the commit it pushed
#                           (--head-sha), and turns it off again before it labels
#                           needs-review; report has issues write and no secret
#   standards-ci-gate       `frappe-nix gate lint|typecheck|marketplace` on the fixture with
#                           no GITHUB_* variables (test_gate.py, which the package build
#                           skips: its source is py/frappe_nix_tools alone)
{
  pkgs,
  checks,
  frappeNixTools,
  ...
}:

let
  inherit (pkgs) lib;
  python = pkgs.python314.withPackages (_: [ frappeNixTools ]);
  fixture = ../fixtures/standards-app;
  exampleOrg = ../fixtures/profiles/example-org;
  github = ../../.github;
  tests = ../../py/frappe_nix_tools/tests;
  # ci_render.py copies <checkout>/tests/fixtures/{standards-app,profiles/example-org}.
  checkout = pkgs.linkFarm "standards-ci-checkout" [
    {
      name = "tests/fixtures/standards-app";
      path = fixture;
    }
    {
      name = "tests/fixtures/profiles/example-org";
      path = exampleOrg;
    }
  ];
in
lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
  standards-ci-workflows =
    checks.script "standards-ci-workflows"
      {
        tools = [
          python
          pkgs.git
          pkgs.bash
          pkgs.actionlint
          pkgs.shellcheck
          pkgs.zizmor
          pkgs.oxfmt
          pkgs.jq
        ];
      }
      ''
        set -euo pipefail
        export HOME="$TMPDIR" GIT_CONFIG_GLOBAL=/dev/null
        config=${fixture}/.github/zizmor.yml
        mkdir repo
        cp -r ${github} repo/.github
        chmod -R u+w repo
        cd repo
        git init -q
        actionlint
        zizmor --offline --config "$config" .github/workflows
        python - <<'EOF'
        import yaml

        def load(name):
            doc = yaml.safe_load(open(f".github/workflows/{name}"))
            return doc, doc.get("on", doc.get(True))

        _, triggers = load("fleet-audit.yml")
        assert list(triggers) == ["workflow_call"], list(triggers)

        doc, triggers = load("pr-policy.yml")
        assert list(doc["jobs"]) == ["pr-policy"], list(doc["jobs"])
        job = doc["jobs"]["pr-policy"]
        assert job["name"] == "pr-policy" and "uses" not in job, job
        assert "edited" in triggers["pull_request"]["types"], triggers
        run = " ".join(step.get("run", "") for step in job["steps"])
        assert "uv tool install --python 3.14 ./py/frappe_nix_tools" in run, run
        assert "frappe-nix policy --pr --major-free" in run, run

        doc, triggers = load("check.yml")
        pull = triggers["pull_request"] or {}
        assert "edited" not in pull.get("types", []), pull
        dispatch = triggers["workflow_dispatch"]["inputs"]["vm-tests"]
        assert dispatch["type"] == "boolean" and dispatch["default"] is False, dispatch
        gate = doc["jobs"]["vm-tests"]["if"]
        assert gate == "github.event_name != 'workflow_dispatch' || inputs.vm-tests", gate
        assert {"lint", "flake"} <= set(doc["jobs"]), list(doc["jobs"])

        doc, _ = load("release.yml")
        steps = " ".join(s.get("run", "") for s in doc["jobs"]["dispatch-ci"]["steps"])
        assert "--context lint=check.yml --context pr-policy=pr-policy.yml" in steps, steps

        doc, _ = load("app-deps.yml")
        plan = doc["jobs"]["relock-plan"]
        assert plan["permissions"] == {"contents": "read"}, plan["permissions"]
        uses = [s.get("uses", "") for job in doc["jobs"].values() for s in job.get("steps", [])]
        assert not any(u.startswith("actions/cache@") or u.startswith("actions/cache/save@") for u in uses), uses
        steps = plan["steps"]
        runs = [s.get("run", "") for s in steps]
        patch = next(i for i, r in enumerate(runs) if "frappe-nix ci relock-patch" in r)
        probe = next(i for i, r in enumerate(runs) if "frappe-nix ci relock-doctypes --probe" in r)
        notice = next(i for i, s in enumerate(steps) if "not regenerated" in s.get("run", ""))
        assert probe < notice < patch, (probe, notice, patch)
        for s in steps[probe:notice]:
            assert s.get("continue-on-error") is True, s
        assert any(s.get("uses", "").startswith("actions/cache/restore@") for s in steps[probe:notice]), steps
        assert any("frappe-nix ci relock-doctypes\n" in s.get("run", "") for s in steps[probe:notice]), steps

        # rollout.yml (§5.8, S5): the app's code runs only where no secret and no write token is.
        doc, triggers = load("rollout.yml")
        assert doc["permissions"] == {}, doc["permissions"]
        assert doc["concurrency"] == {"group": "frappe-nix-rollout", "cancel-in-progress": False}, doc["concurrency"]
        wr = triggers["workflow_run"]
        assert wr == {"workflows": ["release"], "types": ["completed"], "branches": ["main"]}, wr
        assert {"schedule", "workflow_dispatch"} <= set(triggers), list(triggers)
        jobs = doc["jobs"]
        assert list(jobs) == ["resolve", "plan", "publish", "report"], list(jobs)
        for name, job in jobs.items():
            assert job["runs-on"] == "ubuntu-latest", (name, job["runs-on"])
        assert "workflow_run.conclusion == 'success'" in jobs["resolve"]["if"], jobs["resolve"]["if"]
        assert jobs["resolve"]["permissions"] == {"contents": "read", "pull-requests": "read"}, jobs["resolve"]
        assert jobs["plan"]["permissions"] == {"contents": "read"}, jobs["plan"]["permissions"]
        assert jobs["publish"]["permissions"] == {"contents": "read"}, jobs["publish"]["permissions"]
        assert jobs["report"]["permissions"] == {"issues": "write"}, jobs["report"]["permissions"]
        caches = ("actions/cache", "DeterminateSystems/magic-nix-cache", "cachix/")
        for name in ("resolve", "plan"):
            job = jobs[name]
            assert "secrets." not in yaml.safe_dump(job), name
            assert "environment" not in job, name
            uses = [s.get("uses", "") for s in job["steps"]]
            assert not any(u.startswith(caches) or u.startswith("actions/create-github-app-token@") for u in uses), uses
        plan_runs = " ".join(s.get("run", "") for s in jobs["plan"]["steps"])
        assert "sudo -u rollout" in plan_runs and "--emit-patch" in plan_runs, plan_runs
        assert any(s.get("env", {}).get("FRAPPE_NIX_YARN_IGNORE_SCRIPTS") == "1" for s in jobs["plan"]["steps"])
        assert jobs["publish"]["environment"] == "frappe-nix-rollout", jobs["publish"].get("environment")
        # The rollout App's key is an environment secret: only a job in that environment names
        # it, rollout.yml never uses the avunu-ci key, and no other workflow names the rollout's.
        import glob
        key = "secrets.FRAPPE_NIX_ROLLOUT_PRIVATE_KEY"
        for name, job in jobs.items():
            text = yaml.safe_dump(job, width=10**6)
            if key in text or "vars.FRAPPE_NIX_ROLLOUT_APP_ID" in text:
                assert job.get("environment") == "frappe-nix-rollout", name
            assert "AVUNU_CI_PRIVATE_KEY" not in text and "AVUNU_CI_APP_ID" not in text, name
        for path in glob.glob(".github/workflows/*.y*ml"):
            if not path.endswith("/rollout.yml"):
                assert "FRAPPE_NIX_ROLLOUT_PRIVATE_KEY" not in open(path).read(), path
        # `frappe-nix repo rollout` reads this env.BOT (rollout_bot) to know the rollout's commits.
        from frappe_nix_tools.commands.repo import rollout as repo_rollout
        assert repo_rollout.ROLLOUT_WORKFLOW == ".github/workflows/rollout.yml"
        assert doc["env"]["BOT"] == "avunu-rollout[bot]", doc["env"]
        steps = jobs["publish"]["steps"]
        verify = next(i for i, s in enumerate(steps) if "ci verify-rollout-patch" in s.get("run", ""))
        recheck = next(i for i, s in enumerate(steps) if "write-tree" in s.get("run", ""))
        token = [i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/create-github-app-token@")]
        assert len(token) == 1 and verify < recheck < token[0], (verify, recheck, token)
        for s in steps[: token[0]]:
            assert "secrets." not in yaml.safe_dump(s), s
            assert "app-token" not in yaml.safe_dump(s), s
        mint = steps[token[0]]
        assert "env.DRY_RUN != 'true'" in mint["if"] and "steps.recheck.outputs.go" in mint["if"], mint["if"]
        assert mint["with"]["owner"] == "Avunu" and "matrix.name" in mint["with"]["repositories"], mint["with"]
        assert mint["with"]["private-key"] == "''${{ " + key + " }}", mint["with"]
        assert mint["with"]["client-id"] == "''${{ vars.FRAPPE_NIX_ROLLOUT_APP_ID }}", mint["with"]
        # needs-review never keeps an auto-merge an earlier run enabled: publish turns it off
        # (disarm, which fails the step when it stays on) before every push and before keeping
        # a head that cannot end pr-open, and again after enable-automerge or the head check
        # failed, before the label; enable-automerge names the commit it pushed (--head-sha);
        # publish drops the label.
        act = next(s["run"] for s in steps if "--add-label needs-review" in s.get("run", ""))
        off = "gh pr merge --disable-auto"
        assert act.count(off) == 1 and act.index("disarm() {") < act.index(off), act
        gate = act.index('if [ -n "$pr_url" ] && { [ "$can_merge" = false ] || [ "$same_tree" = false ]; }; then\n')
        before_push = act.index('disarm "$pr_url"', gate)
        assert act.index('[ "$AUTO_MERGE" = true ] && [ "$allowed" = true ]') < gate < before_push < act.index(" push -q "), act
        label = act.index("--add-label needs-review")
        assert act.rindex('disarm "$pr_url"', 0, label) > act.index(" push -q "), act
        enable = act.index("ci enable-automerge")
        assert act.index('want="''${commit:-$observed}"') < enable, act
        assert '--merge-method "$method" --head-sha "$want")' in act[enable:act.index("\n", enable)], act
        assert "--remove-label needs-review" in act and "autoMergeRequest" in act, act
        # A branch its merged or closed PR left behind is replaced, in resolve and in publish.
        resolve = " ".join(s.get("run", "") for s in jobs["resolve"]["steps"])
        assert '.get("sha") == head' in resolve and "if others and not left" in resolve and "state=all" in act, (resolve, act)
        report = jobs["report"]
        assert "environment" not in report and "secrets." not in yaml.safe_dump(report), report
        assert not any(s.get("uses", "").startswith("actions/create-github-app-token@") for s in report["steps"])

        import glob, json, os, re, subprocess, tempfile

        SECRET = re.compile(r"secrets\.([A-Za-z0-9_]+)")
        APP_KEY = ("APP_PRIVATE_KEY", "AVUNU_CI_PRIVATE_KEY", "create-github-app-token")

        def secrets_of(job):
            return set(SECRET.findall(json.dumps(job)))

        def on_of(name):
            return load(name)[1]

        # Every job that mints a GitHub App token or reads the App's key runs on ubuntu-latest,
        # written as such: never a caller's `runs-on`, never a shared self-hosted runner. A job
        # that calls a reusable workflow (`uses:`) only passes the key on, and that workflow's
        # jobs are checked where they live (python-devops-nix, for dependabot-auto-merge.yml).
        holders = {}
        for path in sorted(glob.glob(".github/workflows/*.yml")):
            doc = yaml.safe_load(open(path))
            for name, job in doc["jobs"].items():
                if "uses" not in job and any(k in json.dumps(job) for k in APP_KEY):
                    assert job["runs-on"] == "ubuntu-latest", (path, name, job["runs-on"])
                    holders.setdefault(os.path.basename(path), set()).add(name)
        assert holders == {
            "app-deps.yml": {"relock-push", "auto-merge", "update-branches", "relock-upgrade-push"},
            "app-nightly.yml": {"shots-push"},
            "app-release.yml": {"release-please", "fast-forward"},
            "release.yml": {"release-please", "fixture", "fast-forward"},
            "rollout.yml": {"publish"},
        }, holders
        doc, _ = load("app-deps.yml")
        assert doc["jobs"]["sweep"]["runs-on"] == "ubuntu-latest", doc["jobs"]["sweep"]

        # A job that calls a reusable workflow from elsewhere passes the App's key only to a pin
        # whose jobs that hold it run on hosted runners, and never through `secrets: inherit`.
        # python-devops-nix 0c9a3a0 ran its dependabot-* jobs on ["self-hosted","nixos"];
        # python-devops-nix#72 (merged as e76d5d8) runs every job of both on ubuntu-latest, and
        # its key job only after `policy` and `diff`, neither of which holds the key. A pin is
        # named here only once its workflows have been read at that commit.
        HOSTED_PINS = {"e76d5d850b35e4d0abc58fae6617713764f3683a"}
        key_pins = set()
        for path in sorted(glob.glob(".github/workflows/*.yml")):
            for name, job in yaml.safe_load(open(path))["jobs"].items():
                if "uses" not in job:
                    continue
                assert job.get("secrets") != "inherit", (path, name)
                if any(k in json.dumps(job.get("secrets", {})) for k in APP_KEY):
                    pin = job["uses"].rsplit("@", 1)[1]
                    assert pin in HOSTED_PINS and not pin.startswith("0c9a3a0"), (path, name, pin)
                    key_pins.add((os.path.basename(path), name, job["uses"].split("@")[0], pin))
        assert key_pins == {
            ("dependabot-auto-merge.yml", "auto-merge",
             "Avunu/python-devops-nix/.github/workflows/dependabot-auto-merge.yml", next(iter(HOSTED_PINS))),
            ("dependabot-auto-merge.yml", "update-branches",
             "Avunu/python-devops-nix/.github/workflows/dependabot-update-branches.yml", next(iter(HOSTED_PINS))),
        }, key_pins

        # Every job that can hold the App's key installs frappe-nix-tools with nothing resolved
        # at run time: uv pinned and without the Actions cache, every dependency from the
        # committed uv.lock by hash and as a wheel, frappe-nix-tools built offline against a
        # hashed flit_core, and (from an app's lock) only Avunu/frappe-nix at a full revision.
        installers = {
            "app-deps.yml": {"relock-push", "auto-merge", "update-branches", "relock-upgrade-push"},
            "app-nightly.yml": {"shots-push"},
            "app-release.yml": {"fast-forward"},
            "release.yml": {"fixture", "fast-forward"},
        }
        tails = set()
        for wf, names in holders.items():
            doc, _ = load(wf)
            for name in names:
                job = doc["jobs"][name]
                installs = []
                for s in job["steps"]:
                    if s.get("uses", "").startswith("astral-sh/setup-uv@"):
                        w = s.get("with", {})
                        assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(w.get("version", ""))), (wf, name, w)
                        assert w.get("enable-cache") == "false", (wf, name, w)
                    run = s.get("run", "")
                    assert not re.search(r"\buv (tool|run)\b|\buvx\b|pip install(?! -q --python)", run), (wf, name, run)
                    if "uv --no-config pip install" in run:
                        installs.append(s)
                assert len(installs) == (name in installers.get(wf, ())), (wf, name, len(installs))
                for s in installs:
                    run = s["run"]
                    assert re.fullmatch(r"flit-core==[0-9.]+ --hash=sha256:[0-9a-f]{64}", s["env"]["FLIT_CORE"]), s["env"]
                    for want in ("uv --no-config export -q --frozen --no-dev --no-emit-project",
                                 "--require-hashes --no-build", "--offline --no-deps --no-build-isolation"):
                        assert want in run, (wf, name, want)
                    if wf.startswith("app-"):
                        assert '[ "''${src,,}" != "''${ALLOWED,,}" ]' in run, (wf, name)
                        assert '^[0-9a-f]{40}$' in run and '"https://github.com/$ALLOWED.git" "$rev"' in run, (wf, name)
                        assert s["env"]["ALLOWED"] == "''${{ inputs.frappe-nix-repo }}", (wf, name, s["env"])
                        frappe_nix_repo = on_of(wf)["workflow_call"]["inputs"]["frappe-nix-repo"]
                        assert frappe_nix_repo["default"] == "Avunu/frappe-nix", (wf, frappe_nix_repo)
                    tails.add(run[run.index("uv --no-config export"):])
        assert len(tails) == 1, "one install, after the source is chosen"

        # The split jobs: the half that runs the app or third-party code has no write token, no
        # App and only the read secrets; the half with the token runs after it on ubuntu-latest,
        # downloads its patch and nothing else, runs no Nix and no app, and checks the patch with
        # one script, shared by all three, before the App's token is minted.
        splits = [
            ("app-deps.yml", "relock-plan", "relock-push", "relock-patch"),
            ("app-deps.yml", "relock-upgrade-plan", "relock-upgrade-push", "relock-upgrade-patch"),
            ("app-nightly.yml", "shots", "shots-push", "screenshots-patch"),
        ]
        allows, scripts = {}, set()
        for wf, plan_name, push_name, artifact in splits:
            doc, _ = load(wf)
            plan, push = doc["jobs"][plan_name], doc["jobs"][push_name]
            assert plan["permissions"] == {"contents": "read"}, (plan_name, plan["permissions"])
            assert secrets_of(plan) <= {"FETCH_TOKEN", "NIX_CACHE_READ_TOKEN"}, (plan_name, secrets_of(plan))
            assert not any(k in json.dumps(plan) for k in APP_KEY), plan_name
            assert push["needs"] == plan_name and push["runs-on"] == "ubuntu-latest", push_name
            assert secrets_of(push) <= {"APP_ID", "APP_PRIVATE_KEY"}, (push_name, secrets_of(push))
            steps = push["steps"]
            for s in steps:
                action = s.get("uses", "").split("@")[0]
                assert action in ("actions/checkout", "actions/download-artifact", "astral-sh/setup-uv",
                                  "actions/create-github-app-token", ""), (push_name, action)
                for line in s.get("run", "").splitlines():
                    command = (line.split() or [""])[0]
                    assert command not in ("nix", "attic", "frappe-shots", "yarn", "npm", "node", "bench"), (push_name, line)
                    assert "relock-doctypes" not in line and "nix develop" not in line, (push_name, line)
            downloads = [s for s in steps if s.get("uses", "").startswith("actions/download-artifact@")]
            assert [s["with"]["name"] for s in downloads] == [artifact], (push_name, downloads)
            check = next(i for i, s in enumerate(steps) if s.get("name", "").startswith("Check and apply the"))
            token = next(i for i, s in enumerate(steps) if "create-github-app-token" in s.get("uses", ""))
            assert check < token, (push_name, check, token)
            allows[push_name] = steps[check]["env"]["ALLOW"]
            scripts.add(steps[check]["run"])
        assert len(scripts) == 1, "the push jobs check their patch with one script"
        script = scripts.pop()

        # The push jobs that build on the plan job's commit check that the integration branch
        # holds it, with one script, before anything is applied or minted.
        ancestors = set()
        for wf, plan_name, push_name in (("app-deps.yml", "relock-upgrade-plan", "relock-upgrade-push"),
                                         ("app-nightly.yml", "shots", "shots-push")):
            doc, _ = load(wf)
            steps = doc["jobs"][push_name]["steps"]
            assert steps[0]["with"]["ref"] == "''${{ inputs.integration-branch }}", steps[0]
            assert steps[0]["with"]["fetch-depth"] == 0, steps[0]
            assert steps[1]["env"]["SHA"] == f"''${{{{ needs.{plan_name}.outputs.sha }}}}", steps[1]
            ancestors.add(steps[1]["run"])
            assert not any("outputs.sha" in json.dumps(s) for s in steps[2:]), push_name
            last = steps[-1]["run"]
            assert '(cd "$RUNNER_TEMP/base" && frappe-nix ci enable-automerge' in last, (push_name, last)
        assert len(ancestors) == 1, "the push jobs check the plan's commit with one script"
        ancestor = ancestors.pop()

        # relock-push and auto-merge compute whether a rollout input moved with one step, and
        # neither reads relock-plan's `moved`.
        doc, _ = load("app-deps.yml")
        moved_runs = set()
        for name in ("relock-push", "auto-merge"):
            job = doc["jobs"][name]
            ids = [s.get("id") for s in job["steps"]]
            moved_runs.add(job["steps"][ids.index("moved")]["run"])
            assert "relock-plan.outputs.moved" not in json.dumps(job), name
            token = next(i for i, s in enumerate(job["steps"]) if "create-github-app-token" in s.get("uses", ""))
            assert ids.index("moved") < token, name
        assert len(moved_runs) == 1, "one moved step"
        push = doc["jobs"]["relock-push"]["steps"]
        refuse = next(i for i, s in enumerate(push) if s.get("if") == "steps.moved.outputs.moved != '''")
        assert refuse < next(i for i, s in enumerate(push) if s.get("name", "").startswith("Check and apply")), push
        decide = next(s for s in doc["jobs"]["auto-merge"]["steps"] if s.get("name") == "The decision table")
        assert decide["env"]["MOVED"] == "''${{ steps.moved.outputs.moved }}", decide["env"]
        program = re.search(r"--slurpfile head \S+ '\n(.*?)'\)\"\n", moved_runs.pop(), re.S).group(1)

        # What the lock files say is checked in the job, before the token, by the integration
        # branch's frappe-nix-tools: relock-push against the merge-base the moved step reads,
        # relock-upgrade-push against the plan's commit, auto-merge (nix, nothing to relock)
        # against the merge-base. Not ok: the PR is held (auto-merge off) before any push, and
        # auto-merge is enabled only on ok.
        for name in ("relock-push", "relock-upgrade-push", "auto-merge"):
            steps = doc["jobs"][name]["steps"]
            ids = [s.get("id") for s in steps]
            locks = steps[ids.index("locks")]
            assert locks["run"].startswith("frappe-nix ci verify-relock --base "), (name, locks)
            assert ids.index("locks") < ids.index("app-token"), name
        push = doc["jobs"]["relock-push"]["steps"]
        ids = [s.get("id") for s in push]
        assert push[0]["with"]["ref"] == "''${{ github.event.pull_request.head.sha }}", push[0]
        assert push[ids.index("locks")]["env"]["MERGE_BASE"] == "''${{ steps.moved.outputs.merge-base }}"
        hold = next(i for i, s in enumerate(push) if "frappe-nix ci hold" in s.get("run", ""))
        assert push[hold]["if"] == "steps.locks.outputs.decision != 'ok'", push[hold]
        assert ids.index("app-token") < hold < ids.index("push"), (ids, hold)
        assert '"HEAD:refs/heads/$HEAD_REF"' in push[ids.index("push")]["run"] and "--force" not in push[ids.index("push")]["run"]
        merge = next(s for s in push if "enable-automerge" in s.get("run", ""))
        assert merge["if"] == "inputs.auto-merge && steps.locks.outputs.decision == 'ok'", merge
        assert merge["env"]["SHA"] == "''${{ steps.push.outputs.sha }}", merge
        upgrade_steps = doc["jobs"]["relock-upgrade-push"]["steps"]
        upgrade_locks = next(s for s in upgrade_steps if s.get("id") == "locks")["run"]
        assert upgrade_locks == 'frappe-nix ci verify-relock --base "$(git rev-parse HEAD)" --head "$(git write-tree)"', upgrade_locks
        # The plan job names the commit it relocked: checked against the integration branch's head
        # too, and only ok in both is ok.
        upgrade_head = next(s for s in upgrade_steps if s.get("id") == "locks-head")["run"]
        assert upgrade_head == 'frappe-nix ci verify-relock --base "$(git -C "$RUNNER_TEMP/base" rev-parse HEAD)" --head "$(git write-tree)"', upgrade_head
        upgrade_ids = [s.get("id") for s in upgrade_steps]
        assert upgrade_ids.index("locks-head") < upgrade_ids.index("app-token"), upgrade_ids
        want = "''${{ steps.locks.outputs.decision == 'ok' && steps.locks-head.outputs.decision == 'ok' && 'ok' || 'needs-review' }}"
        assert upgrade_steps[-1]["env"]["LOCKS"] == want, upgrade_steps[-1]["env"]
        # GitHub keeps auto-merge on across a push: every new head of a nix PR starts with it off
        # (disarm), and a head no job reached a verdict on is held (hold), with GITHUB_TOKEN alone.
        for name in ("disarm", "hold"):
            job = doc["jobs"][name]
            assert job["runs-on"] == "ubuntu-latest" and not secrets_of(job), name
            assert set(job["permissions"]) == {"contents", "pull-requests"}, (name, job["permissions"])
            assert [list(s) for s in job["steps"]] == [["name", "env", "run"]], (name, job["steps"])
            assert job["steps"][0]["env"]["GH_TOKEN"] == "''${{ github.token }}", name
            assert "gh pr merge --disable-auto" in job["steps"][0]["run"], name
        assert "startsWith(github.head_ref, 'dependabot/nix/')" in doc["jobs"]["disarm"]["if"], doc["jobs"]["disarm"]
        assert not any(doc["jobs"][n].get("needs") for n in ("disarm", "relock-plan")), "disarm runs at once"
        hold_job = doc["jobs"]["hold"]
        assert hold_job["needs"] == ["disarm", "relock-plan", "relock-push", "auto-merge"], hold_job["needs"]
        hold_if = " ".join(hold_job["if"].split())
        for want in ("always()", "contains(needs.*.result, 'failure')", "contains(needs.*.result, 'cancelled')",
                     "needs.auto-merge.outputs.result == 'wait' && needs.relock-push.result != 'success'"):
            assert want in hold_if, (want, hold_if)
        assert hold_job["steps"][0]["run"].index("--disable-auto") < hold_job["steps"][0]["run"].index("--add-label needs-review")
        assert doc["jobs"]["auto-merge"]["outputs"]["result"] == "''${{ steps.decide.outputs.result }}"
        upgrade = upgrade_steps[-1]["run"]
        assert upgrade.index("frappe-nix ci hold") < upgrade.index("git push --force"), upgrade
        assert 'if [ "$LOCKS" != ok ]; then\n  frappe-nix ci hold' in upgrade, upgrade
        auto = doc["jobs"]["auto-merge"]["steps"]
        ids = [s.get("id") for s in auto]
        assert auto[ids.index("locks")]["working-directory"] == ".head", auto[ids.index("locks")]
        assert "needs.relock-plan.outputs.empty == 'true'" in auto[ids.index("locks")]["if"], auto[ids.index("locks")]
        assert decide["env"]["LOCKS"] == "''${{ steps.locks.outputs.decision }}", decide["env"]
        assert '--relock empty --locks "$LOCKS"' in decide["run"] and '--head-sha "$HEAD_SHA"' in decide["run"], decide["run"]

        # Every `gh pr merge --auto` a workflow asks for names the head it was decided on.
        for path in sorted(glob.glob(".github/workflows/*.yml")):
            jobs = yaml.safe_load(open(path))["jobs"].values()
            runs = [s.get("run", "") for job in jobs for s in job.get("steps", [])]
            for line in "\n".join(runs).splitlines():
                if re.search(r"frappe-nix ci (enable-automerge|automerge)\b", line):
                    assert "--head-sha" in line or line.strip() == 'frappe-nix ci automerge "''${args[@]}"', (path, line)
                assert not re.search(r"gh pr merge[^\n]*--auto", line) or "--match-head-commit" in line, (path, line)

        # frappe-nix's own Dependabot PRs: only nix, uv and pre-commit bumps auto-merge; the
        # rest (github-actions above all: they move what runs next to the App's key) are held.
        own = yaml.safe_load(open(".github/workflows/dependabot-auto-merge.yml"))["jobs"]
        merging = " ".join(own["auto-merge"]["if"].split())
        allowed = "(startsWith(github.head_ref, 'dependabot/nix/') || startsWith(github.head_ref, 'dependabot/uv/') || startsWith(github.head_ref, 'dependabot/pre_commit/'))"
        assert merging.endswith(f"&& {allowed}"), merging
        assert "github_actions" not in merging, merging
        held = " ".join(own["hold"]["if"].split())
        assert held.endswith(f"&& !{allowed}"), held
        assert own["hold"]["runs-on"] == "ubuntu-latest" and not secrets_of(own["hold"]) and "uses" not in json.dumps(own["hold"]["steps"])
        hold_run = own["hold"]["steps"][0]["run"]
        assert hold_run.index("gh pr merge --disable-auto") < hold_run.index("--add-label needs-review"), hold_run
        # The reusable workflow holds what the `if:` lets through but changes what runs with the
        # key: github_actions is excluded (its `diff` then holds a changed workflow or action
        # file), and every action a key job runs here or in an app is a review-dependency.
        assert own["auto-merge"]["with"]["exclude-ecosystems"].split() == ["github_actions"], own["auto-merge"]
        reviewed = set(own["auto-merge"]["with"]["review-dependencies"].split())
        key_actions = {"Avunu/python-devops-nix"}
        for wf, names in holders.items():
            jobs = yaml.safe_load(open(f".github/workflows/{wf}"))["jobs"]
            for name in names:
                key_actions |= {s["uses"].split("@")[0] for s in jobs[name]["steps"] if "uses" in s}
        assert key_actions <= reviewed, key_actions - reviewed
        for name in ("auto-merge", "update-branches"):
            assert set(own[name]["secrets"]) == {"APP_ID", "APP_PRIVATE_KEY"}, own[name]

        from frappe_nix_tools.ci import relock as relock_mod

        def jq_moved(base, head):
            with tempfile.TemporaryDirectory() as tmp:
                b, h = os.path.join(tmp, "b"), os.path.join(tmp, "h")
                open(b, "w").write(json.dumps(base))
                open(h, "w").write(json.dumps(head))
                out = subprocess.run(["jq", "-rn", "--slurpfile", "base", b, "--slurpfile", "head", h, program],
                                     check=True, capture_output=True, text=True).stdout.strip()
            return [x for x in out.split(",") if x]

        def lock(fn="a", profile="p", follows=False, extra="x", owner="Avunu"):
            nodes = {"root": {"inputs": {"frappe-nix": ["frappe"] if follows else "frappe-nix",
                                         "standards-profile": "standards-profile", "frappe": "frappe"}},
                     "frappe-nix": {"locked": {"owner": owner, "repo": "frappe-nix", "rev": fn}},
                     "standards-profile": {"locked": {"rev": profile}},
                     "frappe": {"locked": {"rev": extra}}}
            return {"nodes": nodes, "root": "root", "version": 7}

        cases = [
            (lock(), lock()),
            (lock(), lock(extra="y")),
            (lock(), lock(fn="b")),
            (lock(), lock(fn="b", profile="q")),
            (lock(), lock(profile="q")),
            (lock(), lock(follows=True)),
            (lock(), lock(owner="attacker")),
            ({"nodes": {"root": {}}, "root": "root"}, lock()),
        ]
        for base, head in cases:
            want = relock_mod.moved_inputs(json.dumps(base), json.dumps(head))
            assert jq_moved(base, head) == want, (base, head, want)
        assert jq_moved(lock(), lock(fn="b", profile="q")) == ["frappe-nix", "standards-profile"]
        assert jq_moved(lock(), lock(owner="attacker")) == ["frappe-nix"]

        def g(cwd, *args):
            return subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                  check=True, capture_output=True, text=True).stdout

        def write(root, path, data, mode=0o644):
            full = os.path.join(root, path)
            os.makedirs(os.path.dirname(full) or root, exist_ok=True)
            open(full, "wb").write(data)
            os.chmod(full, mode)

        BASE = {"flake.lock": b"{}\n", "nix/uv.lock": b"version = 1\n", "types/doctypes.d.ts": b"// x\n",
                "pyproject.toml": b"[project]\n", ".github/workflows/ci.yml": b"name: ci\n",
                "docs/screenshots/manifest.json": b"[]\n"}

        def check_patch(allow, change, diff_args=(), text=None):
            """The push jobs' check on the patch `change(repo)` stages (or `text`), in a clean clone."""
            with tempfile.TemporaryDirectory() as tmp:
                src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
                os.makedirs(src)
                g(src, "init", "-q")
                for path, data in BASE.items():
                    write(src, path, data)
                g(src, "add", "-A")
                g(src, "commit", "-qm", "base")
                g(tmp, "clone", "-q", src, dst)
                if text is None:
                    change(src)
                    g(src, "add", "-A")
                    text = g(src, "diff", "--cached", "--binary", *diff_args)
                patch = os.path.join(tmp, "x.patch")
                open(patch, "w").write(text)
                env = {**os.environ, "PATCH": patch, "ALLOW": allow, "RUNNER_TEMP": tmp}
                return subprocess.run(["bash", "-c", script], cwd=dst, env=env).returncode

        def relock_ok(r):
            write(r, "nix/node-locks/erpnext/yarn.lock", b"# yarn lockfile v1\n\x00\n")
            write(r, "types/doctypes.d.ts", b"// y\n")
            os.remove(os.path.join(r, "nix/uv.lock"))

        def shots_ok(r):
            write(r, "docs/screenshots/todo-light.webp", b"RIFF\x00\x00WEBP")
            write(r, "docs/screenshots/manifest.json", b"[{}]\n")

        def symlink(r):
            os.makedirs(os.path.join(r, "nix/node-locks"))
            os.symlink("/etc/passwd", os.path.join(r, "nix/node-locks/x"))

        relock, upgrade, shots = allows["relock-push"], allows["relock-upgrade-push"], allows["shots-push"]
        assert check_patch(relock, relock_ok) == 0
        assert check_patch(upgrade, lambda r: write(r, "nix/uv.lock", b"version = 2\n")) == 0
        assert check_patch(shots, shots_ok) == 0
        refused = {
            "workflow": (relock, lambda r: write(r, ".github/workflows/ci.yml", b"name: x\n")),
            "flake.lock in a relock": (relock, lambda r: write(r, "flake.lock", b"{\"v\": 2}\n")),
            "flake.lock in an upgrade": (upgrade, lambda r: write(r, "flake.lock", b"{\"v\": 2}\n")),
            "dotgithub": (relock, lambda r: write(r, "nix/node-locks/.github/x", b"x\n")),
            "outside the allowlist": (relock, lambda r: write(r, "pyproject.toml", b"[project]\nx = 1\n")),
            "doctypes in an upgrade": (upgrade, lambda r: write(r, "types/doctypes.d.ts", b"// y\n")),
            "a relock in the shots": (shots, relock_ok),
            "a script in the shots": (shots, lambda r: write(r, "docs/screenshots/x.sh", b"#!/bin/sh\n")),
            "executable": (relock, lambda r: write(r, "nix/node-locks/x/yarn.lock", b"x\n", 0o755)),
            "mode change": (relock, lambda r: os.chmod(os.path.join(r, "nix/uv.lock"), 0o755)),
            "symlink": (relock, symlink),
            "nothing": (relock, lambda r: None),
        }
        for why, (allow, change) in refused.items():
            assert check_patch(allow, change) != 0, why
        def rename(r):
            os.makedirs(os.path.join(r, "nix/node-locks"))
            os.rename(os.path.join(r, "nix/uv.lock"), os.path.join(r, "nix/node-locks/uv.lock"))

        assert check_patch(relock, rename, ("-M",)) != 0, "rename"
        assert check_patch(relock, rename, ("--no-renames",)) == 0, "a move without renames"
        dotdot = "diff --git a/../x b/../x\nnew file mode 100644\n--- /dev/null\n+++ b/../x\n@@ -0,0 +1 @@\n+x\n"
        assert check_patch(relock, None, text=dotdot) != 0, ".."

        def check_ancestor(pick):
            """The push jobs' check on the plan's commit `pick(shas)`, in a clone of the integration branch."""
            with tempfile.TemporaryDirectory() as tmp:
                src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
                os.makedirs(src)
                g(src, "init", "-q", "-b", "develop")
                shas = {}
                for name in ("one", "two"):
                    write(src, "f", name.encode())
                    g(src, "add", "-A")
                    g(src, "commit", "-qm", name)
                    shas[name] = g(src, "rev-parse", "HEAD").strip()
                g(src, "switch", "-qc", "side", shas["one"])
                write(src, "f", b"side")
                g(src, "commit", "-qam", "side")
                shas["side"] = g(src, "rev-parse", "HEAD").strip()
                g(src, "switch", "-q", "develop")
                g(tmp, "clone", "-q", "--no-single-branch", src, dst)
                g(dst, "cat-file", "-e", shas["side"])  # fetchable, as a fork PR's head is
                env = {**os.environ, "SHA": pick(shas), "RUNNER_TEMP": tmp}
                code = subprocess.run(["bash", "-c", ancestor], cwd=dst, env=env).returncode
                if code == 0:
                    assert g(dst, "rev-parse", "HEAD").strip() == pick(shas)
                    assert g(os.path.join(tmp, "base"), "rev-parse", "HEAD").strip() == shas["two"]
                return code

        assert check_ancestor(lambda s: s["one"]) == 0, "an ancestor"
        assert check_ancestor(lambda s: s["two"]) == 0, "the head"
        assert check_ancestor(lambda s: s["side"]) != 0, "a commit the branch does not hold"
        assert check_ancestor(lambda s: s["one"][:12]) != 0, "a short SHA"
        assert check_ancestor(lambda s: "HEAD") != 0, "a ref"
        assert check_ancestor(lambda s: s["one"] + "\n") != 0, "a newline"
        EOF
        cd "$TMPDIR"
        cp -rL ${checkout} checkout
        chmod -R u+w checkout
        python ${./ci_render.py} checkout render
        for dir in render/*/; do
          echo "== $dir"
          (
            cd "$dir"
            actionlint -- .github/workflows/*.yml
            files=(.github/workflows)
            if [ -f .github/dependabot.yml ]; then
              files+=(.github/dependabot.yml)
              # §2.17: the rendered file is oxfmt's output at the app's width.
              printf '{"useTabs": true, "printWidth": 110}\n' > .oxfmtrc.json
              oxfmt --check .github/dependabot.yml
            fi
            zizmor --offline --config .github/zizmor.yml "''${files[@]}"
          )
        done
        touch "$out"
      '';

  standards-ci-gate =
    checks.script "standards-ci-gate"
      {
        tools = [
          python
          pkgs.git
          pkgs.jq
        ];
      }
      ''
        set -euo pipefail
        export HOME="$TMPDIR" GIT_CONFIG_GLOBAL=/dev/null FRAPPE_NIX_FIXTURE=${fixture}
        cp -r ${tests} tests
        chmod -R u+w tests
        cd tests
        python -m unittest -v test_gate test_nightly
        touch "$out"
      '';
}
