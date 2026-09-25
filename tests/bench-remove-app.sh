#!/usr/bin/env bash
# Checks for `bench-remove-app` — the inverse of bench-get-app — and for the
# umbrella wrapper's routing of it.
#
# Usage: bench-remove-app.sh <rendered-bench-remove-app> <rendered-bench-dispatch>
#
# Network-free: submodule apps come from bare repositories on disk (as in
# bench-get-app.sh); a stub `bench` logs every call and answers `list-apps`
# from an env-controlled per-site map (as in reconcile-apps.sh); a stub `uv`
# stands in for the relock.
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
gm() { git config -f .gitmodules --get "submodule.apps/$1.$2"; }
staged_gitmodules_has() { git show :.gitmodules | grep -qF -- "$1"; }

# ── stubs ───────────────────────────────────────────────────────────────
BIN="$ROOT/bin"
mkdir -p "$BIN"
printf '#!%s\n' "$(command -v bash)" > "$BIN/uv"
cat >> "$BIN/uv" <<'STUB'
echo "$*" >> "$UV_CALLS"
STUB

printf '#!%s\n' "$(command -v bash)" > "$BIN/bench"
cat >> "$BIN/bench" <<'STUB'
echo "$*" >> "$BENCH_CALLS"
if [ "${1:-}" = "--site" ] && [ "${3:-}" = "list-apps" ]; then
  [ -z "${LIST_APPS_FAIL:-}" ] || exit 1
  python3 - "$INSTALLED_APPS_JSON" "$2" <<'PY'
import json, sys
path, site = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {}
print(json.dumps({site: data.get(site, [])}))
PY
fi
exit 0
STUB
chmod +x "$BIN/uv" "$BIN/bench"
export PATH="$BIN:$PATH"
export UV_CALLS="$ROOT/uv-calls" BENCH_CALLS="$ROOT/bench-calls"
export INSTALLED_APPS_JSON="$ROOT/installed.json"
installed() { printf '%s' "$1" > "$INSTALLED_APPS_JSON"; }

# The real tool (its interpreter carries tomlkit), so the fixture is registered
# exactly the way bench-get-app registers an app.
WORKSPACE_TOOL="$(grep -o '/nix/store/[^/]*/bin/frappe-nix-workspace' "$SCRIPT" | head -n1)"
sync_registry() {
  "$WORKSPACE_TOOL" sync-registry --pyproject pyproject.toml --apps-dir apps --sites-dir sites > /dev/null
}

# ── fixtures ────────────────────────────────────────────────────────────
seed_remote() { # <name>
  local name=$1 seed="$ROOT/seed/$1"
  mkdir -p "$seed/$name"
  printf '__version__ = "1.0.0"\n' > "$seed/$name/__init__.py"
  printf 'app_name = "%s"\n' "$name" > "$seed/$name/hooks.py"
  printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$name" > "$seed/pyproject.toml"
  git -C "$seed" init -q -b develop
  git -C "$seed" add -A
  git -C "$seed" commit -q -m "init"
  git init -q --bare "$ROOT/remotes/$name.git"
  git -C "$seed" push -q "$ROOT/remotes/$name.git" develop
}
seed_remote crm
seed_remote telephony
seed_remote insights

BENCH="$ROOT/bench"
export FRAPPE_BENCH_ROOT="$BENCH"

