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
  chmod +x "$BIN/$tool"
done
export PATH="$BIN:$PATH"
export CALLS="$ROOT/calls"

export FRAPPE_BENCH_ROOT="$ROOT/bench"
mkdir -p "$FRAPPE_BENCH_ROOT/apps/alpha"
ALPHA="$FRAPPE_BENCH_ROOT/apps/alpha"

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

echo "── in app mode ──────────────────────────────────────────────────"
: > "$CALLS"
bash "$APP_SCRIPT" > "$ROOT/app.log" 2>&1 \
  && ok "update-deps exits 0" || { no "update-deps exits 0"; cat "$ROOT/app.log"; }
check "yarn runs a plain install (the lock is the developer's to commit)" \
  grep -qxF "yarn $ALPHA install" "$CALLS"
check_not "without --pure-lockfile" grep -q -- '--pure-lockfile' "$CALLS"
check_not "and Python is left to nix run .#relock" grep -q '^uv ' "$CALLS"
check "the closing line still says to commit the lock" grep -q 'Commit any changed yarn.lock' "$ROOT/app.log"

echo ""
if [ "$fails" -eq 0 ]; then
  echo "All update-deps checks passed."
else
  echo "$fails check(s) failed."
  exit 1
fi
