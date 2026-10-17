#!/usr/bin/env bash
# frappe-init's modes, flags and exit codes, where tests/migrate-classic.sh and
# tests/migrate-versions.sh do not reach: scaffolding a bench (the plan, the
# seeds and the next steps), the mode guards and their documented exit codes
# (docs/reference/scaffolder.md), and the migration flags the end-to-end run
# leaves at their defaults.
#
# Usage: scaffold-modes.sh <path-to-frappe-init> <path-to-make-classic-bench.sh>
#
# Offline: frappe-init's GitHub URLs are rewritten to the fixture's bare
# remotes (url.<base>.insteadOf), and every run passes --skip-lock.
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
git config --global init.defaultBranch main
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
# Runs frappe-init with stdin closed off from any terminal, its output in $OUT.
OUT="$ROOT/out"
run() {
  local rc=0
  "$FRAPPE_INIT" "$@" < /dev/null > "$OUT" 2>&1 || rc=$?
  echo "$rc"
}
# <description> <expected exit code> <message fragment> <frappe-init args…>
expect_exit() {
  local desc=$1 want=$2 msg=$3 rc
  shift 3
  rc="$(run "$@")"
  if [ "$rc" != "$want" ]; then
    no "$desc (exit $rc, not $want: $(tail -n 3 "$OUT" | tr '\n' ' '))"
  elif [ -n "$msg" ] && ! grep -qF -- "$msg" "$OUT"; then
    no "$desc (exit $rc, but no '$msg' in: $(tail -n 3 "$OUT" | tr '\n' ' '))"
  else
    ok "$desc (exit $rc)"
  fi
}
says() { grep -qF -- "$1" "$OUT"; }

# A minimal bench: frappe plus one app, apps.txt and a common_site_config.json.
# <dir> [default_site, or - for none]
small_bench() {
  local dir=$1 site=${2:-t.localhost} app
  for app in frappe foo; do
    mkdir -p "$dir/apps/$app/$app"
    printf '__version__ = "15.1.0"\n' > "$dir/apps/$app/$app/__init__.py"
    printf 'app_name = "%s"\n' "$app" > "$dir/apps/$app/$app/hooks.py"
    printf '[project]\nname = "%s"\nversion = "15.1.0"\n' "$app" > "$dir/apps/$app/pyproject.toml"
  done
  mkdir -p "$dir/sites"
  printf 'frappe\nfoo\n' > "$dir/sites/apps.txt"
  if [ "$site" = - ]; then
    printf '{}\n' > "$dir/sites/common_site_config.json"
  else
    printf '{"default_site":"%s"}\n' "$site" > "$dir/sites/common_site_config.json"
  fi
}

# ── fixtures ──────────────────────────────────────────────────────────────
bash "$MAKE_BENCH" "$ROOT/fx" > /dev/null 2>&1
for app in frappe erpnext hrms; do
  git -C "$ROOT/fx/remotes/$app.git" config uploadpack.allowFilter true
  git config --global "url.file://$ROOT/fx/remotes/$app.git.insteadOf" "https://github.com/frappe/$app.git"
done

echo "── the command line ─────────────────────────────────────────"
expect_exit "--help" 0 "Usage: frappe-init" --help
says "--dry-run" && ok "--help lists the flags" || no "--help lists the flags"
expect_exit "an unknown flag" 1 "unknown flag: --bogus" --bogus
expect_exit "a value flag at the end of the line" 1 "--name needs a value" --init --name
expect_exit "a value flag at the end of the line, under --check" 2 "--format needs a value" --check --format

echo "── renamed flags ────────────────────────────────────────────"
# CONVENTIONS.md, Scaffolder: --version and --default-site; the old names are
# read, with a warning, until the next minor release.
expect_exit "--frappe-version is still read" 0 "scaffold bench 'old' (version-16" \
  --init --dry-run --frappe-version version-16 --name old "$ROOT/old"
says "--frappe-version is now --version" && ok "…with a warning" || no "…with a warning: $(cat "$OUT")"
expect_exit "--site is still read" 0 "default site   : old.local" \
  --init --dry-run --version version-16 --site old.local "$ROOT/old"
