#!/usr/bin/env bash
# Offline tests for frappe-nix-backup-fetch.
#
# The fixture is a plain directory tree standing in for a bucket. That works
# because `mc ls --json` emits the identical {"type":"folder","key":…,"size":…}
# shape for a local path as it does for S3 — so the whole discovery, selection,
# download, cache and retention path is exercised with no server, no
# credentials, no network, and no platform-specific daemon.
#
# Usage: backup-fetch.sh <path-to-frappe-nix-backup-fetch>
set -euo pipefail

BF="$1"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

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

# Run the fetcher, capturing stdout (manifest), stderr (progress) and status
# separately. Never `bf … | grep`: this script runs under pipefail and the
# fetcher exits non-zero by design on the refusal paths, which would invert the
# assertion.
OUT="" ERR="" RC=0
bf() {
  OUT="$("$BF" "$@" 2>"$WORK/.err")" && RC=0 || RC=$?
  ERR="$(cat "$WORK/.err")"
}
errsays() { printf '%s' "$ERR" | grep -q -- "$1"; }

# ── fixture ───────────────────────────────────────────────────────────────
SRC="$WORK/bucket"
mk() {
  mkdir -p "$(dirname "$1")"
  printf '%s' "$2" >"$1"
}

# A normal backup: database, the (never-encrypted) site config, both archives.
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-database.sql.gz" "jan-db"
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-site_config_backup.json" \
  '{"encryption_key":"EK1","backup_encryption_key":"BK1"}'
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-files.tar" "public-archive"
mk "$SRC/20260101_010101/20260101_010101-prod_example_com-private-files.tar" "private-archive"

# An encrypted backup. Note the config JSON carries the -enc suffix but is
# plaintext — backup_encryption() never touches it.
mk "$SRC/20260202_020202/20260202_020202-prod_example_com-database-enc.sql.gz" "feb-db"
mk "$SRC/20260202_020202/20260202_020202-prod_example_com-site_config_backup-enc.json" \
  '{"backup_encryption_key":"BK2"}'

# Partial only — unusable for creating a site.
mk "$SRC/20260303_030303/20260303_030303-prod_example_com-partial-database.sql.gz" "mar-partial"

# A site slug ending in "l", which naive "not -partial-" matching gets wrong.
mk "$SRC/20260404_040404/20260404_040404-my_portal-database.sql.gz" "apr-db"

# Noise that must be ignored by the folder scan.
mkdir -p "$SRC/Backups" "$SRC/not-a-timestamp"
mk "$SRC/README.txt" "hello"

export FRAPPE_BACKUP_SOURCE="$SRC"
export FRAPPE_BACKUP_CACHE="$WORK/cache"

# ── 1. folder discovery ───────────────────────────────────────────────────
bf list
if [ "$RC" -eq 0 ] && [ "$OUT" = "20260101_010101
20260202_020202
20260303_030303
20260404_040404" ]; then
  ok "list finds timestamp folders and ignores everything else"
else
  no "list finds timestamp folders and ignores everything else" "rc=$RC out=$(printf '%s' "$OUT" | tr '\n' ' ')"
fi

# ── 2. newest wins, and the slug comes off the filename ──────────────────
bf fetch
if [ "$RC" -eq 0 ] &&
  [ "$(printf '%s' "$OUT" | jq -r .folder)" = "20260404_040404" ] &&
  [ "$(printf '%s' "$OUT" | jq -r .slug)" = "my_portal" ]; then
  ok "newest folder is chosen; a slug ending in 'l' is not mistaken for -partial-"
else
  no "newest folder is chosen; slug parsed" "rc=$RC $(printf '%s' "$OUT" | jq -c '{folder,slug}' 2>&1)"
fi

# ── 3. --at pins a specific backup ───────────────────────────────────────
bf fetch --at 20260101_010101
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r .folder)" = "20260101_010101" ]; then
  ok "--at pins a specific folder"
else
  no "--at pins a specific folder" "rc=$RC"
fi

bf fetch --at 29990101_000000
if [ "$RC" -ne 0 ] && errsays "no such backup folder"; then
  ok "--at on a folder that does not exist fails and lists what does"
else
  no "--at on a folder that does not exist fails" "rc=$RC"
fi

# ── 4. the site config always comes along, encrypted or not ──────────────
# It is what carries encryption_key and backup_encryption_key, and it is
# readable even in an -enc backup, so a restore is self-describing.
bf fetch --at 20260202_020202
if [ "$RC" -eq 0 ] &&
  [ "$(printf '%s' "$OUT" | jq -r .encrypted)" = "true" ] &&
  [ "$(jq -r .backup_encryption_key "$(printf '%s' "$OUT" | jq -r .site_config)")" = "BK2" ]; then
  ok "-enc is detected and the plaintext site config is still readable"
