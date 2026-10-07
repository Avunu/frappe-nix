#!/usr/bin/env bash
# The N1 self-test (docs/ironclad/spec.md §7 "N1: runtime"), run by
# .github/workflows/selftest-runtime.yml and by hand the same way:
#
#   tests/ironclad/selftest-runtime/run.sh main      frappe-test --ci on the fixture app,
#                                                    then its variants, the ports of two
#                                                    worktrees, and the rename to
#                                                    ironclad_fixture2 on the same site
#   tests/ironclad/selftest-runtime/run.sh erpnext   the fixture with erpnext as a sibling:
#                                                    the default test setup, and an
#                                                    extension of erpnext's extension
#                                                    (the A.0 #4 shape) exits 4
#
# Each works on a copy of tests/fixtures/ironclad-app under $WORK (default
# $RUNNER_TEMP/ironclad-n1), locked against this checkout with
# `--override-input frappe-nix path:<checkout>`, and enters its dev shell with
# FRAPPE_NIX_CI=1. Needs nix, git, jq, curl and python3; the network for the
# relock and the Frappe sources.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FN="$(git -C "$HERE" rev-parse --show-toplevel)"
WORK="${WORK:-${RUNNER_TEMP:-/tmp}/ironclad-n1}"
SITE=ironclad-fixture.localhost
NIXFLAGS=(--no-pure-eval --override-input frappe-nix "path:$FN")

fail() {
  echo "::error::$*" >&2
  exit 1
}
ok() { echo "ok   $*"; }
group() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::group::$*"; else echo "── $* ──"; fi; }
endgroup() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::endgroup::"; fi; }
commit() { git -C "$1" add -A && git -C "$1" -c user.name=selftest -c user.email=selftest@localhost commit -q --allow-empty -m "$2"; }

# A copy of the fixture app at <dir>, a git repository of its own whose
# generated directories are ignored without a .gitignore (sync owns that file).
prepare() {
  local dir="$1"
  rm -rf "$dir"
  mkdir -p "$(dirname "$dir")"
  cp -r "$FN/tests/fixtures/ironclad-app" "$dir"
  chmod -R u+w "$dir"
  git -C "$dir" init -q
  printf '%s\n' /.devenv/ /.frappe-nix/ /.dev-dist/ /.direnv/ __pycache__/ '*.pyc' >> "$dir/.git/info/exclude"
  commit "$dir" fixture
}

