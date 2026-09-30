#!/usr/bin/env bash
# Checks for `frappe-nix-apps-report` — what the dev shell does about apps/ on
# entry. On a fresh clone of the bench it checks out every app submodule, once.
# After that it touches none: entry used to check out any registered submodule
# it found without one, which is how an app removed by hand came back,
# re-cloned, on the next direnv reload.
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
check_not() { # <description> <command...> — passes when the command fails
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}
says() { grep -qF -- "$1" <<< "$OUT"; }
silent_on() { ! grep -qF -- "$1" <<< "$OUT"; }
run() { OUT="$("$TOOL" "$PWD" 2>&1)" && RC=0 || RC=$?; }

# Everything git knows about the bench and its submodules, and the file tree.
snapshot() {
  git status --porcelain --ignore-submodules=none
  git ls-files -s
  cat .gitmodules .git/config
  find .git/modules -maxdepth 3 -name HEAD -exec sh -c 'echo "$1: $(cat "$1")"' _ {} \; 2>/dev/null | sort
  find apps -maxdepth 2 | sort
}

# Checked out, at the commit the bench records for it.
at_pin() { # <app>
  [ -f "apps/$1/$1/hooks.py" ] \
    && [ "$(git -C "apps/$1" rev-parse HEAD)" = "$(git ls-files -s -- "apps/$1" | awk '{ print $2 }')" ]
}
empty() { [ -z "$(ls -A "$1" 2>/dev/null)" ]; }

APPS=(present fresh gone taken inplace)

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
for a in "${APPS[@]}"; do seed_remote "$a"; done

BENCH="$ROOT/bench"
mkdir -p "$BENCH/apps"
cd "$BENCH"
git init -q -b main
for a in present fresh gone taken; do
  git submodule add -q -b develop "file://$ROOT/remotes/$a.git" "apps/$a"
done
# inplace: registered over a checkout that was already there, as frappe-init
# --migrate does — its .git stays in apps/inplace, and the bench's git dir
# holds no clone of it.
git clone -q -b develop "file://$ROOT/remotes/inplace.git" apps/inplace
git submodule add -q -b develop "file://$ROOT/remotes/inplace.git" apps/inplace
mkdir -p apps/localapp/localapp
printf 'app_name = "localapp"\n' > apps/localapp/localapp/hooks.py
mkdir -p apps/strayapp && git -C apps/strayapp init -q -b main
printf 'x\n' > apps/strayapp/f && git -C apps/strayapp add -A && git -C apps/strayapp commit -q -m i
git -c advice.addEmbeddedRepo=false add -A 2>/dev/null
git commit -q -m bench

# Fresh clones of the bench as committed, for the sections further down.
git clone -q "$BENCH" "$ROOT/clone"
git clone -q "$BENCH" "$ROOT/clone2"

# fresh: what `git submodule deinit` leaves — its clone kept in the git dir.
git submodule deinit -q -f -- apps/fresh
# gone: what the upstream `bench remove-app` leaves — the directory moved to
# archived/, the gitlink dropped, the .gitmodules entry and clone left behind.
mkdir -p archived/apps
mv apps/gone archived/apps/gone-2026-09-25
git update-index --force-remove -- apps/gone
# taken, inplace: removed by hand.
rm -rf apps/taken apps/inplace

echo "── a bench with one of each ─────────────────────────────────────"
before="$(snapshot)"
run
after="$(snapshot)"
check_eq "exits 0" 0 "$RC"
check_eq "changes nothing — no checkout, no clone, no index or config edit" "$before" "$after"
check "…so apps/fresh is still empty" empty apps/fresh
check "…apps/gone is still gone" test ! -e apps/gone
check "…and so are the apps removed by hand" test ! -e apps/taken -a ! -e apps/inplace
check "…even the one whose .git lived in apps/ (frappe-init --migrate)" test ! -e .git/modules/apps/inplace
check "says it is checking nothing out" silent_on "checking out"
check "names the submodules taken out since" says "registered but not checked out: apps/fresh apps/inplace apps/taken"
check "…with the command that checks out their pinned commits" says "git submodule update --init -- apps/fresh apps/inplace apps/taken"
check "…and the one that pulls them" says "bench update --pull"
check "names the half-finished removal" says ".gitmodules still registers apps/gone"
check "…and how to finish it" says "bench remove-app gone"
check "names the stray repo" says "apps/strayapp is a git repository but not a registered submodule"
check "says nothing of a checked-out submodule" silent_on "apps/present"
check "…or of a local app" silent_on "localapp"

echo "── a bench with nothing to say ──────────────────────────────────"
git submodule update -q --init -- apps/fresh apps/taken apps/inplace
git config -f .gitmodules --remove-section submodule.apps/gone
rm -rf apps/strayapp
git update-index --force-remove -- apps/strayapp
run
check_eq "exits 0" 0 "$RC"
check_eq "and prints nothing" "" "$OUT"

echo "── a fresh clone ────────────────────────────────────────────────"
cd "$ROOT/clone"
# What the first dev-shell entry once left in an app it never checked out:
# node_modules from a `yarn install` in the empty directory.
mkdir -p apps/gone/node_modules
touch apps/gone/node_modules/.yarn-integrity
run
check_eq "exits 0" 0 "$RC"
for a in present fresh taken inplace; do
  check "checks out apps/$a at its pinned commit" at_pin "$a"
done
check "says so" says "checking out apps/present (first shell entry in this clone)"
check "names an app whose directory is in the way" says "apps/gone is not checked out, but its directory is not empty"
check "…and what is in it" says "node_modules"
check "…leaves that directory as it was" test -f apps/gone/node_modules/.yarn-integrity -a ! -e apps/gone/.git
check_not "…and does not set it up, so the next entry still counts it as new" git config --get submodule.apps/gone.url
check "reports nothing as taken out" silent_on "registered but not checked out"

before="$(snapshot)"
run
after="$(snapshot)"
check_eq "a second entry changes nothing" "$before" "$after"
check "…and checks nothing out" silent_on "checking out"

rm -rf apps/taken
git submodule deinit -q -f -- apps/fresh
run
check "an app removed by hand after the first entry stays removed" test ! -e apps/taken
check "…as does a deinitialized one" empty apps/fresh
check "…and both are reported" says "registered but not checked out: apps/fresh apps/taken"

rm -rf apps/gone/node_modules
run
check "once its directory is cleared, the app in the way is checked out" at_pin gone

echo "── a first checkout that fails ──────────────────────────────────"
cd "$ROOT/clone2"
mv "$ROOT/remotes/fresh.git" "$ROOT/remotes/fresh.git.away"
run
check_eq "exits 0" 0 "$RC"
check "names the app it could not check out" says "could not check out: apps/fresh"
check "…still checks out the rest" at_pin present
check "…leaves the failed one's directory empty" empty apps/fresh
check_not "…with no URL left in the config" git config --get submodule.apps/fresh.url
check_not "…nor an activation" git config --get submodule.apps/fresh.active
mv "$ROOT/remotes/fresh.git.away" "$ROOT/remotes/fresh.git"
run
check "and the next entry tries again" at_pin fresh

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
