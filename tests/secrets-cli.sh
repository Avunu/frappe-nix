#!/usr/bin/env bash
# End-to-end test for edit-secret / rekey-secrets against a real ragenix.
#
# This exists because of a bug that shipped: the generated agenix rules were a
# store file with repo-relative keys, and ragenix resolves a rule key relative
# to the *rules file's own directory* — so every key became
# /nix/store/secrets/<name>.age. `agenix -e` reported no rule for the file, and
# `agenix -r` skipped it as "does not exist, ignored" and exited 0. A rekey
# silently did nothing, which is precisely the failure this whole feature was
# built to make impossible.
#
# Usage: secrets-cli.sh <edit-secret.sh> <rekey-secrets.sh> <check-secrets.sh> <key-dir>
#
# <key-dir> holds the committed fixture keypairs alice/alice.pub, bob/bob.pub.
set -euo pipefail

EDIT="$1"
REKEY="$2"
CHECK="$3"
KEYS="$4"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
ok() { printf '  ok   %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL %s\n' "$1"; printf '       %s\n' "${2:-}"; fail=$((fail + 1)); }

export HOME="$WORK/home"
mkdir -p "$HOME"
export GIT_CONFIG_GLOBAL="$WORK/gitconfig"
export GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

ROOT="$WORK/bench"
mkdir -p "$ROOT/secrets"
cd "$ROOT"
git init -q .
export FRAPPE_BENCH_ROOT="$ROOT"

install -m 0600 "$KEYS/alice" "$WORK/alice"
install -m 0600 "$KEYS/bob" "$WORK/bob"
cp "$KEYS/alice.pub" "$KEYS/bob.pub" "$WORK/"

# The rules the scripts were rendered against name alice only. A second set,
# naming alice and bob, stands in for "someone edited recipients in flake.nix".
# ragenix writes ASCII-armored age files, so the recipient stanzas sit inside a
# base64 blob rather than readable at the head of the file. De-armor first, the
# same way lib/agecheck.py has to.
tags() {
  python3 "$WORK/tags.py" "$1"
}
tag_of() { python3 -c "
import base64,hashlib,sys
b=base64.b64decode(open(sys.argv[1]).read().split()[1])
print(base64.b64encode(hashlib.sha256(b).digest()[:4]).decode().rstrip('='))
" "$1"; }


cat > "$WORK/tags.py" <<'TAGS_PY'
import base64, re, sys

data = open(sys.argv[1], "rb").read(1 << 16)
if data.startswith(b"-----BEGIN AGE"):
    body = "".join(
        line.strip()
        for line in data.decode("ascii", "replace").splitlines()
        if not line.startswith("-----")
    )
    data = base64.b64decode(body + "=" * (-len(body) % 4))

found = []
for raw in data.split(b"\n"):
    line = raw.decode("ascii", "replace")
    if line.startswith("---"):
        break
    m = re.match(r"^-> ssh-ed25519 (\S+)", line)
    if m:
        found.append(m.group(1))
sys.stdout.write(" ".join(sorted(found)) + (" " if found else ""))
TAGS_PY

A="$(tag_of "$WORK/alice.pub")"
B="$(tag_of "$WORK/bob.pub")"

# ── 1. edit-secret creates the file, where it belongs ────────────────────
# EDITOR is deliberately a real interactive editor, as it is on most machines.
# edit-secret has to override it when stdin is not a terminal: ryantm/agenix
# substitutes `cp -- /dev/stdin` itself, ragenix does not and fails with
# "Editor 'nano' exited with non-zero status code", which would make every
# secret in a bench hand-typed only.
export EDITOR="nano"
if printf 'BACKUPS_URL=https://example.invalid\n' | bash "$EDIT" backup-access >"$WORK/edit.log" 2>&1; then
  if [ -f "$ROOT/secrets/backup-access.age" ]; then
    ok "edit-secret writes secrets/backup-access.age in the bench, not the store"
  else
    no "edit-secret writes the file in the bench" "$(find / -name 'backup-access.age' 2>/dev/null | head -2)"
  fi
else
  no "edit-secret succeeds" "$(tail -4 "$WORK/edit.log")"
fi

# ── 2. …encrypted to the declared recipient ──────────────────────────────
if [ -f "$ROOT/secrets/backup-access.age" ] && [ "$(tags "$ROOT/secrets/backup-access.age")" = "$A " ]; then
  ok "the new secret is encrypted to the declared recipient"
else
  no "the new secret is encrypted to the declared recipient" \
    "expected '$A', got '$(tags "$ROOT/secrets/backup-access.age" 2>/dev/null)'"
fi

# ── 3. …and staged, because an untracked .age is invisible to the flake ──
if git ls-files --error-unmatch -- secrets/backup-access.age >/dev/null 2>&1; then
  ok "edit-secret stages the new secret"
else
  no "edit-secret stages the new secret" "still untracked"
fi

# ── 4. the round trip ────────────────────────────────────────────────────
if [ "$(rage -d -i "$WORK/alice" "$ROOT/secrets/backup-access.age" 2>/dev/null)" = "BACKUPS_URL=https://example.invalid" ]; then
  ok "the secret decrypts back to what was written"
else
  no "the secret decrypts back" ""
fi

# ── 5. rekey actually re-encrypts ────────────────────────────────────────
# The regression. With the rules pointing into the store this exited 0, printed
# "does not exist, ignored", and changed nothing.
before="$(tags "$ROOT/secrets/backup-access.age")"
bash "$REKEY" -i "$WORK/alice" >"$WORK/rekey.log" 2>&1 || true
after="$(tags "$ROOT/secrets/backup-access.age")"

if grep -qi "does not exist, ignored" "$WORK/rekey.log"; then
  no "rekey-secrets finds the secret" "agenix skipped it: $(grep -i 'ignored' "$WORK/rekey.log" | head -1)"
else
  # tags() sorts, so compare against a sorted expectation rather than the order
  # the recipients were declared in.
  want="$(printf '%s\n%s\n' "$A" "$B" | sort | tr '\n' ' ')"
  if [ "$after" = "$want" ]; then
    ok "rekey-secrets re-encrypts to the new recipient list"
  else
    no "rekey-secrets re-encrypts to the new recipient list" \
      "before='$before' after='$after' expected '$want'"
  fi
fi

# ── 6. and the result is readable by the key that was just added ─────────
if [ "$(rage -d -i "$WORK/bob" "$ROOT/secrets/backup-access.age" 2>/dev/null)" = "BACKUPS_URL=https://example.invalid" ]; then
  ok "the newly added recipient can decrypt"
else
  no "the newly added recipient can decrypt" "this is the whole point of a rekey"
fi

# ── 7. check-secrets agrees ──────────────────────────────────────────────
if bash "$CHECK" >"$WORK/check.log" 2>&1; then
  ok "check-secrets passes after a successful rekey"
else
  no "check-secrets passes after a rekey" "$(tail -4 "$WORK/check.log")"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
