#!/usr/bin/env bash
# Checks for `bench-rename-app` — the bench's half of renaming an app — and for
# the umbrella wrapper's routing of it.
#
# Usage: bench-rename-app.sh <rendered-bench-rename-app> <rendered-bench-dispatch>
#
# Network-free: submodule apps come from bare repositories on disk (as in
# bench-get-app.sh and bench-remove-app.sh); a stub `bench` logs every call; a
# stub `uv` stands in for the relock.
#
# The renamed repository is the old one's history plus one commit that renames
# the package, as `frappe-rename-app code` leaves it: crm.git is the app as it
# was, crm_pro.git is the same history renamed.
set -euo pipefail

SCRIPT="$1"
DISPATCH="$2"

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
is_member() { # <app>
  python3 -c "
import sys, tomllib
d = tomllib.load(open('pyproject.toml', 'rb'))
sys.exit(0 if 'apps/' + sys.argv[1] in d['tool']['uv']['workspace']['members'] else 1)
" "$1"
}
has_source() { # <name>
  python3 -c "
import sys, tomllib
d = tomllib.load(open('pyproject.toml', 'rb'))
sys.exit(0 if sys.argv[1] in d['tool']['uv'].get('sources', {}) else 1)
" "$1"
}
gm() { git config -f .gitmodules --get "submodule.$1.$2"; }
staged_gitmodules_has() { git show :.gitmodules | grep -qF -- "$1"; }
apps_listed() { tr -s ' \n' '\n\n' < sites/apps.txt | sort | xargs; }

# ── stubs ───────────────────────────────────────────────────────────────
BIN="$ROOT/bin"
mkdir -p "$BIN"
printf '#!%s\n' "$(command -v bash)" > "$BIN/uv"
cat >> "$BIN/uv" <<'STUB'
echo "$*" >> "$UV_CALLS"
[ -z "${UV_FAIL:-}" ] || { echo "  × No solution found" >&2; exit 1; }
STUB
printf '#!%s\n' "$(command -v bash)" > "$BIN/bench"
cat >> "$BIN/bench" <<'STUB'
echo "$*" >> "$BENCH_CALLS"
exit 0
STUB
chmod +x "$BIN/uv" "$BIN/bench"
export PATH="$BIN:$PATH"
export UV_CALLS="$ROOT/uv-calls" BENCH_CALLS="$ROOT/bench-calls"

# The real tool (its interpreter carries tomlkit), so the fixture is registered
# exactly the way bench-get-app registers an app.
WORKSPACE_TOOL="${WORKSPACE_TOOL:-$(grep -o '/nix/store/[^/]*/bin/frappe-nix-workspace' "$SCRIPT" | head -n1)}"
sync_registry() {
  "$WORKSPACE_TOOL" sync-registry --pyproject pyproject.toml --apps-dir apps --sites-dir sites > /dev/null
}

# ── fixtures ────────────────────────────────────────────────────────────
write_package() { # <dir> <name> — a Frappe app's package and project file
  mkdir -p "$1/$2"
  printf '__version__ = "1.0.0"\n' > "$1/$2/__init__.py"
  printf 'app_name = "%s"\n' "$2" > "$1/$2/hooks.py"
  printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$2" > "$1/pyproject.toml"
}
seed_remote() { # <name>
  local name=$1 seed="$ROOT/seed/$1"
  write_package "$seed" "$name"
  git -C "$seed" init -q -b develop
  git -C "$seed" add -A
  git -C "$seed" commit -q -m "init"
  bare_remote "$name"
  git -C "$seed" push -q "$ROOT/remotes/$name.git" develop
}
# A bare repository that serves filtered fetches, as GitHub does: without it a
# partial clone quietly falls back to a full one and nothing below tests it.
bare_remote() { # <name>
  git init -q --bare "$ROOT/remotes/$1.git"
  git -C "$ROOT/remotes/$1.git" config uploadpack.allowFilter true
  git -C "$ROOT/remotes/$1.git" config uploadpack.allowAnySHA1InWant true
}
# <old>'s history and one commit more, in a repository of its own called <new>:
# renamed, the way `frappe-rename-app code` leaves it, or (with "stale") not.
seed_successor() { # <old> <new> <renamed|stale>
  local old=$1 new=$2 how=$3 seed="$ROOT/seed/$2"
  git clone -q -b develop "$ROOT/remotes/$old.git" "$seed"
  if [ "$how" = renamed ]; then
    git -C "$seed" mv "$old" "$new"
    printf 'app_name = "%s"\n' "$new" > "$seed/$new/hooks.py"
    printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$new" > "$seed/pyproject.toml"
    git -C "$seed" add -A
    git -C "$seed" commit -q -m "rename the package to $new"
  else
    printf 'x\n' > "$seed/CHANGES"
    git -C "$seed" add -A
    git -C "$seed" commit -q -m "a change that does not rename it"
  fi
  bare_remote "$new"
  git -C "$seed" push -q "$ROOT/remotes/$new.git" develop
}
seed_remote crm
seed_remote telephony
seed_successor crm crm_pro renamed
seed_successor crm crm_stale stale
# Another app's history under the new name: nothing the old checkout is part of.
write_package "$ROOT/seed/crm_unrelated2" crm_pro
git -C "$ROOT/seed/crm_unrelated2" init -q -b develop
git -C "$ROOT/seed/crm_unrelated2" add -A
git -C "$ROOT/seed/crm_unrelated2" commit -q -m "something else"
bare_remote crm_unrelated2
git -C "$ROOT/seed/crm_unrelated2" push -q "$ROOT/remotes/crm_unrelated2.git" develop

