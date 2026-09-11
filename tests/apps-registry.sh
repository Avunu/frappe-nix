#!/usr/bin/env bash
# Checks for `frappe-nix-workspace sync-registry` — the one writer of
# sites/apps.txt and sites/apps.json. See lib/frappe-workspace.py.
#
# Usage: apps-registry.sh <path-to-frappe-nix-workspace>
#
# Frappe-independent: a fixture bench stands in, with apps shaped to exercise
# one case each, and the assertions are about what lands in sites/.
set -euo pipefail

TOOL="$1"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

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
jqv() { jq -r "$@" "$BENCH/sites/apps.json"; }

# ── fixture ───────────────────────────────────────────────────────────────
BENCH="$ROOT/bench"
mkdir -p "$BENCH/apps" "$BENCH/sites"

seed_app() { # <name> [pyproject-version|dynamic|none] [required_apps line]
  local app=$1 mode=${2:-dynamic} required=${3:-}
  mkdir -p "$BENCH/apps/$app/$app"
  printf 'app_name = "%s"\n%s\n' "$app" "$required" > "$BENCH/apps/$app/$app/hooks.py"
  case "$mode" in
    none) : ;;
    dynamic)
      printf '__version__ = "%s"\n' "1.0.0-$app" > "$BENCH/apps/$app/$app/__init__.py"
      printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$app" > "$BENCH/apps/$app/pyproject.toml"
      ;;
    *)
      printf '[project]\nname = "%s"\nversion = "%s"\n' "$app" "$mode" > "$BENCH/apps/$app/pyproject.toml"
      ;;
  esac
}

seed_app frappe dynamic
seed_app erpnext 16.34.2
# Multi-line required_apps, as hrms writes it.
seed_app hrms dynamic 'required_apps = [
    "frappe/erpnext",
]'
# Distribution name differs from the directory name: registered by directory.
seed_app print_designer dynamic
sed -i 's/name = "print_designer"/name = "print-designer"/' "$BENCH/apps/print_designer/pyproject.toml"
# setup.py only: version from the setup() call.
seed_app legacy none
# bench's regex anchors `version=` to a line start, as a multi-line setup() has it.
printf 'from setuptools import setup\nsetup(\n    name="legacy",\n    version="0.9.1",\n)\n' > "$BENCH/apps/legacy/setup.py"
# Tracked in apps/ but never registered — stays out of both files.
seed_app unregistered dynamic
# A member that is not a Frappe app (no hooks.py) and one that is not on disk.
mkdir -p "$BENCH/apps/notanapp"
printf '[project]\nname = "notanapp"\n' > "$BENCH/apps/notanapp/pyproject.toml"
# No version anywhere.
seed_app noversion none
# hooks.py that does not parse.
seed_app brokenhooks dynamic 'required_apps = ['

cat > "$BENCH/pyproject.toml" <<'TOML'
[project]
name = "fixture-bench"

[tool.uv.workspace]
members = [
    "apps/hrms",
    "apps/erpnext",
    "apps/frappe",
    "apps/print_designer",
    "apps/legacy",
    "apps/notanapp",
    "apps/missing",
    "apps/noversion",
    "apps/brokenhooks",
    "apps/hrms",
]
TOML

# erpnext: a real checkout on version-16 (branch from the live checkout).
git -C "$BENCH/apps/erpnext" init -q -b version-16
git -C "$BENCH/apps/erpnext" add -A
git -C "$BENCH/apps/erpnext" commit -q -m init
ERPNEXT_SHA="$(git -C "$BENCH/apps/erpnext" rev-parse HEAD)"
# hrms: detached HEAD, branch only known to .gitmodules.
git -C "$BENCH/apps/hrms" init -q -b main
git -C "$BENCH/apps/hrms" add -A
git -C "$BENCH/apps/hrms" commit -q -m init
HRMS_SHA="$(git -C "$BENCH/apps/hrms" rev-parse HEAD)"
git -C "$BENCH/apps/hrms" checkout -q --detach
# frappe: on a feature branch, but .gitmodules says version-16 — .gitmodules wins.
git -C "$BENCH/apps/frappe" init -q -b feature-x
git -C "$BENCH/apps/frappe" add -A
git -C "$BENCH/apps/frappe" commit -q -m init
FRAPPE_SHA="$(git -C "$BENCH/apps/frappe" rev-parse HEAD)"

cat > "$BENCH/.gitmodules" <<'EOF2'
[submodule "apps/frappe"]
	path = apps/frappe
	url = https://github.com/frappe/frappe.git
	branch = version-16
	shallow = true
[submodule "apps/hrms"]
	path = apps/hrms
	url = https://github.com/frappe/hrms.git
	branch = version-16
EOF2

# A stale apps.txt in bench's own shape: an entry that is not a member, and
# no trailing newline.
printf 'frappe\nstale\nerpnext' > "$BENCH/sites/apps.txt"

