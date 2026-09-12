#!/usr/bin/env bash
# Checks for `frappe-nix-node-locks` — the generator of node-locks/<target>/.
# See lib/node-locks.nix.
#
# Usage: node-locks.sh <path-to-frappe-nix-node-locks> <fixture-apps-dir>
#
# Network-free: a stub npm on PATH stands in for the resolver. It records where
# it was run and with what, copies the manifest it was handed, and writes a
# canned package-lock.json with the shapes the post-processing has to handle.
# The assertions are about what lands in node-locks/ and when.
set -euo pipefail

TOOL="$1"
FIXTURE="$2"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

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
check_not() { # <description> <command...>
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}

# ── a stub npm ─────────────────────────────────────────────────────────────
# NPM_CALLS — one line per invocation: "<cwd>\t<args>"
# NPM_SEEN  — directory receiving a copy of each package.json npm was handed
# NPM_FAIL  — fail every invocation, as an unresolvable range would
# NPM_LOCK  — the lock to write (default: one registry entry with integrity,
#             one GitHub git dependency spelled the way npm spells it)
BIN="$ROOT/bin"
mkdir -p "$BIN" "$ROOT/seen"
printf '#!%s\n' "$(command -v bash)" > "$BIN/npm"
cat >> "$BIN/npm" <<'STUB'
if [ "${1:-}" = "--version" ]; then echo "11.0.0-stub"; exit 0; fi
printf '%s\t%s\n' "$PWD" "$*" >> "$NPM_CALLS"
n=$(wc -l < "$NPM_CALLS" | tr -d ' ')
cp package.json "$NPM_SEEN/$n.package.json"
[ -f yarn.lock ] && touch "$NPM_SEEN/$n.had-yarn-lock"
[ -n "${NODE_ENV:-}" ] && echo "$NODE_ENV" > "$NPM_SEEN/$n.node-env"
if [ "${NPM_FAIL:-}" = "1" ]; then
  echo "npm error ERESOLVE unable to resolve dependency tree" >&2
  exit 1
fi
if [ -n "${NPM_LOCK:-}" ]; then
  cp "$NPM_LOCK" package-lock.json
else
  cat > package-lock.json <<'LOCK'
{
  "name": "stub", "lockfileVersion": 3, "requires": true,
  "packages": {
    "": { "name": "stub", "dependencies": { "left-pad": "^1.3.0" } },
    "node_modules/left-pad": {
      "version": "1.3.0",
      "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
      "integrity": "sha512-XI5MPzVNApjAyhQzphX8BZTKfMYWU6BbWw4ijh6snfe7bF9RBv0X2qvNYEwSqLgJbW0OwcGL2vP8P3nXgg2rag=="
    },
    "node_modules/air-datepicker": {
      "version": "2.2.3",
      "resolved": "git+ssh://git@github.com/example/air-datepicker.git#ed37b94d95c68d8544357e330be0c89d044a3eea"
    }
  }
}
LOCK
fi
# npm rewrites an existing yarn.lock in its own dialect.
[ -f yarn.lock ] && printf '# rewritten by npm\n' > yarn.lock
exit 0
STUB
chmod +x "$BIN/npm"
export PATH="$BIN:$PATH"
export NPM_CALLS="$ROOT/npm-calls" NPM_SEEN="$ROOT/seen"
: > "$NPM_CALLS"
calls() { wc -l < "$NPM_CALLS" | tr -d ' '; }

# ── the fixture bench ──────────────────────────────────────────────────────
BENCH="$ROOT/bench"
mkdir -p "$BENCH"
cp -r "$FIXTURE" "$BENCH/apps"
chmod -R u+w "$BENCH"
LOCKS="$BENCH/node-locks"

echo "── discovery and the files it writes ─────────────────────────"
"$TOOL" "$BENCH" node-locks > "$ROOT/run1.log" 2>&1 || { no "first run exits 0"; cat "$ROOT/run1.log"; }
check_eq "the targets are the Nix side's: alpha, alpha/desk, beta" \
  "alpha alpha/desk beta" \
  "$(find "$LOCKS" -name package-lock.json -printf '%h\n' | sed "s|$LOCKS/||" | sort | xargs)"
check_eq "one npm run per target" "3" "$(calls)"
check "each target gets package.json, package-lock.json and source.json" \
  test -f "$LOCKS/alpha/package.json" -a -f "$LOCKS/alpha/package-lock.json" -a -f "$LOCKS/alpha/source.json"
check "each write is reported" grep -q '+ node-locks/alpha/desk' "$ROOT/run1.log"
check_not "alpha's tracked node_modules/left-pad is not a target" test -e "$LOCKS/alpha/node_modules"
check_not "alpha's .gitmodules-listed vendored/ is not a target" test -e "$LOCKS/alpha/vendored"
check_not "gamma, with no package.json of its own, is not a target" test -e "$LOCKS/gamma"