says "--site is now --default-site" && ok "…with a warning" || no "…with a warning: $(cat "$OUT")"
expect_exit "--version=<v>" 0 "(version-15 → python 3.12 / node 20)" --init --dry-run --version=version-15 "$ROOT/old"
expect_exit "--version with no value" 1 "--version needs a value" --init --version
expect_exit "--help names the new flags" 0 "--default-site <site>" --help
says "--frappe-version" && no "--help does not name the old ones" || ok "--help does not name the old ones"

echo "── scaffold: refusals ───────────────────────────────────────"
mkdir -p "$ROOT/full" && echo x > "$ROOT/full/a"
expect_exit "--init into a directory that is not empty" 2 "is not empty" --init "$ROOT/full"
expect_exit "--init without a version in a non-TTY" 5 "is required" --init "$ROOT/nv"
check "…creates nothing" test '!' -e "$ROOT/nv"
expect_exit "--init with an unknown version" 5 "unknown frappe version 'version-9'" \
  --init --version version-9 "$ROOT/nv"
expect_exit "a target that is not empty and is no bench or app" 6 "is not a Frappe bench" "$ROOT/full"

echo "── scaffold: --dry-run ──────────────────────────────────────"
expect_exit "--init --dry-run" 0 "--dry-run: nothing was changed." \
  --init --dry-run --version version-16 --name 'My_Bench!' --apps erpnext,acme/widgets "$ROOT/dry"
says "scaffold bench 'my-bench' (version-16 → python 3.14 / node 24)" &&
  ok "the plan names the normalised bench, the preset's python and node" ||
  no "the plan names the normalised bench, the preset's python and node: $(cat "$OUT")"
says "apps           : frappe erpnext widgets" && ok "the plan lists the apps by repository name" ||
  no "the plan lists the apps by repository name: $(cat "$OUT")"
says "default site   : frappe.localhost" && ok "a new bench's default site is frappe.localhost" ||
  no "a new bench's default site is frappe.localhost"
check "--dry-run creates nothing" test '!' -e "$ROOT/dry"

mkdir -p "$ROOT/cwd" && cd "$ROOT/cwd"
expect_exit "no target, an empty directory: scaffold into ./frappe-bench" 0 "in frappe-bench" \
  --dry-run --version version-15
says "scaffold bench 'frappe-bench'" && ok "…named after it" || no "…named after it: $(cat "$OUT")"
cd "$ROOT"

echo "── scaffold: a new bench ────────────────────────────────────"
expect_exit "--init a bench with an app" 0 "Next steps:" \
  --init --skip-lock --allow-file-remotes --version version-15 --name acme --default-site acme.localhost \
  --apps hrms "$ROOT/new"
cp "$OUT" "$ROOT/new.log"
cd "$ROOT/new"
check "flake.nix is the wrapper, named for the bench" grep -q 'benchName = "acme";' flake.nix
check "flake.nix names the default site" grep -q 'siteName = "acme.localhost";' flake.nix
check "flake.nix pins the preset's python" grep -q 'pkgs.python312' flake.nix
check "pyproject.toml is the workspace root" grep -q '^name = "acme"' pyproject.toml
check "README.md is the bench's" grep -qx '# acme' README.md
check ".envrc uses the flake" grep -q 'use flake' .envrc
check "secrets/README.md is seeded" grep -q 'age-encrypted credentials for this bench' secrets/README.md
check "the .gitignore block is installed" grep -qF '# >>> frappe-nix >>>' .gitignore
check ".frappe-nix-backup/README.md is seeded" test -f .frappe-nix-backup/README.md
check_eq "common_site_config.json: default_site" acme.localhost "$(jq -r .default_site sites/common_site_config.json)"
# 8000 + sha256("acme")[0:4] % 900, as portOffsetFor in modules/devenv.nix.
check_eq "common_site_config.json: the bench's own port" \
  "$((8000 + 0x$(printf acme | sha256sum | cut -c1-4) % 900))" "$(jq -r .webserver_port sites/common_site_config.json)"
check_eq "sites/apps.txt" "frappe
hrms" "$(cat sites/apps.txt)"
check "apps/hrms is a registered submodule" git config -f .gitmodules submodule.apps/hrms.url
check "everything is staged" git ls-files --error-unmatch flake.nix secrets/README.md sites/apps.json
check "nothing is committed" test -z "$(git rev-list --all 2> /dev/null)"
OUT="$ROOT/new.log"
says "Next steps:" && says "git diff --cached --stat" && says "direnv allow" && says "devenv up" &&
  ok "the next steps" || no "the next steps: $(cat "$OUT")"
