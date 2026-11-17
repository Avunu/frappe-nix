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
#                           run here on accepted and refused patches
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

        import glob, json, os, re, subprocess, tempfile

        SECRET = re.compile(r"secrets\.([A-Za-z0-9_]+)")
        APP_KEY = ("APP_PRIVATE_KEY", "AVUNU_CI_PRIVATE_KEY", "create-github-app-token")

        def secrets_of(job):
            return set(SECRET.findall(json.dumps(job)))

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
        }, holders
        doc, _ = load("app-deps.yml")
        assert doc["jobs"]["sweep"]["runs-on"] == "ubuntu-latest", doc["jobs"]["sweep"]

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
            write(r, "flake.lock", b"{\"v\": 2}\n")
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
            "dotgithub": (relock, lambda r: write(r, "nix/node-locks/.github/x", b"x\n")),
            "outside the allowlist": (relock, lambda r: write(r, "pyproject.toml", b"[project]\nx = 1\n")),
            "doctypes in an upgrade": (upgrade, lambda r: write(r, "types/doctypes.d.ts", b"// y\n")),
            "a relock in the shots": (shots, relock_ok),
            "a script in the shots": (shots, lambda r: write(r, "docs/screenshots/x.sh", b"#!/bin/sh\n")),
            "executable": (relock, lambda r: write(r, "nix/node-locks/x/yarn.lock", b"x\n", 0o755)),
            "mode change": (relock, lambda r: os.chmod(os.path.join(r, "flake.lock"), 0o755)),
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