run() { # [extra args...]
  "$TOOL" sync-registry --pyproject "$BENCH/pyproject.toml" \
    --apps-dir "$BENCH/apps" --sites-dir "$BENCH/sites" "$@"
}

echo "── membership and order ────────────────────────────────────────"
run > "$ROOT/run1.log" 2> "$ROOT/run1.err"
check_eq "apps.txt is the members, frappe first, declared order, trailing newline" \
  "$(printf 'frappe\nhrms\nerpnext\nprint_designer\nlegacy\nnoversion\nbrokenhooks\n' | od -c)" \
  "$(od -c < "$BENCH/sites/apps.txt")"
check_not "a non-member app directory is not registered" grep -qx unregistered "$BENCH/sites/apps.txt"
check_not "a stale apps.txt entry is dropped" grep -qx stale "$BENCH/sites/apps.txt"
check "a member without hooks.py is skipped with a warning" grep -q 'apps/notanapp.*skipped' "$ROOT/run1.err"
check "a member missing on disk is skipped with a warning" grep -q 'apps/missing.*skipped' "$ROOT/run1.err"
check "both writes are reported" grep -q 'sites/apps.json' "$ROOT/run1.log"

echo "── apps.json shape ─────────────────────────────────────────────"
check_eq "keys follow apps.txt order" \
  '["frappe","hrms","erpnext","print_designer","legacy","noversion","brokenhooks"]' \
  "$(jqv -c 'keys_unsorted')"
check_eq "idx is 1-based in file order" "[1,2,3,4,5,6,7]" "$(jqv -c '[.[].idx]')"
check_eq "every entry has exactly bench's five keys" \
  '["is_repo","resolution","required","idx","version"]' \
  "$(jqv -c '[.[] | keys_unsorted] | unique | .[0]')"
check_eq "resolution is always an object" "true" "$(jqv '[.[].resolution | type == "object"] | all')"
check "the file is json.dumps(indent=4) plus a newline" \
  bash -c "python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.stdout.write(json.dumps(d, indent=4)+chr(10))' '$BENCH/sites/apps.json' | cmp -s - '$BENCH/sites/apps.json'"

echo "── version ─────────────────────────────────────────────────────"
check_eq "static [project].version" "16.34.2" "$(jqv '.erpnext.version')"
check_eq "dynamic version from <app>/__init__.py" "1.0.0-frappe" "$(jqv '.frappe.version')"
check_eq "setup.py fallback" "0.9.1" "$(jqv '.legacy.version')"
check_eq "no version anywhere is null" "null" "$(jqv '.noversion.version')"
check "the missing version is warned about" grep -q 'apps/noversion: no version' "$ROOT/run1.err"

echo "── required ────────────────────────────────────────────────────"
check_eq "required_apps from hooks.py" '["frappe/erpnext"]' "$(jqv -c '.hrms.required')"
check_eq "absent required_apps is []" '[]' "$(jqv -c '.erpnext.required')"
check_eq "unparsable hooks.py is []" '[]' "$(jqv -c '.brokenhooks.required')"
check "the unparsable hooks.py is warned about" grep -q 'brokenhooks.*could not be parsed' "$ROOT/run1.err"

echo "── provenance from disk ────────────────────────────────────────"
check_eq "live checkout: commit is HEAD" "$ERPNEXT_SHA" "$(jqv '.erpnext.resolution.commit_hash')"
check_eq "live checkout: branch from symbolic-ref" "version-16" "$(jqv '.erpnext.resolution.branch')"
check_eq "live checkout: is_repo" "true" "$(jqv '.erpnext.is_repo')"
check_eq "detached HEAD: commit still known" "$HRMS_SHA" "$(jqv '.hrms.resolution.commit_hash')"
check_eq "detached HEAD: branch from .gitmodules" "version-16" "$(jqv '.hrms.resolution.branch')"
check_eq ".gitmodules outranks the live branch" "version-16" "$(jqv '.frappe.resolution.branch')"
check_eq "no .git, no .gitmodules: nulls" '{"commit_hash":null,"branch":null}' "$(jqv -c '.legacy.resolution')"
check_eq "no .git, no .gitmodules: not a repo" "false" "$(jqv '.legacy.is_repo')"

echo "── idempotence ─────────────────────────────────────────────────"
touch "$ROOT/stamp"
sleep 1
run > "$ROOT/run2.log" 2>/dev/null
check "a second run rewrites neither file" \
  bash -c "[ ! '$BENCH/sites/apps.txt' -nt '$ROOT/stamp' ] && [ ! '$BENCH/sites/apps.json' -nt '$ROOT/stamp' ]"
check_eq "and reports nothing" "" "$(cat "$ROOT/run2.log")"

echo "── no git on PATH ──────────────────────────────────────────────"
cp "$BENCH/sites/apps.json" "$ROOT/with-git.json"
# The tool is a shebang script with an absolute interpreter, so PATH only
# matters for the git it shells out to.
PATH=/nonexistent "$TOOL" sync-registry \
  --pyproject "$BENCH/pyproject.toml" --apps-dir "$BENCH/apps" --sites-dir "$BENCH/sites" \
  > /dev/null 2> "$ROOT/nogit.err" || no "the tool must not need git"
