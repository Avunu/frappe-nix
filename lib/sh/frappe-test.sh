# shellcheck shell=bash
# frappe-test: run an app's tests the way CI does (docs/app-standards/spec.md §5.1).
#
# Sourced-free: lib/scripts.d/frappe-test.nix execs this with bash, with jq,
# curl, git, coreutils, frappe-nix, nixfmt, statix and deadnix on PATH, from an
# app-mode dev shell, which provides FRAPPE_BENCH_ROOT, FRAPPE_SITE,
# DEVENV_ROOT, PC_SOCKET_PATH, `devenv`, `process-compose`, `provision-site`
# and `uv`.
#
# Stages, in order; every stage after 3 runs even when an earlier one failed:
#
#   1 up            devenv up -D unless process-compose is already listening   (exit 10)
#   2 site          provision-site, allow_tests, the [tool.frappe-nix.tests] setup (exit 10)
#   3 tests         coverage run … frappe … run-tests --app A                   verdict 1
#   4 coverage      coverage report (fail_under) and the upward ratchet         verdict 2
#   5 testmap       frappe-nix testmap                                          verdict 3
#   6 composition   frappe-nix composition                                      verdict 4
#   7 ty            uv run --frozen --project tools ty check                    verdict 5
#   8 nix-lint      nixfmt --check, statix, deadnix                             verdict 6
#   8b shell-checks [tool.frappe-nix] shell-checks, each leaving the tree as found verdict 7
#   9 report        frappe-test-report.json, summary.md, $GITHUB_STEP_SUMMARY
#
# --ci runs exactly the stages whose modules the app's resolved configuration
# turns on (`frappe-nix test-plan`): with `tests` off, stages 1 to 6 are skipped
# and no bench is started. An app without [tool.frappe-nix] has no
# configuration to read and gets the recommended profile's stages. The coverage
# target and raise margin of the upward ratchet come from the same plan, with
# or without --ci.
#
# The exit status is the verdict of the first failing stage, 10 for an
# environment failure (stage 1 or 2, an unreadable configuration, or no dev
# shell), 64 (EX_USAGE) for a usage error, and 0 otherwise. Neither collides
# with a verdict.
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: frappe-test [--app APP] [--site SITE] [--reuse-site] [--keep-up | --down] [--ci]
                   [--module M] [--doctype DT] [--test T]
                   [--no-coverage] [--no-testmap] [--no-composition] [--ty] [--nix-lint] [--shell-checks]
                   [--junit PATH] [--out DIR]

Runs the app's tests on a site of this dev shell's bench, with coverage scoped to the
app, then the testmap and composition checks. --ci runs what CI's `ci / test` runs:
the stages the app's configuration turns on ([tool.frappe-nix] and its profile; ty,
the Nix linters and the shell checks among them), a JUnit report and the step summary.

  --app APP          the app under test (default: the app this repository is)
  --site SITE        the site (default: $FRAPPE_SITE)
  --reuse-site       keep an existing site instead of recreating it
  --keep-up          leave the bench running afterwards (default: stop what this started)
  --down             stop a bench --keep-up left running, and exit
  --ci               the configured stages, plus --junit $OUT/junit.xml
  --module, --doctype, --test
                     run only these tests (passed to run-tests); skips the coverage
                     gate and the testmap
  --no-coverage      run the tests without coverage (skips the gate and the testmap)
  --no-testmap, --no-composition, --ty, --nix-lint, --shell-checks
                     turn one check off or on, over --ci's choice
  --junit PATH       write a JUnit report here
  --out DIR          where the reports go (default: $DEVENV_ROOT/.dev-dist/test)

Exit status: the first failing stage's verdict: 1 tests, 2 coverage, 3 testmap,
4 composition, 5 ty, 6 nix-lint, 7 shell checks; 10 when the bench or the site
could not be brought up, the configuration could not be read, or outside the
app dev shell; 64 for a usage error; 0 when everything passed.
EOF
}

APP="" SITE="${FRAPPE_SITE:-}" REUSE=0 KEEP_UP=0 DOWN=0 CI=0
MODULE="" DOCTYPE="" TESTS=()
# Empty: not given on the command line, so --ci's plan (or the default) decides.
COVERAGE="" TESTMAP="" COMPOSITION="" TY="" NIXLINT="" SHELLCHECKS=""
JUNIT="" OUT=""

