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
# YARN_REWRITE_LOCK / YARN_CREATE_LOCK / YARN_DELETE_LOCK — a lockfile path that
#              a postinstall's non-frozen nested `yarn install` rewrites, writes
#              where upstream ships none, or removes. Ahead of YARN_FAIL: a
#              failed install can have touched a lock first.
BIN="$ROOT/bin"
mkdir -p "$BIN"
# Not `#!/usr/bin/env bash`: this also runs as a Nix check, where the sandbox
# has no /usr/bin/env.
printf '#!%s\n' "$(command -v bash)" > "$BIN/yarn"
cat >> "$BIN/yarn" <<'STUB'
echo "$PWD" >> "$YARN_CALLS"
if [ -n "${YARN_REWRITE_LOCK:-}" ]; then
  printf '# yarn lockfile v1 (rewritten by yarn %s)\n' "$RANDOM" > "$YARN_REWRITE_LOCK"
fi
if [ -n "${YARN_CREATE_LOCK:-}" ]; then
  printf '# yarn lockfile v1 (written by yarn)\n' > "$YARN_CREATE_LOCK"
fi
if [ -n "${YARN_DELETE_LOCK:-}" ]; then
  rm -f "$YARN_DELETE_LOCK"
fi
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

echo "── the app is not checked out ───────────────────────────────────"
# The app list comes from what Nix saw, and `self.submodules = true` has Nix
# fetch every submodule itself — so a fresh clone's still-empty apps/<x> is on
# it. yarn in an empty directory succeeds having done nothing, and the
# node_modules/ it leaves is what `git submodule update --init` then refuses
# to clone into.
mkdir -p "$BENCH/apps/delta"
before=$(calls)
check_not "the tool reports it" "$TOOL" "$BENCH" delta
"$TOOL" "$BENCH" delta > "$ROOT/absent.log" 2>&1 || true
check_eq "yarn is not run" "$before" "$(calls)"
check_eq "and the directory is left empty" "0" "$(find "$BENCH/apps/delta" -mindepth 1 | wc -l | tr -d ' ')"
check "the app is named" grep -qF 'apps/delta' "$ROOT/absent.log"
check_not "a directory that is not there at all is reported too" "$TOOL" "$BENCH" epsilon
check_not "…and not created" test -e "$BENCH/apps/epsilon"
echo '# yarn lockfile v1 (bumped next to an absent app)' > "$BENCH/apps/alpha/yarn.lock"
before=$(calls)
"$TOOL" "$BENCH" alpha delta > /dev/null 2>&1 || true
check_eq "an app that is checked out is still installed alongside" "$((before + 1))" "$(calls)"
check_eq "…and the absent one still left empty" "0" "$(find "$BENCH/apps/delta" -mindepth 1 | wc -l | tr -d ' ')"

echo "── an install leaves the lockfiles as it found them ─────────────"
# The postinstall of most apps runs a non-frozen `yarn install` in each nested
# frontend: it rewrites a tracked lock, or writes one where upstream ships none.
# Those are the app's repository's files, and the app's next `git checkout`
# refuses over a modified one — which is what stopped `bench update`.
# Fresh apps, so the call counts above are not disturbed.
ZETA="$BENCH/apps/zeta"
mkdir -p "$ZETA/desk" "$ZETA/banking" "$ZETA/roster"
echo '{"name":"zeta"}' > "$ZETA/package.json"
echo '# root lock' > "$ZETA/yarn.lock"
echo '{"name":"zeta-desk"}' > "$ZETA/desk/package.json"
echo '# desk lock' > "$ZETA/desk/yarn.lock"
echo '{"name":"zeta-banking"}' > "$ZETA/banking/package.json" # upstream ships no lock
echo '{"name":"zeta-roster"}' > "$ZETA/roster/package.json"
echo '# roster lock' > "$ZETA/roster/yarn.lock"
# What a real app's checkout carries besides: an edited tracked file, an untracked
# one. They are not the install's to touch.
echo 'edited by hand' > "$ZETA/NOTES"
echo 'scratch' > "$ZETA/scratch.txt"
reinstall() { # <log> — as if package.json had moved
  rm -f "$ZETA/node_modules/.frappe-nix-installed"
  "$TOOL" "$BENCH" zeta > "$ROOT/$1.log" 2>&1
}

