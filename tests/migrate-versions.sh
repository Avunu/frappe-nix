#!/usr/bin/env bash
# Preset resolution: which of develop / version-16 / version-15 an existing
# bench is taken to be, and what happens when the answer is none of them.
#
# Usage: migrate-versions.sh <path-to-frappe-init>
#
# Runs --dry-run only, so it needs no remotes and no network.
set -euo pipefail

FRAPPE_INIT="$1"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
export HOME="$ROOT/home"
mkdir -p "$HOME"
export GIT_CONFIG_GLOBAL="$ROOT/gitconfig"
export GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
git config --global init.defaultBranch main

fails=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no() {
  printf '  \033[31m✗\033[0m %s\n' "$1"
  fails=$((fails + 1))
}

# <name> <frappe branch> <__version__> <apps.json branch, or -> -> a bench dir
make_bench() {
  local dir="$ROOT/$1" br=$2 ver=$3 ajson=$4
  mkdir -p "$dir/apps/frappe/frappe" "$dir/sites"
  printf '__version__ = "%s"\n' "$ver" > "$dir/apps/frappe/frappe/__init__.py"
  printf 'app_name = "frappe"\n' > "$dir/apps/frappe/frappe/hooks.py"
  printf '[project]\nname = "frappe"\nversion = "%s"\n' "$ver" > "$dir/apps/frappe/pyproject.toml"
  printf 'frappe\n' > "$dir/sites/apps.txt"
  printf '{"default_site": "t.localhost"}\n' > "$dir/sites/common_site_config.json"
  if [ "$ajson" != - ]; then
    printf '{"frappe":{"resolution":{"commit_hash":null,"branch":"%s"},"version":"%s"}}\n' \
      "$ajson" "$ver" > "$dir/sites/apps.json"
  fi
  if [ "$br" != - ]; then
    (
      cd "$dir/apps/frappe"
      git init -q -b "$br"
      git add -A
      git commit -q -m x
    )
  fi
  printf '%s' "$dir"
}

expect_preset() { # <label> <dir> <expected preset>
  local out
  if out="$("$FRAPPE_INIT" --migrate --dry-run "$2" 2>&1)"; then
    if printf '%s' "$out" | grep -q "frappe version : $3 "; then
      ok "$1 → $3"
    else
      no "$1 → $3 (got: $(printf '%s' "$out" | grep 'frappe version' | head -1))"
    fi
  else
    no "$1 → $3 (command failed: $(printf '%s' "$out" | tail -2 | tr '\n' ' '))"
  fi
}

expect_failure() { # <label> <dir> <expected message fragment>
  local out rc=0
  out="$("$FRAPPE_INIT" --migrate --dry-run "$2" 2>&1)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    no "$1 should have failed"
  elif printf '%s' "$out" | grep -qF "$3"; then
    ok "$1 → refused ($rc)"
  else
    no "$1 → refused ($rc) but the message did not mention '$3'"
  fi
}

echo "── preset resolution ────────────────────────────────────────"

# The branch wins over __version__: develop and version-16 both report 16.0.0-dev.
expect_preset "branch develop, __version__ 16.0.0-dev" \
  "$(make_bench b1 develop 16.0.0-dev -)" develop
expect_preset "branch version-16" \
  "$(make_bench b2 version-16 16.0.0-dev -)" version-16
# A branch name that is not a preset falls through to __version__.
expect_preset "branch feature/x, __version__ 15.42.0" \
  "$(make_bench b3 feature/x 15.42.0 -)" version-15
# No git at all: sites/apps.json, then __version__.
expect_preset "no git, apps.json says version-16" \
  "$(make_bench b4 - 16.1.0 version-16)" version-16
expect_preset "no git, __version__ 15.1.0 only" \
  "$(make_bench b5 - 15.1.0 -)" version-15

echo "── unsupported / undetectable ───────────────────────────────"
expect_failure "frappe 14" "$(make_bench b6 - 14.9.0 -)" "no preset below version-15"
expect_failure "nothing detectable in a non-TTY" \
  "$(make_bench b7 - '' -)" "could not detect the frappe version"

echo "── explicit override ────────────────────────────────────────"
d="$(make_bench b8 - 15.1.0 -)"
out="$("$FRAPPE_INIT" --migrate --dry-run --frappe-version version-16 "$d" 2>&1)"
if printf '%s' "$out" | grep -q 'python 3.14'; then
  ok "--frappe-version overrides detection"
else
  no "--frappe-version overrides detection"
fi
if printf '%s' "$out" | grep -q 'looks like version-15'; then
  ok "a disagreeing --frappe-version warns"
else
  no "a disagreeing --frappe-version warns"
fi

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
