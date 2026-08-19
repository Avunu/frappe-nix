#!/usr/bin/env bash
# End-to-end check for `frappe-init --migrate` over a synthetic classic bench.
#
# Usage: migrate-classic.sh <path-to-frappe-init> <path-to-make-classic-bench.sh>
#
# Offline by construction: the fixture's "remotes" are local bare repos and the
# migrator runs with --skip-lock, so this works inside the Nix build sandbox.
set -euo pipefail

FRAPPE_INIT="$1"
MAKE_BENCH="$2"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

export HOME="$ROOT/home"
mkdir -p "$HOME"
export GIT_CONFIG_GLOBAL="$ROOT/gitconfig"
export GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
git config --global init.defaultBranch version-15
git config --global protocol.file.allow always

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

# ── fixtures ──────────────────────────────────────────────────────────────
bash "$MAKE_BENCH" "$ROOT/fx" > /dev/null 2>&1
cp -a "$ROOT/fx/bench" "$ROOT/dry"

cd "$ROOT/fx/bench"
FRAPPE_SHA="$(git -C apps/frappe rev-parse HEAD)"
ERPNEXT_SHA="$(git -C apps/erpnext rev-parse HEAD)"

echo "── migrating ────────────────────────────────────────────────"
"$FRAPPE_INIT" --migrate --yes --skip-lock --allow-file-remotes . > "$ROOT/run1.log" 2>&1 ||
  { cat "$ROOT/run1.log"; echo "migration failed"; exit 1; }

echo "── assertions ───────────────────────────────────────────────"

# Preset detection drives the python/node pins in the generated wrapper.
check "flake.nix pins python312 (version-15 preset)" grep -q 'pkgs.python312' flake.nix
check "flake.nix pins nodejs_20" grep -q 'pkgs.nodejs_20' flake.nix
check "flake.nix is a frappe-nix wrapper" grep -q 'frappe-nix.flakeModules.default' flake.nix

# Submodules are pinned at the app's own HEAD, not moved to a branch tip.
check_eq "apps/frappe gitlink == its HEAD" \
  "160000 $FRAPPE_SHA 0	apps/frappe" "$(git ls-files -s apps/frappe)"
check_eq "apps/erpnext gitlink == its HEAD (was detached)" \
  "160000 $ERPNEXT_SHA 0	apps/erpnext" "$(git ls-files -s apps/erpnext)"

# lib/scripts.nix's `bench-update --pull` skips any submodule with no branch.
check_eq "apps/frappe records a branch" version-15 \
  "$(git config -f .gitmodules submodule.apps/frappe.branch)"
check_eq "apps/erpnext records a branch despite detached HEAD" version-15 \
  "$(git config -f .gitmodules submodule.apps/erpnext.branch)"
check_eq "apps/frappe records shallow" true \
  "$(git config -f .gitmodules submodule.apps/frappe.shallow)"

# `git submodule add --force` invents `apps/frappeN` when a name is in use; a
# second run must repair in place rather than register a duplicate.
check_eq "no duplicate submodule names" "" \
  "$(git config -f .gitmodules --name-only --get-regexp '^submodule\.apps/[a-z]+[0-9]' || true)"

# The `upstream`-only remote gets an origin alias, because bench-update fetches
# from origin.
check "apps/erpnext gained an origin alias" git -C apps/erpnext remote get-url origin

# The half-converted case: an already-registered submodule with an absorbed
# gitdir (.git is a gitfile) must be repaired in place, not skipped, and its
# stale .gitmodules branch corrected.
check "apps/hrms .git is a gitfile (absorbed submodule)" test -f apps/hrms/.git
check_eq "apps/hrms stale branch is corrected" version-15 \
  "$(git config -f .gitmodules submodule.apps/hrms.branch)"
check "apps/hrms is a workspace member" \
  grep -q '"apps/hrms"' pyproject.toml
check_eq "apps/hrms contributes only a gitlink" 1 "$(git ls-files apps/hrms | wc -l)"

# Vendoring: history preserved, source tracked.
check "localapp .git moved to the backup" test -f .frappe-nix-backup/localapp.git/HEAD

