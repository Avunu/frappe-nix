#!/usr/bin/env bash
# Checks for `frappe-nix-node-modules` — the reinstall trigger that keeps
# `bench build` from compiling against a node_modules older than the app.
# See lib/node-modules.nix.
#
# Usage: node-modules.sh <path-to-frappe-nix-node-modules>
#
# Node-independent: a stub `yarn` on PATH records where it was called and does
# to the tree what a real install does, and the assertions are about *when* the
# tool decides to call it.
set -euo pipefail

TOOL="$1"

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
check_not() { # <description> <command...> — passes when the command fails
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}

# ── a stub yarn ────────────────────────────────────────────────────────────
# YARN_CALLS  — one line per invocation, so "did it reinstall?" is a line count
# YARN_FAIL   — make the install fail, as a broken lockfile would
# YARN_REWRITE — a manifest path a postinstall rewrites during the install
BIN="$ROOT/bin"
mkdir -p "$BIN"
# Not `#!/usr/bin/env bash`: this also runs as a Nix check, where the sandbox
# has no /usr/bin/env.
printf '#!%s\n' "$(command -v bash)" > "$BIN/yarn"
cat >> "$BIN/yarn" <<'STUB'
echo "$PWD" >> "$YARN_CALLS"
if [ "${YARN_FAIL:-}" = "1" ]; then
  echo "error Your lockfile needs to be updated" >&2
  exit 1
fi
# A real install writes thousands of package.json files under node_modules.
mkdir -p node_modules/left-pad
echo '{"name":"left-pad","version":"1.0.0"}' > node_modules/left-pad/package.json
if [ -n "${YARN_REWRITE:-}" ]; then
  printf '{"name":"rewritten-by-postinstall","stamp":"%s"}\n' "$RANDOM" > "$YARN_REWRITE"
fi
exit 0
STUB
chmod +x "$BIN/yarn"
export PATH="$BIN:$PATH"

export YARN_CALLS="$ROOT/yarn-calls"
: > "$YARN_CALLS"
calls() { wc -l < "$YARN_CALLS" | tr -d ' '; }

# ── a fixture bench ────────────────────────────────────────────────────────
# One app with a nested frontend, which is the shape that broke: the dependency
# that goes missing is declared in the *nested* package.json, not the app's.
BENCH="$ROOT/bench"
mkdir -p "$BENCH/apps/alpha/desk"
echo '{"name":"alpha","scripts":{"postinstall":"cd desk && yarn install"}}' \
  > "$BENCH/apps/alpha/package.json"
echo '# yarn lockfile v1' > "$BENCH/apps/alpha/yarn.lock"
echo '{"name":"alpha-ui","dependencies":{}}' > "$BENCH/apps/alpha/desk/package.json"
echo '# yarn lockfile v1' > "$BENCH/apps/alpha/desk/yarn.lock"

SENTINEL="$BENCH/apps/alpha/node_modules/.frappe-nix-installed"

echo "── a bench that has never been installed ────────────────────────"
"$TOOL" "$BENCH" alpha > "$ROOT/first.log" 2>&1
check_eq "yarn install runs once" "1" "$(calls)"
check "in the app directory" grep -qxF "$BENCH/apps/alpha" "$YARN_CALLS"
check "and the sentinel records what it installed" test -s "$SENTINEL"

echo "── nothing has changed since ────────────────────────────────────"
"$TOOL" "$BENCH" alpha > /dev/null 2>&1
check_eq "the install is skipped" "1" "$(calls)"

echo "── node_modules churns on its own ───────────────────────────────"
# The install's own output must not be an input to the decision to re-run it,
# or the tool reinstalls forever.
mkdir -p "$BENCH/apps/alpha/node_modules/right-pad"
echo '{"name":"right-pad"}' > "$BENCH/apps/alpha/node_modules/right-pad/package.json"
"$TOOL" "$BENCH" alpha > /dev/null 2>&1
check_eq "a package.json under node_modules is not a change" "1" "$(calls)"

echo "── the app gains a dependency (the helpdesk case) ───────────────"
# `bench update` pulls a commit that adds a dep to the *nested* frontend. This
# is exactly what a bare touch-sentinel misses, and what surfaces later as
# "Cannot find package '@framework/ui'" from a vite config.
echo '{"name":"alpha-ui","dependencies":{"@framework/ui":"link:../../frappe/ui"}}' \
  > "$BENCH/apps/alpha/desk/package.json"
"$TOOL" "$BENCH" alpha > /dev/null 2>&1
check_eq "the nested package.json triggers a reinstall" "2" "$(calls)"

echo "── the app's own yarn.lock moves ────────────────────────────────"
echo '# yarn lockfile v1 (bumped)' > "$BENCH/apps/alpha/yarn.lock"
"$TOOL" "$BENCH" alpha > /dev/null 2>&1
check_eq "the lockfile triggers a reinstall" "3" "$(calls)"

echo "── a nested frontend appears ────────────────────────────────────"
mkdir -p "$BENCH/apps/alpha/roster"
echo '{"name":"alpha-roster"}' > "$BENCH/apps/alpha/roster/package.json"
echo '# yarn lockfile v1' > "$BENCH/apps/alpha/roster/yarn.lock"
"$TOOL" "$BENCH" alpha > /dev/null 2>&1
check_eq "a new nested frontend triggers a reinstall" "4" "$(calls)"

