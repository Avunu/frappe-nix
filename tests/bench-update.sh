#!/usr/bin/env bash
# Checks for `bench-update --pull` over a bench that has one of each: a
# registered submodule, a local app, and a stray nested repository.
#
# Usage: bench-update.sh <path-to-rendered-bench-update-script>
set -euo pipefail

SCRIPT="$1"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
# Submodule operations on file:// URLs are refused by default.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=protocol.file.allow GIT_CONFIG_VALUE_0=always

# ── a stub lock generator ─────────────────────────────────────────────────
# NODE_LOCKS_CALLS — one line per invocation: "<cwd>\t<args>"
# NODE_LOCKS_FAIL  — fail, as an unresolvable manifest would
BIN="$ROOT/bin"
mkdir -p "$BIN"
printf '#!%s\n' "$(command -v bash)" > "$BIN/frappe-nix-node-locks"
cat >> "$BIN/frappe-nix-node-locks" <<'STUB'
printf '%s\t%s\n' "$PWD" "$*" >> "$NODE_LOCKS_CALLS"
if [ "${NODE_LOCKS_FAIL:-}" = "1" ]; then
  echo "  ✗ frappe: npm could not resolve a lock" >&2
  exit 1
fi
mkdir -p node-locks/frappe
echo '{}' > node-locks/frappe/package-lock.json
STUB
chmod +x "$BIN/frappe-nix-node-locks"
export PATH="$BIN:$PATH"
export NODE_LOCKS_CALLS="$ROOT/node-locks-calls"
: > "$NODE_LOCKS_CALLS"
lock_calls() { wc -l < "$NODE_LOCKS_CALLS" | tr -d ' '; }

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
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}

seed_app() { # <dir> <name> <version>
  mkdir -p "$1/$2"
  printf '__version__ = "%s"\n' "$3" > "$1/$2/__init__.py"
  printf 'app_name = "%s"\n' "$2" > "$1/$2/hooks.py"
  printf '[project]\nname = "%s"\ndynamic = ["version"]\n' "$2" > "$1/pyproject.toml"
}

# ── fixture ───────────────────────────────────────────────────────────────
# A bare "upstream" for frappe, two commits ahead of what the bench pins.
mkdir -p "$ROOT/seed"
seed_app "$ROOT/seed/frappe" frappe 16.0.0
git -C "$ROOT/seed/frappe" init -q -b version-16
git -C "$ROOT/seed/frappe" add -A
# Dated well in the past: the self-heal below deepens back to HEAD's own date,
# and a root commit older than that is what leaves the clone shallow afterwards
# — as a real bench's is — rather than fully unshallowed by a tiny fixture.
GIT_COMMITTER_DATE="2020-01-01T00:00:00" GIT_AUTHOR_DATE="2020-01-01T00:00:00" \
  git -C "$ROOT/seed/frappe" commit -q -m "v16.0.0"
PINNED="$(git -C "$ROOT/seed/frappe" rev-parse HEAD)"
git init -q --bare "$ROOT/remotes/frappe.git"
# A bare init points HEAD at init.defaultBranch; a shallow clone needs it on
# the branch that exists.
git -C "$ROOT/remotes/frappe.git" symbolic-ref HEAD refs/heads/version-16
git -C "$ROOT/seed/frappe" remote add origin "$ROOT/remotes/frappe.git"
git -C "$ROOT/seed/frappe" push -q origin version-16

BENCH="$ROOT/bench"
mkdir -p "$BENCH/apps" "$BENCH/sites"
cd "$BENCH"
git init -q -b main
# Shallow, as frappe-init registers them (`shallow = true`): the case where a
# depth-1 fetch used to leave the ancestry check unable to ever pass.
git submodule add -q --depth 1 -b version-16 "file://$ROOT/remotes/frappe.git" apps/frappe
git config -f .gitmodules submodule.apps/frappe.shallow true
# A developer's checkout: origin is a fork that has no version-16 at all, and
# the URL .gitmodules declares is on a remote called upstream. Pulling from
# origin here is the "couldn't find remote ref version-16" failure.
git init -q --bare "$ROOT/remotes/fork.git"
git -C "$ROOT/seed/frappe" push -q "$ROOT/remotes/fork.git" version-16:develop
git -C apps/frappe remote rename origin upstream
git -C apps/frappe remote add origin "file://$ROOT/remotes/fork.git"
# A local app: committed source, no .git of its own.
seed_app apps/localapp localapp 1.0.0
# A stray repo: its own .git, no remote, recorded as a gitlink by `git add`.
seed_app apps/strayapp strayapp 0.1.0
git -C apps/strayapp init -q -b main
git -C apps/strayapp add -A
git -C apps/strayapp commit -q -m "feat: Initialize App"
STRAY_SHA="$(git -C apps/strayapp rev-parse HEAD)"
cat > pyproject.toml <<'TOML'
[project]
name = "fixture-bench"

[tool.uv.workspace]
members = ["apps/frappe", "apps/localapp", "apps/strayapp"]
TOML
git -c advice.addEmbeddedRepo=false add -A
git commit -q -m "bench"