export YARN_REWRITE_LOCK="$ZETA/desk/yarn.lock"
export YARN_CREATE_LOCK="$ZETA/banking/yarn.lock"
export YARN_DELETE_LOCK="$ZETA/roster/yarn.lock"
before=$(calls)
reinstall lock1 && ok "the install succeeds" || { no "the install succeeds"; cat "$ROOT/lock1.log"; }
check_eq "and yarn ran" "$((before + 1))" "$(calls)"
check_eq "a lock it rewrote is put back" "# desk lock" "$(cat "$ZETA/desk/yarn.lock")"
check_not "a lock it wrote where upstream ships none is removed" test -e "$ZETA/banking/yarn.lock"
check_eq "a lock it deleted is restored" "# roster lock" "$(cat "$ZETA/roster/yarn.lock")"
check_eq "the app's own lock, which it left alone, is untouched" "# root lock" "$(cat "$ZETA/yarn.lock")"
check "each is reported" bash -c "grep -q 'desk/yarn.lock: rewritten' '$ROOT/lock1.log' \
  && grep -q 'banking/yarn.lock: created' '$ROOT/lock1.log' \
  && grep -q 'roster/yarn.lock: deleted' '$ROOT/lock1.log'"
check_eq "an edited tracked file is left alone" "edited by hand" "$(cat "$ZETA/NOTES")"
check_eq "and an untracked one" "scratch" "$(cat "$ZETA/scratch.txt")"
check "node_modules was installed all the same" test -s "$ZETA/node_modules/.frappe-nix-installed"
before=$(calls)
"$TOOL" "$BENCH" zeta > /dev/null 2>&1
check_eq "the sentinel records the restored locks, so the next run is a no-op" "$before" "$(calls)"

echo "  · a lock that was already edited comes back edited"
echo '# desk lock (edited by hand)' > "$ZETA/desk/yarn.lock"
reinstall lock2 || { no "the install succeeds"; cat "$ROOT/lock2.log"; }
check_eq "the edit survives an install that rewrote it" "# desk lock (edited by hand)" "$(cat "$ZETA/desk/yarn.lock")"
echo '# desk lock' > "$ZETA/desk/yarn.lock"

echo "  · an install that fails"
export YARN_FAIL=1
before=$(calls)
check_not "the tool still reports failure" reinstall lock3
check_eq "yarn was attempted" "$((before + 1))" "$(calls)"
check_eq "a lock it rewrote before failing is put back" "# desk lock" "$(cat "$ZETA/desk/yarn.lock")"
check_not "and one it wrote" test -e "$ZETA/banking/yarn.lock"
check_eq "and one it deleted" "# roster lock" "$(cat "$ZETA/roster/yarn.lock")"
unset YARN_FAIL

echo "  · an install that changes no lock says nothing about locks"
unset YARN_REWRITE_LOCK YARN_CREATE_LOCK YARN_DELETE_LOCK
reinstall lock4 || { no "the install succeeds"; cat "$ROOT/lock4.log"; }
check_not "no lock is reported" grep -qE 'put back|removed' "$ROOT/lock4.log"

echo "  · the app is a symlink to its own repository (app mode)"
THETA="$ROOT/theta-repo"
mkdir -p "$THETA/desk" "$THETA/.frappe-nix/bench/apps/frappe"
echo '{"name":"theta"}' > "$THETA/package.json"
echo '# yarn lockfile v1' > "$THETA/yarn.lock"
echo '{"name":"theta-ui"}' > "$THETA/desk/package.json"
echo '# theta desk lock' > "$THETA/desk/yarn.lock"
echo '# generated copy of frappe' > "$THETA/.frappe-nix/bench/apps/frappe/yarn.lock"
ln -s "$THETA" "$BENCH/apps/theta"
export YARN_REWRITE_LOCK="$THETA/desk/yarn.lock"
"$TOOL" "$BENCH" theta > "$ROOT/lock5.log" 2>&1 || { no "the install succeeds"; cat "$ROOT/lock5.log"; }
check_eq "the lock is put back through the symlink" "# theta desk lock" "$(cat "$THETA/desk/yarn.lock")"
# The generated bench inside the repository is not the app's, and is not walked.
rm -f "$THETA/node_modules/.frappe-nix-installed"
export YARN_REWRITE_LOCK="$THETA/.frappe-nix/bench/apps/frappe/yarn.lock"
"$TOOL" "$BENCH" theta > "$ROOT/lock6.log" 2>&1 || { no "the install succeeds"; cat "$ROOT/lock6.log"; }
check "a lock inside the generated bench is not touched" \
  grep -q 'rewritten by yarn' "$THETA/.frappe-nix/bench/apps/frappe/yarn.lock"
unset YARN_REWRITE_LOCK

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
