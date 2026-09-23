#!/usr/bin/env bash
# Checks for `bench-uninstall-app` — the inverse of bench-get-app.
#
# Usage: bench-uninstall-app.sh <path-to-rendered-bench-uninstall-app-script>
#
# Network-free: submodule apps come from bare repositories on disk (same
# technique as bench-get-app.sh); a stub `bench` answers `list-apps` from an
# env-controlled per-site map (same technique as reconcile-apps.sh) and logs
# `uninstall-app` calls; a stub `uv` stands in for the relock.
set -euo pipefail

SCRIPT="$1"

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
toml_get() { # <file> <python expression over d>
  python3 -c "import sys, tomllib; d = tomllib.load(open(sys.argv[1], 'rb')); print($2)" "$1"
}
toml_source_has() { # <file> <source-name> — exit 0 iff [tool.uv.sources] has it
  python3 -c "
import sys, tomllib
d = tomllib.load(open(sys.argv[1], 'rb'))
sys.exit(0 if sys.argv[2] in d['tool']['uv']['sources'] else 1)
" "$1" "$2"
}
gm() { git config -f .gitmodules --get "submodule.apps/$1.$2"; }

# ── a stub uv ────────────────────────────────────────────────────────────
BIN="$ROOT/bin"
mkdir -p "$BIN"
printf '#!%s\n' "$(command -v bash)" > "$BIN/uv"
cat >> "$BIN/uv" <<'STUB'
echo "$PWD $*" >> "$UV_CALLS"
exit 0
STUB
chmod +x "$BIN/uv"
export UV_CALLS="$ROOT/uv-calls"

# ── a stub bench: answers list-apps from a per-site JSON map, logs
#    uninstall-app calls ───────────────────────────────────────────────────
cat > "$BIN/bench" <<STUB
#!$(command -v bash)
STUB
cat >> "$BIN/bench" <<'STUB'
if [ "$1" = "--site" ]; then
  site="$2"
  case "$3" in
    list-apps)
      python3 - "$INSTALLED_APPS_JSON" "$site" <<'PY'
import json, sys
path, site = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {}
print(json.dumps({site: data.get(site, [])}))
PY
      exit 0
      ;;
    uninstall-app)
      printf '%s %s\n' "$site" "$4" >> "$UNINSTALL_LOG"
      exit 0
      ;;
  esac
fi
exit 0
STUB
chmod +x "$BIN/bench"
export PATH="$BIN:$PATH"
export UNINSTALL_LOG="$ROOT/uninstall-calls"
export INSTALLED_APPS_JSON="$ROOT/installed.json"

# frappe-nix-workspace's own interpreter bundles tomlkit (lib/workspace-tool.nix);
# using the real tool to set up fixture state — rather than a second, hand-rolled
# TOML writer — means the fixture only has to agree with what add-app actually
# produces, not with a second implementation of it.
WORKSPACE_TOOL="$(grep -o '/nix/store/[^/]*/bin/frappe-nix-workspace' "$SCRIPT" | head -n1)"

# ── fixture: one "upstream" app to add as a submodule ──────────────────────
seed_remote() { # <name> <branch>
  local name=$1 branch=$2 seed="$ROOT/seed/$1"
  mkdir -p "$seed/$name"
  printf '__version__ = "1.0.0"\n' > "$seed/$name/__init__.py"
  printf 'app_name = "%s"\n' "$name" > "$seed/$name/hooks.py"
  printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$name" > "$seed/pyproject.toml"
  git -C "$seed" init -q -b "$branch"
  git -C "$seed" add -A
  git -C "$seed" commit -q -m "init"
  git init -q --bare "$ROOT/remotes/$name.git"
  git -C "$seed" push -q "$ROOT/remotes/$name.git" "$branch"
}
seed_remote crm develop

BENCH="$ROOT/bench"

fresh_bench() {
  rm -rf "$BENCH"
  mkdir -p "$BENCH/apps" "$BENCH/sites/mysite.local" "$BENCH/sites/othersite.local"
  echo '{}' > "$BENCH/sites/mysite.local/site_config.json"
  echo '{}' > "$BENCH/sites/othersite.local/site_config.json"
  cd "$BENCH"
  git init -q -b main
  cat > pyproject.toml <<'TOML'
[project]
name = "fixture-bench"

[tool.uv.workspace]
members = ["apps/frappe"]

[tool.uv.sources]
frappe = { workspace = true }
TOML
  mkdir -p apps/frappe/frappe
  printf '__version__ = "1.0.0"\n' > apps/frappe/frappe/__init__.py
  printf 'app_name = "frappe"\n' > apps/frappe/frappe/hooks.py
  printf '[project]\nname = "frappe"\ndynamic = ["version"]\n' > apps/frappe/pyproject.toml
  "$WORKSPACE_TOOL" sync-registry --pyproject pyproject.toml --apps-dir apps --sites-dir sites
  git add -A
  git commit -q -m "bench"
  export FRAPPE_BENCH_ROOT="$BENCH"
}

