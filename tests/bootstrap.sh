#!/usr/bin/env bash
# Checks for frappe-nix-bootstrap and the bench template's .envrc: a fresh
# clone's apps/* submodules, checked out before Nix evaluates the bench's flake
# (which reads them, inputs.self.submodules), from the frappe-nix-sources that
# flake.lock pins. The same script as odoo-nix's tests/bootstrap.sh, which
# also covers its project shape; keep the two in step.
#
# `nix` is a stub: a check's sandbox has no Nix to evaluate a flake with. It
# answers the bootstrap's two questions (the pinned revision, from the lock;
# whether that revision has `sources`) and runs `nix run …#bootstrap` as the
# bootstrap under test. What the stub cannot show -- `nix eval` of a project
# that reads its submodules, on a fresh clone with a dirty tree -- is the
# manual check in docs/reference/troubleshooting.md, "A fresh clone". lib/bootstrap-lock.nix,
# the lock reading, is checks.bootstrap-lock.
#
# Usage: bootstrap.sh <path-to-frappe-nix-bootstrap> <templates/bench/.envrc> <label>
set -euo pipefail

BOOTSTRAP="$1"
ENVRC="$2"
LABEL="$3"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=protocol.file.allow GIT_CONFIG_VALUE_0=always

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
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}
says() { grep -qF -- "$1" <<< "$OUT"; }
silent_on() { ! grep -qF -- "$1" <<< "$OUT"; }

# The project's shape: odoo-nix's OCB at odoo/ and OCA repos under modules/,
# frappe-nix's apps under apps/.
case "$LABEL" in
  odoo-nix)
    SUBS=(odoo modules/a)
    TREELESS=odoo
    ;;
  frappe-nix)
    SUBS=(apps/frappe apps/erpnext)
    TREELESS=
    ;;
  *)
    echo "unknown label $LABEL" >&2
    exit 2
    ;;
esac

# ── the stub nix ──────────────────────────────────────────────────────────
# The bootstrap's own <label>-sources, wrapped as the pinned revision's: it
# notes that it ran, so a check can tell the two apart.
REAL_SOURCES="$(sed -n 's/^SOURCES=//p' "$BOOTSTRAP")"
test -x "$REAL_SOURCES"
PINNED="$ROOT/pinned"
mkdir -p "$PINNED/bin" "$ROOT/bin"
BASH_BIN="$(command -v bash)"
cat > "$PINNED/bin/$LABEL-sources" << EOF
#!$BASH_BIN
echo pinned >> "$ROOT/sources.log"
exec "$REAL_SOURCES" "\$@"
EOF
chmod +x "$PINNED/bin/$LABEL-sources"

# STUB_PIN=new: the pinned revision has `sources`; old: it predates it.
# STUB_RUN=fail: `nix run` cannot fetch the bootstrap.
cat > "$ROOT/bin/nix" << EOF
#!$BASH_BIN
echo "nix \$*" >> "$ROOT/nix.log"
case "\$1" in
  eval)
    if [ "\$4" = --expr ]; then
      # The lock reading: the real lock, and the input it is asked for.
      grep -qF '"$LABEL"' "\$BOOTSTRAP_LOCK" || exit 1
      grep -qF 'input = "$LABEL"' <<< "\$5" || exit 1
      printf stub:pinned
      exit 0
    fi
    [ "\$3" = 'stub:pinned#sources.outPath' ] || exit 1
    if [ "\${STUB_PIN:-new}" = new ]; then printf '%s' "$PINNED"; exit 0; fi
    echo "error: flake 'stub:pinned' does not provide attribute 'packages.x86_64-linux.sources'" >&2
    exit 1
    ;;
  build)
    [ "\$4" = 'stub:pinned#sources' ] && [ "\${STUB_PIN:-new}" = new ] || exit 1
    echo "$PINNED"
    ;;
  run)
    if [ "\${STUB_RUN:-ok}" != ok ]; then echo "error: unable to download" >&2; exit 1; fi
    shift 2
    [ "\$1" = -- ] && shift
    exec "$BOOTSTRAP" "\$@"
    ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$ROOT/bin/nix"
export PATH="$ROOT/bin:$PATH"

nix_calls() { cat "$ROOT/nix.log" 2> /dev/null || true; }
pinned_ran() { [ -s "$ROOT/sources.log" ]; }

# direnv's way with an .envrc, near enough: sourced by bash, here in strict
# mode (direnv's strict_env), with `use` standing in for its stdlib.
load() { # <dir> -- sets OUT (stderr and stdout) and RC
  : > "$ROOT/nix.log"
  : > "$ROOT/sources.log"
  OUT="$(cd "$1" && bash -c '
    set -euo pipefail
    use() { echo "use $*"; }
    source "$0"
    echo loaded
  ' "$ENVRC" 2>&1)" && RC=0 || RC=$?
}

# ── the fixture: a project with its submodules on file:// remotes ─────────
seed_remote() { # <name>
  local seed="$ROOT/seed/$1"
  mkdir -p "$seed"
  printf '%s\n' "$1" > "$seed/README"
  git -C "$seed" init -q -b main
  git -C "$seed" add -A
  git -C "$seed" commit -q -m init
  git init -q --bare "$ROOT/remotes/$1.git"
  git -C "$ROOT/remotes/$1.git" symbolic-ref HEAD refs/heads/main
  # Filters allowed, as GitHub does: partial clones, as shell entry makes them.
  git -C "$ROOT/remotes/$1.git" config uploadpack.allowFilter true
  git -C "$ROOT/remotes/$1.git" config uploadpack.allowAnySHA1InWant true
  git -C "$seed" push -q "$ROOT/remotes/$1.git" main
}
PROJECT="$ROOT/project"
mkdir -p "$PROJECT"
cd "$PROJECT"
git init -q -b main
for p in "${SUBS[@]}"; do
  seed_remote "${p##*/}"
  git submodule add -q -b main "file://$ROOT/remotes/${p##*/}.git" "$p"
