#!/usr/bin/env bash
# Build a synthetic classic `bench init` bench for the migration check.
#
# Usage: make-classic-bench.sh <root>
# Creates <root>/remotes/*.git (bare "upstreams") and <root>/bench.
#
# Each app is shaped to exercise one hard case:
#   frappe      shallow clone, attached HEAD, remote `origin`   → happy path
#   erpnext     full clone, DETACHED HEAD, remote `upstream`,
#               dirty working tree                              → branch resolution
#   localapp    git repo with NO remote, has <app>/public/js/   → vendoring
#   legacyapp   no .git at all, setup.py only                   → vendor + shim
#   hrms        an ALREADY-registered submodule with an absorbed
#               gitdir (.git is a gitfile) and a STALE branch
#               in .gitmodules                                  → repair in place
#
# The last one is the half-converted bench: a real-world bench that someone
# already put under git and partly submodule-ised, but that has no pyproject.toml
# or flake.nix yet.
set -euo pipefail

root="$1"
mkdir -p "$root"
cd "$root"

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

seed_app() { # <dir> <name> <version>
  mkdir -p "$1/$2"
  printf '__version__ = "%s"\n' "$3" > "$1/$2/__init__.py"
  printf 'app_name = "%s"\n' "$2" > "$1/$2/hooks.py"
  cat > "$1/pyproject.toml" <<EOF
[project]
name = "$2"
version = "$3"
requires-python = ">=3.12"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
EOF
}

# ── bare "remotes" ────────────────────────────────────────────────────────
mkdir -p remotes
for app in frappe erpnext hrms; do
  git init -q --bare "remotes/$app.git"
  rm -rf "seed/$app"
  mkdir -p "seed/$app"
  (
    cd "seed/$app"
    git init -q -b version-15
    cd ..
  )
  case "$app" in
    frappe) seed_app "seed/$app" frappe 15.42.0 ;;
    erpnext) seed_app "seed/$app" erpnext 15.30.0 ;;
    hrms) seed_app "seed/$app" hrms 15.10.0 ;;
  esac
  printf '{"dependencies":{}}\n' > "seed/$app/package.json"
  printf '# yarn lockfile v1\n' > "seed/$app/yarn.lock"
  (
    cd "seed/$app"
    git add -A
    git commit -q -m "initial"
    # A second commit, so erpnext can detach onto a non-tip one.
    printf '# changelog\n' > CHANGELOG.md
    git add -A
    git commit -q -m "second"
    git remote add origin "$root/remotes/$app.git"
    git push -q origin version-15
  )
  # `git init --bare` points HEAD at whatever init.defaultBranch says, so a
  # clone would find no checkout-able ref.
  git -C "remotes/$app.git" symbolic-ref HEAD refs/heads/version-15
done

# ── the bench ─────────────────────────────────────────────────────────────
mkdir -p bench/apps bench/sites bench/config/pids bench/logs bench/env/bin
cd bench

# file:// so the clone is genuinely shallow (a plain local path is always a full
# clone), which is how `bench init` leaves apps when shallow_clone is on.
git clone -q --depth 1 --branch version-15 "file://$root/remotes/frappe.git" apps/frappe
git clone -q "$root/remotes/erpnext.git" apps/erpnext
(
  cd apps/erpnext
  git remote rename origin upstream
  # Detached HEAD on the first commit, and an uncommitted edit.
  git checkout -q "$(git rev-list --max-parents=0 HEAD)"
  printf 'dirty\n' >> erpnext/hooks.py
)

# An in-house app with no remote. public/js is the case a `**/public/*` ignore
# rule would silently drop from the flake source.
seed_app apps/localapp localapp 1.0.0
mkdir -p apps/localapp/localapp/public/js
printf 'console.log("local");\n' > apps/localapp/localapp/public/js/x.js
(
  cd apps/localapp
  git init -q -b main
  git add -A
  git commit -q -m "local app"
)

# The half-converted case: the bench root is already a git repo and hrms is
# already a real submodule, so its gitdir is absorbed into .git/modules/ and its
# .git is a gitfile. Its recorded branch is deliberately stale — exactly the
# drift seen on real benches, and the thing that makes `bench-update --pull`
# fetch the wrong ref.
git init -q -b main
printf 'placeholder\n' > .bench-root
git add .bench-root
git commit -q -m "bench root"
git -c protocol.file.allow=always submodule add -q \
  -b version-15 "file://$root/remotes/hrms.git" apps/hrms
git config -f .gitmodules submodule.apps/hrms.branch stale-branch
git add .gitmodules
git commit -q -m "add hrms"

# A pre-PEP-621 app: setup.py, no pyproject.toml, no git.
mkdir -p apps/legacyapp/legacyapp
printf '__version__ = "0.0.1"\n' > apps/legacyapp/legacyapp/__init__.py
printf 'app_name = "legacyapp"\n' > apps/legacyapp/legacyapp/hooks.py
printf 'from setuptools import setup\nsetup(name="legacyapp")\n' > apps/legacyapp/setup.py
printf 'requests\n# a comment\n' > apps/legacyapp/requirements.txt

# ── classic bench litter ──────────────────────────────────────────────────
printf '#!/usr/bin/env python\n' > env/bin/python
chmod +x env/bin/python
printf 'web: bench serve\n' > Procfile
printf 'frappe.patches.v1.foo\n' > patches.txt
printf 'port 13000\n' > config/redis_cache.conf
mkdir -p apps/frappe/node_modules/foo
printf 'module.exports = {};\n' > apps/frappe/node_modules/foo/index.js

# apps.txt with NO trailing newline, as frappe writes it.
printf 'frappe\nerpnext\nhrms' > sites/apps.txt
cat > sites/apps.json <<'EOF'
{
  "frappe": {
    "resolution": {"commit_hash": null, "branch": null},
    "required": [], "idx": 1, "version": "15.42.0"
  },
  "erpnext": {
    "is_repo": true,
    "resolution": "not calculated",
    "required": [], "idx": 2, "version": "15.30.0"
  }
}
EOF
cat > sites/common_site_config.json <<'EOF'
{
    "background_workers": 1,
    "db_host": "db.internal",
    "default_site": "mysite.local",
    "developer_mode": 1,
    "gunicorn_workers": 17,
    "host_name": "https://erp.example.com",
    "http_port": 443,
    "mariadb_root_password": "s3cret",
    "redis_cache": "redis://localhost:13000",
    "redis_queue": "redis://localhost:11000",
    "redis_socketio": "redis://localhost:12000",
    "socketio_port": 9000,
    "use_redis_auth": true,
    "webserver_port": 8000,
    "workers": {"shipstation": {"timeout": 8000}}
}
EOF

mkdir -p sites/mysite.local/private/files sites/mysite.local/public/files
printf '{"db_name":"x","db_password":"hunter2","encryption_key":"k"}\n' \
  > sites/mysite.local/site_config.json
# Committed, the way a classic bench routinely has it — this is what the
# migrator has to notice.
git add -f sites/mysite.local/site_config.json
git commit -q -m "site config"
printf 'backup\n' > sites/mysite.local/private/files/big.bin
printf 'png\n' > sites/mysite.local/public/files/x.png
