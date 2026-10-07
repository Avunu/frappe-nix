# shellcheck shell=bash
# frappe-test: run an app's tests the way CI does (docs/ironclad/spec.md §5.1).
#
# Sourced-free: lib/scripts.d/frappe-test.nix execs this with bash, with jq,
# curl, git and coreutils on PATH, from an app-mode dev shell, which provides
# FRAPPE_BENCH_ROOT, FRAPPE_SITE, DEVENV_ROOT, PC_SOCKET_PATH, `devenv`,
# `process-compose`, `provision-site`, `ironclad`, `uv`, and nixfmt, statix and
# deadnix.
#
# Stages, in order; every stage after 3 runs even when an earlier one failed:
#
#   1 up            devenv up -D unless process-compose is already listening   (exit 10)
#   2 site          provision-site, allow_tests, the [tool.ironclad.test] setup (exit 10)
#   3 tests         coverage run … frappe … run-tests --app A                   verdict 1
#   4 coverage      coverage report (fail_under) and the upward ratchet         verdict 2
#   5 testmap       ironclad testmap                                            verdict 3
#   6 composition   ironclad composition                                        verdict 4
#   7 ty            uv run --frozen --project tools ty check                    verdict 5
#   8 nix-lint      nixfmt --check, statix, deadnix                             verdict 6
#   8b shell-checks [tool.ironclad] shell-checks, each leaving the tree as found verdict 7
#   9 report        ironclad-report.json, summary.md, $GITHUB_STEP_SUMMARY
#
# The exit status is the verdict of the first failing stage, 10 for an
# environment failure in stage 1 or 2, and 0 otherwise.
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: frappe-test [--app APP] [--site SITE] [--reuse-site] [--keep-up | --down] [--ci]
                   [--module M] [--doctype DT] [--test T]
                   [--no-coverage] [--no-testmap] [--no-composition] [--ty] [--nix-lint] [--shell-checks]
                   [--junit PATH] [--out DIR]

Runs the app's tests on a site of this dev shell's bench, with coverage scoped to the
app, then the testmap and composition checks. --ci adds ty, the Nix linters and the
shell checks, a JUnit report and the step summary: what CI's `ci / test` runs.

  --app APP          the app under test (default: the app this repository is)
  --site SITE        the site (default: $FRAPPE_SITE)
  --reuse-site       keep an existing site instead of recreating it
  --keep-up          leave the bench running afterwards (default: stop what this started)
  --down             stop a bench --keep-up left running, and exit
  --ci               --ty --nix-lint --shell-checks --junit $OUT/junit.xml
  --module, --doctype, --test
                     run only these tests (passed to run-tests); skips the coverage
                     gate and the testmap
  --no-coverage      run the tests without coverage (skips the gate and the testmap)
  --no-testmap, --no-composition, --ty, --nix-lint, --shell-checks
                     turn one check off or on
  --junit PATH       write a JUnit report here
  --out DIR          where the reports go (default: $DEVENV_ROOT/.dev-dist/test)

Exit status: the first failing stage's verdict: 1 tests, 2 coverage, 3 testmap,
4 composition, 5 ty, 6 nix-lint, 7 shell checks; 10 when the bench or the site
could not be brought up; 0 when everything passed.
EOF
}

APP="" SITE="${FRAPPE_SITE:-}" REUSE=0 KEEP_UP=0 DOWN=0 CI=0
MODULE="" DOCTYPE="" TESTS=()
COVERAGE=1 TESTMAP=1 COMPOSITION=1 TY=0 NIXLINT=0 SHELLCHECKS=0
JUNIT="" OUT=""

need() { [ "$#" -ge 2 ] && [ -n "$2" ] || {
  echo "frappe-test: $1 needs a value" >&2
  exit 2
}; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --app) need "$@"; APP="$2"; shift ;;
    --site) need "$@"; SITE="$2"; shift ;;
    --reuse-site) REUSE=1 ;;
    --keep-up) KEEP_UP=1 ;;
    --down) DOWN=1 ;;
    --ci) CI=1 ;;
    --module) need "$@"; MODULE="$2"; shift ;;
    --doctype) need "$@"; DOCTYPE="$2"; shift ;;
    --test) need "$@"; TESTS+=("$2"); shift ;;
    --no-coverage) COVERAGE=0 ;;
    --no-testmap) TESTMAP=0 ;;
    --no-composition) COMPOSITION=0 ;;
    --ty) TY=1 ;;
    --nix-lint) NIXLINT=1 ;;
    --shell-checks) SHELLCHECKS=1 ;;
    --junit) need "$@"; JUNIT="$2"; shift ;;
    --out) need "$@"; OUT="$2"; shift ;;
    -h | --help) usage; exit 0 ;;
    *)
      echo "frappe-test: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

