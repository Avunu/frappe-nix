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
#                           relock-patch (§7 N4 "relock-doctypes"); rollout.yml (§5.8) runs
#                           every job on ubuntu-latest with minimal permissions, its resolve
#                           and plan jobs hold no secret, environment or cache, its plan runs
#                           the app's code as an unprivileged user, and publish (in the
#                           frappe-nix-rollout environment) mints the App token only after
#                           the verify and re-derive steps, from the environment's own
#                           FRAPPE_NIX_ROLLOUT_* variable and secret, which no other job or
#                           workflow names; publish turns an earlier auto-merge off before
#                           it pushes a head that cannot auto-merge, and before it labels
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
          pkgs.actionlint
          pkgs.shellcheck
          pkgs.zizmor
          pkgs.oxfmt
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
        # needs-review never keeps an auto-merge an earlier run enabled: a run that cannot end
        # pr-open turns it off before it pushes, and again after enable-automerge or the head
        # check failed, before the label; publish drops the label.
        act = next(s["run"] for s in steps if "--add-label needs-review" in s.get("run", ""))
        off = "gh pr merge --disable-auto"
        assert act.count(off) == 2, act
        gate = act.index('if [ -n "$pr_url" ] && [ "$can_merge" = false ]; then')
        assert gate < act.index(off) < act.index(" push -q "), act
        assert act.rindex(off) < act.index("--add-label needs-review"), act
        assert act.index('[ "$AUTO_MERGE" = true ] && [ "$allowed" = true ]') < gate, act
        assert "--remove-label needs-review" in act and "autoMergeRequest" in act, act
        # A branch its merged or closed PR left behind is replaced, in resolve and in publish.
        resolve = " ".join(s.get("run", "") for s in jobs["resolve"]["steps"])
        assert '.get("sha") == head' in resolve and "if others and not left" in resolve and "state=all" in act, (resolve, act)
        report = jobs["report"]
        assert "environment" not in report and "secrets." not in yaml.safe_dump(report), report
        assert not any(s.get("uses", "").startswith("actions/create-github-app-token@") for s in report["steps"])
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
