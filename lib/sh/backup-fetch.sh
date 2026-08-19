# frappe-nix-backup-fetch — discover and download one Frappe backup set from an
# S3-protocol object store.
#
# Deliberately knows nothing about Frappe, bench, or a database: it resolves a
# backup folder, downloads what was asked for, and prints a JSON manifest. That
# split is what lets `bench restore` stay short, lets the production NixOS
# restore reuse the same discovery, and lets the whole thing be tested offline —
# `mc ls --json` emits the same {"type":"folder","key":…,"size":…} shape for a
# local directory as it does for a bucket, so the tests point SOURCE at a
# fixture tree and need no server, no credentials and no network.
#
# The layout it reads is Frappe's own. From
# frappe/integrations/doctype/s3_backup_settings/s3_backup_settings.py:
#
#     folder = doc.backup_path + os.path.basename(db_filename)[:15] + "/"
#
# i.e. one folder per backup named for its timestamp — [:15] is exactly
# "YYYYMMDD_HHMMSS", so the names sort lexicographically. Inside, from
# frappe/utils/backups.py set_backup_file_name():
#
#     {ts}-{slug}[-partial]-database[-enc].sql.gz
#     {ts}-{slug}-site_config_backup[-enc].json
#     {ts}-{slug}-files[-enc].{tar,tgz}
#     {ts}-{slug}-private-files[-enc].{tar,tgz}
#
# Two things about those names are load-bearing and easy to get wrong:
#
#   * "-enc" is a *naming convention only*. `bench restore` decides whether a
#     dump is encrypted by running `file` on it and looking for "AES"
#     (frappe/commands/site.py). We surface the suffix so the caller can resolve
#     a key up front, but we do not treat it as authoritative.
#   * backup_encryption() encrypts only the database and the two file archives —
#     never the site-config JSON (backups.py: `paths = (backup_path_db,
#     backup_path_files, backup_path_private_files)`). So the config file is
#     readable plaintext even in an "-enc" backup, and it carries both
#     `encryption_key` and `backup_encryption_key`. A restore is therefore
#     self-describing, which is why this tool always fetches it.

usage() {
  cat <<'EOF'
Usage: frappe-nix-backup-fetch <list|fetch> [options]

  list                     Print available backup folders, oldest first.
  fetch                    Download a backup set; print a JSON manifest.

Options for `fetch`:
  --at <YYYYMMDD_HHMMSS>   A specific backup folder (default: the newest).
  --files                  Also fetch the public files archive.
  --private-files          Also fetch the private files archive.
  --no-cache               Re-download even if the cached copy is intact.
  --dest <dir>             Download directory (default: $FRAPPE_BACKUP_CACHE).
  --keep <n>               Cached backup folders to retain (default: 2).

Source (either form):
  FRAPPE_BACKUP_SOURCE     An mc target: a local path, or <alias>/<bucket>[/prefix].
                           Set this to restore from a restic mount or a fixture.
  BACKUPS_URL              S3 endpoint, plus BACKUPS_ACCESS_KEY,
                           BACKUPS_SECRET_KEY, BACKUPS_BUCKET and optionally
                           BACKUPS_PREFIX. This is the shape of the
                           `backup-access` agenix secret.
EOF
}

die() {
  printf 'frappe-nix: %s\n' "$1" >&2
  shift
  for line in "$@"; do printf '  %s\n' "$line" >&2; done
  exit 1
}

note() { printf '  %s\n' "$*" >&2; }

# ── arguments ─────────────────────────────────────────────────────────────
MODE=""
AT=""
WANT_PUBLIC=false
WANT_PRIVATE=false
NO_CACHE=false
DEST="${FRAPPE_BACKUP_CACHE:-}"
KEEP=2

case "${1:-}" in
  list | fetch)
    MODE="$1"
    shift
    ;;
  -h | --help | "")
    usage
    exit 0
    ;;
  *) die "unknown subcommand: $1" "run with --help" ;;
esac

while [ "$#" -gt 0 ]; do
  case "$1" in
    --at)
      AT="$2"
      shift 2
      ;;
    --files)
      WANT_PUBLIC=true
      shift
      ;;
    --private-files)
      WANT_PRIVATE=true
      shift
      ;;
    --no-cache)
      NO_CACHE=true
      shift
      ;;
    --dest)
      DEST="$2"
      shift 2
      ;;
    --keep)
      KEEP="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "unknown option: $1" "run with --help" ;;
  esac
done

# ── the source ────────────────────────────────────────────────────────────
# `mc` is reached through $FRAPPE_BACKUP_MC so the tests can substitute a stub;
# in the dev shell it is just `mc`.
MC="${FRAPPE_BACKUP_MC:-mc}"

# Percent-encode, because an S3 secret routinely contains '/' and '+' and both
# would terminate the userinfo field of the URL below.
urlenc() { jq -rn --arg s "$1" '$s|@uri'; }

if [ -n "${FRAPPE_BACKUP_SOURCE:-}" ]; then
  BASE="${FRAPPE_BACKUP_SOURCE%/}/"
