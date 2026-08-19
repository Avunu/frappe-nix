#!/usr/bin/env bash
# Offline test for lib/agecheck.py — the recipient-drift check.
#
# Generates throwaway SSH keys, encrypts a fixture to a subset of them, and
# asserts the checker's verdict. No network, no identity of the caller's, and
# no decryption: the whole point is that an age header names its recipients in
# the clear, so drift is detectable from the ciphertext alone.
#
# Usage: agecheck.sh <path-to-agecheck> <path-to-rage> <path-to-ssh-keygen>
set -euo pipefail

AGECHECK="$1"
RAGE="$2"
KEYGEN="$3"

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

# Run the checker, capturing merged output and exit status separately.
#
# NOT `checker … | grep -q x`: this script runs under `set -o pipefail`, and the
# checker exits non-zero by design whenever it finds drift — so the pipeline
# would report failure even when grep matched, and every "does it name the key?"
# assertion would silently invert.
CHECK_OUT=""
CHECK_RC=0
check() {
  CHECK_OUT="$("$AGECHECK" check "$@" 2>&1)" && CHECK_RC=0 || CHECK_RC=$?
}
says() { printf '%s' "$CHECK_OUT" | grep -q -- "$1"; }

# ── fixtures ──────────────────────────────────────────────────────────────
for n in alice bob carol; do
  "$KEYGEN" -q -t ed25519 -N "" -C "$n" -f "$WORK/$n" </dev/null
done
alice="$(cat "$WORK/alice.pub")"
bob="$(cat "$WORK/bob.pub")"
carol="$(cat "$WORK/carol.pub")"

mkdir -p secrets/site.example.com
echo "BACKUPS_URL=https://example.invalid" >plain.txt
"$RAGE" --encrypt -R "$WORK/alice.pub" -R "$WORK/bob.pub" \
  -o secrets/backup-access.age plain.txt

rules() {
  # $@ = public keys the rule declares
  local out='{"secrets/backup-access.age": ['
  local sep=""
  for k in "$@"; do
    out="$out$sep\"$k\""
    sep=", "
  done
  printf '%s]}' "$out"
}

# ── 1. tag derivation is stable and matches age's own scheme ──────────────
# Cross-checked against real ciphertext: the header stanza rage just wrote for
# alice must carry exactly the tag agecheck computes from alice's public key.
want="$("$AGECHECK" tag "$alice")"
got="$(grep -a '^-> ssh-ed25519 ' secrets/backup-access.age | awk '{print $3}' | grep -Fx "$want" || true)"
if [ -n "$got" ]; then
  ok "recipient tag matches the stanza rage wrote"
else
  no "recipient tag matches the stanza rage wrote" \
    "computed '$want', header has: $(grep -a '^-> ' secrets/backup-access.age | awk '{print $3}' | tr '\n' ' ')"
fi

# ── 2. declared == encrypted → pass, and rage's grease is not mistaken ───
# age implementations emit a "grease" stanza of a random type on every
# encryption, deliberately, so parsers cannot ossify. Treating an unrecognised
# stanza as something to report would put a spurious note on every real file.
rules "$alice" "$bob" >r.json
check r.json --root .
if [ "$CHECK_RC" -eq 0 ] && ! says "not verified"; then
  ok "matching recipients pass, with no grease false positive"
else
  no "matching recipients pass, with no grease false positive" "rc=$CHECK_RC $(printf '%s' "$CHECK_OUT" | head -2)"
fi

# ── 3. a key added to the rules but never re-encrypted → FAIL ─────────────
# This is the real-world bug this check exists for: a rotated key sits in the
# rules for months while the ciphertext still names its predecessor, and the
# person it was rotated for silently cannot decrypt anything.
rules "$alice" "$bob" "$carol" >r.json
check r.json --root .
if [ "$CHECK_RC" -ne 0 ] && says carol; then
  ok "un-rekeyed recipient fails, and names the key"
else
  no "un-rekeyed recipient fails, and names the key" "rc=$CHECK_RC $(printf '%s' "$CHECK_OUT" | head -2)"
fi

# ── 4. a key dropped from the rules but still able to decrypt → note ──────
# Not fatal: the file is still readable by everyone who should read it. But it
# is worth saying, because the removal has not actually taken effect yet.
rules "$alice" >r.json
check r.json --root .
if [ "$CHECK_RC" -eq 0 ] && says "no longer declared"; then
  ok "stale recipient is a note, not a failure"
else
  no "stale recipient is a note, not a failure" "rc=$CHECK_RC $(printf '%s' "$CHECK_OUT" | head -2)"
fi

# ── 5. declared but absent from disk → FAIL ──────────────────────────────
rules "$alice" "$bob" >r.json
mv secrets/backup-access.age secrets/moved.age
check r.json --root .
if [ "$CHECK_RC" -ne 0 ]; then
  ok "missing file fails"
else
  no "missing file fails" "exit 0 for a secret that is not on disk"
fi

# ── 6. on disk but undeclared → note (a renamed secret leaves one behind) ─
if says "not declared"; then
  ok "orphaned .age is reported"
else
  no "orphaned .age is reported" "$(printf '%s' "$CHECK_OUT" | head -2)"
fi
mv secrets/moved.age secrets/backup-access.age

# ── 7. a declared non-SSH recipient cannot be verified — say so ───────────
# An age native (X25519) recipient has no tag derivable from the public key, so
# the check cannot prove it is present. That must be a note, not a pass and not
# a failure.
agekey="$("$RAGE"-keygen 2>/dev/null | grep '^AGE-SECRET-KEY' || true)"
agepub="$(printf '%s' "$agekey" | "$RAGE"-keygen -y 2>/dev/null || true)"
if [ -n "$agepub" ]; then
  rules "$alice" "$bob" "$agepub" >r.json
  check r.json --root .
  if says "cannot be verified"; then
    ok "an age-native declared recipient is reported as unverifiable"
  else
    no "an age-native declared recipient is reported as unverifiable" "$(printf '%s' "$CHECK_OUT" | head -2)"
  fi
else
  printf '  skip age-native recipient (rage-keygen unavailable)\n'
fi

# ── 8. armored ciphertext is read the same as binary ─────────────────────
# ryantm/agenix writes a binary age file; ragenix wraps it in base64 PEM armor.
# A checker that only understands the binary form sees no recipients at all in
# anything ragenix wrote, reports "not encrypted to any declared recipient",
# and never clears no matter how many times you rekey.
"$RAGE" --encrypt --armor -R "$WORK/alice.pub" -R "$WORK/bob.pub" \
  -o secrets/armored.age plain.txt
head -n1 secrets/armored.age | grep -q 'BEGIN AGE ENCRYPTED FILE' ||
  no "fixture is actually armored" "rage did not armor it"
cat >r.json <<EOF
{"secrets/armored.age": ["$alice", "$bob"]}
EOF
check r.json --root .
if [ "$CHECK_RC" -eq 0 ]; then
  ok "an armored file's recipients are read"
else
  no "an armored file's recipients are read" "$(printf '%s' "$CHECK_OUT" | head -3)"
fi

rules "$alice" "$bob" "$carol" >/dev/null
cat >r.json <<EOF
{"secrets/armored.age": ["$alice", "$bob", "$carol"]}
EOF
check r.json --root .
if [ "$CHECK_RC" -ne 0 ] && says carol; then
  ok "drift is still caught in an armored file"
else
  no "drift is still caught in an armored file" "rc=$CHECK_RC"
fi
rm -f secrets/armored.age

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