# Now move upstream ahead.
printf '__version__ = "16.1.0"\n' > "$ROOT/seed/frappe/frappe/__init__.py"
git -C "$ROOT/seed/frappe" commit -q -am "v16.1.0"
printf 'x\n' > "$ROOT/seed/frappe/NEW"
git -C "$ROOT/seed/frappe" add -A
git -C "$ROOT/seed/frappe" commit -q -m "more"
git -C "$ROOT/seed/frappe" push -q origin version-16
TIP="$(git -C "$ROOT/seed/frappe" rev-parse HEAD)"

echo "── --pull over a submodule, a local app and a stray repo ──────"
check "git submodule foreach itself dies on the stray gitlink (the bug being fixed)" \
  bash -c "! git submodule foreach true"
FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --pull > "$ROOT/pull.log" 2>&1 \
  && ok "--pull exits 0" || { no "--pull exits 0"; cat "$ROOT/pull.log"; }
check "the pull used the remote that has the .gitmodules URL, not origin" \
  grep -q 'frappe (version-16 from upstream)' "$ROOT/pull.log"
check_eq "the submodule was pulled to the remote tip" "$TIP" "$(git -C apps/frappe rev-parse HEAD)"
check_eq "and left on its branch" "version-16" "$(git -C apps/frappe symbolic-ref --short HEAD)"
check_eq "and still shallow" "true" "$(git -C apps/frappe rev-parse --is-shallow-repository)"
check "the local app is reported as having nothing to pull" \
  grep -q 'localapp: local app' "$ROOT/pull.log"
check "the stray repo is reported, with the fix" \
  grep -q 'strayapp: a git repository that is not a registered submodule' "$ROOT/pull.log"
check "and how to vendor it" grep -q 'frappe-init --migrate' "$ROOT/pull.log"
check_eq "the stray repo is left untouched" "$STRAY_SHA" "$(git -C apps/strayapp rev-parse HEAD)"

echo "── node-locks/ follows the pull ────────────────────────────────"
check_eq "the lock generator ran once, from the bench root, with the exclusion" \
  "$(printf '%s\t--exclude=alpha/desk . node-locks' "$BENCH")" "$(cat "$NODE_LOCKS_CALLS")"
check "and the user is told to commit node-locks/" grep -q 'commit node-locks/' "$ROOT/pull.log"

echo "── the registry follows the pull ───────────────────────────────"
check "sites/apps.json was written" test -f sites/apps.json
check_eq "it records the new frappe commit" "$TIP" "$(jq -r .frappe.resolution.commit_hash sites/apps.json)"
check_eq "and the new version" "16.1.0" "$(jq -r .frappe.version sites/apps.json)"
check_eq "the local app is not a repo" "false" "$(jq -r .localapp.is_repo sites/apps.json)"
check_eq "apps.txt is the members" "frappe localapp strayapp" "$(xargs < sites/apps.txt)"
check "the user is told to commit it" grep -q 'commit sites/apps.json' "$ROOT/pull.log"

echo "── --node-locks ────────────────────────────────────────────────"
: > "$NODE_LOCKS_CALLS"
FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --node-locks > "$ROOT/locks.log" 2>&1 \
  && ok "--node-locks exits 0" || { no "--node-locks exits 0"; cat "$ROOT/locks.log"; }
check_eq "it runs the generator over the whole bench" "1" "$(lock_calls)"
check "and nothing else" bash -c "! grep -q 'Pulling latest' '$ROOT/locks.log'"
if NODE_LOCKS_FAIL=1 FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --node-locks > "$ROOT/locks-fail.log" 2>&1; then
  no "a generator failure fails --node-locks"
else
  ok "a generator failure fails --node-locks"
fi
NODE_LOCKS_FAIL=1 FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --pull > "$ROOT/pull-lockfail.log" 2>&1 \
  && ok "but only warns during --pull" || { no "but only warns during --pull"; cat "$ROOT/pull-lockfail.log"; }
check "…and says how to retry" grep -q 'nix build keeps the previous locks' "$ROOT/pull-lockfail.log"
: > "$NODE_LOCKS_CALLS"
FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --node-hashes > "$ROOT/hashes.log" 2>&1 \
  && ok "--node-hashes still works" || { no "--node-hashes still works"; cat "$ROOT/hashes.log"; }
check "…as an alias that says so" grep -q 'now --node-locks' "$ROOT/hashes.log"
check_eq "…and runs the generator" "1" "$(lock_calls)"

echo "── a pull with nothing to pull ─────────────────────────────────"
FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --pull > "$ROOT/pull2.log" 2>&1 \
  && ok "a second --pull exits 0" || { no "a second --pull exits 0"; cat "$ROOT/pull2.log"; }
check_eq "the submodule is still at the tip" "$TIP" "$(git -C apps/frappe rev-parse HEAD)"

