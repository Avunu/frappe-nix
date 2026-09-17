#!/usr/bin/env bash
# Offline integration test for the `reconcile-apps` script.
#
# A stub `bench` answers `list-apps` from an env-controlled fixture and
# records every `install-app` call; no database, no Frappe, no network.
#
# Usage: reconcile-apps.sh <rendered-reconcile-apps.sh>
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

# ── the stub bench ────────────────────────────────────────────────────────
BIN="$WORK/bin"
mkdir -p "$BIN"
# The shebang is resolved at write time: /usr/bin/env does not exist inside a
# Nix build sandbox, and a stub that cannot execute makes every later
# assertion fail for a reason that has nothing to do with the script under
# test.
cat >"$BIN/bench" <<STUB
#!$(command -v bash)
STUB
cat >>"$BIN/bench" <<'STUB'
# --site <site> list-apps --format json  -> answers from INSTALLED_APPS_JSON
# --site <site> install-app <app>        -> logs it; fails for FAIL_APP
if [ "$1" = "--site" ]; then
  site="$2"
  case "$3" in
    list-apps)
      cat "${INSTALLED_APPS_JSON:-/dev/null}"
      exit 0
      ;;
    install-app)
      app="$4"
      printf '%s\n' "$app" >>"$INSTALL_LOG"
      [ "$app" = "${FAIL_APP:-}" ] && exit 1
      exit 0
      ;;
  esac
fi
exit 0
STUB
chmod +x "$BIN/bench"
export PATH="$BIN:$PATH"

# ── a bench root ──────────────────────────────────────────────────────────
ROOT="$WORK/bench"
mkdir -p "$ROOT/sites"
export FRAPPE_BENCH_ROOT="$ROOT"
export INSTALLED_APPS_JSON="$WORK/installed.json"
export INSTALL_LOG="$WORK/installs.log"

RC=0
OUT=""
run() {
  : >"$INSTALL_LOG"
  OUT="$(FRAPPE_SITE="${FRAPPE_SITE:-}" bash "$SCRIPT" "$@" 2>&1)" && RC=0 || RC=$?
}
installed() { grep -qxF -- "$1" "$INSTALL_LOG" 2>/dev/null; }
install_count() { wc -l <"$INSTALL_LOG" 2>/dev/null || echo 0; }

printf 'frappe\nerpnext\nhrms\nmynewapp\n' >"$ROOT/sites/apps.txt"

# ── 1. the site does not exist yet: no-op, exit 0 ────────────────────────
rm -rf "$ROOT/sites/mysite.local"
FRAPPE_SITE=mysite.local run
if [ "$RC" -eq 0 ] && [ "$(install_count)" -eq 0 ]; then
  ok "an unprovisioned site is a no-op (exit 0, nothing installed)"
else
  no "unprovisioned site is a no-op" "rc=$RC installs=$(cat "$INSTALL_LOG" 2>/dev/null)"
fi

mkdir -p "$ROOT/sites/mysite.local"

# ── 2. nothing missing: zero install-app calls (the idempotency the whole
#      feature leans on) ─────────────────────────────────────────────────
printf '{"mysite.local":["frappe","erpnext","hrms","mynewapp"]}' >"$INSTALLED_APPS_JSON"
FRAPPE_SITE=mysite.local run
if [ "$RC" -eq 0 ] && [ "$(install_count)" -eq 0 ]; then
  ok "an already-reconciled site installs nothing"
else
  no "already-reconciled site installs nothing" "rc=$RC installs=$(cat "$INSTALL_LOG" 2>/dev/null)"
fi

# ── 3. some missing: exactly those installed, in apps.txt order, frappe
#      skipped ────────────────────────────────────────────────────────────
printf '{"mysite.local":["frappe","erpnext"]}' >"$INSTALLED_APPS_JSON"
FRAPPE_SITE=mysite.local run
if [ "$RC" -eq 0 ] && [ "$(install_count)" -eq 2 ] && installed hrms && installed mynewapp && ! installed frappe; then
  ok "sites/apps.txt entries missing from the site are installed, frappe skipped"
else
  no "missing entries are installed" "rc=$RC installs=$(cat "$INSTALL_LOG" 2>/dev/null)"
fi

# ── 4. one install-app failing: the rest still run, script still exits 0 ─
printf '{"mysite.local":["frappe"]}' >"$INSTALLED_APPS_JSON"
FAIL_APP=hrms FRAPPE_SITE=mysite.local run
if [ "$RC" -eq 0 ] && installed hrms && installed mynewapp && installed erpnext; then
  ok "one failed install does not stop the rest, and the task still succeeds"
else
  no "a failed install does not abort the loop" "rc=$RC installs=$(cat "$INSTALL_LOG" 2>/dev/null)"
fi
unset FAIL_APP

# ── 5. no FRAPPE_SITE and no positional arg: usage + exit 1 ─────────────
unset FRAPPE_SITE
run
if [ "$RC" -eq 1 ] && printf '%s' "$OUT" | grep -qi "usage"; then
  ok "no site given prints usage and exits 1"
else
  no "no site given exits 1 with usage" "rc=$RC out=$OUT"
fi

# ── 6. a positional argument works in place of $FRAPPE_SITE ─────────────
: >"$INSTALL_LOG"
printf '{"othersite.local":["frappe"]}' >"$INSTALLED_APPS_JSON"
mkdir -p "$ROOT/sites/othersite.local"
OUT="$(bash "$SCRIPT" othersite.local 2>&1)" && RC=0 || RC=$?
if [ "$RC" -eq 0 ] && installed erpnext && installed hrms && installed mynewapp; then
  ok "a positional site argument is honoured over \$FRAPPE_SITE"
else
  no "a positional site argument works" "rc=$RC out=$OUT installs=$(cat "$INSTALL_LOG" 2>/dev/null)"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