done
cp "$ENVRC" .envrc
printf '{ outputs = _: { }; }\n' > flake.nix
cat > flake.lock << EOF
{
  "nodes": {
    "root": { "inputs": { "$LABEL": "${LABEL}_2" } },
    "${LABEL}_2": { "locked": { "type": "github", "owner": "Avunu", "repo": "$LABEL", "rev": "0000000000000000000000000000000000000000" } }
  },
  "root": "root",
  "version": 7
}
EOF
git add -A
git commit -q -m project
for c in clone clone-old clone-nolock clone-offline; do git clone -q "$PROJECT" "$ROOT/$c"; done

at_pin() { # <path>
  [ -f "$1/README" ] \
    && [ "$(git -C "$1" rev-parse HEAD)" = "$(git ls-files -s -- "$1" | awk '{ print $2 }')" ]
}
filter() { git -C "$1" config remote.origin.partialclonefilter; }

echo "── a fresh clone, its tree dirty ────────────────────────────────"
cd "$ROOT/clone"
# What made Nix read the submodules from their (empty) working-tree paths.
printf '# local edit\n' >> flake.nix
load .
check_eq "the .envrc loads" 0 "$RC"
check "…and still uses the flake" says "use flake . --no-pure-eval"
check "…after running $LABEL's bootstrap on the project root" grep -qE "^nix run github:Avunu/$LABEL(/[^#]*)?#bootstrap -- $ROOT/clone\$" <<< "$(nix_calls)"
for p in "${SUBS[@]}"; do
  check "checks out $p at its pinned commit" at_pin "$p"
done
check "…with the sources command flake.lock pins" pinned_ran
check "…says so" says "$LABEL: checking out submodules before Nix evaluates the flake, with $LABEL-sources from stub:pinned"
if [ -n "$TREELESS" ]; then
  check_eq "…$TREELESS commits only" tree:0 "$(filter "$TREELESS")"
  check_eq "…the rest commits and folders" blob:none "$(filter "${SUBS[1]}")"
else
  check_eq "…as partial clones" blob:none "$(filter "${SUBS[0]}")"
fi
check_eq "…leaving the local edit" " M flake.nix" "$(git status --porcelain)"

load .
check_eq "the next load runs no nix at all" "" "$(nix_calls)"
check_eq "…and says only what direnv says" "$(printf 'use flake . --no-pure-eval\nloaded')" "$OUT"
OUT="$("$BOOTSTRAP" "$ROOT/clone" 2>&1)" && RC=0 || RC=$?
check_eq "the bootstrap on its own: exits 0 with nothing to do" 0 "$RC"
check_eq "…silently" "" "$OUT"

echo "── a lock from before the sources command ───────────────────────"
cd "$ROOT/clone-old"
STUB_PIN=old load .
check_eq "the .envrc loads" 0 "$RC"
for p in "${SUBS[@]}"; do
  check "checks out $p" at_pin "$p"
done
refute_pinned() { ! pinned_ran; }
check "…with the bootstrap's own sources command" refute_pinned
check "…says so" says "flake.lock pins no $LABEL with a sources command"
check "…without the stub's attribute error" silent_on "does not provide attribute"

echo "── no flake.lock ────────────────────────────────────────────────"
cd "$ROOT/clone-nolock"
git rm -q flake.lock
load .
for p in "${SUBS[@]}"; do
  check "checks out $p" at_pin "$p"
done
check "…with the bootstrap's own sources command" refute_pinned
no_lock_read() { ! grep -qF -- --expr <<< "$(nix_calls)"; }
check "…reading no lock" no_lock_read

echo "── nix run fails ────────────────────────────────────────────────"
cd "$ROOT/clone-offline"
STUB_RUN=fail load .
check_eq "the .envrc still loads" 0 "$RC"
check "…and uses the flake, for Nix to report what it cannot read" says "use flake . --no-pure-eval"
check "…the submodules as they were" test ! -e "${SUBS[0]}/README"

echo "── not a git checkout ───────────────────────────────────────────"
mkdir -p "$ROOT/plain"
load "$ROOT/plain"
check_eq "the .envrc loads" 0 "$RC"
check_eq "…saying nothing of git" "$(printf 'use flake . --no-pure-eval\nloaded')" "$OUT"
check_eq "…and running no nix" "" "$(nix_calls)"
OUT="$("$BOOTSTRAP" "$ROOT/plain" 2>&1)" && RC=0 || RC=$?
check_eq "the bootstrap there exits 0" 0 "$RC"
check_eq "…silently" "" "$OUT"
OUT="$("$BOOTSTRAP" "$ROOT/missing" 2>&1)" && RC=0 || RC=$?
check_eq "…as it does for a directory that is not there" "0:" "$RC:$OUT"

echo "── no git on PATH ───────────────────────────────────────────────"
mkdir -p "$ROOT/nogit"
ln -s "$(command -v grep)" "$ROOT/nogit/grep"
ln -s "$ROOT/bin/nix" "$ROOT/nogit/nix"
: > "$ROOT/nix.log"
OUT="$(cd "$ROOT/clone-offline" && PATH="$ROOT/nogit" "$BASH_BIN" -c '
  set -euo pipefail
  use() { echo "use $*"; }
  source "$0"
  echo loaded
' "$ENVRC" 2>&1)" && RC=0 || RC=$?
check_eq "the .envrc loads" 0 "$RC"
check_eq "…saying nothing of git" "$(printf 'use flake . --no-pure-eval\nloaded')" "$OUT"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