echo "── a postinstall rewrites a manifest ────────────────────────────"
# patch-package and friends do this. Recording the *pre*-install fingerprint
# would leave the app permanently dirty and reinstalling on every shell entry.
export YARN_REWRITE="$BENCH/apps/alpha/desk/package.json"
echo '{"name":"alpha-ui","dependencies":{"a":"1"}}' > "$YARN_REWRITE"
"$TOOL" "$BENCH" alpha > /dev/null 2>&1
check_eq "that install runs" "5" "$(calls)"
unset YARN_REWRITE
"$TOOL" "$BENCH" alpha > /dev/null 2>&1
check_eq "and the next run is a no-op, not a loop" "5" "$(calls)"

echo "── the install fails ────────────────────────────────────────────"
echo '# yarn lockfile v1 (bumped again)' > "$BENCH/apps/alpha/yarn.lock"
export YARN_FAIL=1
check_not "the tool reports failure" "$TOOL" "$BENCH" alpha
check_eq "yarn was attempted" "6" "$(calls)"
"$TOOL" "$BENCH" alpha > "$ROOT/fail.log" 2>&1 || true
check_eq "and is retried on the next run rather than recorded as done" "7" "$(calls)"
check "the failing app is named" grep -q 'alpha' "$ROOT/fail.log"
unset YARN_FAIL

echo "── node_modules is a Nix store symlink (the old layout) ─────────"
# Nix-built node_modules are read-only and built with --ignore-scripts, so the
# nested frontends inside have no deps at all. A dev shell has to replace it.
mkdir -p "$BENCH/apps/beta"
echo '{"name":"beta"}' > "$BENCH/apps/beta/package.json"
echo '# yarn lockfile v1' > "$BENCH/apps/beta/yarn.lock"
ln -s /nix/store/00000000000000000000000000000000-beta-node-modules/node_modules \
  "$BENCH/apps/beta/node_modules"
"$TOOL" "$BENCH" beta > /dev/null 2>&1
check_not "the symlink is gone" test -L "$BENCH/apps/beta/node_modules"
check "replaced by a real install" test -s "$BENCH/apps/beta/node_modules/.frappe-nix-installed"

echo "── several apps in one call ─────────────────────────────────────"
# alpha is still stale — the two runs above failed and recorded nothing.
"$TOOL" "$BENCH" alpha beta > /dev/null 2>&1
check_eq "the app the failure left stale is picked back up" "9" "$(calls)"
before=$(calls)
"$TOOL" "$BENCH" alpha beta > /dev/null 2>&1
check_eq "and a second pass reinstalls neither" "$before" "$(calls)"

echo "── the app is a symlink to its own repository (app mode) ────────"
# In app mode the app under development lives outside the bench and apps/<app>
# is a symlink to it. find's default -P mode prints a symlinked start point and
# does not descend, so the fingerprint would see *nothing* — and sha256sum of an
# empty stream is a constant, so the sentinel would match forever and the
# install would be skipped no matter what package.json did.
REPO="$ROOT/gamma-repo"
mkdir -p "$REPO/desk"
echo '{"name":"gamma"}' > "$REPO/package.json"
echo '# yarn lockfile v1' > "$REPO/yarn.lock"
echo '{"name":"gamma-ui"}' > "$REPO/desk/package.json"
echo '# yarn lockfile v1' > "$REPO/desk/yarn.lock"
ln -s "$REPO" "$BENCH/apps/gamma"

before=$(calls)
"$TOOL" "$BENCH" gamma > /dev/null 2>&1
check_eq "the first install runs" "$((before + 1))" "$(calls)"
check "in the repository the symlink points at" grep -qxF "$BENCH/apps/gamma" "$YARN_CALLS"
before=$(calls)
"$TOOL" "$BENCH" gamma > /dev/null 2>&1
check_eq "an unchanged app is skipped" "$before" "$(calls)"
echo '{"name":"gamma-ui","dependencies":{"@framework/ui":"link:x"}}' > "$REPO/desk/package.json"
"$TOOL" "$BENCH" gamma > /dev/null 2>&1
check_eq "a change through the symlink still triggers a reinstall" "$((before + 1))" "$(calls)"

# The materialized bench lives *inside* the app repository, so following the
# symlink walks straight back into a full copy of frappe. Pruned by name.
mkdir -p "$REPO/.frappe-nix/bench/apps/frappe"
echo '{"name":"frappe"}' > "$REPO/.frappe-nix/bench/apps/frappe/package.json"
echo '# yarn lockfile v1' > "$REPO/.frappe-nix/bench/apps/frappe/yarn.lock"
before=$(calls)
"$TOOL" "$BENCH" gamma > /dev/null 2>&1
check_eq "the generated bench under the app is not part of its fingerprint" "$before" "$(calls)"

echo "── usage ────────────────────────────────────────────────────────"
check_not "no arguments is an error" "$TOOL"
check_not "a bench root with no apps is an error" "$TOOL" "$BENCH"

echo ""
if [ "$fails" -eq 0 ]; then
  echo "All node-modules checks passed."
else
  echo "$fails check(s) failed."
  exit 1
fi
