#!/usr/bin/env bash
# The N1 self-test (docs/app-standards/spec.md §7 "N1: runtime"), run by
# .github/workflows/selftest-runtime.yml and by hand the same way:
#
#   tests/standards/selftest-runtime/run.sh main      frappe-test --ci on the fixture app
#                                                    (first with the tests module off),
#                                                    then its variants, the ports of two
#                                                    worktrees, and the rename to
#                                                    standards_fixture2 on the same site
#   tests/standards/selftest-runtime/run.sh erpnext   the fixture with erpnext as a sibling:
#                                                    the default test setup, and an
#                                                    extension of erpnext's extension
#                                                    (the A.0 #4 shape) exits 4
#   tests/standards/selftest-runtime/run.sh parallel  two copies of the fixture, one name,
#                                                    tested at once: each leases its
#                                                    own ports and answers as itself;
#                                                    then a port held outside
#                                                    frappe-nix is moved past
#   tests/standards/selftest-runtime/run.sh collect-ports   after a failure: every
#                                                    ports.json still there, and who
#                                                    holds each port, into $WORK
#
# Each works on a copy of tests/fixtures/standards-app under $WORK (default
# $RUNNER_TEMP/standards-n1), locked against this checkout with
# `--override-input frappe-nix path:<checkout>`, and enters its dev shell with
# FRAPPE_NIX_CI=1. Needs nix, git, jq, curl and python3; the network for the
# relock and the Frappe sources.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FN="$(git -C "$HERE" rev-parse --show-toplevel)"
WORK="${WORK:-${RUNNER_TEMP:-/tmp}/standards-n1}"
SITE=standards-fixture.localhost
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
  cp -r "$FN/tests/fixtures/standards-app" "$dir"
  chmod -R u+w "$dir"
  git -C "$dir" init -q
  printf '%s\n' /.devenv/ /.frappe-nix/ /.dev-dist/ /.direnv/ __pycache__/ '*.pyc' >> "$dir/.git/info/exclude"
  commit "$dir" fixture
}

# frappe at the revision frappe-nix's own dev env pins (dev/pyproject.toml,
# moved by update-frappe), and erpnext at the matching release, not the tips of
# version-16: a branch tip can break the resolution (frappe's `dev` extra did,
# 2026-10-07) and that is not what these tests are about.
FRAPPE_REV="$(sed -n 's|.*frappe/archive/\([0-9a-f]\{40\}\)\.tar\.gz.*|\1|p' "$FN/dev/pyproject.toml")"
ERPNEXT_REV=7474d9e786277383de1242ab882f16856d17a9c9 # v16.50.0, beside frappe's

# PyPI as of the last change to what pins the resolution (this script pins
# erpnext), not as of today: each relock below resolves every transitive
# dependency afresh, and a release made since is not what these tests are
# about either. Needs the history (the workflow checks out with fetch-depth 0);
# UV_EXCLUDE_NEWER, if set, wins.
if [ -z "${UV_EXCLUDE_NEWER:-}" ]; then
  UV_EXCLUDE_NEWER="$(git -C "$FN" log -1 --format=%cI -- dev/pyproject.toml templates/bench/pyproject.toml tests/fixtures/standards-app "$HERE")"
fi
export UV_EXCLUDE_NEWER
echo "selftest-runtime: resolving PyPI as of ${UV_EXCLUDE_NEWER:-now}"

