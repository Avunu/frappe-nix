#!/usr/bin/env bash
# Checks for `frappe-nix-node-locks` — the generator of the fallback locks in
# node-locks/<target>/yarn.lock. See lib/node-locks.nix.
#
# Usage: node-locks.sh <path-to-frappe-nix-node-locks> <fixture-apps-dir>
#
# Network-free: a stub yarn on PATH stands in for the resolver. It records where
# it was run and with what, copies the manifest and any seed lock it was handed,
# and writes a canned yarn.lock. The assertions are about what lands in
# node-locks/, for which targets, and when it is regenerated.
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

# ── a stub yarn ────────────────────────────────────────────────────────────
# YARN_CALLS — one line per invocation: "<cwd>\t<args>"
# YARN_SEEN  — directory receiving a copy of each package.json (and seed
#              yarn.lock) yarn was handed, numbered by invocation
# YARN_FAIL  — fail every invocation, as an unresolvable range would
# YARN_LOCK  — the lock to write (default: one registry entry with integrity
#              and one git dependency, appended to the seed if there was one)
BIN="$ROOT/bin"
mkdir -p "$BIN" "$ROOT/seen"
printf '#!%s\n' "$(command -v bash)" > "$BIN/yarn"
cat >> "$BIN/yarn" <<'STUB'
if [ "${1:-}" = "--version" ]; then echo "1.22.22-stub"; exit 0; fi
printf '%s\t%s\n' "$PWD" "$*" >> "$YARN_CALLS"
n=$(wc -l < "$YARN_CALLS" | tr -d ' ')
cp package.json "$YARN_SEEN/$n.package.json"
[ -f yarn.lock ] && cp yarn.lock "$YARN_SEEN/$n.seed.lock"
[ -n "${NODE_ENV:-}" ] && echo "$NODE_ENV" > "$YARN_SEEN/$n.node-env"
if [ "${YARN_FAIL:-}" = "1" ]; then
  echo 'error Couldn'"'"'t find any versions for "nosuch" that matches "^9.9.9"' >&2
  exit 1
fi
if [ -n "${YARN_LOCK:-}" ]; then
  cp "$YARN_LOCK" yarn.lock
else
  [ -f yarn.lock ] || printf '# yarn lockfile v1\n' > yarn.lock
  cat >> yarn.lock <<'LOCK'

left-pad@^1.3.0:
  version "1.3.0"
  resolved "https://registry.yarnpkg.com/left-pad/-/left-pad-1.3.0.tgz#5b8a3a7765dfe001261dde915589e782f8c94d1e"
  integrity sha512-XI5MPzVNApjAyhQzphX8BZTKfMYWU6BbWw4ijh6snfe7bF9RBv0X2qvNYEwSqLgJbW0OwcGL2vP8P3nXgg2rag==

"air-datepicker@git+https://github.com/example/air-datepicker":
  version "2.2.3"
  resolved "git+https://github.com/example/air-datepicker#ed37b94d95c68d8544357e330be0c89d044a3eea"
