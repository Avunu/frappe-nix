# checks.dev-tools: the bench template's dev group, built and run.
#
# Every tool in templates/bench/pyproject.toml's dev group is unpinned or has
# only a floor, so a fresh bench's `uv lock` takes whatever PyPI has that day,
# and a new release can stop building under Nix while frappe-nix changes
# nothing: semgrep 1.183.0's wheel did, patchelf aborting on its semgrep-core
# (lib/overrides.nix, semgrep). This builds the dev virtualenv lib/python.nix
# makes from tests/fixtures/dev-tools, with the overrides every bench gets
# (lib/overrides.nix, builtin), and runs each tool on a small input of its own,
# so a wheel that installs but whose native part does not load fails too.
#
# Against the committed lock it is a regression check, on every pull request
# through check.yml. Against a lock re-made from that day's PyPI it is the
# canary (.github/workflows/canary-dev-tools.yml).
{
  pkgs,
  checks,
  inputs,
}:

let
  inherit (pkgs) lib;

  fixture = ./fixtures/dev-tools;
  devGroup = pyproject: (builtins.fromTOML (builtins.readFile pyproject)).dependency-groups.dev;
  templateDev = devGroup ../templates/bench/pyproject.toml;
  fixtureDev = devGroup (fixture + "/pyproject.toml");
  reqName = r: lib.toLower (builtins.head (builtins.match "([A-Za-z0-9_.-]+).*" r));

  # One script for each tool in the dev group, by its requirement name: the
  # tool's own work on the inputs below, not only --version. The check refuses
  # a dev group with a tool this does not name.
  use = {
    coverage = ''
      coverage run -m pytest -q -p no:cacheprovider test_sample.py
      coverage report --include=test_sample.py --fail-under=100
    '';
    pre-commit = ''
      pre-commit validate-config pre-commit.yaml
    '';
    pydantic = ''
      python -c 'import pydantic; assert pydantic.TypeAdapter(int).validate_python("2") == 2'
    '';
    pytest = ''
      pytest -q -p no:cacheprovider test_sample.py
    '';
    responses = ''
      python mocked.py
    '';
    ruff = ''
      out="$(ruff check --isolated --no-cache --select F401 --output-format concise unused.py || true)"
      grep -q F401 <<< "$out" || { echo "$out"; exit 1; }
    '';
    # semgrep-core loads a trust store at start-up even for an offline scan, and
    # the sandbox's SSL_CERT_FILE is /no-cert-file.crt; a bench's shell has one.
    semgrep = ''
      export SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt
      semgrep scan --metrics=off --disable-version-check --config rule.yml --json --output semgrep.json target.py
      python -c 'import json; r = json.load(open("semgrep.json"))["results"]; assert len(r) == 1, r'
    '';
    unittest-xml-reporting = ''
      python -m xmlrunner --output-file report.xml test_sample
      grep -q 'testcase.*test_sum' report.xml
    '';
  };

  envs = import ../lib/python.nix {
    inherit pkgs lib;
    python = pkgs.python314;
    workspaceRoot = fixture;
    name = "dev-tools";
    inherit (inputs)
      pyproject-nix
      pyproject-build-systems
      uv2nix
      python-devops-nix
      ;
    extraOverrides = (import ../lib/overrides.nix).builtin {
      inherit pkgs;
      inherit (pkgs) mariadb;
    };
    lockAuditRelock = ''
      tests/fixtures/dev-tools no longer matches its uv.lock:

          uv lock --project tests/fixtures/dev-tools
    '';
  };
in
{
  dev-tools =
    assert lib.assertMsg (fixtureDev == templateDev)
      "tests/fixtures/dev-tools's dev group is not templates/bench/pyproject.toml's: copy it over and re-lock (uv lock --project tests/fixtures/dev-tools)";
    assert lib.assertMsg (lib.attrNames use == lib.sort lib.lessThan (map reqName templateDev))
      "tests/dev-tools.nix runs ${toString (lib.attrNames use)}, but the template's dev group is ${toString (map reqName templateDev)}: give each tool a script";
    checks.script "dev-tools" { } ''
      set -euo pipefail
      export PATH="${envs.devPythonEnv}/bin:$PATH"

      cat > test_sample.py <<'EOF'
      import unittest


      class Sample(unittest.TestCase):
          def test_sum(self):
              self.assertEqual(sum([1, 2]), 3)
      EOF
      cat > pre-commit.yaml <<'EOF'
      repos:
        - repo: local
          hooks:
            - id: noop
              name: noop
              entry: "true"
              language: system
      EOF
      cat > mocked.py <<'EOF'
      import requests
      import responses


      @responses.activate
      def fetch():
          responses.get("http://example.invalid/ping", json={"ok": True})
          return requests.get("http://example.invalid/ping").json()


      assert fetch() == {"ok": True}
      EOF
      printf 'import os\n' > unused.py
      cat > rule.yml <<'EOF'
      rules:
        - id: fstring-sql
          languages: [python]
          severity: ERROR
          message: an f-string passed to frappe.db.sql
          pattern: frappe.db.sql(f"...")
      EOF
      printf 'import frappe\n\n\ndef f(x):\n    frappe.db.sql(f"select name from tabUser where name = {x}")\n' > target.py

      failed=()
      ${lib.concatStrings (
        lib.mapAttrsToList (name: script: ''
          echo "-- ${name}"
          if bash -euo pipefail ${pkgs.writeText "dev-tools-${name}" script}; then
            echo "ok   ${name}"
          else
            failed+=(${name})
          fi
        '') use
      )}
      if [ ''${#failed[@]} -gt 0 ]; then
        echo "FAIL these dev tools do not run from the bench's dev env: ''${failed[*]}" >&2
        exit 1
      fi
    '';
}
