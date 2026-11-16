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
#                           relock-patch (§7 N4 "relock-doctypes")
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
        # The actions kind (classify) runs a frappe-nix revision a PR chose: it gets no secret, and
        # the plan never holds the App key (app-deps-apply.yml does, and only applies the patch).
        classify = doc["jobs"]["classify"]
        assert classify["permissions"] == {"contents": "read"}, classify["permissions"]
        assert "secrets." not in str(classify), classify
        assert plan["needs"] == "classify", plan.get("needs")
        cache = next(s for s in steps if s.get("id") == "cache")
        assert "classify.outputs.kind == 'nix'" in cache["env"]["FETCH_TOKEN"], cache["env"]
        assert "APP_PRIVATE_KEY" not in str(plan) and "secrets.APP_ID" not in str(plan), "plan holds the App key"
        # The plan's push moved to the apply, which never checks out the PR's code.
        assert "relock-push" not in doc["jobs"], list(doc["jobs"])
        assert "create-github-app-token" not in str(doc["jobs"]["auto-merge"]), "auto-merge holds an App token"
        upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@"))
        assert "relock-meta.json" in upload["with"]["path"], upload
        apply_doc, apply_triggers = load("app-deps-apply.yml")
        assert list(apply_triggers) == ["workflow_call"], list(apply_triggers)
        apply = apply_doc["jobs"]["apply"]
        assert apply["permissions"]["contents"] == "write", apply["permissions"]
        steps = apply["steps"]
        for s in steps:
            if s.get("uses", "").startswith("actions/checkout@"):
                assert s["with"]["ref"] == "''${{ inputs.integration-branch }}", s
        check = next(i for i, s in enumerate(steps) if "git apply --cached --check" in s.get("run", ""))
        token = next(i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/create-github-app-token@"))
        push = next(i for i, s in enumerate(steps) if s.get("id") == "push")
        assert check < token < push, (check, token, push)
        assert "--force-with-lease=refs/heads/" in next(s["run"] for s in steps if s.get("id") == "push"), steps
        # The plan's caller passes no secret; the trusted events' caller passes the App and the caches.
        with open("${fixture}/.github/workflows/deps.yml") as f:
            callers = yaml.safe_load(f)
        pull = callers["jobs"]["deps"]
        assert pull["if"] == "github.event_name == 'pull_request'" and "secrets" not in pull, pull
        assert "workflow_run" in str(yaml.safe_load(open("${fixture}/.github/workflows/deps-apply.yml"))), "no apply caller"
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