says "provision-site               # (in another shell) create acme.localhost + install apps" &&
  ok "…name provision-site for a bench without secrets" || no "…name provision-site for a bench without secrets"
says "To restore this bench from production backups, declare its secrets" &&
  ok "…and say how to declare them, though the template mentions them in a comment" ||
  no "…and say how to declare them, though the template mentions them in a comment"
OUT="$ROOT/out"

expect_exit "a frappe-nix bench is reconciled (repair mode)" 0 "already a frappe-nix bench" --dry-run .
says "frappe version : version-15" && ok "…at its own version" || no "…at its own version: $(cat "$OUT")"
says "bench name     : acme" && ok "…under the name its flake.nix gives it" ||
  no "…under the name its flake.nix gives it: $(cat "$OUT")"
cd "$ROOT"

echo "── migrate: refusals ────────────────────────────────────────"
expect_exit "--migrate on a missing directory" 1 "nothing to migrate" --migrate "$ROOT/missing"
expect_exit "--app on a missing directory" 1 "app mode sets up an existing" --app "$ROOT/missing"
expect_exit "--migrate on a directory that is no bench" 6 "does not look like a Frappe bench" --migrate --yes "$ROOT/full"
mkdir -p "$ROOT/skel/apps" "$ROOT/skel/sites"
expect_exit "a bench skeleton with no app in it" 7 "Nothing to migrate" "$ROOT/skel"

small_bench "$ROOT/foreign"
printf '{ outputs = _: { }; }\n' > "$ROOT/foreign/flake.nix"
expect_exit "a bench with a flake.nix of its own" 4 "is not a frappe-nix wrapper" "$ROOT/foreign"
git -C "$ROOT/foreign" init -q
expect_exit "--migrate --force writes the wrapper beside it" 0 "flake.nix.frappe-nix" \
  --migrate --force --yes --skip-lock "$ROOT/foreign"
check "…the bench's own flake.nix is left alone" grep -qx '{ outputs = _: { }; }' "$ROOT/foreign/flake.nix"
check "…and the wrapper is flake.nix.frappe-nix" grep -q 'frappe-nix.flakeModules.default' "$ROOT/foreign/flake.nix.frappe-nix"

mkdir -p "$ROOT/outer"
git -C "$ROOT/outer" init -q
small_bench "$ROOT/outer/bench"
expect_exit "a bench inside another repository" 8 "inside another git repository" --migrate --yes --dry-run "$ROOT/outer/bench"
expect_exit "…which --force lets through" 0 "--dry-run: nothing was changed." \
  --migrate --yes --dry-run --force "$ROOT/outer/bench"

small_bench "$ROOT/plain"
expect_exit "migrating without --yes in a non-TTY" 6 "aborted" --migrate --skip-lock "$ROOT/plain"
check "…changes nothing" test '!' -e "$ROOT/plain/flake.nix"

echo "── migrate: the default site ────────────────────────────────"
small_bench "$ROOT/sites1" -
mkdir -p "$ROOT/sites1/sites/only.local"
printf '{}\n' > "$ROOT/sites1/sites/only.local/site_config.json"
expect_exit "no default_site and one site" 0 "default site   : only.local" --migrate --dry-run "$ROOT/sites1"
small_bench "$ROOT/sites2" -
mkdir -p "$ROOT/sites2/sites/a.local" "$ROOT/sites2/sites/b.local"
printf '{}\n' > "$ROOT/sites2/sites/a.local/site_config.json"
printf '{}\n' > "$ROOT/sites2/sites/b.local/site_config.json"
expect_exit "no default_site and several sites, in a non-TTY" 5 "pass --default-site" --migrate --dry-run "$ROOT/sites2"
expect_exit "…which --default-site settles" 0 "default site   : b.local" --migrate --dry-run --default-site b.local "$ROOT/sites2"
small_bench "$ROOT/sites0" -
expect_exit "no default_site and no site" 0 "default site   : frappe.localhost" --migrate --dry-run "$ROOT/sites0"
expect_exit "--default-site over the bench's default_site" 0 "default site   : other.local" \
  --migrate --dry-run --default-site other.local "$ROOT/plain"
expect_exit "--name over the directory's" 0 "bench name     : renamed" --migrate --dry-run --name Renamed "$ROOT/plain"