BENCH="$ROOT/bench"
export FRAPPE_BENCH_ROOT="$BENCH"

fresh_bench() {
  cd "$ROOT"
  rm -rf "$BENCH"
  mkdir -p "$BENCH/apps/frappe/frappe" "$BENCH/sites/mysite.local"
  cd "$BENCH"
  echo '{}' > sites/mysite.local/site_config.json
  write_package apps/frappe frappe
  cat > pyproject.toml <<'TOML'
[project]
name = "fixture-bench"

[tool.uv.workspace]
members = ["apps/frappe"]

[tool.uv.sources]
frappe = { workspace = true }
TOML
  git init -q -b main
  sync_registry
  git add -A
  git commit -q -m "bench"
}

add_submodule_app() { # <name> [submodule name] — what bench-get-app produces
  local name=$1 sm_name=${2:-}
  local name_args=()
  [ -z "$sm_name" ] || name_args=(--name "$sm_name")
  git submodule add -q "${name_args[@]}" -b develop -- "file://$ROOT/remotes/$name.git" "apps/$name"
  "$WORKSPACE_TOOL" add-app --pyproject pyproject.toml --app "$name"
  sync_registry
  git add -A
  git commit -q -m "add $name"
}

add_local_app() { # <name> [package] — committed source, no .git of its own
  local name=$1 package=${2:-$1}
  write_package "apps/$name" "$package"
  "$WORKSPACE_TOOL" add-app --pyproject pyproject.toml --app "$name"
  git add -A
  git commit -q -m "vendor $name"
}

run() { # <args…>
  : > "$UV_CALLS"
  : > "$BENCH_CALLS"
  OUT="$(bash "$SCRIPT" "$@" 2>&1)" && RC=0 || RC=$?
}

# Everything the command could change: git's view of the bench and its
# submodules, the config files, the clones, the workspace and the registry.
snapshot() {
  git status --porcelain --ignore-submodules=none
  git ls-files -s
  git diff
  cat .gitmodules .git/config pyproject.toml sites/apps.txt 2> /dev/null
  find .git/modules -maxdepth 4 -name HEAD 2> /dev/null | sort
  ls apps
}

