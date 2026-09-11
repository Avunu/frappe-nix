#!/usr/bin/env bash
# Run the unit suite against a real bench.
#
#   scripts/run-tests.sh <bench-root> [extra-site-packages-dir ...]
#
# The suite mocks socketio and most of Frappe, but `frappe` itself must be
# importable: auth.py imports it, and the compat shim in __init__.py attaches to
# frappe.realtime. That is why this needs a bench rather than a bare interpreter,
# and why `nix flake check` only builds the package instead of running these.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BENCH=${1:?usage: run-tests.sh <bench-root> [extra-site-packages-dir ...]}
shift

PY="$BENCH/env/bin/python"
[ -x "$PY" ] || { echo "run-tests.sh: no interpreter at $PY" >&2; exit 1; }

paths=("$HERE/src")
for extra in "$@"; do paths+=("$extra"); done
for app in "$BENCH"/apps/*/; do paths+=("${app%/}"); done

# Quoting "${VERBOSE:+-v}" would always pass one argument, empty when VERBOSE is
# unset, and unittest reads that as a pattern and discovers nothing.
args=(-m unittest discover -s "$HERE/tests" -p 'test_*.py')
[ -n "${VERBOSE:-}" ] && args+=(-v)

PYTHONPATH=$(IFS=:; echo "${paths[*]}") "$PY" "${args[@]}"
