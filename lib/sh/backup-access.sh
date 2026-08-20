# Prompt for the object-store credentials `bench restore` needs, check they
# work, and write them into secrets/backup-access.age.
#
# The secret is a shell env-file, because `bench restore` sources it with
# `set -a`. Writing it by hand means knowing five variable names and the
# quoting rules; this asks five questions instead.
#
# Preamble (baked by lib/secrets-tools.nix) provides: AGENIX, AGECHECK,
# RULES_JSON, SECRET_REL, IDENTITY_PATHS, FETCH, and write_rules().

if [ ! -t 0 ] || [ ! -t 1 ]; then
  cat >&2 <<'EOF'
frappe-nix: setup-backup-access needs an interactive terminal.

To script it, pipe the env-file into edit-secret instead:

    edit-secret backup-access <<'ENV'
    BACKUPS_URL=https://s3.us-east-005.backblazeb2.com
    BACKUPS_ACCESS_KEY=...
    BACKUPS_SECRET_KEY=...
    BACKUPS_BUCKET=...
    BACKUPS_PREFIX=
    ENV
EOF
  exit 1
fi

cd "${FRAPPE_BENCH_ROOT:?run this from the dev shell}"

# ── identities ────────────────────────────────────────────────────────────
# Same list agenix-shell decrypts with, so "it worked in the shell" and "I can
# write the secret" cannot disagree.
#
# Two arrays, because the two tools spell this differently and getting it wrong
# is a hard error, not a fallback:
#   ragenix  -i takes multiple values  (-i a b)   — repeating the flag is
#            rejected with "cannot be used multiple times"
#   rage     -i takes one value        (-i a -i b)
# ryantm/agenix matches rage here, which is why the repeated form looks right.
# shellcheck disable=SC2206  # deliberate word splitting: IDENTITY_PATHS is a list
candidates=($IDENTITY_PATHS)
readable=()
for path in "${candidates[@]}"; do
  [ -r "$path" ] && readable+=("$path")
done
identity_args=()
rage_args=()
if [ "${#readable[@]}" -gt 0 ]; then
  identity_args=(-i "${readable[@]}")
  for path in "${readable[@]}"; do
    rage_args+=(-i "$path")
  done
fi
if [ "${#readable[@]}" -eq 0 ]; then
  gum style --foreground 1 "No readable SSH key among: ${candidates[*]}"
  echo
  echo "agenix needs one of those to encrypt to itself, so that you can read"
  echo "back what you are about to write. Point identityPaths at your key:"
  echo
  echo "    frappe-nix.secrets.identityPaths = [ \"\$HOME/.ssh/id_something\" ];"
  exit 1
fi

gum style --border rounded --padding "0 1" --border-foreground 4 \
  "Backup access for $(basename "$PWD")" \
  "" \
  "These are the object-store credentials \`bench restore\` uses to find and" \
  "download production backups. They are encrypted to this bench's declared" \
  "recipients and committed as $SECRET_REL."

# ── existing values, if any ───────────────────────────────────────────────
# Editing is the common case after the first run — a rotated key changes two
# of five fields — so pre-fill from whatever is already there.
URL="" ; ACCESS="" ; SECRET="" ; BUCKET="" ; PREFIX=""
if [ -f "$SECRET_REL" ]; then
  # rage, not agenix: ragenix implements only --edit/--rekey/--schema, so there
  # is no `agenix -d` to read the current values back with.
  if existing="$(rage --decrypt "${rage_args[@]}" "$SECRET_REL" 2>/dev/null)"; then
    # Parsed, not sourced: this is ciphertext someone else may have written,
    # and sourcing it would execute whatever is in it.
    while IFS='=' read -r key value; do
      value="${value%\'}"
      value="${value#\'}"
      case "$key" in
        BACKUPS_URL) URL="$value" ;;
        BACKUPS_ACCESS_KEY) ACCESS="$value" ;;
        BACKUPS_SECRET_KEY) SECRET="$value" ;;
        BACKUPS_BUCKET) BUCKET="$value" ;;
        BACKUPS_PREFIX) PREFIX="$value" ;;
        *) ;;
      esac
    done <<<"$existing"
    gum style --foreground 3 "Editing the existing secret; current values are pre-filled."
  else
    gum style --foreground 3 \
      "$SECRET_REL exists but none of your keys can read it." \
      "Continuing will overwrite it with what you enter."
    gum confirm "Overwrite it?" || exit 1
  fi
fi

echo