echo "── what npm was handed ────────────────────────────────────────"
# Which invocation handled which target: by the name in the manifest it saw.
call_for() { # <package name> -> invocation number
  local f
  for f in "$NPM_SEEN"/*.package.json; do
    if [ "$(jq -r .name "$f")" = "$1" ]; then
      basename "$f" .package.json
      return 0
    fi
  done
  return 1
}
alpha_call="$(call_for alpha)"
beta_call="$(call_for beta)"
check "npm ran in a scratch directory, not in apps/" \
  bash -c "! sed -n '${alpha_call}p' '$NPM_CALLS' | cut -f1 | grep -q '^$BENCH/apps'"
check "with the lock-only, script-free, legacy-peer flags" \
  bash -c "sed -n '${alpha_call}p' '$NPM_CALLS' | grep -q -- 'install --package-lock-only --ignore-scripts --legacy-peer-deps --no-audit --no-fund'"
check "alpha's yarn.lock travelled with it" test -f "$NPM_SEEN/$alpha_call.had-yarn-lock"
check_not "beta, which has no yarn.lock, was resolved from package.json" test -f "$NPM_SEEN/$beta_call.had-yarn-lock"
check_not "NODE_ENV was unset for the resolve" test -f "$NPM_SEEN/$alpha_call.node-env"
check "the source stamp says which mode was used" grep -q 'beta (from package.json' "$ROOT/run1.log"

echo "── the manifest npm resolved from is the one the build gets ──"
SEEN="$NPM_SEEN/$alpha_call.package.json"
check "resolutions are gone" bash -c "! jq -e '.resolutions' '$SEEN' > /dev/null"
check "workspaces are gone" bash -c "! jq -e '.workspaces' '$SEEN' > /dev/null"
check_eq "a plain resolution is a top-level override" "^1.15.7" "$(jq -r '.overrides.sortablejs' "$SEEN")"
check_eq "a nested resolution nests" "^0.28.0" "$(jq -r '.overrides["esbuild-plugin-vue3"].esbuild' "$SEEN")"
check_eq "a scoped parent keeps its scope" "2.0.0" "$(jq -r '.overrides["@scope/pkg"].child' "$SEEN")"
check_eq "a **/ glob is just the package" "3.1.0" "$(jq -r '.overrides.deep' "$SEEN")"
check "the committed package.json is byte-identical to what npm saw" cmp -s "$SEEN" "$LOCKS/alpha/package.json"
check "the app's own package.json is untouched" jq -e '.resolutions' "$BENCH/apps/alpha/package.json"
check_not "beta gained no empty overrides" jq -e '.overrides' "$LOCKS/beta/package.json"

echo "── post-processing ────────────────────────────────────────────"
check_eq "a GitHub git dependency is spelled https, commit kept" \
  "git+https://github.com/example/air-datepicker.git#ed37b94d95c68d8544357e330be0c89d044a3eea" \
  "$(jq -r '.packages["node_modules/air-datepicker"].resolved' "$LOCKS/alpha/package-lock.json")"
check_eq "registry entries are untouched" \
  "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz" \
  "$(jq -r '.packages["node_modules/left-pad"].resolved' "$LOCKS/alpha/package-lock.json")"
check_eq "the stamp covers the manifests that exist" '["package.json","yarn.lock"]' "$(jq -c 'keys' "$LOCKS/alpha/source.json")"
check_eq "and only those" '["package.json"]' "$(jq -c 'keys' "$LOCKS/beta/source.json")"

echo "── a git dependency npm left unpinned is pinned from yarn.lock ─"
cat > "$ROOT/unpinned.json" <<'LOCK'
{ "lockfileVersion": 3, "packages": {
  "": { "name": "x" },
  "node_modules/air-datepicker": { "version": "2.2.3", "resolved": "git+https://github.com/example/air-datepicker.git" } } }
LOCK
printf 'x\n' >> "$BENCH/apps/alpha/yarn.lock"   # invalidate the stamp
printf '\n"air-datepicker@git+https://github.com/example/air-datepicker":\n  version "2.2.3"\n  resolved "git+https://github.com/example/air-datepicker#ed37b94d95c68d8544357e330be0c89d044a3eea"\n' >> "$BENCH/apps/alpha/yarn.lock"
NPM_LOCK="$ROOT/unpinned.json" "$TOOL" "$BENCH" node-locks alpha > /dev/null 2>&1 && ok "regenerated" || no "regenerated"
check_eq "the commit comes from the app's yarn.lock" \
  "git+https://github.com/example/air-datepicker.git#ed37b94d95c68d8544357e330be0c89d044a3eea" \
  "$(jq -r '.packages["node_modules/air-datepicker"].resolved' "$LOCKS/alpha/package-lock.json")"

echo "── entries the build could not fetch are refused ─────────────"
cat > "$ROOT/bad.json" <<'LOCK'
{ "lockfileVersion": 3, "packages": {
  "": { "name": "x" },
  "node_modules/linked": { "resolved": "../linked", "link": true },
  "node_modules/nointegrity": { "version": "1.0.0", "resolved": "https://registry.npmjs.org/nointegrity/-/nointegrity-1.0.0.tgz" },
  "node_modules/unpinned": { "version": "1.0.0", "resolved": "git+https://github.com/example/unpinned.git" },
  "node_modules/bundled": { "version": "1.0.0", "inBundle": true },
  "node_modules/fine": { "version": "1.0.0", "resolved": "https://registry.npmjs.org/fine/-/fine-1.0.0.tgz", "integrity": "sha512-x" } } }
LOCK
cp "$LOCKS/beta/package-lock.json" "$ROOT/beta-before.json"
printf '\n' >> "$BENCH/apps/beta/package.json"
if NPM_LOCK="$ROOT/bad.json" "$TOOL" "$BENCH" node-locks beta > "$ROOT/bad.log" 2>&1; then no "an unbuildable lock fails the run"; else ok "an unbuildable lock fails the run"; fi
check "…naming the link: entry" grep -q 'node_modules/linked' "$ROOT/bad.log"
check "…the registry entry without integrity" grep -q 'node_modules/nointegrity' "$ROOT/bad.log"
check "…and the git entry without a commit" grep -q 'node_modules/unpinned' "$ROOT/bad.log"
check_not "but not the bundled or the fine one" grep -qE 'node_modules/(bundled|fine)' "$ROOT/bad.log"
check "the previous lock is kept" cmp -s "$ROOT/beta-before.json" "$LOCKS/beta/package-lock.json"
check "and the stamp is not advanced, so it is retried" \
  bash -c "[ \"\$(jq -r '.[\"package.json\"]' '$LOCKS/beta/source.json')\" != \"\$(sha256sum '$BENCH/apps/beta/package.json' | cut -d' ' -f1)\" ]"
"$TOOL" "$BENCH" node-locks beta > /dev/null 2>&1 || true   # settle beta again

echo "── the stamp ──────────────────────────────────────────────────"
before="$(calls)"
"$TOOL" "$BENCH" node-locks > "$ROOT/run2.log" 2>&1 || no "a settled run exits 0"
check_eq "a settled bench costs no npm run" "$before" "$(calls)"
check_eq "and prints nothing" "" "$(cat "$ROOT/run2.log")"
printf '\n' >> "$BENCH/apps/alpha/desk/package.json"
"$TOOL" "$BENCH" node-locks > /dev/null 2>&1 || no "run after a nested manifest change exits 0"
check_eq "a changed nested manifest regenerates that target only" "$((before + 1))" "$(calls)"
check_eq "…the nested one" "desk" "$(jq -r .name "$NPM_SEEN/$(calls).package.json")"
rm "$LOCKS/beta/package-lock.json"
"$TOOL" "$BENCH" node-locks > /dev/null 2>&1 || no "run after a deleted lock exits 0"
check "a deleted lock is regenerated even though the stamp matched" test -f "$LOCKS/beta/package-lock.json"

echo "── pruning ────────────────────────────────────────────────────"
mkdir -p "$LOCKS/gone" && printf '{}' > "$LOCKS/gone/package-lock.json"
"$TOOL" "$BENCH" node-locks alpha > /dev/null 2>&1 || no "explicit targets exit 0"
check "explicit targets never prune" test -e "$LOCKS/gone/package-lock.json"
"$TOOL" "$BENCH" node-locks > "$ROOT/prune.log" 2>&1 || no "discovery run exits 0"
check_not "discovery prunes a lock whose target is gone" test -e "$LOCKS/gone"
check "and says so" grep -q 'node-locks/gone (no longer a target)' "$ROOT/prune.log"
"$TOOL" --exclude=alpha/desk "$BENCH" node-locks > /dev/null 2>&1 || no "excluded run exits 0"
check_not "an excluded frontend's lock is pruned" test -e "$LOCKS/alpha/desk"
check "its parent is untouched" test -f "$LOCKS/alpha/package-lock.json"

echo "── failures ───────────────────────────────────────────────────"
printf '\n' >> "$BENCH/apps/beta/package.json"
printf '\n' >> "$BENCH/apps/alpha/package.json"
if NPM_FAIL=1 "$TOOL" "$BENCH" node-locks alpha beta > "$ROOT/fail.log" 2>&1; then no "an npm failure fails the run"; else ok "an npm failure fails the run"; fi
check "every failed target is named at the end" grep -q 'NOT regenerated for: alpha beta' "$ROOT/fail.log"
check "with npm's own words" grep -q 'ERESOLVE' "$ROOT/fail.log"
check_not "a target that is not there is an error" "$TOOL" "$BENCH" node-locks nosuchapp
check_not "no arguments is an error" "$TOOL"

echo "── the app-mode shape: a symlink mirror of the apps ──────────"
MIRROR="$ROOT/mirror"
mkdir -p "$MIRROR/apps"
cp -rs "$BENCH/apps/beta" "$MIRROR/apps/beta"
"$TOOL" "$MIRROR" "$ROOT/mirror-locks" > /dev/null 2>&1 && ok "a cp -rs mirror resolves" || no "a cp -rs mirror resolves"
check "and yields a real lock" jq -e '.packages' "$ROOT/mirror-locks/beta/package-lock.json"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