usage_error() {
  echo "frappe-test: $1" >&2
  exit 64
}
need() { [ "$#" -ge 2 ] && [ -n "$2" ] || usage_error "$1 needs a value"; }
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
      usage >&2
      usage_error "unknown argument: $1"
      ;;
  esac
  shift
done

if [ -z "${FRAPPE_BENCH_ROOT:-}" ] || [ -z "${DEVENV_ROOT:-}" ]; then
  echo "frappe-test: run it inside the app dev shell (nix develop)" >&2
  exit 10
fi
REPO="$(realpath "$DEVENV_ROOT")"
BENCH="$FRAPPE_BENCH_ROOT"
PY="$BENCH/env/bin/python"

if [ "$DOWN" = 1 ]; then
  exec process-compose -U -u "${PC_SOCKET_PATH:?}" down
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
[ -n "$APP" ] || usage_error "cannot tell which app this is; pass --app"
[ -n "$SITE" ] || usage_error "no site: set FRAPPE_SITE or pass --site"

FILTERED=0
if [ -n "$MODULE" ] || [ -n "$DOCTYPE" ] || [ "${#TESTS[@]}" -gt 0 ]; then
  FILTERED=1
fi

for stale in .coverage coverage.json coverage.xml testmap.json testmap.md composition.json \
  composition.md frappe-test-report.json summary.md shell-checks.log; do
  rm -f -- "${OUT:?}/$stale"
done
[ -z "$JUNIT" ] || rm -f -- "$JUNIT"

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
  frappe-nix test-report "${args[@]}" || echo "frappe-test: could not write the report" >&2
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

# ── 0. plan ──────────────────────────────────────────────────────────────────
# What the app's resolved configuration says (spec §5.1, §8.2): the stages --ci
# runs, the upward ratchet's target and margin, the setup steps, the shell
# checks and the siblings. Read once, before anything starts.
if ! PLAN="$(frappe-nix test-plan --pyproject "$REPO/pyproject.toml")"; then
  echo "::error::frappe-test: could not read the app's configuration (frappe-nix test-plan); see above" >&2
  ENV_FAILED=1
  finish