ask() { # ask <header> <placeholder> [current]
  gum input --header "$1" --placeholder "$2" --value "${3:-}" --width 72
}

URL="$(ask 'Endpoint URL' 'https://s3.us-east-005.backblazeb2.com' "$URL")"
[ -n "$URL" ] || { gum style --foreground 1 "An endpoint is required."; exit 1; }
case "$URL" in
  http://* | https://*) ;;
  *)
    gum style --foreground 3 "That has no scheme; assuming https://"
    URL="https://$URL"
    ;;
esac

ACCESS="$(ask 'Access key ID' '005ecc20398e6520000000004' "$ACCESS")"
[ -n "$ACCESS" ] || { gum style --foreground 1 "An access key is required."; exit 1; }

# --password: the only one of the five worth keeping off the scrollback, which
# also means it cannot be pre-filled visibly like the others. Blank keeps the
# one already stored, so rotating an access key does not mean re-typing a
# secret that has not changed.
prev_secret="$SECRET"
SECRET="$(gum input --password --header 'Secret access key' \
  --placeholder "${prev_secret:+leave blank to keep the stored one}" --width 72)"
if [ -z "$SECRET" ]; then
  if [ -n "$prev_secret" ]; then
    SECRET="$prev_secret"
    gum style --foreground 3 "Keeping the stored secret key."
  else
    gum style --foreground 1 "A secret key is required."
    exit 1
  fi
fi

BUCKET="$(ask 'Bucket' 'MyERPBackups' "$BUCKET")"
[ -n "$BUCKET" ] || { gum style --foreground 1 "A bucket is required."; exit 1; }

PREFIX="$(ask 'Path inside the bucket (optional)' 'Backups' "$PREFIX")"

# ── does it work? ─────────────────────────────────────────────────────────
# Checked before encrypting, because a typo in a key is otherwise a mystery
# several minutes into the first restore.
echo
if gum confirm "Test these against the bucket now?"; then
  set +e
  listing="$(
    BACKUPS_URL="$URL" \
      BACKUPS_ACCESS_KEY="$ACCESS" \
      BACKUPS_SECRET_KEY="$SECRET" \
      BACKUPS_BUCKET="$BUCKET" \
      BACKUPS_PREFIX="$PREFIX" \
      "$FETCH" list 2>&1
  )"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    count="$(printf '%s\n' "$listing" | grep -c . || true)"
    newest="$(printf '%s\n' "$listing" | tail -n1)"
    gum style --foreground 2 "✓ $count backup(s) found; newest is $newest"
  else
    gum style --foreground 1 "Could not list the bucket:"
    printf '%s\n' "$listing" | sed 's/^/    /' >&2
    echo
    gum confirm "Save anyway?" || exit 1
  fi
fi

# ── write it ──────────────────────────────────────────────────────────────
# Single-quoted values: the file is sourced, and an S3 secret routinely
# contains characters the shell would otherwise expand. Embedded single quotes
# are closed, escaped and reopened the usual way.
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

payload="$(
  printf '# Written by setup-backup-access. Sourced by bench restore.\n'
  printf 'BACKUPS_URL=%s\n' "$(q "$URL")"
  printf 'BACKUPS_ACCESS_KEY=%s\n' "$(q "$ACCESS")"
  printf 'BACKUPS_SECRET_KEY=%s\n' "$(q "$SECRET")"
  printf 'BACKUPS_BUCKET=%s\n' "$(q "$BUCKET")"
  [ -n "$PREFIX" ] && printf 'BACKUPS_PREFIX=%s\n' "$(q "$PREFIX")"
)"

write_rules
# EDITOR is how agenix takes content without opening an editor. Both it and
# ragenix accept an absolute path, which is what write_rules generates rules
# for — see lib/secrets-tools.nix.
printf '%s\n' "$payload" |
  EDITOR="cp /dev/stdin" "$AGENIX" -e "$PWD/$SECRET_REL" "${identity_args[@]}"

# An untracked .age is invisible to the flake — a flake's source tree is
# exactly its git-tracked files — and the failure reads as "my key is wrong".
if ! git ls-files --error-unmatch -- "$SECRET_REL" >/dev/null 2>&1; then
  git add -- "$SECRET_REL"
fi

echo
"$AGECHECK" check "$RULES_JSON" --root . || true

gum style --border rounded --padding "0 1" --border-foreground 2 \
  "Wrote $SECRET_REL" \
  "" \
  "git commit $SECRET_REL" \
  "bench restore"
