#!/usr/bin/env bash
# Offline integration test for the `bench restore` script.
#
# A directory tree stands in for the bucket (mc reads local paths identically)
# and a stub `bench` records the arguments it was handed. Between them, the
# whole flow is exercised — site bootstrap, key carrying, flag assembly,
# post-restore checks — with no database, no Frappe and no network.
#
# Usage: bench-restore.sh <rendered-bench-restore.sh>
#
# The same command rendered with other options, each optional (the cases that
# need one are skipped without it):
#   UNGUARDED_SCRIPT  devguard.enable = false
#   NOFETCH_SCRIPT    restore.enable = false
#   ALLFILES_SCRIPT   both file archives by default (restore.withFiles = "all")
set -euo pipefail

SCRIPT="$1"
UNGUARDED_SCRIPT="${UNGUARDED_SCRIPT:-}"
NOFETCH_SCRIPT="${NOFETCH_SCRIPT:-}"
ALLFILES_SCRIPT="${ALLFILES_SCRIPT:-}"
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

# Older than the rest, so "the newest" stays 20260202_020202: an encrypted dump
# whose site config carries no key at all.
mk "$SRC/20251231_235959/20251231_235959-prod_example_com-database-enc.sql.gz" "dec-db"
mk "$SRC/20251231_235959/20251231_235959-prod_example_com-site_config_backup.json" \
  '{"host_name":"https://prod.example.com"}'

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
# Records every invocation, and the PATH it was run with; answers list-apps
# from a fixture.
printf '%s\n' "$*" >> "$BENCH_LOG"
printf '%s\n' "$PATH" > "$BENCH_LOG.path"
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
# `bench restore` migrates through bench-migrate, which is what puts the offline
# step in front of the migrate; standing in for it, this is `bench migrate` with
# the site flag bench-migrate adds.
cat >"$BIN/bench-migrate" <<STUB
#!$(command -v bash)
STUB
cat >>"$BIN/bench-migrate" <<'STUB'
exec bench --site "$FRAPPE_SITE" migrate "$@"
STUB
chmod +x "$BIN/bench-migrate"
export PATH="$BIN:$PATH"

# ── a bench root ──────────────────────────────────────────────────────────
ROOT="$WORK/bench"
mkdir -p "$ROOT/sites"
printf 'frappe\nerpnext\n' >"$ROOT/sites/apps.txt"
export FRAPPE_BENCH_ROOT="$ROOT"
export FRAPPE_SITE="erp.example.com"
export FRAPPE_DB_SOCKET="/run/x/mysql.sock"
export DEVENV_STATE="$WORK/state"
export FRAPPE_NIX_BACKUP_SOURCE="$SRC"
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
# run, with another rendering of the command.
run_with() {
  local script="$1"
  shift
  : >"$WORK/bench.log"
  export BENCH_LOG="$WORK/bench.log"
  OUT="$(bash "$script" "$@" 2>&1)" && RC=0 || RC=$?
}
said() { printf '%s' "$OUT" | grep -qF -- "$1"; }
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
FRAPPE_NIX_BACKUP_SOURCE="$WORK/nonexistent" run "$WORK/local.sql.gz"
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

# ── 13. the old variable names still work, and say so ────────────────────
# FRAPPE_BACKUP_SOURCE became FRAPPE_NIX_BACKUP_SOURCE (python-devops-nix docs/conventions.md,
# Tool-owned variables); the old name is read, with a warning, through 1.x, until frappe-nix 2.0.0.
FRAPPE_NIX_BACKUP_SOURCE="" FRAPPE_BACKUP_SOURCE="$SRC" run --list
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q 20260202_020202 &&
  printf '%s' "$OUT" | grep -q 'FRAPPE_BACKUP_SOURCE is deprecated; set FRAPPE_NIX_BACKUP_SOURCE'; then
  ok "FRAPPE_BACKUP_SOURCE is still read, with a deprecation warning"
else
  no "the legacy FRAPPE_BACKUP_SOURCE still works" "rc=$RC out=$OUT"
fi

# ── 14. --help ───────────────────────────────────────────────────────────
run --help
if [ "$RC" -eq 0 ] && said "Usage: bench restore" && said "--no-force" && [ ! -s "$WORK/bench.log" ]; then
  ok "--help prints the usage and runs nothing"
