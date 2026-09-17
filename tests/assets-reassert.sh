#!/usr/bin/env bash
# Offline integration test for the asset-shadow reassert check
# (lib/assets-reassert.nix).
#
# A stub `bench`/`redis-cli` record their invocations; a real sites/assets/
# tree (real files, real jq) exercises the invariant check itself. No
# database, no Frappe, no esbuild.
#
# Usage: assets-reassert.sh <rendered-check.sh>
set -euo pipefail

SCRIPT="$1"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
ok() {
  printf '  ok   %s\n' "$1"
  pass=$((pass + 1))
}
no() {
  printf '  FAIL %s\n' "$1"
  printf '       %s\n' "${2:-}"
  fail=$((fail + 1))
}

# ── stub bench / redis-cli ────────────────────────────────────────────────
BIN="$WORK/bin"
mkdir -p "$BIN"
# The shebang is resolved at write time: /usr/bin/env does not exist inside a
# Nix build sandbox.
cat >"$BIN/bench" <<STUB
#!$(command -v bash)
STUB
cat >>"$BIN/bench" <<'STUB'
printf '%s\n' "$*" >>"$BENCH_LOG"
exit 0
STUB
cat >"$BIN/redis-cli" <<STUB
#!$(command -v bash)
STUB
cat >>"$BIN/redis-cli" <<'STUB'
printf '%s\n' "$*" >>"$REDIS_LOG"
exit 0
STUB
chmod +x "$BIN/bench" "$BIN/redis-cli"
export PATH="$BIN:$PATH"

# ── a bench root ──────────────────────────────────────────────────────────
ROOT="$WORK/bench"
mkdir -p "$ROOT/sites/assets"
export FRAPPE_BENCH_ROOT="$ROOT"
export BENCH_LOG="$WORK/bench.log"
export REDIS_LOG="$WORK/redis.log"

ASSETS="$ROOT/sites/assets/assets.json"
BUNDLE="$ROOT/sites/assets/app/dist/js/desk.bundle.AAAA.js"

run() {
  : >"$BENCH_LOG"
  : >"$REDIS_LOG"
  bash "$SCRIPT" >"$WORK/out.log" 2>&1
}
hook_ran() { grep -qF -- "$1" "$BENCH_LOG" 2>/dev/null; }
hook_count() { wc -l <"$BENCH_LOG" 2>/dev/null || echo 0; }
redis_cleared() { grep -qF -- "del assets_json" "$REDIS_LOG" 2>/dev/null; }

# ── 1. every referenced file present: no hook, no redis del ─────────────
mkdir -p "$(dirname "$BUNDLE")"
printf 'js' >"$BUNDLE"
printf '{"desk.bundle.js":"/assets/app/dist/js/desk.bundle.AAAA.js"}' >"$ASSETS"
run
if [ "$(hook_count)" -eq 0 ] && ! redis_cleared; then
  ok "every referenced file present: no hook call, no cache clear"
else
  no "no action when nothing is missing" "hooks=$(cat "$BENCH_LOG" 2>/dev/null) redis=$(cat "$REDIS_LOG" 2>/dev/null)"
fi

# ── 2. one missing: the hook runs with the right dotted path, cache
#      cleared ────────────────────────────────────────────────────────────
rm -f "$BUNDLE"
run
if hook_ran "execute myapp.build.reassert_assets" && redis_cleared; then
  ok "a missing file triggers the hook and clears the assets_json cache"
else
  no "a missing file triggers the hook" "hooks=$(cat "$BENCH_LOG" 2>/dev/null) redis=$(cat "$REDIS_LOG" 2>/dev/null)"
fi

# ── 3. two hooks configured: both run, in order ──────────────────────────
# (exercised by the caller passing two hooks — see the .nix wrapper, which
# renders a second variant of the script for this case)
if [ -n "${SECOND_HOOK_SCRIPT:-}" ]; then
  run_second() {
    : >"$BENCH_LOG"
    : >"$REDIS_LOG"
    bash "$SECOND_HOOK_SCRIPT" >"$WORK/out2.log" 2>&1
  }
  run_second
  first_line="$(sed -n '1p' "$BENCH_LOG")"
  second_line="$(sed -n '2p' "$BENCH_LOG")"
  if [[ "$first_line" == *"myapp.build.reassert_assets"* ]] && [[ "$second_line" == *"myapp.build.another_hook"* ]]; then
    ok "two configured hooks both run, in the declared order"
  else
    no "two hooks run in order" "log: $(cat "$BENCH_LOG" 2>/dev/null)"
  fi
fi

# ── 4. deliberately truncated/invalid assets.json: no hook, no cache
#      clear — the retry-then-give-up path never fires a hook off a torn
#      read ────────────────────────────────────────────────────────────────
mkdir -p "$(dirname "$BUNDLE")"
printf 'js' >"$BUNDLE"
printf '{"desk.bundle.js": "/ass' >"$ASSETS"
run
if [ "$(hook_count)" -eq 0 ] && ! redis_cleared && grep -qi "did not parse" "$WORK/out.log"; then
  ok "a torn/invalid assets.json never fires a hook — logged, not acted on"
else
  no "a torn assets.json is not treated as an invariant failure" \
    "hooks=$(cat "$BENCH_LOG" 2>/dev/null) out=$(cat "$WORK/out.log")"
fi

# ── 5. no assets.json at all: no-op ──────────────────────────────────────
rm -f "$ASSETS"
run
if [ "$(hook_count)" -eq 0 ] && ! redis_cleared; then
  ok "no assets.json yet (first build hasn't run) is a quiet no-op"
else
  no "missing assets.json is a no-op" "hooks=$(cat "$BENCH_LOG" 2>/dev/null)"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