fi
plan() { jq -r "$1" <<< "$PLAN"; }
plan_on() { if [ "$(plan ".stages[\"$1\"]")" = true ]; then echo 1; else echo 0; fi; }
# A stage's switch: the command line's, else --ci's plan, else the interactive default.
pick() { # <given> <stage> <interactive default>
  if [ -n "$1" ]; then
    echo "$1"
  elif [ "$CI" = 1 ]; then
    plan_on "$2"
  else
    echo "$3"
  fi
}
BENCH_STAGES="$(pick "" tests 1)"
COVERAGE="$(pick "$COVERAGE" coverage 1)"
TESTMAP="$(pick "$TESTMAP" testmap 1)"
COMPOSITION="$(pick "$COMPOSITION" composition 1)"
TY="$(pick "$TY" ty 0)"
NIXLINT="$(pick "$NIXLINT" nix-lint 0)"
SHELLCHECKS="$(pick "$SHELLCHECKS" shell-checks 0)"
RATCHET_TARGET="$(plan '.coverage.target')"
RATCHET_MARGIN="$(plan '.coverage["raise-margin"]')"
SETUP=() CHECKS=() SIBLINGS=()
mapfile -t SETUP < <(plan '.setup[]')
mapfile -t CHECKS < <(plan '.["shell-checks"][]')
mapfile -t SIBLINGS < <(plan '.siblings[]')
if [ "$(plan .opted_in)" = true ]; then
  echo "frappe-test: profile $(plan .profile); configured stages: $(plan '[.stages | to_entries[] | select(.value) | .key] | join(", ")')"
else
  echo "frappe-test: no [tool.frappe-nix] in pyproject.toml; the recommended profile's stages"
fi

# Stages 1 to 6: they need the bench, so they run only while the tests module
# is on (with --ci) or the tests were asked for.
bench_stages() {
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
      # Its own message (a taken port and the FRAPPE_NIX_PORT_OFFSET hint) is
      # just above; nothing of this bench's is up to wait for.
      up_rc=$?
      # Whatever it did start before failing is ours to stop.
      if [ -n "${PC_SOCKET_PATH:-}" ] && process-compose -U -u "$PC_SOCKET_PATH" process list > /dev/null 2>&1; then
        process-compose -U -u "$PC_SOCKET_PATH" down > /dev/null 2>&1 || true
      fi
      endgroup
      echo "::error::frappe-test: devenv up -D failed (exit $up_rc); see its output above" >&2
      STAGE[up]=error
      ENV_FAILED=1
      finish
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
    # With the app processes stopped: each start of the runtime (or of `web`,
    # without it) re-runs the frappe:apps-reconcile task, and once new-site has
    # made the site's directory that task installs the same apps alongside
    # provision-site, which then fails on a duplicate Module Def or Role. Started
    # again afterwards, the task finds every app installed.
    stopped=()
    if [ -n "${PC_SOCKET_PATH:-}" ]; then
      for proc in runtime web worker scheduler socketio; do
        # Present at all (it may be between restarts, which `stop` refuses).
        if process-compose -U -u "$PC_SOCKET_PATH" process get "$proc" > /dev/null 2>&1; then
          process-compose -U -u "$PC_SOCKET_PATH" process stop "$proc" > /dev/null 2>&1 || true
          stopped+=("$proc")
        fi
      done
    fi
    # bench new-site asks for the MariaDB root password through getpass, which
    # reads a line from stdin without a tty; the dev bench's root has none.
    # provision-site creates $FRAPPE_SITE: point it at the site under test.
    printf '\n' | FRAPPE_SITE="$SITE" provision-site "${FRAPPE_TEST_ADMIN_PASSWORD:-admin}" || site_ok=0
    for proc in "${stopped[@]}"; do
      process-compose -U -u "$PC_SOCKET_PATH" process start "$proc" > /dev/null 2>&1 || true
    done
  fi
  [ "$site_ok" = 0 ] || bench --site "$SITE" set-config allow_tests true || site_ok=0

  if [ "$site_ok" = 1 ] && [ "${#SETUP[@]}" -eq 0 ]; then
    # The default (spec §2.1): erpnext's test bootstrap when erpnext is a
    # sibling, else the setup wizard frappe's own UI tests complete.
    if printf '%s\n' "${SIBLINGS[@]}" | grep -qx erpnext \
      || grep -qx erpnext "$BENCH/sites/apps.txt" 2> /dev/null; then
      SETUP=("module:erpnext.tests.bootstrap_test_data")
    else
      SETUP=("execute:frappe.utils.install.complete_setup_wizard")
    fi
  fi
  if [ "$site_ok" = 1 ]; then
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
        frappe-nix coverage-ratchet --coverage-json "$OUT/coverage.json" --pyproject "$REPO/pyproject.toml" \
          --target "$RATCHET_TARGET" --raise-margin "$RATCHET_MARGIN"
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
    frappe-nix testmap --python "$PY" --site "$SITE" --app "$APP" --sites-path "$BENCH/sites" \
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
    frappe-nix composition --python "$PY" --site "$SITE" --app "$APP" --sites-path "$BENCH/sites" \
      --out "$OUT/composition.json" --markdown "$OUT/composition.md"
    case "$?" in 0) STAGE[composition]=ok ;; 1) STAGE[composition]=failed ;; *) STAGE[composition]=error ;; esac
    endgroup
  fi
}

if [ "$BENCH_STAGES" = 0 ]; then
  # The tests module is off: no bench, no site, no tests (spec §5.1).
  echo "frappe-test: the tests module is off; skipping stages 1 to 6 (no bench is started)"
  for name in up site tests coverage testmap composition; do STAGE[$name]=skipped; done
else
  bench_stages
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
# already dirty is judged only on what the command changed. The scratch index
# starts as a copy of the real one: `git add -A` on an empty index would skip a
# tracked file under an ignored path (generated output committed with
# `git add -f`), which is exactly what these checks are for.
snapshot() {
  local index real
  index="$(mktemp)"
  rm -f "$index"
  real="$(cd "$REPO" && git rev-parse --path-format=absolute --git-path index)"
  [ ! -f "$real" ] || cp "$real" "$index"
  (cd "$REPO" && GIT_INDEX_FILE="$index" git add -A -- . \
    ':(exclude).frappe-nix' ':(exclude).devenv' ':(exclude).direnv' ':(exclude).dev-dist' \
    && GIT_INDEX_FILE="$index" git write-tree)
  rm -f "$index"
}
if [ "$SHELLCHECKS" = 0 ]; then
  STAGE[shell-checks]=skipped
else
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