# flake.lock against this checkout, and nix/uv.lock from `nix run .#relock`.
lock() {
  local dir="$1"
  (cd "$dir" && nix flake lock --override-input frappe-nix "path:$FN")
  (cd "$dir" && nix run "${NIXFLAGS[@]}" .#relock)
  commit "$dir" locks
}

# Run a command in <dir>'s dev shell, in CI mode.
dev() {
  local dir="$1"
  shift
  (cd "$dir" && FRAPPE_NIX_CI=1 nix develop "${NIXFLAGS[@]}" -c "$@")
}

# <expected exit> <log> <command...>: run it, keep its output, and insist on the exit.
expect() {
  local want="$1" log="$2" rc
  shift 2
  set +e
  "$@" > "$log" 2>&1
  rc=$?
  set -e
  if [ "$rc" != "$want" ]; then
    tail -n 120 "$log"
    fail "$*: exit $rc, expected $want (log: $log)"
  fi
}

json() { python3 -c "import json, sys; d = json.load(open(sys.argv[1])); print($2)" "$1"; }

# ── main ──────────────────────────────────────────────────────────────────────

cmd_main() {
  local app="$WORK/app" out
  out="$app/.dev-dist/test"

  group "prepare the fixture app"
  prepare "$app"
  lock "$app"
  endgroup

  group "frappe-test --ci"
  set +e
  dev "$app" frappe-test --ci --keep-up 2>&1 | tee "$WORK/main.log"
  local rc="${PIPESTATUS[0]}"
  set -e
  endgroup
  [ "$rc" = 0 ] || fail "frappe-test --ci on the fixture exited $rc"
  ok "FRAPPE_NIX_CI=1 nix develop … -c frappe-test --ci exits 0"
  ! grep -q 'Installing node_modules' "$WORK/main.log" || fail "CI mode installed node_modules"
  grep -q '^frappe-nix: CI mode (ironclad-fixture, site ironclad-fixture.localhost)$' "$WORK/main.log" || fail "no CI mode banner"
  ok "the log has no 'Installing node_modules' and the one-line CI banner"

  [ "$(json "$out/coverage.json" "sum('/.frappe-nix/' in k for k in d['files'])")" = 0 ] || fail "coverage.json counts .frappe-nix"
  [ "$(json "$out/coverage.json" "'ironclad_fixture/api.py' in d['files']")" = True ] || fail "coverage.json lacks ironclad_fixture/api.py"
  ok "coverage.json: no /.frappe-nix/ key, ironclad_fixture/api.py measured"

  nix run --inputs-from "$FN" nixpkgs#check-jsonschema -- \
    --schemafile "$FN/py/ironclad/ironclad/data/schema/ironclad-report.schema.json" "$out/ironclad-report.json"
  ok "ironclad-report.json validates against its schema"

  [ "$(json "$out/testmap.json" "[t['tested'] for t in d['targets'] if t['path'] == 'ironclad_fixture.request.add_fixture_header']")" = "[True]" ] \
    || fail "the after_request hook (T6) is not a tested target"
  ok "the after_request hook (T6) is a target and tested"
  [ -f "$out/junit.xml" ] && [ "$(json "$out/ironclad-report.json" "d['tests']['ran'] > 0")" = True ] || fail "no JUnit report"

  group "variants"
  dev "$app" bash "$HERE/run.sh" variants
  endgroup

  group "ports"
  cmd_ports
  endgroup

  group "rename to ironclad_fixture2"
  cmd_rename
  endgroup
}

# Inside the fixture's dev shell, with its bench up and its site provisioned.
cmd_variants() {
  cd "$DEVENV_ROOT"
  local out="$DEVENV_ROOT/.dev-dist/test" total fu
  reset() { git checkout -q -- . && git clean -fdq; }
  ft() { expect "$1" "$WORK/variant-$2.log" frappe-test --ci --reuse-site --keep-up; }
  total="$(json "$out/coverage.json" "d['totals']['percent_covered']")"

  python3 - <<'PY'
import re
p = "pyproject.toml"
s = open(p).read()
open(p, "w").write(re.sub(r"\[\[tool\.ironclad\.untested\]\]\n(?:[^\[\n].*\n?)*", "", s))
PY
  ft 3 no-exemption
  grep -q 'testmap: untested: ironclad_fixture.api.legacy' "$WORK/variant-no-exemption.log" || fail "the exit-3 run does not name ironclad_fixture.api.legacy"
  reset
  ok "without its exemption: exit 3, naming ironclad_fixture.api.legacy"

  sed -i 's/^fail_under = .*/fail_under = 100/' pyproject.toml
  ft 2 fail-under-100
  reset
  ok "fail_under = 100: exit 2"

  # 5 points under the total, and under 80, where the upward ratchet applies.
  fu="$(python3 -c "import math, sys; print(min(math.floor(float(sys.argv[1])) - 5, 75))" "$total")"
  sed -i "s/^fail_under = .*/fail_under = $fu/" pyproject.toml
  ft 2 fail-under-ratchet
  grep -q "raise \[tool.coverage.report\] fail_under to" "$WORK/variant-fail-under-ratchet.log" || fail "no raise message"
  reset
  ok "fail_under = $fu with coverage at $total: exit 2 with the raise message"

  sed -i '/^\[tool.ironclad\]$/a shell-checks = ["touch stray.txt"]' pyproject.toml
  ft 7 shell-checks
  grep -q 'stray.txt' "$out/shell-checks.log" || fail "the shell check failure does not name stray.txt"
  reset
  ok "shell-checks = [\"touch stray.txt\"]: exit 7, naming stray.txt"

  cat > ironclad_fixture/dbquery.py <<'EOF'
import frappe

# Queries the database while it is imported, as timeclock's utilities.py does.
OPEN_NOTES = len(frappe.get_all("Fixture Note", filters={"status": "Open"}))


@frappe.whitelist()
def open_notes() -> int:
	return OPEN_NOTES
EOF
  ft 3 import-time-query
  [ "$(json "$out/testmap.json" "[(t['tested'], 'error' in t) for t in d['targets'] if t['path'] == 'ironclad_fixture.dbquery.open_notes']")" = "[(False, False)]" ] \
    || fail "a module querying at import time was not probed cleanly"
  reset
  ok "a module that calls frappe.get_all at import: probed without crashing"

  cat > ironclad_fixture/broken.py <<'EOF'
import frappe


@frappe.whitelist()
def unreachable() -> int:
	return 0


raise RuntimeError("ironclad fixture: broken on purpose")
EOF
  ft 3 import-error
  json "$out/testmap.json" "[t for t in d['targets'] if t['path'] == 'ironclad_fixture.broken.unreachable']" | grep -q 'RuntimeError' \
    || fail "a module raising on import is not reported untested with the error"
  reset
  ok "a module that raises on import: its target untested, with the error"
}

# Two worktrees of one app, at once, on different ports; FRAPPE_NIX_PORT_OFFSET;
# and `devenv up` refusing a port that is taken.
cmd_ports() {
  local app="$WORK/app" wt="$WORK/app-worktree" p1
  p1="$(jq -r .webserver_port "$app/.frappe-nix/bench/sites/common_site_config.json")"
  [ "$p1" = 8190 ] || fail "the primary checkout serves on $p1, not 8190 (8000 + the bench-name hash)"
  curl -s -o /dev/null --max-time 5 "http://127.0.0.1:$p1/" || fail "the primary bench is not answering"
  ok "the primary checkout keeps its bench-name port, $p1"

  rm -rf "$wt"
  git -C "$app" worktree add -q --detach "$wt"
  expect 0 "$WORK/ports-worktree.log" dev "$wt" bash "$HERE/run.sh" up-and-report
  local p2
  p2="$(sed -n 's/^PORT=//p' "$WORK/ports-worktree.log")"
  [ -n "$p2" ] && [ "$p2" != "$p1" ] || fail "the worktree serves on '$p2', the primary on $p1"
  ok "a linked worktree runs beside it on its own port, $p2"

  FRAPPE_NIX_PORT_OFFSET=123 expect 0 "$WORK/ports-123.log" dev "$wt" bash "$HERE/run.sh" up-and-report
  grep -qx 'PORT=8123' "$WORK/ports-123.log" || fail "FRAPPE_NIX_PORT_OFFSET=123 did not serve on 8123"
  grep -qx 'MAILPIT=20123' "$WORK/ports-123.log" || fail "FRAPPE_NIX_PORT_OFFSET=123 did not put Mailpit on 20123"
  ok "FRAPPE_NIX_PORT_OFFSET=123: web on 8123, Mailpit on 20123"

  set +e
  (cd "$wt" && FRAPPE_NIX_CI=1 FRAPPE_NIX_PORT_OFFSET=190 nix develop "${NIXFLAGS[@]}" -c devenv up -D) > "$WORK/ports-taken.log" 2>&1
  local rc=$?
  set -e
  if [ "$rc" = 0 ] || ! grep -q 'already in use: nginx=127.0.0.1:8190' "$WORK/ports-taken.log" \
    || ! grep -q FRAPPE_NIX_PORT_OFFSET "$WORK/ports-taken.log"; then
    tail -n 40 "$WORK/ports-taken.log"
    fail "devenv up on a taken port: exit $rc"
  fi
  ok "devenv up on a port another bench holds stops, naming it and FRAPPE_NIX_PORT_OFFSET"
}

# Inside a dev shell: bring the bench up, report its ports, take it down.
cmd_up_and_report() {
  devenv up -D
  local port="" mail
  for _ in $(seq 150); do
    port="$(jq -r '.webserver_port // empty' "$FRAPPE_BENCH_ROOT/sites/common_site_config.json" 2> /dev/null || true)"
    if [ -n "$port" ] && curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$port/"; then break; fi
    port=""
    sleep 2
  done
  mail="$(jq -r '.guards.mail.http_port // empty' "$DEVENV_RUNTIME/devguard-runtime.json" 2> /dev/null || true)"
  if [ -n "$mail" ] && curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$mail/"; then echo "MAILPIT=$mail"; fi
  process-compose -U -u "$PC_SOCKET_PATH" down > /dev/null 2>&1 || true
  [ -n "$port" ] || fail "the bench did not answer"
  echo "PORT=$port"
}

# frappe-rename-app end to end: record what must survive, rename the code, let
# the dev shell's reconcile-apps rename the site (renamedApps), migrate, verify.
cmd_rename() {
  local app="$WORK/app"
  mkdir -p "$app/ironclad_fixture/patches"
  : > "$app/ironclad_fixture/patches/__init__.py"
  cat > "$app/ironclad_fixture/patches/count_runs.py" <<'EOF'
import frappe


def execute():
	frappe.get_doc({"doctype": "Fixture Note", "title": "patch ran"}).insert(ignore_permissions=True)
EOF
  printf '[pre_model_sync]\n\n[post_model_sync]\nironclad_fixture.patches.count_runs\n' > "$app/ironclad_fixture/patches.txt"
  commit "$app" "a patch that counts its runs"
  expect 0 "$WORK/rename-record.log" dev "$app" bash -c "
    set -e
    cd \"\$FRAPPE_BENCH_ROOT\" && env/bin/bench --site $SITE migrate
    cd sites && ../env/bin/python '$HERE/rename_state.py' record $SITE ironclad_fixture '$WORK/rename-state.json'
    frappe-test --down"
  ok "recorded: a note, the daily job stopped, the patch run once"

  expect 0 "$WORK/rename-dry.log" dev "$app" frappe-rename-app code --from ironclad_fixture --to ironclad_fixture2 --dry-run
  grep -q 'rename from ironclad_fixture/api.py' "$WORK/rename-dry.log" || fail "code --dry-run shows no rename"
  [ -z "$(git -C "$app" status --porcelain)" ] || fail "code --dry-run changed the tree"
  expect 0 "$WORK/rename-code.log" dev "$app" frappe-rename-app code --from ironclad_fixture --to ironclad_fixture2
  grep -qx 'ironclad_fixture2.patches.count_runs' "$app/ironclad_fixture2/patches.txt" || fail "patches.txt not renamed"
  sed -i 's/^\( *\)siteName = \(.*\);$/&\n\1renamedApps.ironclad_fixture = "ironclad_fixture2";/' "$app/flake.nix"
  grep -q 'renamedApps.ironclad_fixture = "ironclad_fixture2";' "$app/flake.nix" || fail "could not set renamedApps"
  nix run --inputs-from "$FN" nixpkgs#nixfmt -- "$app/flake.nix"
  commit "$app" "rename to ironclad_fixture2"
  (cd "$app" && nix run "${NIXFLAGS[@]}" .#relock)
  commit "$app" relock
  ok "code renamed, renamedApps set, relocked"

  expect 0 "$WORK/rename-site.log" dev "$app" bash "$HERE/run.sh" rename-site
}

# Inside the renamed app's dev shell.
cmd_rename_site() {
  devenv up -D
  local port=""
  for _ in $(seq 150); do
    port="$(jq -r '.webserver_port // empty' "$FRAPPE_BENCH_ROOT/sites/common_site_config.json" 2> /dev/null || true)"
    if [ -n "$port" ] && curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$port/"; then break; fi
    port=""
    sleep 2
  done
  [ -n "$port" ] || fail "the renamed bench did not come up (did reconcile-apps rename the site?)"
  # The installed_apps global (list-apps reads Installed Application, which only migrate rebuilds).
  (cd "$FRAPPE_BENCH_ROOT" && env/bin/bench --site "$SITE" execute frappe.get_installed_apps) | tee "$WORK/rename-apps.txt"
  grep -q '"ironclad_fixture2"' "$WORK/rename-apps.txt" || fail "devenv up did not rename the site (renamedApps)"
  ok "devenv up: reconcile-apps renamed the site before anything imported the app"

  (cd "$FRAPPE_BENCH_ROOT" && env/bin/bench --site "$SITE" migrate)
  (cd "$FRAPPE_BENCH_ROOT/sites" && ../env/bin/python "$HERE/rename_state.py" verify "$SITE" ironclad_fixture2 "$WORK/rename-state.json")
  ok "migrated cleanly: the note, the job's name and stopped flag, and the Patch Log kept; the patch did not re-run"

  frappe-rename-app --site "$SITE" --yes ironclad_fixture=ironclad_fixture2 | tee "$WORK/rename-again.log"
  grep -q "ironclad_fixture not installed on $SITE: nothing to do" "$WORK/rename-again.log" || fail "a second run did something"
  ok "a second run prints nothing to do"

  set +e
  frappe-test --ci --reuse-site > "$WORK/rename-test.log" 2>&1
  local rc=$?
  set -e
  [ "$rc" = 0 ] || { tail -n 80 "$WORK/rename-test.log"; fail "frappe-test on the renamed app exited $rc"; }
  ok "the renamed app's own tests, testmap and composition pass on the renamed site"
}

# ── erpnext ───────────────────────────────────────────────────────────────────

cmd_erpnext() {
  local app="$WORK/erpnext" out
  out="$app/.dev-dist/test"
  group "prepare the fixture with erpnext"
  prepare "$app"
  python3 - "$app" <<'PY'
import re, sys
from pathlib import Path
app = Path(sys.argv[1])
flake = app / "flake.nix"
s = flake.read_text()
s = s.replace(
	'    frappe = {\n      url = "github:frappe/frappe/version-16";\n      flake = false;\n    };\n',
	'    frappe = {\n      url = "github:frappe/frappe/version-16";\n      flake = false;\n    };\n'
	'    erpnext = {\n      url = "github:frappe/erpnext/version-16";\n      flake = false;\n    };\n',
)
s = s.replace("siblings = [ ];", 'siblings = [\n                  {\n                    name = "erpnext";\n                    src = inputs.erpnext;\n                  }\n                ];')
assert "inputs.erpnext" in s and 'url = "github:frappe/erpnext/version-16"' in s, s
flake.write_text(s)
py = app / "pyproject.toml"
s = py.read_text().replace("siblings = []", 'siblings = ["erpnext"]')
s = s.replace('frappe = ">=16.0.0,<17.0.0"', 'frappe = ">=16.0.0,<17.0.0"\nerpnext = ">=16.0.0,<17.0.0"')
py.write_text(s)
hooks = app / "ironclad_fixture" / "hooks.py"
s = hooks.read_text().replace("required_apps = []", 'required_apps = ["erpnext"]')
s = s.replace(
	'\t"ToDo": ["ironclad_fixture.overrides.todo.IroncladToDo"],\n',
	'\t"ToDo": ["ironclad_fixture.overrides.todo.IroncladToDo"],\n'
	'\t# The A.0 #4 shape: an extension of erpnext\'s extension rather than of Address.\n'
	'\t"Address": ["ironclad_fixture.overrides.address.FixtureAddress"],\n',
)
assert "FixtureAddress" in s
hooks.write_text(s)
(app / "ironclad_fixture" / "overrides" / "address.py").write_text(
	"from erpnext.accounts.custom.address import ERPNextAddress\n\n\n"
	"class FixtureAddress(ERPNextAddress):\n"
	'\t"""Composes only while erpnext is installed first: the A.0 #4 failure."""\n'
)
PY
  commit "$app" "erpnext sibling and an extension of its extension"
  lock "$app"
  endgroup

  group "frappe-test --ci with erpnext"
  set +e
  dev "$app" frappe-test --ci 2>&1 | tee "$WORK/erpnext.log"
  local rc="${PIPESTATUS[0]}"
  set -e
  endgroup
  grep -q 'frappe-test: setup module:erpnext.tests.bootstrap_test_data' "$WORK/erpnext.log" || fail "the default setup with erpnext was not erpnext's bootstrap"
  [ "$(json "$out/ironclad-report.json" "(d['stages']['site'], d['stages']['tests'])")" = "('ok', 'ok')" ] || fail "the site or the tests failed with erpnext"
  ok "the default setup with erpnext (module:erpnext.tests.bootstrap_test_data) works, and the tests pass"
  [ "$rc" = 4 ] || fail "an extension of erpnext's extension exited $rc, not 4"
  [ "$(json "$out/composition.json" "(d['doctypes']['Address']['real_ok'], d['doctypes']['Address']['app_first_ok'], 'TypeError' in d['doctypes']['Address']['app_first_error'])")" = "(True, False, True)" ] \
    || fail "composition.json does not show the A.0 #4 failure"
  grep -q 'composition: Address with apps in order' "$WORK/erpnext.log" || fail "the failure does not name the doctype and the order"
  ok "an extension of erpnext's extension composes in the real order and exits 4 with the app first"
}

mkdir -p "$WORK"
case "${1:-}" in
  main) cmd_main ;;
  variants) cmd_variants ;;
  up-and-report) cmd_up_and_report ;;
  rename) cmd_rename ;; # resume after main's ports, by hand
  rename-site) cmd_rename_site ;;
  erpnext) cmd_erpnext ;;
  *)
    echo "usage: $0 main|erpnext" >&2
    exit 2
    ;;
esac
