#!/usr/bin/env bash
# Checks for `update-deps`: how it calls yarn in a bench and in app mode.
#
# Usage: update-deps.sh <rendered-bench-script> <rendered-app-mode-script>
#
# In a bench the apps are other people's repositories: a yarn.lock that
# `yarn install` rewrites is a modified file their next `git checkout` refuses
# over (it is what stopped `bench update` on helpdesk). In app mode apps/<app>
# is the developer's own repository and the lock is theirs to commit.
set -euo pipefail

BENCH_SCRIPT="$1"
APP_SCRIPT="$2"

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
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}
check_not() { # <description> <command...> — passes when the command fails
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}

# ── stubs ──────────────────────────────────────────────────────────────────
# One line per invocation: "<tool> <cwd> <args>".
BIN="$ROOT/bin"
mkdir -p "$BIN"
for tool in yarn uv frappe-nix-node-locks; do
  # Not `#!/usr/bin/env bash`: this also runs as a Nix check, where the sandbox
  # has no /usr/bin/env.
  printf '#!%s\n' "$(command -v bash)" > "$BIN/$tool"
  printf 'printf "%%s %%s %%s\\n" "%s" "$PWD" "$*" >> "$CALLS"\n' "$tool" >> "$BIN/$tool"
  # STUB_FAIL="<tool> <cwd>": that call fails.
  printf '[ "${STUB_FAIL:-}" = "%s $PWD" ] && exit 3\nexit 0\n' "$tool" >> "$BIN/$tool"
  chmod +x "$BIN/$tool"
done
export PATH="$BIN:$PATH"
export CALLS="$ROOT/calls"

export FRAPPE_BENCH_ROOT="$ROOT/bench"
mkdir -p "$FRAPPE_BENCH_ROOT/apps/alpha" "$FRAPPE_BENCH_ROOT/apps/beta"
ALPHA="$FRAPPE_BENCH_ROOT/apps/alpha"
BETA="$FRAPPE_BENCH_ROOT/apps/beta"
calls() { cut -d' ' -f1,3- "$CALLS" | tr '\n' '|'; }

echo "── in a bench ───────────────────────────────────────────────────"
: > "$CALLS"
bash "$BENCH_SCRIPT" > "$ROOT/bench.log" 2>&1 \
  && ok "update-deps exits 0" || { no "update-deps exits 0"; cat "$ROOT/bench.log"; }
check "yarn runs in the app, without writing its lock" \
  grep -qxF "yarn $ALPHA install --pure-lockfile" "$CALLS"
check_not "and never as a plain install" grep -qxF "yarn $ALPHA install" "$CALLS"
check "Python is still re-locked and synced" bash -c "grep -q '^uv .* lock\$' '$CALLS' && grep -q '^uv .* sync\$' '$CALLS'"
check "the fallback locks are still refreshed" grep -q '^frappe-nix-node-locks ' "$CALLS"
check "it says the apps' own locks were left alone" grep -q "own yarn.lock files were not rewritten" "$ROOT/bench.log"
check_not "and no longer tells you to commit them" grep -q 'Commit uv.lock, the yarn.lock files' "$ROOT/bench.log"

check_eq "in order: lock, sync, each app, then the fallbacks" \
  "uv lock|uv sync|yarn install --pure-lockfile|yarn install --pure-lockfile|frappe-nix-node-locks . node-locks|" "$(calls)"
check "it says what to commit" grep -q 'Done! Commit uv.lock and node-locks/.' "$ROOT/bench.log"

: > "$CALLS"
rc=0
STUB_FAIL="uv $FRAPPE_BENCH_ROOT" bash "$BENCH_SCRIPT" > "$ROOT/uvfail.log" 2>&1 || rc=$?
check_eq "a failed uv lock stops it, with uv's status" 3 "$rc"
check_eq "…before anything else" "uv lock|" "$(calls)"

: > "$CALLS"
rc=0
STUB_FAIL="yarn $ALPHA" bash "$BENCH_SCRIPT" > "$ROOT/yarnfail.log" 2>&1 || rc=$?
check "a failed yarn install does not stop the next app" grep -qxF "yarn $BETA install --pure-lockfile" "$CALLS"
check "…nor the fallbacks" grep -q '^frappe-nix-node-locks ' "$CALLS"
check_eq "…but fails the run at the end" 1 "$rc"
check "…naming the app" grep -q 'update-deps: failed: yarn install in apps/alpha' "$ROOT/yarnfail.log"

: > "$CALLS"
rc=0
bash "$BENCH_SCRIPT" --bogus > "$ROOT/bogus.log" 2>&1 || rc=$?
check_eq "an argument is refused" 2 "$rc"
check_eq "…before anything runs" "" "$(calls)"

: > "$CALLS"
rc=0
STUB_FAIL="frappe-nix-node-locks $FRAPPE_BENCH_ROOT" bash "$BENCH_SCRIPT" > "$ROOT/locksfail.log" 2>&1 || rc=$?
check_eq "a failed fallback refresh is only a warning" 0 "$rc"
check "…that says so" grep -q 'node-locks/ not fully regenerated' "$ROOT/locksfail.log"

echo "── in app mode ──────────────────────────────────────────────────"
: > "$CALLS"
bash "$APP_SCRIPT" > "$ROOT/app.log" 2>&1 \
  && ok "update-deps exits 0" || { no "update-deps exits 0"; cat "$ROOT/app.log"; }
check "yarn runs a plain install (the lock is the developer's to commit)" \
  grep -qxF "yarn $ALPHA install" "$CALLS"
check_not "without --pure-lockfile" grep -q -- '--pure-lockfile' "$CALLS"
check_not "and Python is left to nix run .#relock" grep -q '^uv ' "$CALLS"
check "the closing line still says to commit the lock" grep -q 'Commit any changed yarn.lock' "$ROOT/app.log"
check "Python is pointed at relock" grep -q 'Use:  nix run .#relock' "$ROOT/app.log"
check_not "and the fallbacks are left to it too" grep -q '^frappe-nix-node-locks ' "$CALLS"

echo ""
if [ "$fails" -eq 0 ]; then
  echo "All update-deps checks passed."
else
  echo "$fails check(s) failed."
  exit 1
fi