fresh_bench() {
  cd "$ROOT"
  rm -rf "$BENCH"
  mkdir -p "$BENCH/apps/frappe/frappe" "$BENCH/sites/mysite.local" "$BENCH/sites/othersite.local"
  cd "$BENCH"
  echo '{}' > sites/mysite.local/site_config.json
  echo '{}' > sites/othersite.local/site_config.json
  printf '__version__ = "1.0.0"\n' > apps/frappe/frappe/__init__.py
  printf 'app_name = "frappe"\n' > apps/frappe/frappe/hooks.py
  printf '[project]\nname = "frappe"\ndynamic = ["version"]\n' > apps/frappe/pyproject.toml
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
  installed '{}'
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

add_local_app() { # <name> — committed source, no .git of its own
  local name=$1
  mkdir -p "apps/$name/$name"
  printf '__version__ = "1.0.0"\n' > "apps/$name/$name/__init__.py"
  printf 'app_name = "%s"\n' "$name" > "apps/$name/$name/hooks.py"
  printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$name" > "apps/$name/pyproject.toml"
  "$WORKSPACE_TOOL" add-app --pyproject pyproject.toml --app "$name"
  sync_registry
  git add -A
  git commit -q -m "vendor $name"
}

run() { # <args…>
  : > "$UV_CALLS"
  : > "$BENCH_CALLS"
  OUT="$(bash "$SCRIPT" "$@" 2>&1)" && RC=0 || RC=$?
}

echo "── a submodule, while .gitmodules has someone's unstaged edit ────"
# The state a bench is usually in mid-work — and the one in which `git rm`
# refuses outright ("please stage your changes to .gitmodules").
fresh_bench
add_submodule_app crm
add_submodule_app telephony
git config -f .gitmodules submodule.apps/telephony.shallow true
mkdir -p node-locks/crm sites/assets
: > node-locks/crm/yarn.lock
ln -s "$BENCH/apps/crm/crm/public" sites/assets/crm
check_not "(fixture: .gitmodules really is dirty)" git diff --quiet -- .gitmodules
run crm
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check_not "crm is gone from .gitmodules" gm crm url
check_not "…and from the staged .gitmodules" staged_gitmodules_has apps/crm
check_eq "the unrelated edit is still there" true "$(gm telephony shallow)"
check_not "…and still unstaged" staged_gitmodules_has shallow
check "…with telephony's own entry still staged" staged_gitmodules_has apps/telephony
check_eq "the gitlink's removal is staged" "D	apps/crm" "$(git diff --cached --name-status -- apps/crm)"
check_not "its .git/config entry is gone" git config --get submodule.apps/crm.url
check_not "its clone under .git/modules is gone" test -e .git/modules/apps/crm
check_not "apps/crm is gone from disk" test -e apps/crm
check_not "it is no longer a workspace member" is_member crm
check_not "…nor a uv source" has_source crm
check_eq "sites/apps.txt no longer lists it" "frappe telephony" "$(xargs < sites/apps.txt)"
check_not "node-locks/crm is gone" test -e node-locks/crm
check_not "the sites/assets/crm symlink is gone" test -L sites/assets/crm
check "uv lock ran" grep -qx lock "$UV_CALLS"
check_not "no site's database was touched" grep -q uninstall-app "$BENCH_CALLS"

echo "── refused while a site still has it installed ─────────────────────"
fresh_bench
add_submodule_app crm
installed '{"othersite.local": ["frappe", "crm"]}'
run crm
check_eq "exits 1" 1 "$RC"
check "names the site and the command to run" says "bench --site othersite.local uninstall-app crm"
check "apps/crm is untouched" test -f apps/crm/crm/hooks.py
check "…and so is .gitmodules" gm crm url
check "…and the workspace" is_member crm
check "nothing was staged" git diff --cached --quiet
check_not "uv lock did not run" test -s "$UV_CALLS"

echo "── refused when a site's apps cannot be read ───────────────────────"
LIST_APPS_FAIL=1 run crm
check_eq "exits 1" 1 "$RC"
check "says why" says "could not read the installed apps of"
check "apps/crm is untouched" test -f apps/crm/crm/hooks.py

echo "── --force skips the site check ────────────────────────────────────"
run --force crm
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check_not "apps/crm is gone" test -e apps/crm
check_not "without even asking the sites" grep -q list-apps "$BENCH_CALLS"

echo "── refused while the submodule has uncommitted changes ─────────────"
fresh_bench
add_submodule_app crm
echo '# local edit' >> apps/crm/crm/hooks.py
run crm
check_eq "exits 1" 1 "$RC"
check "names the changed file" says "crm/hooks.py"
check "the change is still there" grep -q 'local edit' apps/crm/crm/hooks.py
check "…and so is .gitmodules" gm crm url
run --force crm
check_eq "--force discards it and exits 0" 0 "$RC" || echo "$OUT"
check_not "apps/crm is gone" test -e apps/crm

echo "── a submodule already deinitialized, named apart from its path ────"
# What `git submodule deinit` alone leaves: an empty directory, the gitlink,
# the .gitmodules entry and the clone — here under a name that is not its path.
fresh_bench
add_submodule_app insights insights-mod
git submodule deinit -q -f -- apps/insights
check "(fixture: the clone is still there)" test -d .git/modules/insights-mod
run insights
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check_not "its .gitmodules entry is gone" git config -f .gitmodules --get submodule.insights-mod.url
check_not "its clone is gone" test -e .git/modules/insights-mod
check_not "the empty apps/insights is gone" test -e apps/insights
check_eq "the gitlink's removal is staged" "D	apps/insights" "$(git diff --cached --name-status -- apps/insights)"
check_not "it is no longer a workspace member" is_member insights

echo "── a vendored (local) app ─────────────────────────────────────────"
fresh_bench
add_local_app widgets
run widgets
check_eq "exits 0" 0 "$RC" || echo "$OUT"
backup="$(find .frappe-nix-backup -maxdepth 1 -name 'widgets-*' 2>/dev/null | head -n1)"
check "moved to .frappe-nix-backup/widgets-<timestamp>" test -n "$backup"
check "…with its files" test -f "$backup/widgets/hooks.py"
check_not "apps/widgets is gone" test -e apps/widgets
check "its files' removal is staged" test -n "$(git diff --cached --name-only -- apps/widgets)"
check_not "it is no longer a workspace member" is_member widgets

add_local_app gadgets
run --no-backup gadgets
check_eq "--no-backup exits 0" 0 "$RC" || echo "$OUT"
check_not "…and backs nothing up" compgen -G ".frappe-nix-backup/gadgets-*"
check_not "apps/gadgets is gone" test -e apps/gadgets

echo "── refusals ───────────────────────────────────────────────────────"
run widgets
check_eq "an app that is not there exits 1" 1 "$RC"
check "…and says so" says "no app 'widgets' in this bench"
run frappe
check_eq "frappe itself exits 1" 1 "$RC"
check "…and stays a member" is_member frappe
run ../etc
check_eq "a path exits 1" 1 "$RC"
run
check_eq "no app exits 1" 1 "$RC"
run crm telephony
check_eq "two apps exits 1" 1 "$RC"
run --depth 1 crm
check_eq "an unknown flag exits 1" 1 "$RC"
run --help
check_eq "--help exits 0" 0 "$RC"
check "…and points at uninstall-app for the per-site half" says "uninstall-app"

echo "── the umbrella bench: remove-app here, uninstall-app to the real one ──"
printf '#!%s\n' "$(command -v bash)" > "$BIN/bench-remove-app"
cat >> "$BIN/bench-remove-app" <<'STUB'
echo "$*" >> "$REMOVE_CALLS"
STUB
chmod +x "$BIN/bench-remove-app"
export REMOVE_CALLS="$ROOT/remove-calls"
dispatch() { # <args…>
  : > "$REMOVE_CALLS"
  : > "$BENCH_CALLS"
  bash "$DISPATCH" "$@" > /dev/null 2>&1 || true
}

dispatch remove-app --force crm
check_eq "bench remove-app reaches bench-remove-app" "--force crm" "$(cat "$REMOVE_CALLS")"
dispatch uninstall-app crm --yes
check_eq "bench uninstall-app goes to the real bench" "uninstall-app crm --yes" "$(cat "$BENCH_CALLS")"
check_not "…not to bench-remove-app" test -s "$REMOVE_CALLS"
dispatch --site mysite.local uninstall-app crm
check_eq "so does bench --site <s> uninstall-app" "--site mysite.local uninstall-app crm" "$(cat "$BENCH_CALLS")"
check_not "…also not to bench-remove-app" test -s "$REMOVE_CALLS"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