add_submodule_app() { # <name> <branch> — mirrors what bench-get-app produces
  local name=$1 branch=$2
  git submodule add -q -b "$branch" -- "file://$ROOT/remotes/$name.git" "apps/$name"
  git config -f .gitmodules "submodule.apps/$name.shallow" true
  "$WORKSPACE_TOOL" add-app --pyproject pyproject.toml --app "$name"
  "$WORKSPACE_TOOL" sync-registry --pyproject pyproject.toml --apps-dir apps --sites-dir sites
  git add -A
  git commit -q -m "add $name"
}

add_local_app() { # <name> — a vendored app: committed files, no nested .git
  local name=$1
  mkdir -p "apps/$name/$name"
  printf '__version__ = "1.0.0"\n' > "apps/$name/$name/__init__.py"
  printf 'app_name = "%s"\n' "$name" > "apps/$name/$name/hooks.py"
  printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$name" > "apps/$name/pyproject.toml"
  "$WORKSPACE_TOOL" add-app --pyproject pyproject.toml --app "$name"
  "$WORKSPACE_TOOL" sync-registry --pyproject pyproject.toml --apps-dir apps --sites-dir sites
  git add -A
  git commit -q -m "vendor $name"
}

run() { # <app> [flags...]
  : > "$UNINSTALL_LOG"
  : > "$UV_CALLS"
  OUT="$(FRAPPE_SITE=mysite.local bash "$SCRIPT" "$@" 2>&1)" && RC=0 || RC=$?
}

echo "── a submodule app, installed nowhere ────────────────────────────"
fresh_bench
add_submodule_app crm develop
printf '{"mysite.local": [], "othersite.local": []}' > "$INSTALLED_APPS_JSON"
run crm
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check_not "the submodule is gone from .gitmodules" gm crm branch
check_not "the local .git/config entry is gone" git config --get submodule.apps/crm.url
check_not "apps/crm is gone from disk" test -d apps/crm
check_eq "it is no longer a uv workspace member" False \
  "$(toml_get pyproject.toml "'apps/crm' in d['tool']['uv']['workspace']['members']")"
check_not "it is no longer a uv source" toml_source_has pyproject.toml crm
check_eq "sites/apps.txt no longer lists it" frappe "$(xargs < sites/apps.txt)"
check "uv lock ran" grep -q "lock" "$UV_CALLS"
check "uninstall-app was called against the right site/app" grep -qx "mysite.local crm" "$UNINSTALL_LOG"

echo "── a submodule app still installed on another site ────────────────"
fresh_bench
add_submodule_app crm develop
printf '{"mysite.local": [], "othersite.local": ["frappe", "crm"]}' > "$INSTALLED_APPS_JSON"
run crm
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check "the submodule is left in .gitmodules" gm crm branch
check "apps/crm is left on disk" test -d apps/crm
check_eq "it is still a uv workspace member" True \
  "$(toml_get pyproject.toml "'apps/crm' in d['tool']['uv']['workspace']['members']")"
check_eq "sites/apps.txt still lists it" "frappe crm" "$(xargs < sites/apps.txt)"
check_not "uv lock did not run" grep -q "lock" "$UV_CALLS"
check "and says why" grep -q "othersite.local" <<< "$OUT"

echo "── a vendored (local) app, installed nowhere ───────────────────────"
fresh_bench
add_local_app widgets
printf '{"mysite.local": [], "othersite.local": []}' > "$INSTALLED_APPS_JSON"
run widgets
check_eq "exits 0" 0 "$RC" || echo "$OUT"
check "backed up to .frappe-nix-backup/widgets" test -d .frappe-nix-backup/widgets
check "the backup has the app's files" test -f .frappe-nix-backup/widgets/widgets/hooks.py
check_not "apps/widgets is gone from disk" test -d apps/widgets
check_eq "it is no longer a uv workspace member" False \
  "$(toml_get pyproject.toml "'apps/widgets' in d['tool']['uv']['workspace']['members']")"

echo "── idempotency: running it again once already fully removed ───────"
fresh_bench
add_submodule_app crm develop
printf '{"mysite.local": [], "othersite.local": []}' > "$INSTALLED_APPS_JSON"
run crm
check_eq "first run exits 0" 0 "$RC" || echo "$OUT"
run crm
check_eq "second run against an already-removed app still exits 0" 0 "$RC" || echo "$OUT"
check_not "still no .gitmodules entry" gm crm branch
check_eq "still not a workspace member" False \
  "$(toml_get pyproject.toml "'apps/crm' in d['tool']['uv']['workspace']['members']")"

echo "── usage ────────────────────────────────────────────────────────"
check "no app given prints usage and exits 0" bash "$SCRIPT"
check "--help exits 0" bash "$SCRIPT" --help
check "…and mentions .frappe-nix-backup" bash -c "bash '$SCRIPT' --help | grep -q -- '.frappe-nix-backup'"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
