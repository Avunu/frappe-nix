# N4's checks (docs/app-standards/spec.md §7, "N4: CI"), offline. The cases of policy, ratchet,
# minibench, the CI steps (auto-merge decisions, the release guard, relock), the repo group
# and the rendered callers are py/frappe_nix_tools/tests/test_*.py, which run in the package
# build every check here depends on. What needs GitHub or the network (the gates themselves,
# their check-run names, the mini-bench's sparse clones) is .github/workflows/selftest-ci.yml.
#
#   standards-ci-workflows  actionlint (with shellcheck) on frappe-nix's .github/workflows,
#                           and zizmor (strict hash-pin, the zizmor.yml apps get) on the
#                           reusable app-*.yml, fleet-audit.yml and selftest-ci.yml; the
#                           fixture's callers rendered under recommended, example-org and the
#                           gate matrix (tests/standards/ci_render.py) through both; and
#                           fleet-audit.yml is triggered by workflow_call only
#   standards-ci-gate       `frappe-nix gate lint|typecheck|marketplace` on the fixture with
#                           no GITHUB_* variables (test_gate.py, which the package build
#                           skips: its source is py/frappe_nix_tools alone)
{
  pkgs,
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
    pkgs.runCommand "standards-ci-workflows-check"
      {
        nativeBuildInputs = [
          python
          pkgs.git
          pkgs.actionlint
          pkgs.shellcheck
          pkgs.zizmor
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
        zizmor --offline --config "$config" .github/workflows/app-*.yml \
          .github/workflows/fleet-audit.yml .github/workflows/selftest-ci.yml
        python - <<'EOF'
        import yaml
        doc = yaml.safe_load(open(".github/workflows/fleet-audit.yml"))
        triggers = doc.get("on", doc.get(True))
        assert list(triggers) == ["workflow_call"], list(triggers)
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
            fi
            zizmor --offline --config .github/zizmor.yml "''${files[@]}"
          )
        done
        touch "$out"
      '';

  standards-ci-gate =
    pkgs.runCommand "standards-ci-gate-check"
      {
        nativeBuildInputs = [
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
