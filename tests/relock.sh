#!/usr/bin/env bash
# Checks for `nix run .#relock` (lib/relock.nix), against stub uv,
# frappe-nix-node-locks and build-deps commands (tests/relock.nix).
#
# Usage: relock.sh <bench-mode relock> <app-mode relock> <its workspace>
set -euo pipefail

BENCH_RELOCK="$1"
APP_RELOCK="$2"
WORKSPACE="$3"

ROOT="$(mktemp -d)"
trap 'chmod -R u+w "$ROOT"; rm -rf "$ROOT"' EXIT
export CALLS="$ROOT/calls"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

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
check_not() { # <description> <command...>
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}
calls() { grep -v -- ' --version$' "$CALLS" | tr '\n' '|'; }
run() { # <log> <relock> [args…]: from the current directory; status in RC
  local log=$1 tool=$2
  shift 2
  : > "$CALLS"
  RC=0
  "$tool" "$@" > "$ROOT/$log.log" 2>&1 || RC=$?
}

echo "── a bench ──────────────────────────────────────────────────────"
BENCH="$ROOT/bench"
mkdir -p "$BENCH/apps"
cd "$ROOT"
run nobench "$BENCH_RELOCK"
check_eq "outside a bench root it refuses" 1 "$RC"
check "…saying where to run it" grep -q 'run this from the bench root (pyproject.toml + apps/)' "$ROOT/nobench.log"
check_eq "…and runs nothing" "" "$(calls)"
cd "$BENCH"
printf '[project]\nname = "b"\nrequires-python = ">=3.14"\n' > pyproject.toml
run bench "$BENCH_RELOCK" --upgrade-package frappe
check_eq "it exits 0" 0 "$RC"
check_eq "uv lock in the bench root, with the arguments, the build-deps sync, then node-locks/" \
  "uv $BENCH lock --upgrade-package frappe-runtime --upgrade-package frappe|build-deps-sync $BENCH |frappe-nix-node-locks $BENCH --exclude=alpha/desk --command=nix run .#relock -- --node-locks . node-locks|" "$(calls)"
check "it names the uv" grep -q 'Re-locking the Python workspace with uv 0.0.0-stub' "$ROOT/bench.log"
check "pyproject.toml is reconciled first (what the root sync would add)" \
  grep -q '^name = "pypika-stubs"$' "$BENCH/pyproject.toml"
check "…saying so" grep -q '+ \[\[tool.uv.dependency-metadata\]\] += pypika-stubs' "$ROOT/bench.log"
check "and says what to commit" grep -q 'uv.lock and node-locks/ updated — commit them' "$ROOT/bench.log"
run bench-uv "$BENCH_RELOCK" --uv-only
check_eq "--uv-only: uv lock and the sync, as before" "uv $BENCH lock --upgrade-package frappe-runtime|build-deps-sync $BENCH |" "$(calls | sed 's/seed [^|]*|//')"
check "…saying so" grep -q 'uv.lock updated — commit it' "$ROOT/bench-uv.log"
run bench-locks "$BENCH_RELOCK" --node-locks beta
check_eq "--node-locks: the generator only, with its targets" \
  "frappe-nix-node-locks $BENCH --exclude=alpha/desk --command=nix run .#relock -- --node-locks . node-locks beta|" "$(calls)"
run bench-help "$BENCH_RELOCK" --help
check_eq "--help exits 0" 0 "$RC"
check "…and is a bench's" grep -q 'Run it from the bench root' "$ROOT/bench-help.log"
check_eq "…running nothing" "" "$(calls)"
UV_FAIL=1 run benchfail "$BENCH_RELOCK"
check_eq "a failed uv lock fails it" 1 "$RC"
check_not "…before the sync" grep -q build-deps-sync "$CALLS"
check_not "…and the fallbacks" grep -q frappe-nix-node-locks "$CALLS"