echo "── a submodule, renamed upstream, while .gitmodules has someone's edit ─"
# The state a bench is usually in mid-work — and the one in which `git mv`
# refuses outright ("please stage your changes to .gitmodules").
fresh_bench
add_submodule_app crm
add_submodule_app telephony
git config -f .gitmodules submodule.apps/telephony.shallow true
mkdir -p node-locks/crm sites/assets apps/crm/node_modules
: > node-locks/crm/yarn.lock
: > apps/crm/node_modules/.installed
touch .git/modules/apps/crm/marker
ln -s "$BENCH/apps/crm/crm/public" sites/assets/crm
old_pin="$(git ls-files -s apps/crm | awk '{ print $2 }')"
new_tip="$(git -C "$ROOT/remotes/crm_pro.git" rev-parse develop)"
check_not "(fixture: .gitmodules really is dirty)" git diff --quiet -- .gitmodules
run crm crm_pro
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check_not "apps/crm is gone" test -e apps/crm
check "the renamed package is checked out in apps/crm_pro" test -f apps/crm_pro/crm_pro/hooks.py
check_eq "…at the tip it fetched" "$new_tip" "$(git -C apps/crm_pro rev-parse HEAD)"
check_eq "…on the branch .gitmodules follows" develop "$(git -C apps/crm_pro symbolic-ref --short HEAD)"
check "its untracked files came along (nothing was downloaded again)" test -e apps/crm_pro/node_modules/.installed
check "…and so did its history" test "$(git -C apps/crm_pro rev-list --count HEAD)" -ge 2
check "its clone moved under the new name" test -e .git/modules/apps/crm_pro/marker
check_not "…leaving nothing under the old one" test -e .git/modules/apps/crm
check_eq "the checkout and its clone agree where the checkout is" "$BENCH/apps/crm_pro" "$(git -C apps/crm_pro rev-parse --show-toplevel)"
check_eq ".gitmodules names the new path" apps/crm_pro "$(gm apps/crm_pro path)"
check_eq "…and the renamed repository" "file://$ROOT/remotes/crm_pro.git" "$(gm apps/crm_pro url)"
check_eq "…and the branch, as it was" develop "$(gm apps/crm_pro branch)"
check_not "…and no section of the old name" gm apps/crm url
check_eq "the unrelated edit is still there" true "$(gm apps/telephony shallow)"
check_not "…and still unstaged" staged_gitmodules_has shallow
check "the rename is staged in .gitmodules" staged_gitmodules_has "[submodule \"apps/crm_pro\"]"
check_not "…with no section of the old name" staged_gitmodules_has "[submodule \"apps/crm\"]"
check_eq "the old gitlink's removal is staged" "D	apps/crm" "$(git diff --cached --name-status -- apps/crm)"
check_eq "…and the new one's addition, at the tip" "$new_tip" "$(git ls-files -s apps/crm_pro | awk '{ print $2 }')"
check_not "the old pin was not carried over" test "$new_tip" = "$old_pin"
check_eq ".git/config follows the rename" "file://$ROOT/remotes/crm_pro.git" "$(git config --get submodule.apps/crm_pro.url)"
check_not "…with no section of the old name" git config --get submodule.apps/crm.url
check_eq "the clone fetches from the renamed repository" "file://$ROOT/remotes/crm_pro.git" "$(git -C apps/crm_pro config --get remote.origin.url)"
check "git still finds the submodule" git submodule status -- apps/crm_pro
check "it is a workspace member under the new name" is_member crm_pro
check_not "…not the old" is_member crm
check "its uv source is the new package" has_source crm_pro
check_not "…not the old" has_source crm
check_eq "sites/apps.txt lists it by the new name" "crm_pro frappe telephony" "$(apps_listed)"
check_not "node-locks/crm is gone" test -e node-locks/crm
check_not "the dangling sites/assets/crm symlink is gone" test -L sites/assets/crm
check "uv lock ran" grep -qx lock "$UV_CALLS"
check "says how to name the rename for the sites" says 'frappe-nix.renamedApps = { crm = "crm_pro"; };'
check "…and the by-hand command" says "frappe-rename-app --site <site> crm=crm_pro"
check_not "it did not touch a site" grep -q -- --site "$BENCH_CALLS"

echo "── --no-pull: a checkout that is already where it should be ────────"
fresh_bench
add_submodule_app crm
old_pin="$(git ls-files -s apps/crm | awk '{ print $2 }')"
git -C apps/crm fetch -q "file://$ROOT/remotes/crm_pro.git" develop
git -C apps/crm checkout -q FETCH_HEAD
checked_out="$(git -C apps/crm rev-parse HEAD)"
run --no-pull crm crm_pro
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check_eq "the checkout was not moved" "$checked_out" "$(git -C apps/crm_pro rev-parse HEAD)"
check_eq "the pin the bench recorded was kept" "$old_pin" "$(git ls-files -s apps/crm_pro | awk '{ print $2 }')"
check "and it says the new commit is yours to record" says "git add apps/crm_pro"
check "its uv source is the checked-out package's name" has_source crm_pro
check_eq "sites/apps.txt lists it" "crm_pro frappe" "$(apps_listed)"

