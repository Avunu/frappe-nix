#!/usr/bin/env bash
# Checks for `bench-get-app` — adding an app as a submodule and registering it.
#
# Usage: bench-get-app.sh <path-to-rendered-bench-get-app-script>
#
# Network-free: the "apps" are bare repositories on disk, and a stub `uv` stands
# in for the sync at the end. The assertions are about what is recorded — the
# branch in .gitmodules above all, since `bench-update --pull` follows that
# record and an app added without one was never pulled again.
set -euo pipefail

SCRIPT="$1"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
# Submodule operations on file:// URLs are refused by default.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=protocol.file.allow GIT_CONFIG_VALUE_0=always

# ── a stub uv ──────────────────────────────────────────────────────────────
BIN="$ROOT/bin"
mkdir -p "$BIN"
printf '#!%s\n' "$(command -v bash)" > "$BIN/uv"
cat >> "$BIN/uv" <<'STUB'
echo "$PWD $*" >> "$UV_CALLS"
exit 0
STUB
chmod +x "$BIN/uv"
export PATH="$BIN:$PATH"
export UV_CALLS="$ROOT/uv-calls"
: > "$UV_CALLS"

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
gm() { git config -f .gitmodules --get "submodule.apps/$1.$2"; }

# ── fixture: two "upstream" apps ───────────────────────────────────────────
seed_remote() { # <name> <default-branch> [more branches…]
  local name=$1 default=$2 seed="$ROOT/seed/$1"
  shift 2
  mkdir -p "$seed/$name"
  printf '__version__ = "1.0.0"\n' > "$seed/$name/__init__.py"
  printf 'app_name = "%s"\n' "$name" > "$seed/$name/hooks.py"
  printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$name" > "$seed/pyproject.toml"
  git -C "$seed" init -q -b "$default"
  git -C "$seed" add -A
  git -C "$seed" commit -q -m "init"
  git init -q --bare "$ROOT/remotes/$name.git"
  git -C "$ROOT/remotes/$name.git" symbolic-ref HEAD "refs/heads/$default"
  git -C "$seed" push -q "$ROOT/remotes/$name.git" "$default"
  for b in "$@"; do
    git -C "$seed" checkout -q -b "$b"
    printf '__version__ = "1.0.0-%s"\n' "$b" > "$seed/$name/__init__.py"
    git -C "$seed" commit -q -am "$b"
    git -C "$seed" push -q "$ROOT/remotes/$name.git" "$b"
  done
}
# One branch only, like frappe/telephony; and one whose default is develop but
# which also carries a release branch, like frappe/hrms.
seed_remote telephony develop
seed_remote hrms develop version-16
HRMS_V16="$(git -C "$ROOT/seed/hrms" rev-parse version-16)"

BENCH="$ROOT/bench"
mkdir -p "$BENCH/apps" "$BENCH/sites"
cd "$BENCH"
git init -q -b main
cat > pyproject.toml <<'TOML'
[project]
name = "fixture-bench"

[tool.uv.workspace]
members = []

[tool.uv.sources]
TOML
git add -A
git commit -q -m "bench"
export FRAPPE_BENCH_ROOT="$BENCH"

echo "── the remote's default branch, when none is asked for ─────────"
bash "$SCRIPT" "file://$ROOT/remotes/telephony.git" > "$ROOT/tel.log" 2>&1 \
  && ok "exits 0" || { no "exits 0"; cat "$ROOT/tel.log"; }
check_eq "the branch is recorded in .gitmodules" develop "$(gm telephony branch)"
check "and said so" grep -q "recorded branch 'develop'" "$ROOT/tel.log"
check_eq "the URL is recorded" "file://$ROOT/remotes/telephony.git" "$(gm telephony url)"
check_eq "the checkout is on that branch" develop "$(git -C apps/telephony symbolic-ref --short HEAD)"
check_eq "it is a uv workspace member" True \
  "$(toml_get pyproject.toml "'apps/telephony' in d['tool']['uv']['workspace']['members']")"
check_eq "with a workspace source" True \
  "$(toml_get pyproject.toml "d['tool']['uv']['sources']['telephony']['workspace']")"
check_eq "sites/apps.txt lists it" telephony "$(xargs < sites/apps.txt)"
check_eq "uv sync ran, from the bench root" "$BENCH sync" "$(cat "$UV_CALLS")"

echo "── --branch, like bench get-app ────────────────────────────────"
bash "$SCRIPT" --branch version-16 "file://$ROOT/remotes/hrms.git" > "$ROOT/hrms.log" 2>&1 \
  && ok "exits 0" || { no "exits 0"; cat "$ROOT/hrms.log"; }
check_eq "the asked-for branch is recorded" version-16 "$(gm hrms branch)"
check_eq "and checked out" version-16 "$(git -C apps/hrms symbolic-ref --short HEAD)"
check_eq "at the remote's tip of it" "$HRMS_V16" "$(git -C apps/hrms rev-parse HEAD)"
check_eq "both apps are members, in order" "telephony hrms" "$(xargs < sites/apps.txt)"

echo "── what bench-update --pull will see ───────────────────────────"
# The classifier is what the pull loop reads; a recorded branch is what keeps
# the app on its list.
WORKSPACE_TOOL="$(grep -o '/nix/store/[^/]*/bin/frappe-nix-workspace' "$SCRIPT" | head -n1)"
check_eq "telephony: name, kind, branch, url" \
  "$(printf 'telephony\tsubmodule\tdevelop\tfile://%s/remotes/telephony.git' "$ROOT")" \
  "$("$WORKSPACE_TOOL" apps --apps-dir apps | grep '^telephony')"
check_eq "hrms likewise" \
  "$(printf 'hrms\tsubmodule\tversion-16\tfile://%s/remotes/hrms.git' "$ROOT")" \
  "$("$WORKSPACE_TOOL" apps --apps-dir apps | grep '^hrms')"

echo "── refusals ────────────────────────────────────────────────────"
check_not "an app that is already there" bash "$SCRIPT" "file://$ROOT/remotes/hrms.git"
check_eq "…left as it was" version-16 "$(gm hrms branch)"
check_not "no app" bash "$SCRIPT"
check_not "--branch with no name" bash "$SCRIPT" --branch
check_not "an unknown flag" bash "$SCRIPT" --depth 1 "file://$ROOT/remotes/hrms.git"
check_not "two apps at once" bash "$SCRIPT" a b
check "--help exits 0" bash "$SCRIPT" --help
check "…and shows --branch" bash -c "bash '$SCRIPT' --help | grep -q -- '--branch version-16'"
check "nothing was added by any of those" \
  bash -c "[ \"\$(git config -f .gitmodules --get-regexp '^submodule\..*\.path$' | wc -l)\" = 2 ]"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