echo "── app mode ─────────────────────────────────────────────────────"
APP="$ROOT/app"
mkdir -p "$APP/sub"
git -C "$APP" init -q -b main
cd "$APP/sub"
run help "$APP_RELOCK" --help
check_eq "--help exits 0" 0 "$RC"
check "…with the usage" grep -q 'Usage: nix run .#relock \[--uv-only | --node-locks' "$ROOT/help.log"
check "…naming the files" grep -q 'nix/uv.lock' "$ROOT/help.log"
check_eq "…and runs nothing" "" "$(calls)"

run first "$APP_RELOCK"
check_eq "the first relock exits 0" 0 "$RC"
[ "$RC" -eq 0 ] || cat "$ROOT/first.log"
check "uv locks a writable copy of the workspace, not the repository" \
  bash -c "grep '^uv ' '$CALLS' | grep -v -- --version | grep -qv ' $APP'"
check_eq "the lock lands at the repository root's nix/uv.lock" "version = 1" "$(cat "$APP/nix/uv.lock")"
check "…said so" grep -q '+ nix/uv.lock' "$ROOT/first.log"
check "the fallback locks are refreshed over the workspace, into nix/node-locks" \
  grep -qF "frappe-nix-node-locks $APP/sub --exclude=alpha/desk --command=nix run .#relock -- --node-locks $WORKSPACE $APP/nix/node-locks" "$CALLS"
check "the lock is staged" git -C "$APP" ls-files --error-unmatch nix/uv.lock
check "and the new fallback" git -C "$APP" ls-files --error-unmatch nix/node-locks/beta/yarn.lock
check "both stagings are said" bash -c "grep -q 'staged nix/uv.lock' '$ROOT/first.log' && grep -q 'staged new files under nix/node-locks/' '$ROOT/first.log'"
check "and what to commit" grep -q '✅ commit nix/, then re-enter the shell.' "$ROOT/first.log"
git -C "$APP" commit -q -m locks

run again "$APP_RELOCK"
check "an unchanged lock is said to be current" grep -q 'nix/uv.lock already current' "$ROOT/again.log"
check_not "and nothing is staged again" grep -q staged "$ROOT/again.log"

UV_LOCK="version = 2" run moved "$APP_RELOCK" --upgrade-package frappe
check "the committed lock seeds the copy, so only what must move does" grep -qx 'seed version = 1' "$CALLS"
check_eq "a moved lock is written" "version = 2" "$(cat "$APP/nix/uv.lock")"
check "uv lock gets the arguments" grep -q '^uv .* lock --upgrade-package frappe-runtime --upgrade-package frappe$' "$CALLS"

run uvonly "$APP_RELOCK" --uv-only
check "--uv-only locks" grep -q '^uv .* lock --upgrade-package frappe-runtime$' "$CALLS"
check_not "…and leaves the fallbacks" grep -q frappe-nix-node-locks "$CALLS"

run nodelocks "$APP_RELOCK" --node-locks beta alpha/desk
check_not "--node-locks does not lock Python" grep -q '^uv .* lock' "$CALLS"
check "…and hands its targets to the generator" grep -q "frappe-nix-node-locks .* $APP/nix/node-locks beta alpha/desk\$" "$CALLS"

run hashes "$APP_RELOCK" --node-hashes
check "--node-hashes still works, as an alias that says so" grep -q 'now --node-locks' "$ROOT/hashes.log"
check_not "…for the fallbacks only" grep -q '^uv .* lock' "$CALLS"

before="$(cat "$APP/nix/uv.lock")"
UV_FAIL=1 run appfail "$APP_RELOCK"
check_eq "a failed uv lock fails it" 1 "$RC"
check_not "…before the fallbacks" grep -q frappe-nix-node-locks "$CALLS"
check_eq "…and the committed lock is untouched" "$before" "$(cat "$APP/nix/uv.lock")"

NODE_LOCKS_FAIL=1 run lockfail "$APP_RELOCK" --node-locks
check_eq "a failed fallback fails it" 1 "$RC"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