echo "── a submodule named apart from its path keeps its name ──────────────"
fresh_bench
add_submodule_app crm crm-mod
run crm crm_pro
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check_eq ".gitmodules keeps the section, with the new path" apps/crm_pro "$(gm crm-mod path)"
check "its clone stays where it was" test -d .git/modules/crm-mod
check_eq "the checkout and its clone agree" "$BENCH/apps/crm_pro" "$(git -C apps/crm_pro rev-parse --show-toplevel)"
check "the renamed package is checked out" test -f apps/crm_pro/crm_pro/hooks.py

echo "── a partial clone, as shell entry makes them ───────────────────────"
# Commits and folders, no file contents: what frappe-nix-sources gives every app.
# The renamed tip's files are fetched when it is checked out, from the new URL.
fresh_bench
git clone -q --filter=blob:none --single-branch -b develop "file://$ROOT/remotes/crm.git" apps/crm
git submodule add -q -b develop -- "file://$ROOT/remotes/crm.git" apps/crm > /dev/null 2>&1
git submodule absorbgitdirs -- apps/crm > /dev/null 2>&1
"$WORKSPACE_TOOL" add-app --pyproject pyproject.toml --app crm
sync_registry
git add -A
git commit -q -m "add crm"
check_eq "(fixture: a partial clone)" true "$(git -C apps/crm config --get remote.origin.promisor)"
run crm crm_pro
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check "the renamed package is checked out" test -f apps/crm_pro/crm_pro/hooks.py
check_eq "it is still a partial clone" true "$(git -C apps/crm_pro config --get remote.origin.promisor)"
check_eq "…of the renamed repository" "file://$ROOT/remotes/crm_pro.git" "$(git -C apps/crm_pro config --get remote.origin.url)"
check_eq "…and the tree is clean" "" "$(git -C apps/crm_pro status --porcelain)"
check "sites/apps.txt lists it" test "$(apps_listed)" = "crm_pro frappe"

echo "── a vendored (local) app ──────────────────────────────────────────"
fresh_bench
add_local_app widgets widgets_pro
run widgets widgets_pro
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check "its files are in apps/widgets_pro" test -f apps/widgets_pro/widgets_pro/hooks.py
check_not "apps/widgets is gone" test -e apps/widgets
check "the move is staged" test -n "$(git diff --cached --name-status -- apps/widgets_pro)"
check "it is a workspace member under the new name" is_member widgets_pro
check_not "…not the old" is_member widgets
check "its uv source is the new package" has_source widgets_pro
check_eq "sites/apps.txt lists it" "frappe widgets_pro" "$(apps_listed)"
fresh_bench
add_local_app gadgets
before="$(snapshot)"
run gadgets gadgets_pro
check_eq "one whose package is not renamed exits 1" 1 "$RC"
check "…and says to rename the package first" says "frappe-rename-app code --from gadgets --to gadgets_pro"
check_eq "…having changed nothing" "$before" "$(snapshot)"

echo "── refused, and nothing changed ────────────────────────────────────"
fresh_bench
add_submodule_app crm
before="$(snapshot)"
run --url "file://$ROOT/remotes/crm_stale.git" crm crm_pro
check_eq "a repository that does not carry the renamed package exits 1" 1 "$RC"
check "…names the file it lacks" says "has no crm_pro/hooks.py"
check "…and how to make it" says "frappe-rename-app code --from crm --to crm_pro"
check_eq "…having changed nothing" "$before" "$(snapshot)"
check "…not even run uv" test ! -s "$UV_CALLS"

run --url "file://$ROOT/remotes/nowhere.git" crm crm_pro
check_eq "a repository that cannot be fetched exits 1" 1 "$RC"
check "…says so" says "could not fetch 'develop' from file://$ROOT/remotes/nowhere.git"
check_eq "…having changed nothing" "$before" "$(snapshot)"

run --url "file://$ROOT/remotes/crm_unrelated2.git" crm crm_pro
check_eq "history the checkout is not part of exits 1" 1 "$RC"
check "…says so" says "is not an ancestor of develop"
check "…and what to do" says "run this with --no-pull"
check_eq "…having changed nothing" "$before" "$(snapshot)"

run --no-pull crm crm_pro
check_eq "--no-pull on a checkout without the package exits 1" 1 "$RC"
check "…says why" says "apps/crm has no crm_pro/hooks.py"
check_eq "…having changed nothing" "$before" "$(snapshot)"