: "${FRAPPE_BENCH_ROOT:?frappe-test: run it inside the app dev shell (nix develop)}"
: "${DEVENV_ROOT:?frappe-test: run it inside the app dev shell (nix develop)}"
REPO="$(realpath "$DEVENV_ROOT")"
BENCH="$FRAPPE_BENCH_ROOT"
PY="$BENCH/env/bin/python"

if [ "$DOWN" = 1 ]; then
  exec process-compose -U -u "${PC_SOCKET_PATH:?}" down
fi

if [ "$CI" = 1 ]; then
  TY=1 NIXLINT=1 SHELLCHECKS=1
fi
OUT="${OUT:-$REPO/.dev-dist/test}"
mkdir -p "$OUT"
OUT="$(realpath "$OUT")"
if [ "$CI" = 1 ] && [ -z "$JUNIT" ]; then
  JUNIT="$OUT/junit.xml"
fi
[ -z "$JUNIT" ] || JUNIT="$(realpath -m "$JUNIT")"

# The app under development is the one apps/<name> links back to this checkout.
if [ -z "$APP" ]; then
  for link in "$BENCH"/apps/*; do
    if [ -L "$link" ] && [ "$(realpath "$link")" = "$REPO" ]; then
      APP="$(basename "$link")"
      break
    fi
  done
fi
if [ -z "$APP" ]; then
  echo "frappe-test: cannot tell which app this is; pass --app" >&2
  exit 2
fi
if [ -z "$SITE" ]; then
  echo "frappe-test: no site: set FRAPPE_SITE or pass --site" >&2
  exit 2
fi

FILTERED=0
if [ -n "$MODULE" ] || [ -n "$DOCTYPE" ] || [ "${#TESTS[@]}" -gt 0 ]; then
  FILTERED=1
fi

rm -f "$OUT"/{.coverage,coverage.json,coverage.xml,testmap.json,testmap.md,composition.json,composition.md,ironclad-report.json,summary.md,shell-checks.log}
[ -z "$JUNIT" ] || rm -f "$JUNIT"

declare -A STAGE=()
ENV_FAILED=0
TY_DIAGNOSTICS="" TY_IGNORES=""

group() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::group::$1"; else echo "── $1 ──"; fi; }
endgroup() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::endgroup::"; fi; }
bench() { (cd "$BENCH" && _FRAPPE_BENCH_RAW=1 "$BENCH/env/bin/bench" "$@"); }

report() {
  local args=(--out "$OUT" --app "$APP" --repo-root "$REPO" --pyproject "$REPO/pyproject.toml")
  local name
  for name in "${!STAGE[@]}"; do args+=(--stage "$name=${STAGE[$name]}"); done
  [ -z "$JUNIT" ] || args+=(--junit "$JUNIT")
  [ "$ENV_FAILED" = 0 ] || args+=(--environment-failure)
  [ -z "$TY_DIAGNOSTICS" ] || args+=(--ty-diagnostics "$TY_DIAGNOSTICS" --ty-ignores "${TY_IGNORES:-0}")
  [ ! -s "$OUT/shell-checks.log" ] || args+=(--shell-checks-log "$OUT/shell-checks.log")
  ironclad test-report "${args[@]}" || echo "frappe-test: could not write the report" >&2
}

# The first failing stage's verdict, in spec order; 10 for the environment.
verdict() {
  [ "$ENV_FAILED" = 0 ] || return 10
  local pair name code
  for pair in tests:1 coverage:2 testmap:3 composition:4 ty:5 nix-lint:6 shell-checks:7; do
    name="${pair%:*}" code="${pair#*:}"
    case "${STAGE[$name]:-}" in failed | error) return "$code" ;; esac
  done
  return 0
}

finish() {
  group "report"
  report
  endgroup
  verdict
  local rc=$?
  echo "frappe-test: exit $rc"
  exit "$rc"
}

# ── 1. up ────────────────────────────────────────────────────────────────────
group "up"
STARTED=0
if [ -n "${PC_SOCKET_PATH:-}" ] && process-compose -U -u "$PC_SOCKET_PATH" process list > /dev/null 2>&1; then
  echo "frappe-test: the bench is already up"
else
  echo "frappe-test: starting the bench (devenv up -D)"
  if DEVENV_IN_DIRENV_SHELL=true PC_TUI_ENABLED=0 devenv up -D; then
    STARTED=1
  else
    echo "frappe-test: devenv up failed" >&2
  fi
fi
if [ "$STARTED" = 1 ] && [ "$KEEP_UP" = 0 ]; then
  trap 'process-compose -U -u "$PC_SOCKET_PATH" down > /dev/null 2>&1 || true' EXIT
fi

deadline=$((SECONDS + 300))
db_up=0
while [ "$SECONDS" -lt "$deadline" ]; do
  if mariadb-admin --socket="${FRAPPE_DB_SOCKET:-}" --connect-timeout=3 ping > /dev/null 2>&1; then
    db_up=1
    break
  fi
  sleep 2
done
web_up=0
port=""
while [ "$db_up" = 1 ] && [ "$SECONDS" -lt "$deadline" ]; do
  port="$(jq -r '.webserver_port // empty' "$BENCH/sites/common_site_config.json" 2> /dev/null || true)"
  # Any HTTP answer is "up": nginx says 404 until the site exists.
  if [ -n "$port" ] && curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$port/"; then
    web_up=1
    break
  fi
  sleep 2
done
endgroup
if [ "$db_up" = 0 ] || [ "$web_up" = 0 ]; then
  echo "::error::frappe-test: the bench did not come up within 300 s (database: $db_up, web on port ${port:-?}: $web_up)" >&2
  STAGE[up]=error
  ENV_FAILED=1
  finish
fi
STAGE[up]=ok
echo "frappe-test: the bench is up on http://127.0.0.1:$port"

# ── 2. site ──────────────────────────────────────────────────────────────────
group "site $SITE"
site_ok=1
if [ "$REUSE" = 1 ] && [ -d "$BENCH/sites/$SITE" ]; then
  echo "frappe-test: reusing $SITE"
else
  # bench new-site asks for the MariaDB root password through getpass, which
  # reads a line from stdin without a tty; the dev bench's root has none.
  printf '\n' | provision-site "${FRAPPE_TEST_ADMIN_PASSWORD:-admin}" || site_ok=0
fi
[ "$site_ok" = 0 ] || bench --site "$SITE" set-config allow_tests true || site_ok=0

if [ "$site_ok" = 1 ]; then
  mapfile -t SETUP < <(ironclad config test.setup --pyproject "$REPO/pyproject.toml")
  if [ "${#SETUP[@]}" -eq 0 ]; then
    # The default (spec §2.1): erpnext's test bootstrap when erpnext is a
    # sibling, else the setup wizard frappe's own UI tests complete.
    if ironclad config siblings --pyproject "$REPO/pyproject.toml" | grep -qx erpnext \
      || grep -qx erpnext "$BENCH/sites/apps.txt" 2> /dev/null; then
      SETUP=("module:erpnext.tests.bootstrap_test_data")
    else
      SETUP=("execute:frappe.utils.install.complete_setup_wizard")
    fi
  fi
  for step in "${SETUP[@]}"; do
    echo "frappe-test: setup $step"
    case "$step" in
      module:*) bench --site "$SITE" run-tests --module "${step#module:}" || site_ok=0 ;;
      execute:*) bench --site "$SITE" execute "${step#execute:}" || site_ok=0 ;;
      *)
        echo "frappe-test: unknown setup step $step (module:<dotted> or execute:<dotted>)" >&2
        site_ok=0
        ;;
    esac
    [ "$site_ok" = 1 ] || break
  done
fi
endgroup
if [ "$site_ok" = 0 ]; then
  echo "::error::frappe-test: could not prepare $SITE" >&2
  STAGE[site]=error
  ENV_FAILED=1
  finish
fi
STAGE[site]=ok

# ── 3. tests ─────────────────────────────────────────────────────────────────
group "tests: $APP"
RUN_TESTS=(-m frappe.utils.bench_helper frappe --site "$SITE" run-tests --app "$APP")
[ -z "$MODULE" ] || RUN_TESTS+=(--module "$MODULE")
[ -z "$DOCTYPE" ] || RUN_TESTS+=(--doctype "$DOCTYPE")
for t in "${TESTS[@]}"; do RUN_TESTS+=(--test "$t"); done
[ -z "$JUNIT" ] || RUN_TESTS+=(--junit-xml-output "$JUNIT")
# Never frappe's own --coverage: in app mode it measures every app under the
# repository's .frappe-nix/bench/apps (spec S17). --source is the package's real
# directory, which nothing of frappe's or a sibling's is under.
if [ "$COVERAGE" = 1 ]; then
  (cd "$BENCH/sites" && "$PY" -m coverage run \
    --rcfile="$REPO/pyproject.toml" --data-file="$OUT/.coverage" \
    --source="$REPO/$APP" \
    "${RUN_TESTS[@]}")
else
  (cd "$BENCH/sites" && "$PY" "${RUN_TESTS[@]}")
fi
tests_rc=$?
endgroup
if [ "$tests_rc" = 0 ]; then STAGE[tests]=ok; else STAGE[tests]=failed; fi

# ── 4. coverage ──────────────────────────────────────────────────────────────
HAVE_COVERAGE=0
if [ "$COVERAGE" = 1 ] && [ -f "$OUT/.coverage" ]; then
  group "coverage"
  # From the repository, so coverage's file names are repo-relative.
  cov() { (cd "$REPO" && "$PY" -m coverage "$@" --rcfile="$REPO/pyproject.toml" --data-file="$OUT/.coverage"); }
  cov json -q -o "$OUT/coverage.json" > /dev/null && HAVE_COVERAGE=1
  cov xml -q -o "$OUT/coverage.xml" > /dev/null || true
  if [ "$FILTERED" = 1 ]; then
    cov report || true
    echo "frappe-test: a filter is set; the coverage gate is skipped"
    STAGE[coverage]=skipped
  else
    cov report
    report_rc=$?
    if [ "$report_rc" = 0 ] && [ "$HAVE_COVERAGE" = 1 ]; then
      ironclad coverage-ratchet --coverage-json "$OUT/coverage.json" --pyproject "$REPO/pyproject.toml"
      ratchet_rc=$?
      case "$ratchet_rc" in
        0) STAGE[coverage]=ok ;;
        1) STAGE[coverage]=failed ;;
        *) STAGE[coverage]=error ;;
      esac
    elif [ "$report_rc" = 2 ]; then
      echo "frappe-test: coverage is below [tool.coverage.report] fail_under"
      STAGE[coverage]=failed
    else
      STAGE[coverage]=error
    fi
  fi
  endgroup
else
  STAGE[coverage]=skipped
fi

# ── 5. testmap ───────────────────────────────────────────────────────────────
if [ "$TESTMAP" = 0 ] || [ "$FILTERED" = 1 ]; then
  STAGE[testmap]=skipped
elif [ "$HAVE_COVERAGE" = 0 ]; then
  echo "frappe-test: no coverage data, so no testmap" >&2
  STAGE[testmap]=skipped
else
  group "testmap"
  ironclad testmap --python "$PY" --site "$SITE" --app "$APP" --sites-path "$BENCH/sites" \
    --coverage-json "$OUT/coverage.json" --repo-root "$REPO" --pyproject "$REPO/pyproject.toml" \
    --out "$OUT/testmap.json" --markdown "$OUT/testmap.md"
  case "$?" in 0) STAGE[testmap]=ok ;; 1) STAGE[testmap]=failed ;; *) STAGE[testmap]=error ;; esac
  endgroup
fi

# ── 6. composition ───────────────────────────────────────────────────────────
if [ "$COMPOSITION" = 0 ]; then
  STAGE[composition]=skipped
else
  group "composition"
  ironclad composition --python "$PY" --site "$SITE" --app "$APP" --sites-path "$BENCH/sites" \
    --out "$OUT/composition.json" --markdown "$OUT/composition.md"
  case "$?" in 0) STAGE[composition]=ok ;; 1) STAGE[composition]=failed ;; *) STAGE[composition]=error ;; esac
  endgroup
fi

# ── 7. ty ────────────────────────────────────────────────────────────────────
if [ "$TY" = 0 ]; then
  STAGE[ty]=skipped
elif [ ! -f "$REPO/tools/pyproject.toml" ]; then
  echo "::warning::frappe-test: no tools/pyproject.toml, so no ty (frappe-init --sync renders it)"
  STAGE[ty]=skipped
else
  group "ty"
  ty_log="$(mktemp)"
  (cd "$REPO" && uv run --frozen --project tools ty check --python "$BENCH/env" --output-format concise) 2>&1 | tee "$ty_log"
  ty_rc="${PIPESTATUS[0]}"
  TY_DIAGNOSTICS="$(grep -oE 'Found [0-9]+ diagnostic' "$ty_log" | grep -oE '[0-9]+' | tail -1)"
  TY_DIAGNOSTICS="${TY_DIAGNOSTICS:-0}"
  TY_IGNORES="$(cd "$REPO" && git grep -o 'ty: *ignore' -- '*.py' | wc -l | tr -d ' ')"
  rm -f "$ty_log"
  if [ "$ty_rc" = 0 ]; then STAGE[ty]=ok; else STAGE[ty]=failed; fi
  endgroup
fi

# ── 8. nix-lint ──────────────────────────────────────────────────────────────
if [ "$NIXLINT" = 0 ]; then
  STAGE[nix-lint]=skipped
else
  group "nix-lint"
  lint_ok=1
  mapfile -t nix_files < <(cd "$REPO" && git ls-files 'nix/*.nix')
  (cd "$REPO" && nixfmt --check flake.nix "${nix_files[@]}") || lint_ok=0
  (cd "$REPO" && statix check --ignore '.frappe-nix/*' --ignore '.devenv/*' .) || lint_ok=0
  (cd "$REPO" && deadnix --fail --exclude .frappe-nix .devenv -- .) || lint_ok=0
  if [ "$lint_ok" = 1 ]; then STAGE[nix-lint]=ok; else STAGE[nix-lint]=failed; fi
  endgroup
fi

# ── 8b. shell checks ─────────────────────────────────────────────────────────
# Each command must leave the tree as it found it: a generator whose output is
# committed proves that output fresh. Compared as trees (a scratch index over
# the work tree, untracked files included, ignored ones not), so a tree that was
# already dirty is judged only on what the command changed.
snapshot() {
  local index
  index="$(mktemp)"
  rm -f "$index"
  (cd "$REPO" && GIT_INDEX_FILE="$index" git add -A -- . \
    ':(exclude).frappe-nix' ':(exclude).devenv' ':(exclude).direnv' ':(exclude).dev-dist' \
    && GIT_INDEX_FILE="$index" git write-tree)
  rm -f "$index"
}
if [ "$SHELLCHECKS" = 0 ]; then
  STAGE[shell-checks]=skipped
else
  mapfile -t CHECKS < <(ironclad config shell-checks --pyproject "$REPO/pyproject.toml")
  if [ "${#CHECKS[@]}" -eq 0 ]; then
    STAGE[shell-checks]=ok
  else
    group "shell checks"
    checks_ok=1
    for check in "${CHECKS[@]}"; do
      before="$(snapshot)"
      echo "frappe-test: shell check: $check"
      (cd "$REPO" && bash -c "$check")
      check_rc=$?
      after="$(snapshot)"
      if [ "$check_rc" != 0 ]; then
        checks_ok=0
        printf '%s\n  exited %s\n' "$check" "$check_rc" >> "$OUT/shell-checks.log"
      elif [ -z "$before" ] || [ -z "$after" ]; then
        checks_ok=0
        printf '%s\n  could not compare the tree before and after\n' "$check" >> "$OUT/shell-checks.log"
      elif [ "$before" != "$after" ]; then
        checks_ok=0
        {
          printf '%s\n  left the tree changed:\n' "$check"
          (cd "$REPO" && git diff --no-color --name-status "$before" "$after" | sed 's/^/    /')
          (cd "$REPO" && git diff --no-color "$before" "$after" | head -n 200)
        } >> "$OUT/shell-checks.log"
      fi
    done
    if [ "$checks_ok" = 1 ]; then
      STAGE[shell-checks]=ok
    else
      cat "$OUT/shell-checks.log"
      STAGE[shell-checks]=failed
    fi
    endgroup
  fi
fi

finish