check_eq "without git, commits are unknown" "null" "$(jqv '.erpnext.resolution.commit_hash')"
check_eq "without git, .gitmodules still gives the branch" "version-16" "$(jqv '.hrms.resolution.branch')"
check_eq "without git, a live-only branch is unknown" "null" "$(jqv '.erpnext.resolution.branch')"
cp "$ROOT/with-git.json" "$BENCH/sites/apps.json"

echo "── --provenance ────────────────────────────────────────────────"
cat > "$ROOT/prov.json" <<'EOF3'
{
  "erpnext": {"commit_hash": "deadbeef", "branch": "develop", "is_repo": true},
  "legacy": {"is_repo": true},
  "unregistered": {"commit_hash": "cafe", "branch": "x"}
}
EOF3
run --provenance "$ROOT/prov.json" > /dev/null 2>&1
check_eq "--provenance outranks the live checkout" "deadbeef" "$(jqv '.erpnext.resolution.commit_hash')"
check_eq "--provenance branch outranks the live branch" "develop" "$(jqv '.erpnext.resolution.branch')"
check_eq "is_repo can be asserted with no resolution" "true" "$(jqv '.legacy.is_repo')"
check_eq "a provenance entry for an unregistered app is ignored" "null" "$(jqv '.unregistered')"
check_eq "apps without an entry are unaffected" "$FRAPPE_SHA" "$(jqv '.frappe.resolution.commit_hash')"

echo "── --seed ──────────────────────────────────────────────────────"
cat > "$ROOT/seed.json" <<'EOF4'
{
  "legacy": {"resolution": {"commit_hash": "0123456", "branch": "seeded"}, "idx": 9, "version": "stale"},
  "erpnext": {"resolution": {"commit_hash": "seedsha", "branch": "seedbranch"}},
  "print_designer": {"is_repo": true, "resolution": "not calculated"},
  "unregistered": {"resolution": {"commit_hash": "x", "branch": "y"}}
}
EOF4
run --seed "$ROOT/seed.json" > /dev/null 2>&1
check_eq "seed fills what nothing else knows" '{"commit_hash":"0123456","branch":"seeded"}' "$(jqv -c '.legacy.resolution')"
check_eq "seed makes it a repo" "true" "$(jqv '.legacy.is_repo')"
check_eq "seed never outranks the live checkout" "$ERPNEXT_SHA" "$(jqv '.erpnext.resolution.commit_hash')"
check_eq "seed's version and idx are recomputed, not copied" "0.9.1 5" "$(jqv '.legacy | "\(.version) \(.idx)"')"
check_eq "a string resolution in the seed is ignored" '{"commit_hash":null,"branch":null}' "$(jqv -c '.print_designer.resolution')"
check_eq "a seed key for an unregistered app is dropped" "null" "$(jqv '.unregistered')"

echo "── --gitmodules ────────────────────────────────────────────────"
run --gitmodules "" > /dev/null 2>&1
check_eq "an empty --gitmodules disables the lookup" "null" "$(jqv '.hrms.resolution.branch')"
cp "$BENCH/.gitmodules" "$ROOT/elsewhere.gitmodules"
rm "$BENCH/.gitmodules"
run > /dev/null 2>&1
check_eq "no .gitmodules beside the pyproject: live branch only" "feature-x" "$(jqv '.frappe.resolution.branch')"
run --gitmodules "$ROOT/elsewhere.gitmodules" > /dev/null 2>&1
check_eq "an explicit --gitmodules is honoured" "version-16" "$(jqv '.frappe.resolution.branch')"

echo "── globs ───────────────────────────────────────────────────────"
mkdir -p "$ROOT/glob/apps" "$ROOT/glob/sites"
cp -r "$BENCH/apps/frappe" "$BENCH/apps/erpnext" "$ROOT/glob/apps/"
printf '[tool.uv.workspace]\nmembers = ["apps/*", "apps/erpnext"]\n' > "$ROOT/glob/pyproject.toml"
"$TOOL" sync-registry --pyproject "$ROOT/glob/pyproject.toml" \
  --apps-dir "$ROOT/glob/apps" --sites-dir "$ROOT/glob/sites" > /dev/null 2> "$ROOT/glob.err"
check_eq "a glob member is ignored" "erpnext" "$(cat "$ROOT/glob/sites/apps.txt")"
check "and warned about" grep -q 'glob' "$ROOT/glob.err"
check "frappe missing from the members is warned about" grep -q 'frappe is not a workspace member' "$ROOT/glob.err"

echo "── errors ──────────────────────────────────────────────────────"
check_not "an unreadable pyproject is an error" \
  "$TOOL" sync-registry --pyproject "$ROOT/nope.toml" --apps-dir "$BENCH/apps" --sites-dir "$BENCH/sites"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