echo '# local edit' >> apps/crm/crm/hooks.py
before="$(snapshot)"
run crm crm_pro
check_eq "uncommitted changes in the checkout exit 1" 1 "$RC"
check "…say so" says "has uncommitted changes"
check_eq "…having changed nothing" "$before" "$(snapshot)"
git -C apps/crm checkout -q -- crm/hooks.py

echo "── refusals ────────────────────────────────────────────────────────"
before="$(snapshot)"
run crm_pro crm_other
check_eq "an app that is not there exits 1" 1 "$RC"
check "…and says so" says "no app 'crm_pro' in this bench"
run crm telephony
check_eq "a name that is taken exits 1" 1 "$RC"
mkdir -p apps/taken
run crm taken
check_eq "a directory that is in the way exits 1" 1 "$RC"
check "…and says so" says "apps/taken already exists"
rmdir apps/taken
run frappe frappe_pro
check_eq "renaming frappe exits 1" 1 "$RC"
check "…and says why" says "cannot be renamed"
run crm frappe
check_eq "renaming to frappe exits 1" 1 "$RC"
run crm crm
check_eq "to the same name exits 1" 1 "$RC"
for bad in "1crm" "crm-pro" "crm.pro" "a/b" ".." "."; do
  run crm "$bad"
  check_eq "'$bad' is no app name: exit 1" 1 "$RC"
done
run ../crm crm_pro
check_eq "a path as the old name exits 1" 1 "$RC"
run crm
check_eq "one name exits 1" 1 "$RC"
run
check_eq "no names exits 1" 1 "$RC"
run crm crm_pro extra
check_eq "three names exits 1" 1 "$RC"
run --depth 1 crm crm_pro
check_eq "an unknown flag exits 1" 1 "$RC"
run --url
check_eq "--url without a value exits 1" 1 "$RC"
check_eq "none of that changed anything" "$before" "$(snapshot)"
run --help
check_eq "--help exits 0" 0 "$RC"
check "…and points at frappe-rename-app for the other halves" says "frappe-rename-app code --from <old> --to"

echo "── a nested repository, and a submodule that is not checked out ─────"
mkdir -p apps/stray/stray
printf 'app_name = "stray"\n' > apps/stray/stray/hooks.py
git -C apps/stray init -q -b main
git -C apps/stray add -A
git -C apps/stray commit -q -m stray
run stray stray_pro
check_eq "a nested repository exits 1" 1 "$RC"
check "…says what it is" says "is a git repository but not a registered submodule"
check "…and leaves it" test -f apps/stray/stray/hooks.py
rm -rf apps/stray
git submodule deinit -q -f -- apps/crm
run crm crm_pro
check_eq "a submodule that is not checked out exits 1" 1 "$RC"
check "…says to check it out first" says "git submodule update --init -- apps/crm"

echo "── a relock that fails ─────────────────────────────────────────────"
fresh_bench
add_submodule_app crm
UV_FAIL=1 run crm crm_pro
check_eq "exits 1" 1 "$RC"
check "says the lock failed, and how to re-run it" says "uv lock failed"
check "the app is still renamed" test -f apps/crm_pro/crm_pro/hooks.py
check "…and a workspace member under the new name" is_member crm_pro

echo "── the umbrella bench: rename-app here, the rest to the real one ───"
printf '#!%s\n' "$(command -v bash)" > "$BIN/bench-rename-app"
cat >> "$BIN/bench-rename-app" <<'STUB'
echo "$*" >> "$RENAME_CALLS"
STUB
chmod +x "$BIN/bench-rename-app"
export RENAME_CALLS="$ROOT/rename-calls"
dispatch() { # <args…>
  : > "$RENAME_CALLS"
  : > "$BENCH_CALLS"
  bash "$DISPATCH" "$@" > /dev/null 2>&1 || true
}
dispatch rename-app --no-pull crm crm_pro
check_eq "bench rename-app reaches bench-rename-app" "--no-pull crm crm_pro" "$(cat "$RENAME_CALLS")"
dispatch rename-site a b
check_eq "bench rename-site goes to the real bench" "rename-site a b" "$(cat "$BENCH_CALLS")"
check_not "…not to bench-rename-app" test -s "$RENAME_CALLS"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