else
  for var in BACKUPS_URL BACKUPS_ACCESS_KEY BACKUPS_SECRET_KEY BACKUPS_BUCKET; do
    if [ -z "$(eval "printf '%s' \"\${$var:-}\"")" ]; then
      die "\$$var is not set." \
        "" \
        "These come from this bench's 'backup-access' agenix secret, which is a" \
        "shell env-file defining BACKUPS_URL, BACKUPS_ACCESS_KEY," \
        "BACKUPS_SECRET_KEY, BACKUPS_BUCKET and optionally BACKUPS_PREFIX." \
        "" \
        "  check-secrets            is your key a recipient?" \
        "  edit-secret backup-access   create or repair it" \
        "" \
        "Or point at a directory instead:  FRAPPE_BACKUP_SOURCE=/path/to/backups"
    fi
  done

  # MC_HOST_<alias> rather than `mc alias set`: the latter writes the
  # credentials into the user's global ~/.mc/config.json, where they outlive the
  # process and are shared with every other tool the user runs. It also needs a
  # unique alias name and a cleanup trap to match. This needs neither.
  scheme="${BACKUPS_URL%%://*}"
  hostpart="${BACKUPS_URL#*://}"
  # Assigned before export so a failing urlenc is an error rather than an
  # empty credential silently baked into the URL (shellcheck SC2155).
  enc_key="$(urlenc "$BACKUPS_ACCESS_KEY")"
  enc_secret="$(urlenc "$BACKUPS_SECRET_KEY")"
  MC_HOST_frappenix="$scheme://$enc_key:$enc_secret@$hostpart"
  export MC_HOST_frappenix

  prefix="${BACKUPS_PREFIX:-}"
  prefix="${prefix#/}"
  [ -n "$prefix" ] && prefix="${prefix%/}/"
  BASE="frappenix/${BACKUPS_BUCKET}/${prefix}"
fi

# ── folder discovery ──────────────────────────────────────────────────────
# `mc ls --json`, not `awk '{print $NF}'` over the human output: that splits on
# spaces, so any key containing one silently yields the wrong field.
list_folders() {
  "$MC" ls --json "$BASE" 2>/dev/null |
    jq -r 'select(.type == "folder") | .key' |
    sed 's:/*$::' |
    grep -Ex '[0-9]{8}_[0-9]{6}' |
    sort
}

folders="$(list_folders || true)"

if [ -z "$folders" ]; then
  die "no backup folders under $BASE" \
    "" \
    "Frappe's S3 backup integration writes one folder per backup, named for its" \
    "timestamp (YYYYMMDD_HHMMSS). Nothing there matched." \
    "" \
    "If the backups sit under a prefix, set it in the backup-access secret as" \
    "BACKUPS_PREFIX (or frappe-nix.restore.prefix). What the bucket does have:" \
    "" \
    "$("$MC" ls "$BASE" 2>&1 | head -20)"
fi

if [ "$MODE" = list ]; then
  printf '%s\n' "$folders"
  exit 0
fi

if [ -n "$AT" ]; then
  printf '%s\n' "$folders" | grep -qxF "$AT" ||
    die "no such backup folder: $AT" "" "Available:" "$(printf '%s' "$folders" | tr '\n' ' ')"
  FOLDER="$AT"
else
  FOLDER="$(printf '%s\n' "$folders" | tail -n1)"
fi

note "backup folder: $FOLDER/"

# ── object selection ──────────────────────────────────────────────────────
LISTING="$("$MC" ls --json "$BASE$FOLDER/" 2>/dev/null || true)"

# $1 = jq test regex; prints the first matching key.
pick() {
  printf '%s' "$LISTING" | jq -r --arg re "$1" \
    'select(.type == "file") | select(.key | test($re)) | .key' | head -n1
}
size_of() {
  printf '%s' "$LISTING" | jq -r --arg k "$1" \
    'select(.key == $k) | .size' | head -n1
}

# A partial dump cannot create a site — frappe's restore_backup() rejects it
# outright — so it must never be selected as "the database". Excluded here
# rather than left to `bench restore`, which only notices after the download.
DB_OBJ="$(printf '%s' "$LISTING" | jq -r \
  'select(.type == "file")
   | select(.key | test("-database(-enc)?\\.sql\\.gz$"))
   | select(.key | test("-partial-database") | not)
   | .key' | head -n1)"
if [ -z "$DB_OBJ" ]; then
  PARTIAL="$(pick '-partial-database(-enc)?\.sql\.gz$')"
  if [ -n "$PARTIAL" ]; then
    die "$BASE$FOLDER/ holds only a PARTIAL backup ($PARTIAL)." \
      "" \
      "A partial dump cannot create a site; frappe's restore_backup() refuses it." \
      "Pick a full backup:  bench restore --list" \
      "or apply this one to an existing site with \`bench partial-restore\`."
  fi
  die "no database dump in $BASE$FOLDER/" "" "The folder contains:" \
    "$(printf '%s' "$LISTING" | jq -r 'select(.type == "file") | "  " + .key')"
fi

