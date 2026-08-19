#!/usr/bin/env bash
# Offline integration test for the `bench restore` script.
#
# A directory tree stands in for the bucket (mc reads local paths identically)
# and a stub `bench` records the arguments it was handed. Between them, the
# whole flow is exercised — site bootstrap, key carrying, flag assembly,
# post-restore checks — with no database, no Frappe and no network.
#
# Usage: bench-restore.sh <rendered-bench-restore.sh>
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

# ── the bucket ────────────────────────────────────────────────────────────
SRC="$WORK/bucket"
mk() {
  mkdir -p "$(dirname "$1")"
  printf '%s' "$2" >"$1"
}
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-database.sql.gz" "jan-db"
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-site_config_backup.json" \
  '{"encryption_key":"EK1","backup_encryption_key":"BK1","db_password":"PROD-DB-PW","host_name":"https://prod.example.com","mail_password":"PROD-MAIL"}'
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-files.tar" "public-archive"
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-private-files.tar" "private-archive"

mk "$SRC/20260202_020202/20260202_020202-prod_example_com-database-enc.sql.gz" "feb-db"
mk "$SRC/20260202_020202/20260202_020202-prod_example_com-site_config_backup-enc.json" \
  '{"encryption_key":"EK2","backup_encryption_key":"BK2"}'

# ── the stub bench ────────────────────────────────────────────────────────
BIN="$WORK/bin"
mkdir -p "$BIN"
# The shebang is resolved at write time: /usr/bin/env does not exist inside a
# Nix build sandbox, and a stub that cannot execute makes every later assertion
# fail for a reason that has nothing to do with the script under test.
cat >"$BIN/bench" <<STUB
#!$(command -v bash)
STUB
cat >>"$BIN/bench" <<'STUB'
# Records every invocation; answers list-apps from a fixture.
printf '%s\n' "$*" >> "$BENCH_LOG"
args=("$@")
for i in "${!args[@]}"; do
  if [ "${args[$i]}" = "list-apps" ]; then
    cat "${LIST_APPS_JSON:-/dev/null}"
    exit 0
  fi
done
exit 0
STUB
chmod +x "$BIN/bench"
export PATH="$BIN:$PATH"

# ── a bench root ──────────────────────────────────────────────────────────
ROOT="$WORK/bench"
mkdir -p "$ROOT/sites"
printf 'frappe\nerpnext\n' >"$ROOT/sites/apps.txt"
export FRAPPE_BENCH_ROOT="$ROOT"
export FRAPPE_SITE="erp.example.com"
export FRAPPE_DB_SOCKET="/run/x/mysql.sock"
export DEVENV_STATE="$WORK/state"
export FRAPPE_BACKUP_SOURCE="$SRC"
export LIST_APPS_JSON="$WORK/apps.json"
printf '{"erp.example.com":["frappe","erpnext"]}' >"$LIST_APPS_JSON"

SITE_CFG="$ROOT/sites/$FRAPPE_SITE/site_config.json"

RC=0
OUT=""
run() {
  : >"$WORK/bench.log"
  export BENCH_LOG="$WORK/bench.log"
  OUT="$(bash "$SCRIPT" "$@" 2>&1)" && RC=0 || RC=$?
}
logged() { grep -qF -- "$1" "$WORK/bench.log"; }

# ── 1. a fresh clone bootstraps the site ─────────────────────────────────
rm -rf "$ROOT/sites/$FRAPPE_SITE"
run --at 20260101_010101
if [ "$RC" -ne 0 ]; then
  no "a fresh clone restores" "rc=$RC $(printf '%s' "$OUT" | tail -3)"
elif [ ! -f "$SITE_CFG" ]; then
  no "a fresh clone restores" "no site_config.json was seeded"
elif [ ! -d "$ROOT/sites/$FRAPPE_SITE/locks" ]; then
  # filelock() runs before _new_site and does not create its own parent.
  no "a fresh clone restores" "locks/ was not created"
else
  seeded="$(jq -r '[.db_type, (.db_name|startswith("_")), (.db_password|length>0), .db_socket] | @tsv' "$SITE_CFG")"
  if [ "$seeded" = "$(printf 'mariadb\ttrue\ttrue\t/run/x/mysql.sock')" ]; then
    ok "a fresh clone seeds site_config.json and locks/, then restores"
  else
    no "a fresh clone seeds site_config.json" "got: $seeded"
  fi
fi

# ── 2. the keys that make a restore a clone ──────────────────────────────
ek="$(jq -r '.encryption_key // "-"' "$SITE_CFG")"
bk="$(jq -r '.backup_encryption_key // "-"' "$SITE_CFG")"
if [ "$ek" = "EK1" ] && [ "$bk" = "BK1" ]; then
  ok "encryption_key and backup_encryption_key are carried across"
