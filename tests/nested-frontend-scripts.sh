#!/usr/bin/env bash
# Checks for lib/js/drop-nested-frontend-scripts.js — the step that keeps an
# excluded nested frontend from taking `bench build` down with it.
#
# Usage: nested-frontend-scripts.sh <path-to-node> <path-to-script.js>
#
# The failure this guards against: excluding erpnext/banking removes banking's
# node_modules, but erpnext's own build script is `cd banking && yarn build`,
# and frappe's esbuild runs every app's build script with no opt-out. Without
# the drop, `bench build` dies on `/bin/sh: vite: not found`.
set -euo pipefail

NODE="$1"
SCRIPT="$2"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

fails=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no() {
  printf '  \033[31m✗\033[0m %s\n' "$1"
  fails=$((fails + 1))
}
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else
    no "$1"
    printf '      expected: %s\n      actual:   %s\n' "$2" "$3"
  fi
}

# Scripts left in <file> after excluding <subdir>..., as sorted names.
after() { # <package.json contents> <subdir>...
  local pkg=$ROOT/pkg.json
  printf '%s\n' "$1" > "$pkg"
  shift
  "$NODE" "$SCRIPT" "$pkg" "$@" 2> "$ROOT/stderr"
  "$NODE" -e 'const s=require(process.argv[1]).scripts||{};console.log(Object.keys(s).sort().join(","))' "$pkg"
}

echo "── the erpnext v16 shape ────────────────────────────────────────"

# erpnext delegates its entire build to the nested frontend. Both the build and
# the postinstall have to go; `dev` is never run by anything here, so it stays.
ERPNEXT='{"name":"erpnext","scripts":{
  "postinstall":"cd banking && yarn install",
  "dev":"cd banking && yarn dev",
  "build":"cd banking && yarn build"}}'
check_eq "build and postinstall are dropped, dev is not" \
  "dev" "$(after "$ERPNEXT" banking)"
check_eq "each dropped script is named on stderr" \
  "2" "$(grep -c '^dropped ' "$ROOT/stderr")"

echo "── the frappe/ui shape ──────────────────────────────────────────"

# frappe's build has nothing to do with its ui/ subdir, so excluding ui must
# leave the app's build alone -- dropping it would take out all of frappe.
FRAPPE='{"name":"frappe","scripts":{
  "build":"node esbuild",
  "production":"node esbuild --production"}}'
check_eq "an unrelated build script survives" \
  "build,production" "$(after "$FRAPPE" ui)"
check_eq "nothing is reported when nothing is dropped" \
  "0" "$(grep -c '^dropped ' "$ROOT/stderr" || true)"

echo "── matching is on words, not substrings ─────────────────────────"

# `ui-dist` and `ui.html` contain "ui" but are not the ui/ frontend. Matching by
# substring would drop this build and silently lose the app's real assets.
NEARMISS='{"name":"x","scripts":{"build":"vite build --outDir ui-dist && cp ui.html out/"}}'
check_eq "a near-miss word does not match" \
  "build" "$(after "$NEARMISS" ui)"

# ...but the separators a real script uses still delimit. `cd ui&&yarn build`
# has no space around the subdir.
TIGHT='{"name":"x","scripts":{"build":"cd ui&&yarn build"}}'
check_eq "a subdir with no surrounding whitespace matches" \
  "" "$(after "$TIGHT" ui)"

echo "── several excludes in one app ──────────────────────────────────"

MULTI='{"name":"x","scripts":{"build":"cd docs && yarn build","postinstall":"cd dashboard && yarn install","test":"jest"}}'
check_eq "any of the app's excluded subdirs matches" \
  "test" "$(after "$MULTI" docs dashboard)"

echo "── the cases that must not fail the build ───────────────────────"

NOSCRIPTS='{"name":"x","dependencies":{}}'
check_eq "an app with no scripts is left alone" \
  "" "$(after "$NOSCRIPTS" banking)"

if "$NODE" "$SCRIPT" "$ROOT/absent.json" banking > /dev/null 2>&1; then
  ok "an app with no package.json exits clean"
else
  no "an app with no package.json exits clean"
fi

# A rewritten package.json still has to parse, and keep everything but scripts.
printf '%s\n' "$ERPNEXT" > "$ROOT/keep.json"
"$NODE" "$SCRIPT" "$ROOT/keep.json" banking 2> /dev/null
check_eq "non-script keys survive the rewrite" \
  "erpnext" "$("$NODE" -e 'console.log(require(process.argv[1]).name)' "$ROOT/keep.json")"

echo
if [ "$fails" -gt 0 ]; then
  printf '\033[31m%d check(s) failed\033[0m\n' "$fails"
  exit 1
fi
printf '\033[32mall checks passed\033[0m\n'
