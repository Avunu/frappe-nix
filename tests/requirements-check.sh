#!/usr/bin/env bash
# Checks for lib/requirements-check.py — the Python half of
# `bench setup requirements`: is what the apps declare locked, and does every
# app in sites/apps.txt import.
#
# Usage: requirements-check.sh <python> <lib/requirements-check.py>
set -euo pipefail

PY="$1"
SCRIPT="$2"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

fails=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no() { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
check() { local d=$1; shift; if "$@" > /dev/null 2>&1; then ok "$d"; else no "$d"; fi; }
check_eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi; }
says() { grep -qF -- "$1" <<< "$OUT"; }
run() { OUT="$(PYTHONPATH="$ROOT/bench/apps" "$PY" "$SCRIPT" "$ROOT/bench" 2>&1)" && RC=0 || RC=$?; }

B="$ROOT/bench"
mkdir -p "$B/sites" "$B/apps/alpha/alpha" "$B/apps/beta_app/beta_app"
touch "$B/apps/alpha/alpha/__init__.py" "$B/apps/beta_app/beta_app/__init__.py"
printf 'alpha\nbeta_app\n' > "$B/sites/apps.txt"
cat > "$B/apps/alpha/pyproject.toml" <<'T'
[project]
name = "alpha"
dependencies = ["Requests>=2", "python_dateutil ; python_version>'3'", "pydantic[email]==2.*"]
T
cat > "$B/apps/beta_app/pyproject.toml" <<'T'
[project]
name = "beta_app"
dependencies = []
T
cat > "$B/uv.lock" <<'T'
version = 1
[[package]]
name = "requests"
[[package]]
name = "python-dateutil"
[[package]]
name = "pydantic"
T

echo "── everything locked and importable ─────────────────────────────"
run
check_eq "exits 0" 0 "$RC"
check "says so" says "2 app(s), every requirement locked, every app importable"
check "names are compared normalised (Requests, python_dateutil, extras, markers)" test "$RC" -eq 0

echo "── an app requires something uv.lock predates ───────────────────"
printf 'version = 1\n[[package]]\nname = "requests"\n' > "$B/uv.lock"
run
check_eq "exits 1" 1 "$RC"
check "names the app and what is missing" says "apps/alpha requires pydantic, python-dateutil, which uv.lock does not have"
check "and what to run" says "uv lock"

echo "── an app in apps.txt that does not import ──────────────────────"
printf 'version = 1\n[[package]]\nname = "requests"\n[[package]]\nname = "python-dateutil"\n[[package]]\nname = "pydantic"\n' > "$B/uv.lock"
printf 'alpha\nbeta_app\nstray\n' >> "$B/sites/apps.txt"
run
check_eq "exits 1" 1 "$RC"
check "names it" says "stray is in sites/apps.txt but does not import"

echo "── no uv.lock ───────────────────────────────────────────────────"
rm "$B/uv.lock"
printf 'alpha\nbeta_app\n' > "$B/sites/apps.txt"
run
check_eq "exits 1" 1 "$RC"
check "says to lock" says "uv.lock is missing"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
