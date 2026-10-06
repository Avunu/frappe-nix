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

# Whether an object is on disk, without the lazy fetch a partial clone would
# otherwise make to answer the question.
has_obj() { # <repo> <object>
  GIT_NO_LAZY_FETCH=1 git -C "$1" cat-file -e "$2" 2> /dev/null
}

# The shape the first entry gives a clone: partial, not shallow, every branch
# of origin fetched, and commits only from here on.
partial_all_branches() { # <path>
  [ "$(git -C "$1" config remote.origin.promisor)" = true ] \
    && [ "$(git -C "$1" rev-parse --is-shallow-repository)" = false ] \
    && [ "$(git -C "$1" config --get-all remote.origin.fetch)" = '+refs/heads/*:refs/remotes/origin/*' ] \
    && [ "$(git -C "$1" config remote.origin.partialclonefilter)" = tree:0 ] \
    && git -C "$1" rev-parse -q --verify refs/remotes/origin/version-x > /dev/null
}

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

commit_to() { # <name> <branch> <message> — a commit on the seed, pushed
  local seed="$ROOT/seed/$1"
  git -C "$seed" checkout -q "$2"
  mkdir -p "$seed/$1/$2"
  printf '%s\n' "$3" > "$seed/$1/$2/log.txt"
  git -C "$seed" add -A
  git -C "$seed" commit -q -m "$3"
  git -C "$seed" push -q "$ROOT/remotes/$1.git" "$2"
}
# develop, which the bench pins, and version-x, which it never checks out.
# Filters allowed, as GitHub does: without them a partial clone quietly falls
# back to a full one and nothing below would be tested.
seed_remote() { # <name>
  local seed="$ROOT/seed/$1"
  mkdir -p "$seed/$1"
  printf 'app_name = "%s"\n' "$1" > "$seed/$1/hooks.py"
  git -C "$seed" init -q -b develop
  git -C "$seed" add -A
  git -C "$seed" commit -q -m init
  git init -q --bare "$ROOT/remotes/$1.git"
  git -C "$ROOT/remotes/$1.git" symbolic-ref HEAD refs/heads/develop
  git -C "$ROOT/remotes/$1.git" config uploadpack.allowFilter true
  git -C "$ROOT/remotes/$1.git" config uploadpack.allowAnySHA1InWant true
  git -C "$seed" push -q "$ROOT/remotes/$1.git" develop
  git -C "$seed" branch -q version-x
  commit_to "$1" develop "develop 1"
  commit_to "$1" version-x "version-x 1"
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
# Upstream moves on past what the bench pins, so a checkout at the branch tip
# is not a checkout at the pin.
for a in "${APPS[@]}"; do commit_to "$a" develop "develop 2"; done

# Fresh clones of the bench as committed, for the sections further down.
git clone -q "$BENCH" "$ROOT/clone"
git clone -q "$BENCH" "$ROOT/clone2"
git clone -q "$BENCH" "$ROOT/clone3"

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
# inplace: .gitmodules names a branch its remote no longer has.
git config -f .gitmodules submodule.apps/inplace.branch retired
run
check_eq "exits 0" 0 "$RC"
for a in present fresh taken inplace; do
  check "checks out apps/$a at its pinned commit" at_pin "$a"
  check "…as a partial clone of every branch" partial_all_branches "apps/$a"
done
check "says so" says "checking out apps/present (first shell entry in this clone)"
check "the git dir is under .git/modules, as git's own submodule clone puts it" \
  test "$(cat apps/present/.git)" = "gitdir: ../../.git/modules/apps/present"
# origin/develop is a commit past the pin, so nothing of it was checked out.
check "the paired branch has its folders past the checkout…" has_obj apps/present 'origin/develop^{tree}'
check_not "…but not their file contents" has_obj apps/present 'origin/develop:present/develop/log.txt'
check_not "another branch has its commits but not its folders" has_obj apps/present 'origin/version-x^{tree}'
check "and switching to it downloads what it needs" git -C apps/present switch -q version-x
check "…files and all" test -f apps/present/present/version-x/log.txt
git -C apps/present switch -q --detach "$(git ls-files -s -- apps/present | awk '{ print $2 }')"
check "a branch the remote no longer has falls back to its default" says "could not clone apps/inplace at 'retired'"
git checkout -q -- .gitmodules
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

echo "── shallow clones from before ───────────────────────────────────"
cd "$ROOT/clone3"
# What `shallow = true` made of every app: the remote's default branch at depth
# 1, and only that branch followed.
git submodule update -q --init --depth 1 -- apps/present apps/fresh apps/taken apps/inplace
printf 'mine\n' > apps/present/local.txt
git -C apps/present add local.txt
git -C apps/present commit -q -m "a local commit"
declare -A head_before
for a in present fresh taken inplace; do head_before[$a]="$(git -C "apps/$a" rev-parse HEAD)"; done
check "(they start out shallow)" test "$(git -C apps/fresh rev-parse --is-shallow-repository)" = true
run
check_eq "exits 0" 0 "$RC"
for a in present fresh taken inplace; do
  check "apps/$a becomes a partial clone of every branch" partial_all_branches "apps/$a"
  check_eq "…its checkout where it was" "${head_before[$a]}" "$(git -C "apps/$a" rev-parse HEAD)"
done
check_eq "a local commit is kept, and the worktree left clean" "" "$(git -C apps/present status --porcelain)"
check "the paired branch gains its folders" has_obj apps/fresh 'origin/develop~2^{tree}'
check "says what it is fetching" says "apps/fresh is a shallow or single-branch clone"
before="$(snapshot)"
run
after="$(snapshot)"
check_eq "the next entry changes nothing" "$before" "$after"
check_eq "…and says nothing" "" "$OUT"

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

echo "── an app with submodules of its own ────────────────────────────"
# As hrms has frappe-ui. A fetch's on-demand submodule check diffs every commit
# it brings in for a moved gitlink, and in a treeless clone each of those trees
# is a lazy fetch from the remote: one round trip per commit on another branch,
# thousands of them for hrms's conversion, and more on every fetch after it.
seed_remote ui
seed_remote nested
git -C "$ROOT/seed/nested" checkout -q develop
git -C "$ROOT/seed/nested" submodule add -q "file://$ROOT/remotes/ui.git" frontend/ui
git -C "$ROOT/seed/nested" commit -q -m "add ui"
git -C "$ROOT/seed/nested" push -q "$ROOT/remotes/nested.git" develop
commit_to nested version-x "version-x 2"
mkdir -p "$ROOT/nestbench/apps"
cd "$ROOT/nestbench"
git init -q -b main
git submodule add -q -b develop "file://$ROOT/remotes/nested.git" apps/nested
git commit -q -m bench
git clone -q "$ROOT/nestbench" "$ROOT/nestclone"
cd "$ROOT/nestclone"
run
check_eq "exits 0" 0 "$RC"
check "checks it out as a partial clone of every branch" partial_all_branches apps/nested
check_not "…without fetching another branch's folders to look for submodule changes" \
  has_obj apps/nested 'origin/version-x^{tree}'
check_eq "…nor on a later fetch, which the clone is set up not to recurse on" \
  false "$(git -C apps/nested config --get fetch.recurseSubmodules)"
commit_to nested version-x "version-x 3"
git -C apps/nested fetch -q
check_not "…so a plain git fetch in it does not fetch them either" \
  has_obj apps/nested 'origin/version-x^{tree}'
# A clone converted before the setting existed.
git -C apps/nested config --unset fetch.recurseSubmodules || true
run
check_eq "an existing treeless clone gets the setting on the next entry" \
  false "$(git -C apps/nested config --get fetch.recurseSubmodules)"
check_eq "…silently" "" "$OUT"
git -C apps/nested config fetch.recurseSubmodules on-demand
run
check_eq "but one set by hand is left as it is" \
  on-demand "$(git -C apps/nested config --get fetch.recurseSubmodules)"

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