echo "── a clone grafted by the old --depth 1 fetch heals itself ──────"
# bench-update fetched with --depth 1 until 2026-09-11. On a shallow clone that
# grafts the new tip with no parents: HEAD and the tip become two islands with
# no path between them, `merge-base --is-ancestor` can never pass, and every
# later pull skipped the app as having "local or unpushed commits" — forever,
# since a plain fetch never asks for the gap. Reproduce the shape exactly.
for i in 1 2 3; do
  printf '%s\n' "$i" > "$ROOT/seed/frappe/GAP$i"
  git -C "$ROOT/seed/frappe" add -A
  git -C "$ROOT/seed/frappe" commit -q -m "gap $i"
done
git -C "$ROOT/seed/frappe" push -q origin version-16
GRAFTED_TIP="$(git -C "$ROOT/seed/frappe" rev-parse HEAD)"
git -C apps/frappe fetch -q --depth 1 upstream version-16
check "fixture: HEAD and the fetched tip share no history locally" \
  bash -c "[ -z \"\$(git -C apps/frappe merge-base HEAD FETCH_HEAD)\" ]"
# HEAD keeps the history the earlier pull connected under it, so the phantom
# here is "4 ahead" rather than a real bench's "1 ahead"; the heal must not
# depend on HEAD being a graft itself.
check "fixture: git itself reports upstream's own commits as 'ahead' (the phantom)" \
  bash -c "[ \"\$(git -C apps/frappe rev-list --count FETCH_HEAD..HEAD)\" -gt 0 ]"
FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --pull > "$ROOT/pull-graft.log" 2>&1 \
  && ok "--pull exits 0" || { no "--pull exits 0"; cat "$ROOT/pull-graft.log"; }
check "it noticed the shape and deepened" grep -q 'no path between HEAD' "$ROOT/pull-graft.log"
check "and did not mistake it for local commits" \
  bash -c "! grep -q 'HEAD is not an ancestor' '$ROOT/pull-graft.log'"
check_eq "the submodule reached the tip" "$GRAFTED_TIP" "$(git -C apps/frappe rev-parse HEAD)"
check_eq "and is still shallow (only the gap was fetched)" "true" \
  "$(git -C apps/frappe rev-parse --is-shallow-repository)"
check "and the pin is now an ancestor of the tip, so the phantom is gone" \
  git -C apps/frappe merge-base --is-ancestor "$TIP" HEAD

echo "── local commits are protected ─────────────────────────────────"
printf 'mine\n' > apps/frappe/LOCAL
git -C apps/frappe add LOCAL
git -C apps/frappe commit -q -m "local work"
MINE="$(git -C apps/frappe rev-parse HEAD)"
printf 'y\n' > "$ROOT/seed/frappe/NEW2"
git -C "$ROOT/seed/frappe" add -A
git -C "$ROOT/seed/frappe" commit -q -m "even more"
git -C "$ROOT/seed/frappe" push -q origin version-16
FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --pull > "$ROOT/pull3.log" 2>&1 \
  && ok "a pull over local commits exits 0" || { no "a pull over local commits exits 0"; cat "$ROOT/pull3.log"; }
check "the divergence is reported" grep -q 'HEAD is not an ancestor' "$ROOT/pull3.log"
check_eq "and the local commit survives" "$MINE" "$(git -C apps/frappe rev-parse HEAD)"
# A real local commit shares its parent with the tip, so the clone is not
# deepened for it — that would re-graft the remote branch at the commit's date.
check "a real divergence on a shallow clone is not deepened" \
  bash -c "! grep -q 'deepening' '$ROOT/pull3.log'"
check "and the shallow hint is given" grep -q 'git fetch --unshallow' "$ROOT/pull3.log"

echo "── no remote carries the declared URL ──────────────────────────"
git -C apps/frappe reset -q --hard "$GRAFTED_TIP"
git -C apps/frappe remote set-url upstream "$ROOT/remotes/fork.git"
printf 'z\n' > "$ROOT/seed/frappe/NEW3"
git -C "$ROOT/seed/frappe" add -A
git -C "$ROOT/seed/frappe" commit -q -m "further"
git -C "$ROOT/seed/frappe" push -q origin version-16
TIP3="$(git -C "$ROOT/seed/frappe" rev-parse HEAD)"
FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --pull > "$ROOT/pull4.log" 2>&1 \
  && ok "--pull exits 0" || { no "--pull exits 0"; cat "$ROOT/pull4.log"; }
check "the URL is fetched directly, and says so" grep -q 'fetching it directly' "$ROOT/pull4.log"
check_eq "and the submodule reaches the tip" "$TIP3" "$(git -C apps/frappe rev-parse HEAD)"

echo "── a branch the declared URL does not have ─────────────────────"
git config -f .gitmodules submodule.apps/frappe.branch nope
if FRAPPE_BENCH_ROOT="$BENCH" bash "$SCRIPT" --pull > "$ROOT/pull5.log" 2>&1; then
  no "--pull fails"
else
  ok "--pull fails"
fi
check "and names the app, branch and remote" \
  grep -q "frappe: could not fetch 'nope' from" "$ROOT/pull5.log"
check "and points at the .gitmodules entry" grep -q 'submodule.apps/frappe.branch' "$ROOT/pull5.log"
git config -f .gitmodules submodule.apps/frappe.branch version-16

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
