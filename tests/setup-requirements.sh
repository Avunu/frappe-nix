#!/usr/bin/env bash
# Checks for `bench setup requirements`: the umbrella wrapper's dispatch, and
# the bench-setup-requirements script behind it, with stubs for the tools it
# calls. What is asserted is which of them run, in what order, with which flags,
# and what the exit status says.
#
# Usage: setup-requirements.sh <rendered bench-setup-requirements> <rendered bench>
set -euo pipefail

SETUP="$1"
UMBRELLA="$2"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

fails=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no() { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
check() { local d=$1; shift; if "$@" > /dev/null 2>&1; then ok "$d"; else no "$d"; fi; }
check_eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi; }

export FRAPPE_BENCH_ROOT="$ROOT"
LOG="$ROOT/calls.log"
BIN="$ROOT/bin"
mkdir -p "$BIN"
# Stubs stand in for the Nix store paths the rendered script names.
stub() { # <name> <exit-var>
  printf '#!%s\necho "%s $*" >> "$LOG"\nexit "${%s:-0}"\n' "$(command -v bash)" "$1" "$2" > "$BIN/$1"
  chmod +x "$BIN/$1"
}
export LOG
stub node-verify VERIFY_RC
stub node-modules INSTALL_RC
stub python CHECK_RC
printf '#!%s\necho "bench-setup-requirements $*" >> "$LOG"\n' "$(command -v bash)" > "$BIN/bench-setup-requirements"
printf '#!%s\necho "real-bench $*" >> "$LOG"\n' "$(command -v bash)" > "$BIN/real-bench"
chmod +x "$BIN/bench-setup-requirements" "$BIN/real-bench"
export PATH="$BIN:$PATH"

has_help_text() { grep -q '^  node ' <<< "$OUT" && grep -q '^  python ' <<< "$OUT"; }
run() { : > "$LOG"; OUT="$(bash "$SETUP" "$@" 2>&1)" && RC=0 || RC=$?; }
calls() { tr '\n' '|' < "$LOG"; }

echo "── the script ───────────────────────────────────────────────────"
run
check_eq "runs verify (full, with lockfiles), then install, then the python check" \
  "node-verify --full --lockfiles . alpha beta|node-modules . alpha beta|python $ROOT/requirements-check.py .|" "$(calls | sed "s|/nix/store/[^ ]*requirements-check.py|$ROOT/requirements-check.py|")"
check_eq "exits 0" 0 "$RC"
run --check
check_eq "--check verifies read-only, installs nothing" "node-verify --full --lockfiles --check . alpha beta|python $ROOT/requirements-check.py .|" "$(calls | sed "s|/nix/store/[^ ]*requirements-check.py|$ROOT/requirements-check.py|")"
run --node
check_eq "--node skips the python check" "node-verify --full --lockfiles . alpha beta|node-modules . alpha beta|" "$(calls)"
run --python
check "--python skips node" bash -c "! grep -q node- '$LOG' && grep -q '^python' '$LOG'"
run --dev
check_eq "upstream's --dev is accepted" 0 "$RC"
VERIFY_RC=1 run
check_eq "a failing verify fails the run" 1 "$RC"
check "…but the rest still run" bash -c "grep -q '^node-modules' '$LOG' && grep -q '^python' '$LOG'"
CHECK_RC=1 run
check_eq "a failing python check fails the run" 1 "$RC"
run --bogus
check_eq "an unknown option exits 2" 2 "$RC"
check "…and runs nothing" test ! -s "$LOG"
run --help
check_eq "--help exits 0" 0 "$RC"
check "…and describes node and python" has_help_text


echo "── the bench wrapper ────────────────────────────────────────────"
run_umbrella() { : > "$LOG"; OUT="$(bash "$UMBRELLA" "$@" 2>&1)" && RC=0 || RC=$?; }
run_umbrella setup requirements --node
check_eq "bench setup requirements goes to ours, with its flags" "bench-setup-requirements --node|" "$(calls)"
run_umbrella setup nginx
check_eq "the rest of bench setup goes to upstream" "real-bench setup nginx|" "$(calls)"
run_umbrella setup
check_eq "a bare bench setup too" "real-bench setup|" "$(calls)"

echo
if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi
echo "all checks passed"
