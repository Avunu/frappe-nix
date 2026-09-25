#!/usr/bin/env bash
# Checks for `frappe-nix-apps-report` — what the dev shell says about apps/ on
# entry. What it says matters less than what it does not do: touch a submodule.
# Entry used to check out any registered submodule it found without one, which
# is how an app removed by hand came back, re-cloned, on the next direnv reload.
#
# Usage: apps-report.sh <path-to-frappe-nix-apps-report>
set -euo pipefail

TOOL="$1"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
# Submodule operations on file:// URLs are refused by default.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=protocol.file.allow GIT_CONFIG_VALUE_0=always

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

# Everything git knows about the bench and its submodules, and the file tree.
snapshot() {
  git status --porcelain --ignore-submodules=none
  git ls-files -s
  cat .gitmodules .git/config
  find .git/modules -maxdepth 3 -name HEAD -exec sh -c 'echo "$1: $(cat "$1")"' _ {} \; | sort
  find apps -maxdepth 2 | sort
}

seed_remote() { # <name>
  local seed="$ROOT/seed/$1"
  mkdir -p "$seed/$1"
  printf 'app_name = "%s"\n' "$1" > "$seed/$1/hooks.py"
  git -C "$seed" init -q -b develop
  git -C "$seed" add -A
  git -C "$seed" commit -q -m init
  git init -q --bare "$ROOT/remotes/$1.git"
  git -C "$seed" push -q "$ROOT/remotes/$1.git" develop
}
for a in present fresh gone; do seed_remote "$a"; done

BENCH="$ROOT/bench"
mkdir -p "$BENCH/apps"
cd "$BENCH"
git init -q -b main
for a in present fresh gone; do
  git submodule add -q -b develop "file://$ROOT/remotes/$a.git" "apps/$a"
done
mkdir -p apps/localapp/localapp
printf 'app_name = "localapp"\n' > apps/localapp/localapp/hooks.py
mkdir -p apps/strayapp && git -C apps/strayapp init -q -b main
printf 'x\n' > apps/strayapp/f && git -C apps/strayapp add -A && git -C apps/strayapp commit -q -m i
git -c advice.addEmbeddedRepo=false add -A 2>/dev/null
git commit -q -m bench

# fresh: a registered submodule with no checkout — what a clone without
# --recurse-submodules has, and what `git submodule deinit` leaves.
git submodule deinit -q -f -- apps/fresh
# gone: what the upstream `bench remove-app` leaves — the directory moved to
# archived/, the gitlink dropped, the .gitmodules entry and clone left behind.
mkdir -p archived/apps
mv apps/gone archived/apps/gone-2026-09-25
git update-index --force-remove -- apps/gone

echo "── a bench with one of each ─────────────────────────────────────"
before="$(snapshot)"
OUT="$("$TOOL" "$BENCH" 2>&1)" && RC=0 || RC=$?
after="$(snapshot)"
check_eq "exits 0" 0 "$RC"
check_eq "changes nothing — no checkout, no clone, no index or config edit" "$before" "$after"
check_eq "…so apps/fresh is still empty" 0 "$(find apps/fresh -mindepth 1 | wc -l)"
check "…and apps/gone is still gone" test ! -e apps/gone
check "names the submodule with no checkout" says "registered but not checked out: apps/fresh"
check "…with the command that checks out its pinned commit" says "git submodule update --init -- apps/fresh"
check "…and the one that pulls it" says "bench update --pull"
check "names the half-finished removal" says ".gitmodules still registers apps/gone"
check "…and how to finish it" says "bench remove-app gone"
check "names the stray repo" says "apps/strayapp is a git repository but not a registered submodule"
check "says nothing of a checked-out submodule" silent_on "apps/present"
check "…or of a local app" silent_on "localapp"

echo "── a bench with nothing to say ──────────────────────────────────"
git submodule update -q --init -- apps/fresh
git config -f .gitmodules --remove-section submodule.apps/gone
rm -rf apps/strayapp
git update-index --force-remove -- apps/strayapp
OUT="$("$TOOL" "$BENCH" 2>&1)" && RC=0 || RC=$?
check_eq "exits 0" 0 "$RC"
check_eq "and prints nothing" "" "$OUT"

echo "── not a bench ──────────────────────────────────────────────────"
mkdir -p "$ROOT/empty"
OUT="$("$TOOL" "$ROOT/empty" 2>&1)" && RC=0 || RC=$?
check_eq "no apps/: exits 0" 0 "$RC"
check_eq "and prints nothing" "" "$OUT"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