else
  no "--help prints the usage and runs nothing" "rc=$RC $(cat "$WORK/bench.log")"
fi

# ── 15. one file at most ─────────────────────────────────────────────────
run "$WORK/local.sql.gz" "$WORK/other.sql.gz"
if [ "$RC" -eq 1 ] && said "unexpected argument" && [ ! -s "$WORK/bench.log" ]; then
  ok "a second file is refused"
else
  no "a second file is refused" "rc=$RC out=$OUT"
fi

# ── 16. what restore does not know reaches bench restore ─────────────────
run "$WORK/local.sql.gz" --verbose -- --admin-password secret
if [ "$RC" -eq 0 ] && logged "--verbose" && logged "--admin-password secret"; then
  ok "unknown options, and everything after --, are passed through"
else
  no "unknown options are passed through" "rc=$RC $(cat "$WORK/bench.log")"
fi

# ── 17. the download cache, and --no-cache ───────────────────────────────
run --at 20260101_010101
run --at 20260101_010101
cached_rc=$RC
cached_out="$OUT"
run --at 20260101_010101 --no-cache
if [ "$cached_rc" -eq 0 ] && printf '%s' "$cached_out" | grep -q 'cached .*-database.sql.gz' &&
  [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q 'fetch .*-database.sql.gz'; then
  ok "a repeat restore is served from cache, and --no-cache downloads again"
else
  no "the cache, and --no-cache" "first: $cached_out | --no-cache: $OUT"
fi

# ── 18. a key given on the command line is the one used ──────────────────
run --at 20260202_020202 -- --encryption-key MYKEY
if [ "$RC" -eq 0 ] && logged "--encryption-key MYKEY" && ! logged "--encryption-key BK2"; then
  ok "--encryption-key on the command line wins over the backup's own key"
else
  no "--encryption-key on the command line wins" "rc=$RC $(cat "$WORK/bench.log")"
fi

# ── 19. an encrypted dump with no key anywhere is refused ────────────────
rm -rf "$ROOT/sites/$FRAPPE_SITE"
run --at 20251231_235959
if [ "$RC" -eq 1 ] && said "no backup_encryption_key" && ! logged " restore "; then
  ok "an encrypted dump with no key anywhere is refused before bench restore runs"
else
  no "an encrypted dump with no key is refused" "rc=$RC out=$OUT log=$(cat "$WORK/bench.log")"
fi

# ── 20. the backup's slug, this bench's site, and what is said about it ──
rm -rf "$ROOT/sites/$FRAPPE_SITE"
run --at 20260101_010101
first_db="$(jq -r .db_name "$SITE_CFG")"
first_pw="$(jq -r .db_password "$SITE_CFG")"
if [ "$RC" -eq 0 ] && said "the backup is of 'prod_example_com', this bench is 'erp_example_com'" &&
  said "restored erp.example.com from 20260101_010101"; then
  ok "a restore across sites says so, and ends by naming the backup"
else
  no "a restore across sites says so" "rc=$RC out=$OUT"
fi

# ── 21. a second restore keeps the site's own database credentials ───────
run --at 20260101_010101
if [ "$RC" -eq 0 ] && [ "$(jq -r .db_name "$SITE_CFG")" = "$first_db" ] &&
  [ "$(jq -r .db_password "$SITE_CFG")" = "$first_pw" ] &&
  [ "$first_db" = "_$(printf '%s' "$FRAPPE_SITE" | sha1sum | cut -c1-16)" ]; then
  ok "the seeded db_name is frappe's own scheme, and a second restore keeps it"
else
  no "a second restore keeps the site's credentials" "db_name=$first_db -> $(jq -r .db_name "$SITE_CFG")"
fi

# ── 22. no sites/apps.txt: nothing to compare, migrate runs ──────────────
mv "$ROOT/sites/apps.txt" "$WORK/apps.txt"
run --at 20260101_010101
if [ "$RC" -eq 0 ] && logged "migrate"; then
  ok "without sites/apps.txt the missing-app check is skipped and migrate runs"
else
  no "without sites/apps.txt migrate runs" "rc=$RC $(cat "$WORK/bench.log")"
fi
mv "$WORK/apps.txt" "$ROOT/sites/apps.txt"

# ── 23. a multi-tenant bench with no site to restore into ────────────────
FRAPPE_SITE="" run
if [ "$RC" -eq 1 ] && said "multi-tenant" && [ ! -s "$WORK/bench.log" ]; then
  ok "with FRAPPE_SITE unset, a fetched restore is refused"
else
  no "with FRAPPE_SITE unset, a fetched restore is refused" "rc=$RC out=$OUT"
fi

# ── 24. no backup source configured ──────────────────────────────────────
if [ -n "$NOFETCH_SCRIPT" ]; then
  run_with "$NOFETCH_SCRIPT"
  if [ "$RC" -eq 1 ] && said "no backup source is configured" && [ ! -s "$WORK/bench.log" ]; then
    ok "with fetching off, a restore with no file says how to turn it on"
  else
    no "with fetching off, a restore with no file is refused" "rc=$RC out=$OUT"
  fi
  run_with "$NOFETCH_SCRIPT" "$WORK/local.sql.gz"
  if [ "$RC" -eq 0 ] && logged "local.sql.gz"; then
    ok "with fetching off, an explicit file still restores"
  else
    no "with fetching off, an explicit file still restores" "rc=$RC $(cat "$WORK/bench.log")"
  fi
fi

# ── 25. both archives by default ─────────────────────────────────────────
if [ -n "$ALLFILES_SCRIPT" ]; then
  run_with "$ALLFILES_SCRIPT" --at 20260101_010101
  if [ "$RC" -eq 0 ] && logged "--with-public-files" && logged "--with-private-files"; then
    ok "both file archives are restored when the bench asks for them by default"
  else
    no "both archives by default" "rc=$RC $(cat "$WORK/bench.log")"
  fi
fi

# ── 26. an unguarded bench ───────────────────────────────────────────────
if [ -n "$UNGUARDED_SCRIPT" ]; then
  rm -rf "$ROOT/sites/$FRAPPE_SITE"
  run_with "$UNGUARDED_SCRIPT" --at 20260101_010101
  if [ "$RC" -eq 1 ] && said "refusing" && ! logged " restore "; then
    ok "an unguarded bench refuses production's encryption key"
  else
    no "an unguarded bench refuses production's key" "rc=$RC out=$OUT"
  fi

  FRAPPE_NIX_RESTORE_ALLOW_UNGUARDED=1 run_with "$UNGUARDED_SCRIPT" --at 20260101_010101
  if [ "$RC" -eq 0 ] && [ "$(jq -r '.encryption_key // "-"' "$SITE_CFG")" = "EK1" ] && ! said "deprecated"; then
    ok "FRAPPE_NIX_RESTORE_ALLOW_UNGUARDED=1 restores anyway, key and all"
  else
    no "the unguarded override restores" "rc=$RC out=$OUT"
  fi

  # The name it had before (python-devops-nix docs/conventions.md, Tool-owned
  # variables), read with a warning through 1.x, until frappe-nix 2.0.0.
  FRAPPE_RESTORE_ALLOW_UNGUARDED=1 run_with "$UNGUARDED_SCRIPT" --at 20260101_010101
  if [ "$RC" -eq 0 ] && said "FRAPPE_RESTORE_ALLOW_UNGUARDED is deprecated; set FRAPPE_NIX_RESTORE_ALLOW_UNGUARDED"; then
    ok "FRAPPE_RESTORE_ALLOW_UNGUARDED=1 still works, with a deprecation warning"
  else
    no "the legacy unguarded override still works" "rc=$RC out=$OUT"
  fi

  # Unguarded always refuses now (python-devops-nix docs/conventions.md,
  # Unguarded restore): not only when production's key would cross over, and an
  # explicit file too.
  rm -rf "$ROOT/sites/$FRAPPE_SITE"
  run_with "$UNGUARDED_SCRIPT" --at 20260101_010101 --no-site-config
  if [ "$RC" -eq 1 ] && said "refusing" && ! logged " restore "; then
    ok "an unguarded bench refuses even without production's key (--no-site-config)"
  else
    no "an unguarded bench refuses with --no-site-config" "rc=$RC out=$OUT"
  fi
  run_with "$UNGUARDED_SCRIPT" "$WORK/local.sql.gz"
  if [ "$RC" -eq 1 ] && said "refusing" && [ ! -s "$WORK/bench.log" ]; then
    ok "an unguarded bench refuses an explicit file too"
  else
    no "an unguarded bench refuses an explicit file" "rc=$RC out=$OUT"
  fi
  run_with "$UNGUARDED_SCRIPT" --list
  if [ "$RC" -eq 0 ] && said 20260202_020202; then
    ok "an unguarded bench can still list the backups"
  else
    no "an unguarded bench can list" "rc=$RC out=$OUT"
  fi
fi

# ── 27. neutralizing ─────────────────────────────────────────────────────
# A fetched restore pauses the scheduler and switches mail off in the restored
# database (restore.neutralize, on by default in the dev shell).
run --at 20260101_010101
if [ "$RC" -eq 0 ] && logged "--site erp.example.com disable-scheduler" &&
  logged "execute frappe.db.sql" && logged "enable_outgoing = 0, enable_incoming = 0"; then
  ok "a fetched restore is neutralized: scheduler paused, Email Accounts off"
else
  no "a fetched restore is neutralized" "rc=$RC $(cat "$WORK/bench.log")"
fi
if [ "$(grep -n 'disable-scheduler' "$WORK/bench.log" | cut -d: -f1)" -gt "$(grep -n ' restore ' "$WORK/bench.log" | cut -d: -f1)" ] &&
  [ "$(grep -n 'disable-scheduler' "$WORK/bench.log" | cut -d: -f1)" -lt "$(grep -n ' migrate' "$WORK/bench.log" | cut -d: -f1)" ]; then
  ok "it is neutralized after the restore and before the migrate"
else
  no "neutralize runs between restore and migrate" "$(cat "$WORK/bench.log")"
fi
run --at 20260101_010101 --no-neutralize
if [ "$RC" -eq 0 ] && ! logged "disable-scheduler" && ! logged "execute frappe.db.sql"; then
  ok "--no-neutralize leaves the restored database as production had it"
else
  no "--no-neutralize skips it" "$(cat "$WORK/bench.log")"
fi
run "$WORK/local.sql.gz"
if [ "$RC" -eq 0 ] && ! logged "disable-scheduler"; then
  ok "an explicit file is not neutralized unless asked"
else
  no "an explicit file is not neutralized by default" "$(cat "$WORK/bench.log")"
fi
run "$WORK/local.sql.gz" --neutralize
if [ "$RC" -eq 0 ] && logged "local.sql.gz" && logged "disable-scheduler" && logged "execute frappe.db.sql"; then
  ok "--neutralize neutralizes an explicit file's restore"
else
  no "--neutralize on an explicit file" "rc=$RC $(cat "$WORK/bench.log")"
fi

# ── 28. --at takes ISO 8601 and any leading part of a time ───────────────
for at in 2026-01-01T01:01:01 "2026-01-01 01:01" 2026-01 20260101_010101; do
  run --at "$at"
  if [ "$RC" -eq 0 ] && said "restored erp.example.com from 20260101_010101"; then
    ok "--at $at picks 20260101_010101"
  else
    no "--at $at picks 20260101_010101" "rc=$RC $(printf '%s' "$OUT" | tail -3)"
  fi
done
run --at yesterday
if [ "$RC" -ne 0 ] && said "is not a point in time" && ! logged " restore "; then
  ok "--at that is not a time is refused"
else
  no "--at yesterday is refused" "rc=$RC out=$OUT"
fi

# What restore runs gets the caller's PATH, unchanged: a console-script
# wrapper would put python3.14's bin/ and an unwrapped frappe-nix ahead of the
# dev shell's own.
run "$WORK/local.sql.gz"
if [ "$(cat "$WORK/bench.log.path" 2>/dev/null)" = "$PATH" ]; then
  ok "bench runs with the caller's PATH, unchanged"
else
  no "bench runs with the caller's PATH, unchanged" "got: $(cut -c1-300 "$WORK/bench.log.path" 2>/dev/null)"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