LOCK
fi
mkdir -p node_modules
exit 0
STUB
chmod +x "$BIN/yarn"
export PATH="$BIN:$PATH"
export YARN_CALLS="$ROOT/yarn-calls" YARN_SEEN="$ROOT/seen"
: > "$YARN_CALLS"
calls() { wc -l < "$YARN_CALLS" | tr -d ' '; }
# Which invocation handled which target: by the name in the manifest it saw.
call_for() { # <package name> -> invocation number
  local f
  for f in "$YARN_SEEN"/*.package.json; do
    if [ "$(jq -r .name "$f")" = "$1" ]; then
      basename "$f" .package.json
      return 0
    fi
  done
  return 1
}

# ── the fixture bench ──────────────────────────────────────────────────────
BENCH="$ROOT/bench"
mkdir -p "$BENCH"
cp -r "$FIXTURE" "$BENCH/apps"
chmod -R u+w "$BENCH"
LOCKS="$BENCH/node-locks"

echo "── discovery: only the targets without a yarn.lock ────────────"
"$TOOL" "$BENCH" node-locks > "$ROOT/run1.log" 2>&1 || { no "first run exits 0"; cat "$ROOT/run1.log"; }
check_eq "beta, which ships no yarn.lock, gets a fallback; alpha and alpha/desk, which do, get nothing" \
  "beta" \
  "$(find "$LOCKS" -name yarn.lock -printf '%h\n' | sed "s|$LOCKS/||" | sort | xargs)"
check_eq "one yarn run" "1" "$(calls)"
check "the target gets yarn.lock and source.json" test -f "$LOCKS/beta/yarn.lock" -a -f "$LOCKS/beta/source.json"
check "the write is reported" grep -q '+ node-locks/beta$' "$ROOT/run1.log"
check "and the mode" grep -q 'beta (from package.json — no yarn.lock upstream)' "$ROOT/run1.log"
check_eq "the stamp covers the manifest, and is not forced" '{"package.json":"'"$(sha256sum "$BENCH/apps/beta/package.json" | cut -d' ' -f1)"'"}' "$(jq -c . "$LOCKS/beta/source.json")"
check_not "alpha's tracked node_modules/left-pad is not a target" test -e "$LOCKS/alpha/node_modules"
check_not "gamma, with no package.json of its own, is not a target" test -e "$LOCKS/gamma"

echo "── what yarn was handed ───────────────────────────────────────"
beta_call="$(call_for beta)"
check "yarn ran in a scratch directory, not in apps/" \
  bash -c "! sed -n '${beta_call}p' '$YARN_CALLS' | cut -f1 | grep -q '^$BENCH/apps'"
check "with the sandbox's own script/engine/platform flags, devDependencies included" \
  bash -c "sed -n '${beta_call}p' '$YARN_CALLS' | grep -q -- 'install --ignore-scripts --ignore-engines --ignore-platform --production=false --non-interactive --no-progress'"
check "the manifest is the app's own, untouched" cmp -s "$YARN_SEEN/$beta_call.package.json" "$BENCH/apps/beta/package.json"
check_not "with no seed lock — there was none" test -f "$YARN_SEEN/$beta_call.seed.lock"
check_not "NODE_ENV was unset for the resolve" test -f "$YARN_SEEN/$beta_call.node-env"
check "the lock yarn wrote is what was committed" grep -q '^left-pad@^1.3.0:' "$LOCKS/beta/yarn.lock"
check "the app's tree is untouched" test ! -e "$BENCH/apps/beta/node_modules" -a ! -e "$BENCH/apps/beta/yarn.lock"

echo "── forcing a lock over the app's own ─────────────────────────"
"$TOOL" "$BENCH" node-locks alpha > "$ROOT/force.log" 2>&1 || { no "an explicit target exits 0"; cat "$ROOT/force.log"; }
alpha_call="$(call_for alpha)"
check "alpha's own yarn.lock was the seed" cmp -s "$YARN_SEEN/$alpha_call.seed.lock" "$BENCH/apps/alpha/yarn.lock"
check "and the resolved lock is the fallback" test -f "$LOCKS/alpha/yarn.lock"
check "…which starts from the seed" grep -q '^left-pad@^1.3.0:' "$LOCKS/alpha/yarn.lock"
check_eq "the stamp says forced, and covers both manifests" '["forced","package.json","yarn.lock"]' "$(jq -c 'keys' "$LOCKS/alpha/source.json")"
check "and the run says so" grep -q '+ node-locks/alpha (forced over apps/alpha/yarn.lock)' "$ROOT/force.log"
check "the app's own yarn.lock is untouched" grep -q 'left-pad-1.3.0.tgz' "$BENCH/apps/alpha/yarn.lock"

echo "── the stamp ──────────────────────────────────────────────────"
before="$(calls)"
"$TOOL" "$BENCH" node-locks > "$ROOT/run2.log" 2>&1 || no "a settled run exits 0"
check_eq "a settled bench costs no yarn run — the forced lock included" "$before" "$(calls)"
check_eq "and prints nothing" "" "$(cat "$ROOT/run2.log")"
printf '\n' >> "$BENCH/apps/alpha/yarn.lock"
"$TOOL" "$BENCH" node-locks > /dev/null 2>&1 || no "run after an upstream lock change exits 0"
check_eq "a forced target follows its upstream yarn.lock" "$((before + 1))" "$(calls)"
check "…seeded from the new one" cmp -s "$YARN_SEEN/$(calls).seed.lock" "$BENCH/apps/alpha/yarn.lock"
check_eq "…and stays forced" "true" "$(jq -r .forced "$LOCKS/alpha/source.json")"
printf '\n' >> "$BENCH/apps/beta/package.json"
"$TOOL" "$BENCH" node-locks > /dev/null 2>&1 || no "run after a manifest change exits 0"
check_eq "a changed manifest regenerates that target" "$((before + 2))" "$(calls)"
check_eq "…the right one" "beta" "$(jq -r .name "$YARN_SEEN/$(calls).package.json")"
check "…seeded from the previous fallback, so only what must move does" test -f "$YARN_SEEN/$(calls).seed.lock"
rm "$LOCKS/beta/yarn.lock"
"$TOOL" "$BENCH" node-locks > /dev/null 2>&1 || no "run after a deleted lock exits 0"
check "a deleted lock is regenerated even though the stamp matched" test -f "$LOCKS/beta/yarn.lock"

echo "── a fallback beside an upstream lock ─────────────────────────"
mkdir -p "$LOCKS/alpha/desk"
cp "$LOCKS/beta/yarn.lock" "$LOCKS/alpha/desk/yarn.lock"
jq -n '{"package.json": "stale"}' > "$LOCKS/alpha/desk/source.json"
before="$(calls)"
"$TOOL" "$BENCH" node-locks > "$ROOT/unused.log" 2>&1 || no "run exits 0"
check "an unforced fallback next to an upstream yarn.lock is reported unused" \
  grep -q '~ node-locks/alpha/desk is unused — apps/alpha/desk ships a yarn.lock' "$ROOT/unused.log"
check "…with both ways out" grep -q 'git rm -r node-locks/alpha/desk, or force it: bench-update --node-locks alpha/desk' "$ROOT/unused.log"
check "…and is left alone" test -f "$LOCKS/alpha/desk/yarn.lock"
check_eq "…at no cost" "$before" "$(calls)"
"$TOOL" --command='nix run .#relock -- --node-locks' "$BENCH" node-locks > "$ROOT/unused2.log" 2>&1 || no "run exits 0"
check "the command in the advice is the caller's" grep -q 'force it: nix run .#relock -- --node-locks alpha/desk' "$ROOT/unused2.log"
rm -rf "$LOCKS/alpha/desk"

echo "── leftovers of the npm-based scheme ─────────────────────────"
mkdir -p "$LOCKS/alpha/desk"
echo '{}' > "$LOCKS/alpha/desk/package-lock.json"
echo '{}' > "$LOCKS/alpha/desk/package.json"
echo '{}' > "$LOCKS/alpha/desk/source.json"
echo '{}' > "$LOCKS/beta/package-lock.json"
echo '{}' > "$LOCKS/beta/package.json"
printf '\n' >> "$BENCH/apps/beta/package.json"
"$TOOL" "$BENCH" node-locks > "$ROOT/npm.log" 2>&1 || no "run exits 0"
check_not "for a target that builds from its own yarn.lock they are removed" test -e "$LOCKS/alpha/desk"
check "…and said so" grep -q -- '- node-locks/alpha/desk (npm-based lock; apps/alpha/desk builds from its own yarn.lock now)' "$ROOT/npm.log"
check_not "for a target being regenerated they are replaced" test -e "$LOCKS/beta/package-lock.json" -o -e "$LOCKS/beta/package.json"
check "…by the yarn.lock" test -f "$LOCKS/beta/yarn.lock"

echo "── entries the build could not fetch are refused ─────────────"
cat > "$ROOT/bad.lock" <<'LOCK'
# yarn lockfile v1

nointegrity@1.0.0:
  version "1.0.0"
  resolved "https://registry.yarnpkg.com/nointegrity/-/nointegrity-1.0.0.tgz"

"unpinned@git+https://github.com/example/unpinned":
  version "1.0.0"
  resolved "git+https://github.com/example/unpinned"

"linked@file:../linked":
  version "1.0.0"

fine@1.0.0:
  version "1.0.0"
  resolved "https://registry.yarnpkg.com/fine/-/fine-1.0.0.tgz#0123456789abcdef0123456789abcdef01234567"
LOCK
cp "$LOCKS/beta/yarn.lock" "$ROOT/beta-before.lock"
printf '\n' >> "$BENCH/apps/beta/package.json"
if YARN_LOCK="$ROOT/bad.lock" "$TOOL" "$BENCH" node-locks beta > "$ROOT/bad.log" 2>&1; then no "an unbuildable lock fails the run"; else ok "an unbuildable lock fails the run"; fi
check "…naming the registry entry without integrity" grep -q 'nointegrity@1.0.0: no integrity' "$ROOT/bad.log"
check "…the git entry without a commit" grep -q 'unpinned.*without a commit' "$ROOT/bad.log"
check "…and the file: entry" grep -q 'linked@file:../linked.*file:/link:' "$ROOT/bad.log"
check_not "but not the one with a #sha1" grep -q 'fine@1.0.0' "$ROOT/bad.log"
check "the previous lock is kept" cmp -s "$ROOT/beta-before.lock" "$LOCKS/beta/yarn.lock"
check "and the stamp is not advanced, so it is retried" \
  bash -c "[ \"\$(jq -r '.[\"package.json\"]' '$LOCKS/beta/source.json')\" != \"\$(sha256sum '$BENCH/apps/beta/package.json' | cut -d' ' -f1)\" ]"
"$TOOL" "$BENCH" node-locks > /dev/null 2>&1 || true   # settle beta again

echo "── pruning ────────────────────────────────────────────────────"
mkdir -p "$LOCKS/gone" && printf '# yarn lockfile v1\n' > "$LOCKS/gone/yarn.lock"
"$TOOL" "$BENCH" node-locks beta > /dev/null 2>&1 || no "explicit targets exit 0"
check "explicit targets never prune" test -e "$LOCKS/gone/yarn.lock"
"$TOOL" "$BENCH" node-locks > "$ROOT/prune.log" 2>&1 || no "discovery run exits 0"
check_not "discovery prunes a lock whose target is gone" test -e "$LOCKS/gone"
check "and says so" grep -q 'node-locks/gone (no longer a target)' "$ROOT/prune.log"
"$TOOL" "$BENCH" node-locks alpha/desk > /dev/null 2>&1 || no "forcing a nested frontend exits 0"
"$TOOL" --exclude=alpha/desk "$BENCH" node-locks > /dev/null 2>&1 || no "excluded run exits 0"
check_not "an excluded frontend's lock is pruned" test -e "$LOCKS/alpha/desk"
check "its parent is untouched" test -f "$LOCKS/alpha/yarn.lock"

echo "── failures ───────────────────────────────────────────────────"
printf '\n' >> "$BENCH/apps/beta/package.json"
printf '\n' >> "$BENCH/apps/alpha/package.json"
if YARN_FAIL=1 "$TOOL" "$BENCH" node-locks alpha beta > "$ROOT/fail.log" 2>&1; then no "a yarn failure fails the run"; else ok "a yarn failure fails the run"; fi
check "every failed target is named at the end" grep -q 'NOT regenerated for: alpha beta' "$ROOT/fail.log"
check "with yarn's own words" grep -q "Couldn't find any versions" "$ROOT/fail.log"
check_not "a target that is not there is an error" "$TOOL" "$BENCH" node-locks nosuchapp
check_not "no arguments is an error" "$TOOL"

echo "── the app-mode shape: a symlink mirror of the apps ──────────"
MIRROR="$ROOT/mirror"
mkdir -p "$MIRROR/apps"
cp -rs "$BENCH/apps/beta" "$MIRROR/apps/beta"
"$TOOL" "$MIRROR" "$ROOT/mirror-locks" > /dev/null 2>&1 && ok "a cp -rs mirror resolves" || no "a cp -rs mirror resolves"
check "and yields a real lock" grep -q '^left-pad@' "$ROOT/mirror-locks/beta/yarn.lock"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