# A committed site_config.json holds the site's encryption key, db password and
# object-storage credentials. The managed .gitignore block excludes the path,
# but git keeps honouring an index entry regardless — so the exclusion alone
# changes nothing and the migrator has to say so.
check "the migrator warns about a tracked site_config.json" \
  grep -q 'sites/mysite.local/site_config.json is tracked by git' "$ROOT/run1.log"
check "…and names the fix" grep -q 'git rm --cached' "$ROOT/run1.log"
# Never fixed for you: `git rm --cached` stages a deletion of a file the bench
# is actively reading, and the migrator's contract is that it never deletes.
check "…but does not untrack it itself" \
  git ls-files --error-unmatch -- sites/mysite.local/site_config.json
check "…and leaves the file on disk" test -f sites/mysite.local/site_config.json
check "localapp provenance recorded" \
  jq -e '.commit != "" and .branch == "main"' .frappe-nix-backup/localapp.json
check "localapp hooks.py is tracked" \
  git ls-files --error-unmatch apps/localapp/localapp/hooks.py
# The one that a `**/public/*` ignore rule silently drops.
check "localapp public/js asset is tracked" \
  git ls-files --error-unmatch apps/localapp/localapp/public/js/x.js
check_eq "apps/frappe contributes only a gitlink (submodule, not vendored)" 1 \
  "$(git ls-files apps/frappe | wc -l)"

# setup.py-only app: vendored, so a shim is safe and it becomes a member.
check "legacyapp got a pyproject.toml shim" test -f apps/legacyapp/pyproject.toml
check "the shim lifted requirements.txt" \
  grep -q 'requests' apps/legacyapp/pyproject.toml
check "the shim is tracked" git ls-files --error-unmatch apps/legacyapp/pyproject.toml

if python3 - <<'PY'
import sys, tomllib
d = tomllib.load(open("pyproject.toml", "rb"))
uv = d["tool"]["uv"]
members = uv["workspace"]["members"]
bad = []
for want in ("apps/frappe", "apps/erpnext", "apps/localapp", "apps/legacyapp"):
    if want not in members:
        bad.append(f"{want} missing from members")
if uv.get("package") is not False:
    bad.append("[tool.uv].package is not false")
for app in ("frappe", "erpnext", "localapp", "legacyapp"):
    if d["tool"]["uv"]["sources"].get(app, {}).get("workspace") is not True:
        bad.append(f"[tool.uv.sources].{app} missing")
if d["project"]["requires-python"] != ">=3.12":
    bad.append("requires-python not from the preset")
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
then ok "pyproject.toml workspace is complete"; else no "pyproject.toml workspace"; fi

# common_site_config: forced where the runtime demands it, preserved otherwise.
#
# The redis URLs are dropped rather than rewritten: in the dev shell Redis is on
# a unix socket under $DEVENV_RUNTIME, whose path is a hash of the project
# directory and so cannot be committed. FRAPPE_REDIS_* carries it instead.
check "bench-init's redis_queue is dropped" \
  jq -e 'has("redis_queue") | not' sites/common_site_config.json
check "redis_cache is dropped" \
  jq -e 'has("redis_cache") | not' sites/common_site_config.json
check "redis_socketio is dropped (nothing reads it any more)" \
  jq -e 'has("redis_socketio") | not' sites/common_site_config.json
check "file_watcher_port is dropped (nothing binds it)" \
  jq -e 'has("file_watcher_port") | not' sites/common_site_config.json
check "db_host is dropped in favour of the socket" \
  jq -e 'has("db_host") | not' sites/common_site_config.json
check "use_redis_auth forced off" \
  jq -e '.use_redis_auth == false' sites/common_site_config.json

# Ports are per-bench so several benches can run at once, and derived from the
# bench *name* so every clone of one bench agrees — otherwise this committed
# file would carry a machine-specific value and never stop conflicting.
# 8662 is 8000 + sha256("bench")[0:4] % 900; keep in step with frappe_web_port
# in lib/sh/template.sh and portOffsetFor in modules/devenv.nix.
check "webserver_port is derived from the bench name" \
  jq -e '.webserver_port == 8662' sites/common_site_config.json