# flake.lock against this checkout, and nix/uv.lock from `nix run .#relock`.
lock() {
  local dir="$1" pins=(--override-input frappe-nix "path:$FN" --override-input frappe "github:frappe/frappe/$FRAPPE_REV")
  if grep -q 'erpnext = {' "$dir/flake.nix"; then
    pins+=(--override-input erpnext "github:frappe/erpnext/$ERPNEXT_REV")
  fi
  (cd "$dir" && nix flake lock "${pins[@]}")
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

# Inside a dev shell: its process-compose answers, not merely left a socket file.
pc_up() { [ -S "$PC_SOCKET_PATH" ] && timeout 10 process-compose -U -u "$PC_SOCKET_PATH" process list > /dev/null 2>&1; }

# A self-hosted runner reuses its work directory, so a bench a previous job on
# it left running (or a socket it left behind) sits at the same paths: stop it.
stop_leftover() {
  dev "$1" bash -c 'if [ -S "$PC_SOCKET_PATH" ]; then timeout 60 process-compose -U -u "$PC_SOCKET_PATH" down > /dev/null 2>&1 || true; rm -f "$PC_SOCKET_PATH"; fi'
}

json() { python3 -c "import json, sys; d = json.load(open(sys.argv[1])); print($2)" "$1"; }

# The primary checkout's preferred web port: 8000 + sha256(name)[0:4] mod 900,
# the offset every bench has had since ports were hashed (python-devops-nix's
# lib.ports). It is the port unless another bench holds it: on a CI host the
# other legs run benches of this same name at the same time.
PRIMARY_PORT="$((8000 + $(python3 -c 'import hashlib; print(int(hashlib.sha256(b"standards-fixture").hexdigest()[:4], 16) % 900)')))"

# ── main ──────────────────────────────────────────────────────────────────────

cmd_main() {
  local app="$WORK/app" out
  out="$app/.dev-dist/test"

  group "prepare the fixture app"
  prepare "$app"
  lock "$app"
  stop_leftover "$app"
  endgroup

  group "frappe-test --ci with the tests module off"
  # Before anything has started the bench: with tests off, nothing may.
  cp "$app/pyproject.toml" "$WORK/pyproject.toml.orig"
  printf '\n[tool.frappe-nix.tests]\nenable = false\n' >> "$app/pyproject.toml"
  expect 0 "$WORK/tests-off.log" dev "$app" bash "$HERE/run.sh" tests-off
  cp "$WORK/pyproject.toml.orig" "$app/pyproject.toml"
  endgroup
  ok "tests off: frappe-test --ci starts no bench and reports stages 1 to 6 skipped"

  group "frappe-test --ci"
  set +e
  dev "$app" frappe-test --ci --keep-up 2>&1 | tee "$WORK/main.log"
  local rc="${PIPESTATUS[0]}"
  set -e
  endgroup
  [ "$rc" = 0 ] || fail "frappe-test --ci on the fixture exited $rc"
  ok "FRAPPE_NIX_CI=1 nix develop … -c frappe-test --ci exits 0"
  ! grep -q 'Installing node_modules' "$WORK/main.log" || fail "CI mode installed node_modules"
  grep -q '^frappe-nix: CI mode (standards-fixture, site standards-fixture.localhost)$' "$WORK/main.log" || fail "no CI mode banner"
  ok "the log has no 'Installing node_modules' and the one-line CI banner"

  [ "$(json "$out/coverage.json" "sum('/.frappe-nix/' in k for k in d['files'])")" = 0 ] || fail "coverage.json counts .frappe-nix"
  [ "$(json "$out/coverage.json" "'standards_fixture/api.py' in d['files']")" = True ] || fail "coverage.json lacks standards_fixture/api.py"
  ok "coverage.json: no /.frappe-nix/ key, standards_fixture/api.py measured"

  nix run --inputs-from "$FN" nixpkgs#check-jsonschema -- \
    --schemafile "$FN/py/frappe_nix_tools/frappe_nix_tools/data/schema/frappe-test-report.schema.json" "$out/frappe-test-report.json"
  ok "frappe-test-report.json validates against its schema"

  [ "$(json "$out/testmap.json" "[t['tested'] for t in d['targets'] if t['path'] == 'standards_fixture.request.add_fixture_header']")" = "[True]" ] \
    || fail "the after_request hook (T6) is not a tested target"
  ok "the after_request hook (T6) is a target and tested"
  [ -f "$out/junit.xml" ] && [ "$(json "$out/frappe-test-report.json" "d['tests']['ran'] > 0")" = True ] || fail "no JUnit report"

  group "variants"
  dev "$app" bash "$HERE/run.sh" variants
  endgroup

  group "ports"
  cmd_ports
  endgroup

  group "rename to standards_fixture2"
  cmd_rename
  endgroup
}

# Inside the fixture's dev shell, with nothing started and the tests module off.
cmd_tests_off() {
  local out="$DEVENV_ROOT/.dev-dist/test" name
  ! pc_up || fail "the bench is already up before the tests-off run"
  frappe-test --ci || fail "frappe-test --ci with tests off exited $?"
  ! pc_up || fail "frappe-test --ci with tests off started process-compose ($PC_SOCKET_PATH)"
  for name in up site tests coverage testmap composition; do
    [ "$(json "$out/frappe-test-report.json" "d['stages']['$name']")" = skipped ] || fail "tests off: stage $name was not skipped"
  done
  [ "$(json "$out/frappe-test-report.json" "d['stages']['nix-lint']")" = ok ] || fail "tests off: nix-lint did not run"
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
open(p, "w").write(re.sub(r"\[\[tool\.frappe-nix\.untested\]\]\n(?:[^\[\n].*\n?)*", "", s))
PY
  ft 3 no-exemption
  grep -q 'testmap: untested: standards_fixture.api.legacy' "$WORK/variant-no-exemption.log" || fail "the exit-3 run does not name standards_fixture.api.legacy"
  reset
  ok "without its exemption: exit 3, naming standards_fixture.api.legacy"

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

  # tests.coverage.target caps the upward ratchet (spec §5.1 stage 4): a
  # target T under the total and fail_under 5 below it asks for exactly T; at
  # the target, nothing more is asked however high the total is.
  local target=$(( ${total%.*} - 3 ))
  sed -i "s/^fail_under = .*/fail_under = $((target - 5))/" pyproject.toml
  printf '\n[tool.frappe-nix.tests.coverage]\ntarget = %s\n' "$target" >> pyproject.toml
  ft 2 coverage-target
  grep -q "raise \[tool.coverage.report\] fail_under to $target\$" "$WORK/variant-coverage-target.log" \
    || fail "the ratchet does not stop at tests.coverage.target = $target"
  sed -i "s/^fail_under = .*/fail_under = $target/" pyproject.toml
  ft 0 coverage-at-target
  reset
  ok "tests.coverage.target = $target: fail_under $((target - 5)) is asked to rise to $target, and $target passes with coverage at $total"

  # python-types on (recommended): stage 7 runs ty from the fixture's
  # tools/pyproject.toml (sync renders it). Off: stage 7 is skipped without
  # running it.
  [ "$(json "$out/frappe-test-report.json" "d['stages']['ty']")" = ok ] || fail "python-types on: stage 7 did not run ty"
  grep -qxE '(::group::|── )ty( ──)?' "$WORK/variant-coverage-at-target.log" || fail "python-types on: no ty group in the log"
  printf '\n[tool.frappe-nix.python-types]\nenable = false\n' >> pyproject.toml
  ft 0 python-types-off
  ! grep -qxE '(::group::|── )ty( ──)?' "$WORK/variant-python-types-off.log" || fail "python-types off: ty still ran"
  [ "$(json "$out/frappe-test-report.json" "d['stages']['ty']")" = skipped ] || fail "python-types off: stage 7 is not skipped"
  reset
  ok "python-types on: stage 7 ran ty; off: skipped"

  sed -i '/^\[tool.frappe-nix\]$/a shell-checks = ["touch stray.txt"]' pyproject.toml
  ft 7 shell-checks
  grep -q 'stray.txt' "$out/shell-checks.log" || fail "the shell check failure does not name stray.txt"
  reset
  ok "shell-checks = [\"touch stray.txt\"]: exit 7, naming stray.txt"

  cat > standards_fixture/dbquery.py <<'EOF'
import frappe

# Queries the database while it is imported, as some apps' utility modules do.
OPEN_NOTES = len(frappe.get_all("Fixture Note", filters={"status": "Open"}))


@frappe.whitelist()
def open_notes() -> int:
	return OPEN_NOTES
EOF
  ft 3 import-time-query
  [ "$(json "$out/testmap.json" "[(t['tested'], 'error' in t) for t in d['targets'] if t['path'] == 'standards_fixture.dbquery.open_notes']")" = "[(False, False)]" ] \
    || fail "a module querying at import time was not probed cleanly"
  reset
  ok "a module that calls frappe.get_all at import: probed without crashing"

  cat > standards_fixture/broken.py <<'EOF'
import frappe


@frappe.whitelist()
def unreachable() -> int:
	return 0


raise RuntimeError("standards fixture: broken on purpose")
EOF
  ft 3 import-error
  json "$out/testmap.json" "[t for t in d['targets'] if t['path'] == 'standards_fixture.broken.unreachable']" | grep -q 'RuntimeError' \
    || fail "a module raising on import is not reported untested with the error"
  reset
  ok "a module that raises on import: its target untested, with the error"
}

# Two worktrees of one app, at once, on different ports; FRAPPE_NIX_PORT_OFFSET;
# and `devenv up` on a port another bench holds: leasing the next one, or, with
# FRAPPE_NIX_STRICT_PORTS=1, refusing and naming the holder.
cmd_ports() {
  local app="$WORK/app" wt="$WORK/app-worktree" p1 moved
  p1="$(jq -r .webserver_port "$app/.frappe-nix/bench/sites/common_site_config.json")"
  moved="$(dev "$app" frappe-nix-ports --json | jq -r '.moved.web // empty')"
  if [ "$p1" != "$PRIMARY_PORT" ]; then
    [ "$moved" = "$PRIMARY_PORT" ] || fail "the primary checkout serves on $p1, not $PRIMARY_PORT (8000 + the bench-name hash), and recorded no move from it"
  fi
  curl -s -o /dev/null --max-time 5 "http://127.0.0.1:$p1/" || fail "the primary bench is not answering"
  ok "the primary checkout serves on its bench-name port, $PRIMARY_PORT, or past it when another bench holds it ($p1)"

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

  # The primary's own port, as the worktree's preferred one: held by the primary.
  FRAPPE_NIX_PORT_OFFSET="$((p1 - 8000))" expect 0 "$WORK/ports-taken.log" dev "$wt" bash "$HERE/run.sh" up-and-report
  local p3
  p3="$(sed -n 's/^PORT=//p' "$WORK/ports-taken.log")"
  [ -n "$p3" ] && [ "$p3" != "$p1" ] || fail "devenv up on a taken port serves on '$p3', the primary on $p1"
  grep -qx "MOVED=$p1" "$WORK/ports-taken.log" || fail "devenv up on a taken port recorded no move from $p1"
  grep -qx "WEB=$p3" "$WORK/ports-taken.log" || fail "ports.json and webserver_port disagree"
  grep -qx 'IDENTITY=ok' "$WORK/ports-taken.log" || fail "the moved bench does not answer as itself"
  ok "devenv up on a port another bench holds leases the next one, $p3, and answers there as itself"

  set +e
  (cd "$wt" && FRAPPE_NIX_CI=1 FRAPPE_NIX_STRICT_PORTS=1 FRAPPE_NIX_PORT_OFFSET="$((p1 - 8000))" nix develop "${NIXFLAGS[@]}" -c devenv up -D) > "$WORK/ports-strict.log" 2>&1
  local rc=$?
  set -e
  if [ "$rc" = 0 ] || ! grep -q "already in use: nginx=127.0.0.1:$p1" "$WORK/ports-strict.log" \
    || ! grep -q "held by bench standards-fixture/.*$app" "$WORK/ports-strict.log" \
    || ! grep -q FRAPPE_NIX_PORT_OFFSET "$WORK/ports-strict.log"; then
    tail -n 40 "$WORK/ports-strict.log"
    fail "devenv up with FRAPPE_NIX_STRICT_PORTS=1 on a taken port: exit $rc"
  fi
  ok "FRAPPE_NIX_STRICT_PORTS=1: devenv up on a held port stops, naming it, its holder and FRAPPE_NIX_PORT_OFFSET"
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
  if [ -n "$port" ]; then
    echo "WEB=$(frappe-nix-ports web)"
    echo "MOVED=$(frappe-nix-ports --json | jq -r '.moved.web // empty')"
    if [ "$(curl -s --max-time 3 "http://127.0.0.1:$port/__frappe_nix/bench")" = "$(frappe-nix-ports bench_id)" ]; then
      echo "IDENTITY=ok"
    fi
  fi
  process-compose -U -u "$PC_SOCKET_PATH" down > /dev/null 2>&1 || true
  [ -n "$port" ] || fail "the bench did not answer"
  echo "PORT=$port"
}

# frappe-rename-app end to end: record what must survive, rename the code, let
# the dev shell's reconcile-apps rename the site (renamedApps), migrate, verify.
cmd_rename() {
  local app="$WORK/app"
  mkdir -p "$app/standards_fixture/patches"
  : > "$app/standards_fixture/patches/__init__.py"
  cat > "$app/standards_fixture/patches/count_runs.py" <<'EOF'
import frappe


def execute():
	frappe.get_doc({"doctype": "Fixture Note", "title": "patch ran"}).insert(ignore_permissions=True)
EOF
  printf '[pre_model_sync]\n\n[post_model_sync]\nstandards_fixture.patches.count_runs\n' > "$app/standards_fixture/patches.txt"
  commit "$app" "a patch that counts its runs"
  expect 0 "$WORK/rename-record.log" dev "$app" bash -c "
    set -e
    cd \"\$FRAPPE_BENCH_ROOT\" && env/bin/bench --site $SITE migrate
    cd sites && ../env/bin/python '$HERE/rename_state.py' record $SITE standards_fixture '$WORK/rename-state.json'
    frappe-test --down"
  ok "recorded: a note, the daily job stopped, the patch run once"

  expect 0 "$WORK/rename-dry.log" dev "$app" frappe-rename-app code --from standards_fixture --to standards_fixture2 --dry-run
  grep -q 'rename from standards_fixture/api.py' "$WORK/rename-dry.log" || fail "code --dry-run shows no rename"
  [ -z "$(git -C "$app" status --porcelain)" ] || fail "code --dry-run changed the tree"
  expect 0 "$WORK/rename-code.log" dev "$app" frappe-rename-app code --from standards_fixture --to standards_fixture2
  grep -qx 'standards_fixture2.patches.count_runs' "$app/standards_fixture2/patches.txt" || fail "patches.txt not renamed"
  sed -i 's/^\( *\)defaultSite = \(.*\);$/&\n\1renamedApps.standards_fixture = "standards_fixture2";/' "$app/flake.nix"
  grep -q 'renamedApps.standards_fixture = "standards_fixture2";' "$app/flake.nix" || fail "could not set renamedApps"
  nix run --inputs-from "$FN" nixpkgs#nixfmt -- "$app/flake.nix"
  commit "$app" "rename to standards_fixture2"
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
  grep -q '"standards_fixture2"' "$WORK/rename-apps.txt" || fail "devenv up did not rename the site (renamedApps)"
  ok "devenv up: reconcile-apps renamed the site before anything imported the app"

  (cd "$FRAPPE_BENCH_ROOT" && env/bin/bench --site "$SITE" migrate)
  (cd "$FRAPPE_BENCH_ROOT/sites" && ../env/bin/python "$HERE/rename_state.py" verify "$SITE" standards_fixture2 "$WORK/rename-state.json")
  ok "migrated cleanly: the note, the job's name and stopped flag, and the Patch Log kept; the patch did not re-run"

  frappe-rename-app --site "$SITE" --yes standards_fixture=standards_fixture2 | tee "$WORK/rename-again.log"
  grep -q "standards_fixture not installed on $SITE: nothing to do" "$WORK/rename-again.log" || fail "a second run did something"
  ok "a second run prints nothing to do"

  set +e
  frappe-test --ci --reuse-site > "$WORK/rename-test.log" 2>&1
  local rc=$?
  set -e
  [ "$rc" = 0 ] || { tail -n 80 "$WORK/rename-test.log"; fail "frappe-test on the renamed app exited $rc"; }
  ok "the renamed app's own tests, testmap and composition pass on the renamed site"
}

# ── parallel ──────────────────────────────────────────────────────────────────

# Two copies of the fixture under one name, so one set of preferred ports,
# tested at the same moment: what CI legs on one host do. Each must lease its
# own ports, answer on them as itself, and catch its own mail. Then a port held
# by something that is not frappe-nix: moved past, or with
# FRAPPE_NIX_STRICT_PORTS=1 refused, named as such.
cmd_parallel() {
  local a="$WORK/par-a" b="$WORK/par-b" ra=0 rb=0 pa pb
  group "prepare two copies of the fixture, one name"
  prepare "$a"
  lock "$a"
  rm -rf "$b"
  cp -a "$a" "$b"
  rm -rf "$b/.devenv" "$b/.frappe-nix" "$b/.direnv" "$b/.dev-dist"
  stop_leftover "$a"
  stop_leftover "$b"
  endgroup

  group "frappe-test --ci --keep-up in both at once"
  dev "$a" frappe-test --ci --keep-up > "$WORK/parallel-a.log" 2>&1 &
  pa=$!
  dev "$b" frappe-test --ci --keep-up > "$WORK/parallel-b.log" 2>&1 &
  pb=$!
  wait "$pa" || ra=$?
  wait "$pb" || rb=$?
  endgroup
  if [ "$ra" != 0 ] || [ "$rb" != 0 ]; then
    tail -n 80 "$WORK/parallel-a.log" "$WORK/parallel-b.log"
    fail "two same-name benches at once: frappe-test --ci exited $ra and $rb"
  fi
  ok "two copies of one app, one name, frappe-test --ci at once: both exit 0"
  dev "$a" bash "$HERE/run.sh" pc-state > "$WORK/parallel-pc-a.log" 2>&1 || true
  dev "$b" bash "$HERE/run.sh" pc-state > "$WORK/parallel-pc-b.log" 2>&1 || true

  expect 0 "$WORK/parallel-ports-a.log" dev "$a" bash "$HERE/run.sh" report-ports
  expect 0 "$WORK/parallel-ports-b.log" dev "$b" bash "$HERE/run.sh" report-ports
  local ja jb key
  ja="$(sed -n 's/^PORTS=//p' "$WORK/parallel-ports-a.log")"
  jb="$(sed -n 's/^PORTS=//p' "$WORK/parallel-ports-b.log")"
  for key in web mailpit_smtp mailpit_ui bench_id; do
    [ "$(jq -r ".$key" <<< "$ja")" != "$(jq -r ".$key" <<< "$jb")" ] || fail "both benches have $key $(jq -r ".$key" <<< "$ja")"
  done
  [ "$(jq -r .name <<< "$ja")" = "$(jq -r .name <<< "$jb")" ] || fail "the copies do not share a name"
  ok "distinct web, Mailpit SMTP and UI ports and identities: $(jq -c '[.web, .mailpit_smtp, .mailpit_ui]' <<< "$ja") and $(jq -c '[.web, .mailpit_smtp, .mailpit_ui]' <<< "$jb")"
  ok "each answers /__frappe_nix/bench with its own identity, and its devguard-runtime.json and webserver_port match its ports.json"

  # Mail sent to A's catcher is in A's Mailpit, and not in B's.
  python3 - "$(jq -r .mailpit_smtp <<< "$ja")" <<'PY'
import smtplib, sys
with smtplib.SMTP("127.0.0.1", int(sys.argv[1]), timeout=10) as s:
	s.sendmail("a@example.com", ["x@example.com"], "Subject: parallel-a\r\n\r\nfrom A\r\n")
PY
  local na nb
  na="$(curl -s "http://127.0.0.1:$(jq -r .mailpit_ui <<< "$ja")/api/v1/search?query=subject:parallel-a" | jq .messages_count)"
  nb="$(curl -s "http://127.0.0.1:$(jq -r .mailpit_ui <<< "$jb")/api/v1/search?query=subject:parallel-a" | jq .messages_count)"
  [ "$na" = 1 ] && [ "$nb" = 0 ] || fail "mail sent to A: $na in A's Mailpit, $nb in B's"
  ok "mail sent to A's catcher is in A's Mailpit only"

  expect 0 "$WORK/parallel-down-a.log" dev "$a" frappe-test --down
  expect 0 "$WORK/parallel-down-b.log" dev "$b" frappe-test --down
  [ "$(python3 "$FN/lib/port-lease.py" who "$(jq -r .web <<< "$ja")")" = "$(jq -r .web <<< "$ja"): free" ] \
    || fail "A's web port is still held after frappe-test --down"
  ok "down, each bench's lease is gone with it"

  group "a port held outside frappe-nix"
  # An offset no other leg prefers, its web port held by a plain listener.
  local off=777 held=8777 holder
  python3 -c 'import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(); time.sleep(3600)' "$held" &
  holder=$!
  # shellcheck disable=SC2064 # the holder's pid, now
  trap "kill $holder 2> /dev/null || true" EXIT
  FRAPPE_NIX_PORT_OFFSET="$off" expect 0 "$WORK/parallel-outside.log" dev "$a" bash "$HERE/run.sh" up-and-report
  local p
  p="$(sed -n 's/^PORT=//p' "$WORK/parallel-outside.log")"
  [ -n "$p" ] && [ "$p" != "$held" ] && grep -qx "MOVED=$held" "$WORK/parallel-outside.log" \
    && grep -qx 'IDENTITY=ok' "$WORK/parallel-outside.log" || fail "with $held held outside frappe-nix the bench serves on '$p'"
  ok "a web port held outside frappe-nix ($held) is moved past, to $p"
  set +e
  (cd "$a" && FRAPPE_NIX_CI=1 FRAPPE_NIX_STRICT_PORTS=1 FRAPPE_NIX_PORT_OFFSET="$off" nix develop "${NIXFLAGS[@]}" -c devenv up -D) > "$WORK/parallel-outside-strict.log" 2>&1
  local rc=$?
  set -e
  if [ "$rc" = 0 ] || ! grep -q "nginx=127.0.0.1:$held is in use by a process outside frappe-nix" "$WORK/parallel-outside-strict.log"; then
    tail -n 40 "$WORK/parallel-outside-strict.log"
    fail "FRAPPE_NIX_STRICT_PORTS=1 with $held held outside frappe-nix: exit $rc"
  fi
  kill "$holder" 2> /dev/null || true
  endgroup
  ok "FRAPPE_NIX_STRICT_PORTS=1: refused, naming a process outside frappe-nix"
}

# Inside a dev shell: what its process-compose runs, and the tail of the logs of
# the processes that hold or bind the leased ports.
cmd_pc_state() {
  local p
  timeout 10 process-compose -U -u "$PC_SOCKET_PATH" process list -o wide || true
  for p in frappe-ports nginx mailpit redis; do
    echo "── $p"
    timeout 10 process-compose -U -u "$PC_SOCKET_PATH" process logs "$p" --tail 40 || true
  done
}

# Inside a dev shell whose bench is up: its ports.json, checked against what
# answers on them and what devguard and the site config were handed.
cmd_report_ports() {
  local f="$FRAPPE_NIX_PORTS_FILE" web id
  frappe-nix-ports > /dev/null || { cmd_pc_state; fail "frappe-nix-ports: the bench is not up"; }
  web="$(jq -r .web "$f")"
  id="$(jq -r .bench_id "$f")"
  [ "$(curl -s --max-time 5 "http://127.0.0.1:$web/__frappe_nix/bench")" = "$id" ] || fail "port $web does not answer as $id"
  curl -s -o /dev/null -D - --max-time 5 "http://127.0.0.1:$web/" | grep -qi "^x-frappe-nix-bench: $id" || fail "no X-Frappe-Nix-Bench: $id header on $web"
  jq -e --slurpfile p "$f" '.guards.mail.port == $p[0].mailpit_smtp and .guards.mail.http_port == $p[0].mailpit_ui' \
    "$DEVENV_RUNTIME/devguard-runtime.json" > /dev/null || fail "devguard-runtime.json is not this bench's lease"
  [ "$(jq -r .webserver_port "$FRAPPE_BENCH_ROOT/sites/common_site_config.json")" = "$web" ] || fail "webserver_port is not the leased $web"
  echo "PORTS=$(jq -c . "$f")"
}

# After a failure, outside any shell: every ports.json left on this host's
# runtime directories, and who holds each of its ports, into $WORK/ports/.
cmd_collect_ports() {
  local out="$WORK/ports" f n=0 port
  mkdir -p "$out"
  while IFS= read -r f; do
    n=$((n + 1))
    cp "$f" "$out/ports-$n.json"
    for port in $(jq -r '[.web, .mailpit_smtp, .mailpit_ui, .mailpit_pop3] | map(select(. != null)) | .[]' "$f"); do
      echo "$port: $(python3 "$FN/lib/port-lease.py" who "$port")"
    done >> "$out/who-$n.txt"
  done < <(find /tmp "${XDG_RUNTIME_DIR:-/nonexistent}" -maxdepth 3 -path '*/frappe-nix/ports.json' 2> /dev/null)
  echo "collect-ports: $n ports.json into $out"
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
hooks = app / "standards_fixture" / "hooks.py"
s = hooks.read_text().replace("required_apps = []", 'required_apps = ["erpnext"]')
s = s.replace(
	'\t"ToDo": ["standards_fixture.overrides.todo.FixtureToDo"],\n',
	'\t"ToDo": ["standards_fixture.overrides.todo.FixtureToDo"],\n'
	'\t# The A.0 #4 shape: an extension of erpnext\'s extension rather than of Address.\n'
	'\t"Address": ["standards_fixture.overrides.address.FixtureAddress"],\n',
)
assert "FixtureAddress" in s
hooks.write_text(s)
(app / "standards_fixture" / "overrides" / "address.py").write_text(
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
  [ "$(json "$out/frappe-test-report.json" "(d['stages']['site'], d['stages']['tests'])")" = "('ok', 'ok')" ] || fail "the site or the tests failed with erpnext"
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
  tests-off) cmd_tests_off ;;
  up-and-report) cmd_up_and_report ;;
  rename) cmd_rename ;; # resume after main's ports, by hand
  rename-site) cmd_rename_site ;;
  erpnext) cmd_erpnext ;;
  parallel) cmd_parallel ;;
  report-ports) cmd_report_ports ;;
  pc-state) cmd_pc_state ;;
  collect-ports) cmd_collect_ports ;;
  *)
    echo "usage: $0 main|erpnext|parallel|collect-ports" >&2
    exit 2
    ;;
esac