else
  no "-enc is detected and the site config is readable" "rc=$RC $(printf '%s' "$OUT" | jq -c '{encrypted,site_config}' 2>&1)"
fi

# ── 5. a partial dump is refused before anything is downloaded ───────────
bf fetch --at 20260303_030303
if [ "$RC" -ne 0 ] && errsays "PARTIAL"; then
  if find "$FRAPPE_BACKUP_CACHE" -name '*mar-partial*' -o -name '*partial-database*' | grep -q .; then
    no "partial refused before download" "it downloaded the file anyway"
  else
    ok "partial backup is refused, and nothing was downloaded"
  fi
else
  no "partial backup is refused" "rc=$RC"
fi

# ── 6. public vs private archives ────────────────────────────────────────
# "-files.tar" is a suffix of "-private-files.tar", so a naive match picks the
# private archive as the public one and restores it into the wrong place.
bf fetch --at 20260101_010101 --files --private-files
pub="$(printf '%s' "$OUT" | jq -r .files)"
priv="$(printf '%s' "$OUT" | jq -r .private_files)"
if [ "$RC" -eq 0 ] && [ "$(cat "$pub")" = "public-archive" ] && [ "$(cat "$priv")" = "private-archive" ]; then
  ok "public and private archives are told apart"
else
  no "public and private archives are told apart" "pub=$(basename "$pub") priv=$(basename "$priv")"
fi

# ── 7. archives are opt-in, but their availability is reported ───────────
bf fetch --at 20260101_010101
if [ "$(printf '%s' "$OUT" | jq -r .files)" = "null" ] &&
  [ "$(printf '%s' "$OUT" | jq -r .available.files)" = "true" ]; then
  ok "archives are skipped by default and reported as available"
else
  no "archives are skipped by default and reported as available" "$(printf '%s' "$OUT" | jq -c '{files,available}')"
fi

# ── 8. cache reuse ───────────────────────────────────────────────────────
bf fetch --at 20260101_010101
if errsays "cached" && ! errsays "fetch   "; then
  ok "a second fetch of the same folder is served from cache"
else
  no "a second fetch of the same folder is served from cache" "$(printf '%s' "$ERR" | tr '\n' ' ')"
fi

# ── 9. a truncated cache entry is not trusted ────────────────────────────
db="$(printf '%s' "$OUT" | jq -r .database)"
printf 'x' >"$db"
bf fetch --at 20260101_010101
if errsays "fetch   " && [ "$(cat "$(printf '%s' "$OUT" | jq -r .database)")" = "jan-db" ]; then
  ok "a size mismatch invalidates the cache entry"
else
  no "a size mismatch invalidates the cache entry" "$(printf '%s' "$ERR" | tr '\n' ' ')"
fi

# ── 10. retention never eats the folder this run just wrote ──────────────
# `sort | head` takes the oldest, and fetching an older backup while newer ones
# are cached makes the fresh directory exactly that.
bf fetch --at 20260404_040404
bf fetch --at 20260202_020202
bf fetch --at 20260101_010101 --keep 1
db="$(printf '%s' "$OUT" | jq -r .database)"
if [ "$RC" -eq 0 ] && [ -f "$db" ]; then
  ok "retention keeps the folder just downloaded"
else
  no "retention keeps the folder just downloaded" "rc=$RC manifest points at a deleted file"
fi

# ── 11. a prefix inside the bucket ───────────────────────────────────────
mk "$SRC/Backups/20260505_050505/20260505_050505-prod_example_com-database.sql.gz" "may-db"
FRAPPE_BACKUP_SOURCE="$SRC/Backups" bf list
if [ "$OUT" = "20260505_050505" ]; then
  ok "a prefix inside the bucket is honoured"
else
  no "a prefix inside the bucket is honoured" "out=$OUT"
fi

# ── 12. actionable errors ────────────────────────────────────────────────
FRAPPE_BACKUP_SOURCE="$WORK/empty" bf list || true
if [ "$RC" -ne 0 ] && errsays "no backup folders"; then
  ok "an empty source explains itself and mentions the prefix"
else
  no "an empty source explains itself" "rc=$RC"
fi

# With no source at all, the message has to name the secret, not just the var.
(
  unset FRAPPE_BACKUP_SOURCE
  "$BF" list 2>"$WORK/.err" >/dev/null || true
)
if grep -q "backup-access" "$WORK/.err" && grep -q "BACKUPS_URL" "$WORK/.err"; then
  ok "missing credentials name the agenix secret, not just the variable"
else
  no "missing credentials name the agenix secret" "$(head -3 "$WORK/.err")"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