check "socketio_port matches it, so the browser reaches nginx at one origin" \
  jq -e '.socketio_port == .webserver_port' sites/common_site_config.json
check "default_site preserved" \
  jq -e '.default_site == "mysite.local"' sites/common_site_config.json
check "an unknown user key is preserved" \
  jq -e '.workers.shipstation.timeout == 8000' sites/common_site_config.json
check "gunicorn_workers not clobbered by the default" \
  jq -e '.gunicorn_workers == 17' sites/common_site_config.json
check "mariadb_root_password scrubbed before commit" \
  jq -e '.mariadb_root_password == ""' sites/common_site_config.json
check "production host_name dropped" \
  jq -e 'has("host_name") | not' sites/common_site_config.json
check "production http_port dropped" \
  jq -e 'has("http_port") | not' sites/common_site_config.json
check "the original config was backed up" test -f .frappe-nix-backup/common_site_config.json.orig

# Legacy artifacts: on disk, out of git.
check "Procfile still on disk" test -f Procfile
check "config/redis_cache.conf still on disk" test -f config/redis_cache.conf
for p in Procfile patches.txt config/redis_cache.conf .frappe-nix-backup; do
  check "$p is gitignored" git check-ignore -q "$p"
done
# The .gitignore rule covers site_config.json — but this fixture has it
# committed, the way a classic bench routinely does, and `git check-ignore`
# without --no-index deliberately reports a tracked file as *not* ignored.
# That is the whole reason audit_tracked_site_configs exists: adding the rule
# changes nothing until the file is removed from the index, and nothing else
# would ever tell you.
check "the .gitignore rule covers site_config.json" \
  git check-ignore -q --no-index sites/mysite.local/site_config.json
check "…but it is still tracked, so the rule is inert for it" \
  git ls-files --error-unmatch -- sites/mysite.local/site_config.json
check "site private files are gitignored" \
  git check-ignore -q sites/mysite.local/private/files/big.bin
# Everything else under the site stays out; only the pre-existing tracked file
# remains, and the migrator never deletes.
check_eq "no new site data reached the index" "sites/mysite.local/site_config.json" \
  "$(git ls-files sites/mysite.local)"
check_eq "no node_modules reached the index" "" "$(git ls-files -- '*node_modules*')"

# env/ is a real directory in a classic bench, and `ln -sfn` cannot replace one.
check "env/ moved out of the bench root" test '!' -e env
check "the classic virtualenv is recoverable" test -x .frappe-nix-backup/env/bin/python

# apps.txt: union, frappe first, existing order kept, no missing trailing newline.
check_eq "sites/apps.txt union preserves order and adds new apps" \
  "frappe
erpnext
hrms
legacyapp
localapp" "$(cat sites/apps.txt)"

echo "── idempotency ──────────────────────────────────────────────"
git status --porcelain > "$ROOT/status.1"
md5sum .gitignore .gitmodules pyproject.toml sites/apps.txt sites/common_site_config.json \
  > "$ROOT/sums.1"
"$FRAPPE_INIT" --migrate --yes --skip-lock --allow-file-remotes . > "$ROOT/run2.log" 2>&1 ||
  { cat "$ROOT/run2.log"; echo "second run failed"; exit 1; }
git status --porcelain > "$ROOT/status.2"
check "a second run changes no tracked content" md5sum -c "$ROOT/sums.1"
check "a second run changes no git status" diff -q "$ROOT/status.1" "$ROOT/status.2"

echo "── --dry-run ────────────────────────────────────────────────"
cd "$ROOT/dry"
(find . -not -path '*/.git/*' -not -name .git | sort) > "$ROOT/tree.1"
git status --porcelain > "$ROOT/drystatus.1"
"$FRAPPE_INIT" --migrate --dry-run --allow-file-remotes . > "$ROOT/dry.log" 2>&1
(find . -not -path '*/.git/*' -not -name .git | sort) > "$ROOT/tree.2"
git status --porcelain > "$ROOT/drystatus.2"
check "--dry-run leaves the tree untouched" diff -q "$ROOT/tree.1" "$ROOT/tree.2"
check "--dry-run does not create flake.nix" test '!' -e flake.nix
check "--dry-run stages nothing" diff -q "$ROOT/drystatus.1" "$ROOT/drystatus.2"
check "--dry-run does not rewrite the config" \
  jq -e '.redis_queue == "redis://localhost:11000"' sites/common_site_config.json
