#!/usr/bin/env bash
# Checks for `bench-new-app` — scaffolding an app with upstream's `bench
# new-app` and registering it in the uv workspace and the registry.
#
# Usage: bench-new-app.sh <path-to-rendered-bench-new-app-script>
#
# Frappe-free: a stub `bench` stands in for upstream's scaffolder (writing the
# files it would, then failing as its pip-install step does in a read-only
# environment), and a stub `uv` for the sync.
set -euo pipefail

SCRIPT="$1"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

fails=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no() {
  printf '  \033[31m✗\033[0m %s\n' "$1"
  fails=$((fails + 1))
}
check() { # <description> <command...>
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then ok "$desc"; else no "$desc"; fi
}
check_not() { # <description> <command...> — passes when the command fails
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}
says() { grep -qF -- "$1" <<< "$OUT"; }
toml_get() { # <file> <python expression over d>
  python3 -c "import sys, tomllib; d = tomllib.load(open(sys.argv[1], 'rb')); print($2)" "$1"
}

# ── stubs ───────────────────────────────────────────────────────────────
# bench new-app --no-git <app>: writes the app (unless STUB_NO_SCAFFOLD) and
# exits STUB_NEW_APP_RC, 1 by default: upstream's pip install fails here.
BIN="$ROOT/bin"
mkdir -p "$BIN"
printf '#!%s\n' "$(command -v bash)" > "$BIN/bench"
cat >> "$BIN/bench" <<'STUB'
echo "$PWD ${_FRAPPE_BENCH_RAW:-} $*" >> "$BENCH_CALLS"
if [ "${1:-}" = new-app ] && [ "${2:-}" = --no-git ]; then
  app="$3"
  if [ -z "${STUB_NO_SCAFFOLD:-}" ]; then
    mkdir -p "apps/$app/$app"
    printf '__version__ = "0.0.1"\n' > "apps/$app/$app/__init__.py"
    printf 'app_name = "%s"\n' "$app" > "apps/$app/$app/hooks.py"
    printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$app" > "apps/$app/pyproject.toml"
  fi
  exit "${STUB_NEW_APP_RC:-1}"
fi
exit 0
STUB
printf '#!%s\n' "$(command -v bash)" > "$BIN/uv"
cat >> "$BIN/uv" <<'STUB'
echo "$PWD $*" >> "$UV_CALLS"
STUB
chmod +x "$BIN/bench" "$BIN/uv"
export PATH="$BIN:$PATH"
export UV_CALLS="$ROOT/uv-calls" BENCH_CALLS="$ROOT/bench-calls"

BENCH="$ROOT/bench"
mkdir -p "$BENCH/apps" "$BENCH/sites"
cd "$BENCH"
cat > pyproject.toml <<'TOML'
[project]
name = "fixture-bench"

[tool.uv.workspace]
members = []

[tool.uv.sources]
TOML
export FRAPPE_BENCH_ROOT="$BENCH"
cd "$ROOT"

run() { # <args…>
  : > "$UV_CALLS"
  : > "$BENCH_CALLS"
  OUT="$(bash "$SCRIPT" "$@" 2>&1)" && RC=0 || RC=$?
}

echo "── a new app ────────────────────────────────────────────────────"
run my_app
check_eq "exits 0, though upstream's pip install failed" 0 "$RC" || echo "$OUT"
check_eq "upstream's scaffolder ran, from the bench root, raw" "$BENCH 1 new-app --no-git my_app" "$(cat "$BENCH_CALLS")"
check "it says the pip install failing is expected" says "this is expected"
check_eq "it is a uv workspace member" True \
  "$(toml_get "$BENCH/pyproject.toml" "'apps/my_app' in d['tool']['uv']['workspace']['members']")"
check_eq "with a workspace source" True \
  "$(toml_get "$BENCH/pyproject.toml" "d['tool']['uv']['sources']['my_app']['workspace']")"
check_eq "sites/apps.txt lists it" my_app "$(xargs < "$BENCH/sites/apps.txt")"
check_eq "uv sync ran, from the bench root" "$BENCH sync" "$(cat "$UV_CALLS")"
check "the next step names it" says "install-app my_app"

echo "── refusals ─────────────────────────────────────────────────────"
run my_app
check_eq "an app that is already there exits 1" 1 "$RC"
check "…and says so" says "App 'my_app' already exists in apps/my_app"
check_not "…without running the scaffolder" test -s "$BENCH_CALLS"
run
check_eq "no name exits 1" 1 "$RC"
check "…with usage" says "Usage: bench-new-app <app-name>"
STUB_NO_SCAFFOLD=1 run other_app
check_eq "a scaffold that was not created exits 1" 1 "$RC"
check "…and says so" says "App scaffold was not created at apps/other_app"
check_not "…and registers nothing" grep -q other_app "$BENCH/pyproject.toml"
check_not "…nor syncs" test -s "$UV_CALLS"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