else
  no "encryption keys are carried across" "encryption_key=$ek backup_encryption_key=$bk"
fi

# ── 3. and only those keys ───────────────────────────────────────────────
# An allowlist, not a denylist: production's host_name would make Frappe mint
# absolute URLs at the production host in password-reset mail, and its
# db_password would point this site at a database it does not own.
leaked="$(jq -r '[.host_name, .mail_password] | map(select(. != null)) | join(",")' "$SITE_CFG")"
if [ -z "$leaked" ] && [ "$(jq -r .db_password "$SITE_CFG")" != "PROD-DB-PW" ]; then
  ok "host_name, mail_password and production's db_password are left behind"
else
  no "only the allowlisted keys cross over" "leaked: $leaked db_password=$(jq -r .db_password "$SITE_CFG")"
fi

# ── 4. the site config is not world-readable ─────────────────────────────
mode="$(stat -c '%a' "$SITE_CFG" 2>/dev/null || stat -f '%Lp' "$SITE_CFG")"
if [ "$mode" = "600" ]; then
  ok "site_config.json holding the production key is 0600"
else
  no "site_config.json is 0600" "mode=$mode"
fi

# ── 5. --force is on by default ──────────────────────────────────────────
# Without it setup_database refuses with "Database … already exists", so every
# restore after the first one fails.
if logged "--force"; then
  ok "--force is passed by default, so a second restore works"
else
  no "--force is passed by default" "$(cat "$WORK/bench.log")"
fi

run --no-force
if logged "--force"; then
  no "--no-force opts out" "still passed --force"
else
  ok "--no-force opts out"
fi

# ── 6. migrate runs, and can be skipped ──────────────────────────────────
run
if logged "migrate"; then ok "bench migrate runs after a restore"; else no "bench migrate runs" ""; fi
run --no-migrate
if logged "migrate"; then no "--no-migrate skips it" "migrate still ran"; else ok "--no-migrate skips it"; fi

# ── 7. an app in the dump that this bench lacks blocks migrate ───────────
# remove_missing_apps() knows only two legacy names, so the rest survive into
# the restored database and take `bench migrate` down with ModuleNotFoundError.
printf '{"erp.example.com":["frappe","erpnext","hrms","payments"]}' >"$LIST_APPS_JSON"
run
if printf '%s' "$OUT" | grep -q "hrms" && printf '%s' "$OUT" | grep -q "bench get-app" && ! logged "migrate"; then
  ok "an app the bench does not have is named, and migrate is held back"
else
  no "a missing app is named and migrate held back" "$(printf '%s' "$OUT" | tail -3)"
fi
printf '{"erp.example.com":["frappe","erpnext"]}' >"$LIST_APPS_JSON"

# ── 8. encrypted dumps resolve their own key ─────────────────────────────
# Left to frappe, _restore generates a fresh key, writes it into this site's
# config, and only then fails to decrypt.
run --at 20260202_020202
if logged "--encryption-key BK2"; then
  ok "an -enc dump is restored with the key from its own site config"
else
  no "an -enc dump resolves its key" "$(cat "$WORK/bench.log")"
fi

# ── 9. archives are opt-in ───────────────────────────────────────────────
# Against the folder that *has* archives, so "not requested" is what is being
# tested rather than "not present".
run --at 20260101_010101
if logged "--with-public-files" || logged "--with-private-files"; then
  no "archives are opt-in" "fetched without being asked"
else
  ok "archives are opt-in"
fi
run --at 20260101_010101 --files --private-files
if logged "--with-public-files" && logged "--with-private-files"; then
  ok "--files / --private-files reach bench restore"
else
  no "--files / --private-files reach bench restore" "$(cat "$WORK/bench.log")"
fi

# ── 10. --no-site-config leaves the dev keys alone ───────────────────────
rm -rf "$ROOT/sites/$FRAPPE_SITE"
run --no-site-config
if [ "$(jq -r '.encryption_key // "-"' "$SITE_CFG")" = "-" ]; then
  ok "--no-site-config restores without carrying production's key"
else
  no "--no-site-config carries nothing" "key was written anyway"
fi

# ── 11. an explicit file bypasses the object store entirely ──────────────
printf 'local' >"$WORK/local.sql.gz"
FRAPPE_BACKUP_SOURCE="$WORK/nonexistent" run "$WORK/local.sql.gz"
if [ "$RC" -eq 0 ] && logged "local.sql.gz" && logged "--db-root-username root"; then
  ok "an explicit file is restored without touching the object store"
else
  no "an explicit file bypasses the fetch" "rc=$RC $(cat "$WORK/bench.log")"
fi

# ── 12. --list ───────────────────────────────────────────────────────────
run --list
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q 20260202_020202; then
  ok "--list shows the available backups"
else
  no "--list shows the available backups" "rc=$RC out=$OUT"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