check "--dry-run does not renumber the ports" \
  jq -e '.webserver_port == 8000' sites/common_site_config.json

echo "── guard rails ──────────────────────────────────────────────"
mkdir -p "$ROOT/junk" && echo x > "$ROOT/junk/a"
if "$FRAPPE_INIT" --migrate --yes "$ROOT/junk" > /dev/null 2>&1; then
  no "a non-bench directory is refused"
else
  ok "a non-bench directory is refused"
fi
cd "$ROOT/dry"
if "$FRAPPE_INIT" --migrate --skip-lock --allow-file-remotes . > /dev/null 2>&1; then
  no "migrating without --yes in a non-TTY is refused"
else
  ok "migrating without --yes in a non-TTY is refused"
fi
# Without --allow-file-remotes an app whose remote is a filesystem path is not
# refused — it is vendored, because recording that URL in .gitmodules would
# produce a bench that only clones on this machine.
if "$FRAPPE_INIT" --migrate --yes --skip-lock --no-vendor . > /dev/null 2>&1; then
  no "--no-vendor refuses an app with an unusable remote"
else
  ok "--no-vendor refuses an app with an unusable remote"
fi
if "$FRAPPE_INIT" --migrate --yes --skip-lock . > "$ROOT/novendorflag.log" 2>&1; then
  # frappe/erpnext have filesystem remotes → vendored (source tracked, and no
  # unusable URL written to .gitmodules). hrms was already a submodule with a
  # file:// URL, so it stays one.
  if git ls-files --error-unmatch apps/frappe/frappe/hooks.py > /dev/null 2>&1 &&
    ! git config -f .gitmodules --get submodule.apps/frappe.url > /dev/null 2>&1; then
    ok "a filesystem remote is vendored, not recorded as a submodule URL"
  else
    no "a filesystem remote is vendored, not recorded as a submodule URL"
  fi
else
  no "migrating with filesystem remotes (vendoring them) succeeds"
fi

echo "── bench named after its own app ────────────────────────────"
# uv refuses a workspace whose root shares a name with a member, and a bench
# directory named after its main app is a common real-world layout.
mkdir -p "$ROOT/foo/apps/foo/foo" "$ROOT/foo/sites"
printf '__version__ = "15.1.0"\n' > "$ROOT/foo/apps/foo/foo/__init__.py"
printf 'app_name = "foo"\n' > "$ROOT/foo/apps/foo/foo/hooks.py"
printf '[project]\nname = "foo"\nversion = "1.0"\n' > "$ROOT/foo/apps/foo/pyproject.toml"
mkdir -p "$ROOT/foo/apps/frappe/frappe"
printf '__version__ = "15.42.0"\n' > "$ROOT/foo/apps/frappe/frappe/__init__.py"
printf 'app_name = "frappe"\n' > "$ROOT/foo/apps/frappe/frappe/hooks.py"
printf '[project]\nname = "frappe"\nversion = "15.42.0"\n' > "$ROOT/foo/apps/frappe/pyproject.toml"
printf 'frappe\nfoo\n' > "$ROOT/foo/sites/apps.txt"
printf '{"default_site":"t.localhost"}\n' > "$ROOT/foo/sites/common_site_config.json"
"$FRAPPE_INIT" --migrate --yes --skip-lock "$ROOT/foo" > "$ROOT/foo.log" 2>&1 ||
  { cat "$ROOT/foo.log"; no "migrating a bench named after its app"; }
if grep -q '^name = "foo-bench"' "$ROOT/foo/pyproject.toml"; then
  ok "the workspace root is renamed away from the colliding app"
else
  no "the workspace root is renamed away from the colliding app (got: $(grep -m1 '^name' "$ROOT/foo/pyproject.toml"))"
fi
check "the colliding app is still a member" grep -q '"apps/foo"' "$ROOT/foo/pyproject.toml"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