echo "── migrate: flags ───────────────────────────────────────────"
cp -a "$ROOT/fx/bench" "$ROOT/strict"
expect_exit "--strict refuses a dirty or unpushed app" 1 "--strict: apps/erpnext is dirty or unpushed" \
  --migrate --dry-run --strict --allow-file-remotes "$ROOT/strict"
expect_exit "--vendor forces an app with a remote to be vendored" 0 "forced-vendor" \
  --migrate --dry-run --allow-file-remotes --vendor frappe "$ROOT/strict"
grep -qE '^  frappe +vendor ' "$OUT" && ok "…in the plan" || no "…in the plan: $(cat "$OUT")"

cp -a "$ROOT/fx/bench" "$ROOT/abort"
expect_exit "--legacy-apps abort refuses a setup.py-only app" 1 "--legacy-apps=abort" \
  --migrate --yes --skip-lock --allow-file-remotes --legacy-apps abort "$ROOT/abort"
cp -a "$ROOT/fx/bench" "$ROOT/badpolicy"
expect_exit "an unknown --legacy-apps policy" 1 "unknown --legacy-apps policy 'bogus'" \
  --migrate --yes --skip-lock --allow-file-remotes --legacy-apps bogus "$ROOT/badpolicy"
cp -a "$ROOT/fx/bench" "$ROOT/skiplegacy"
expect_exit "--legacy-apps skip leaves a setup.py-only app out of the workspace" 0 "not a uv workspace member" \
  --migrate --yes --skip-lock --allow-file-remotes --legacy-apps skip "$ROOT/skiplegacy"
check "…with no shim" test '!' -e "$ROOT/skiplegacy/apps/legacyapp/pyproject.toml"

cp -a "$ROOT/fx/bench" "$ROOT/flags"
expect_exit "--keep-db-root-password --absorb-gitdirs --name --commit" 0 "committed: chore: migrate bench to frappe-nix" \
  --migrate --yes --skip-lock --allow-file-remotes --keep-db-root-password --absorb-gitdirs --name Flagged --commit "$ROOT/flags"
cd "$ROOT/flags"
check_eq "--keep-db-root-password keeps it" s3cret "$(jq -r .mariadb_root_password sites/common_site_config.json)"
check "--absorb-gitdirs leaves apps/frappe a gitfile" test -f apps/frappe/.git
check "--name names the bench" grep -q 'benchName = "flagged";' flake.nix
check_eq "--commit commits the result" "chore: migrate bench to frappe-nix" "$(git log -1 --format=%s)"
check "…with nothing left staged" git diff --cached --quiet
cd "$ROOT"

small_bench "$ROOT/msg"
expect_exit "--commit=<message>" 0 "committed: chore: adopt frappe-nix" \
  --migrate --yes --skip-lock "--commit=chore: adopt frappe-nix" "$ROOT/msg"
check_eq "…is the commit's subject" "chore: adopt frappe-nix" "$(git -C "$ROOT/msg" log -1 --format=%s)"
check "a migrated bench is not seeded a secrets/ directory" test '!' -e "$ROOT/msg/secrets"

echo "── app mode: refusals ───────────────────────────────────────"
mkdir -p "$ROOT/notapp" && printf '[project]\nname = "x"\n' > "$ROOT/notapp/pyproject.toml"
expect_exit "--app on a directory that is no Frappe app" 6 "does not look like a Frappe app" --app "$ROOT/notapp"
mkdir -p "$ROOT/nogit/an_app"
printf '[project]\nname = "an_app"\n' > "$ROOT/nogit/pyproject.toml"
printf 'app_name = "an_app"\n' > "$ROOT/nogit/an_app/hooks.py"
expect_exit "--app on an app that is not a git repository" 6 "is not a git repository" \
  --app --version version-16 --skip-lock "$ROOT/nogit"
expect_exit "--app without a version in a non-TTY" 5 "is required" --app "$ROOT/nogit"
expect_exit "an app is found without --app" 0 "this is a Frappe app, not a bench" \
  --dry-run --version version-16 "$ROOT/nogit"
says "default site   : an-app.localhost" && ok "…whose default site is <name>.localhost" ||
  no "…whose default site is <name>.localhost: $(cat "$OUT")"
expect_exit "--standards outside app mode" 1 "an app standards flag: app mode only" \
  --migrate --dry-run --standards minimal "$ROOT/plain"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