CONF_OBJ="$(pick '-site_config_backup(-enc)?\.json$')"
# `-files.tar` is a suffix of `-private-files.tar`, so the public archive has to
# exclude the private one explicitly or it matches both.
PUB_OBJ="$(printf '%s' "$LISTING" | jq -r \
  'select(.type == "file")
   | select(.key | test("-files(-enc)?\\.(tar|tgz)$"))
   | select(.key | test("-private-files") | not)
   | .key' | head -n1)"
PRIV_OBJ="$(pick '-private-files(-enc)?\.(tar|tgz)$')"

ENCRYPTED=false
case "$DB_OBJ" in *-enc.sql.gz) ENCRYPTED=true ;; esac

# The production site slug, recovered from the filename. It is routinely not
# this bench's site name — production may be erpnext.example.com while the dev
# bench is erp.example.com — and the caller needs to know that to warn about it.
SLUG="$(printf '%s' "$DB_OBJ" | sed -E "s/^${FOLDER}-//; s/-database(-enc)?\.sql\.gz\$//")"

# ── download ──────────────────────────────────────────────────────────────
[ -n "$DEST" ] || die "no download directory" "pass --dest, or set FRAPPE_BACKUP_CACHE"
DIR="$DEST/$(printf '%s' "$BASE" | tr -c 'A-Za-z0-9._-' '_')/$FOLDER"
mkdir -p "$DIR"

# $1 = object key. Prints the local path, or nothing when $1 is empty.
#
# The cache key is the backup folder, which is a timestamp and therefore
# immutable — the name IS the version, so there is no staleness window and
# nothing to hash. Downloads land on a .partial and are renamed only after the
# size matches the listing, so an interrupted transfer can never be served from
# cache as if it were whole.
fetch_one() {
  local obj="$1" dest want have
  [ -n "$obj" ] || return 0
  dest="$DIR/$obj"
  want="$(size_of "$obj")"

  if [ "$NO_CACHE" = false ] && [ -f "$dest" ]; then
    have="$(wc -c <"$dest" | tr -d ' ')"
    if [ "$have" = "$want" ]; then
      note "cached  $obj"
      printf '%s' "$dest"
      return 0
    fi
  fi

  note "fetch   $obj"
  # --quiet drops the progress bar but not the summary table; keep the output
  # back so a real failure still says why.
  if ! cp_log="$("$MC" cp --quiet "$BASE$FOLDER/$obj" "$dest.partial" 2>&1)"; then
    rm -f "$dest.partial"
    die "could not download $obj" "" "$cp_log"
  fi
  have="$(wc -c <"$dest.partial" | tr -d ' ')"
  if [ "$have" != "$want" ]; then
    rm -f "$dest.partial"
    die "short download: $obj got $have of $want bytes"
  fi
  mv -f "$dest.partial" "$dest"
  printf '%s' "$dest"
}

DB_PATH="$(fetch_one "$DB_OBJ")"
CONF_PATH="$(fetch_one "$CONF_OBJ")"
PUB_PATH=""
PRIV_PATH=""
[ "$WANT_PUBLIC" = true ] && PUB_PATH="$(fetch_one "$PUB_OBJ")"
[ "$WANT_PRIVATE" = true ] && PRIV_PATH="$(fetch_one "$PRIV_OBJ")"

# Retention, per source. Keeping the previous folder as well as the current one
# makes "restore, realise you wanted yesterday's, --at <older>" a cache hit.
if [ "$KEEP" -gt 0 ]; then
  # Not `head -n -$KEEP`: negative counts are a GNU extension and this shell
  # ships to darwin too.
  # `|| true`: with pipefail set, grep exiting 1 because it filtered everything
  # out — the normal case when this run's folder is the only cached one — would
  # otherwise fail the assignment and abort the script after a successful
  # download, with no manifest and no explanation.
  cached="$(find "$(dirname "$DIR")" -mindepth 1 -maxdepth 1 -type d 2>/dev/null |
    grep -vxF "$DIR" | sort || true)"
  total="$(printf '%s\n' "$cached" | grep -c . || true)"
  # -1 because $DIR is excluded above but still occupies one of the slots.
  drop=$((total - KEEP + 1))
  if [ "$drop" -gt 0 ]; then
    printf '%s\n' "$cached" | head -n "$drop" | while IFS= read -r old; do
      [ -n "$old" ] && rm -rf -- "$old"
    done
  fi
fi

# ── manifest ──────────────────────────────────────────────────────────────
jq -n \
  --arg folder "$FOLDER" \
  --arg slug "$SLUG" \
  --argjson encrypted "$ENCRYPTED" \
  --arg database "$DB_PATH" \
  --arg site_config "$CONF_PATH" \
  --arg files "$PUB_PATH" \
  --arg private_files "$PRIV_PATH" \
  --arg available_files "$PUB_OBJ" \
  --arg available_private_files "$PRIV_OBJ" \
  '{
     folder: $folder,
     slug: $slug,
     encrypted: $encrypted,
     database: $database,
     site_config: (if $site_config == "" then null else $site_config end),
     files: (if $files == "" then null else $files end),
     private_files: (if $private_files == "" then null else $private_files end),
     available: {
       files: ($available_files != ""),
       private_files: ($available_private_files != "")
     }
   }'
