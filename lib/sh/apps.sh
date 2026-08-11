# frappe-init — turning apps/ into something the flake can see.
#
# A flake's source tree is exactly its git-tracked files (plus submodule
# contents, via `self.submodules = true`), and lib/bench.nix discovers apps by
# reading apps/. So every app must end up either a registered submodule or
# committed source; an app left as an untracked nested repo is invisible to the
# build and `nix build` silently produces a bench without it.

# Whoever finds .frappe-nix-backup/ months from now needs to know it is not
# scratch. It is gitignored, so this costs nothing in the repo.
write_backup_readme() {
  mkdir -p .frappe-nix-backup
  [ -f .frappe-nix-backup/README.md ] && return 0
  cat > .frappe-nix-backup/README.md <<'EOF'
# frappe-nix migration backups

Written by `frappe-init --migrate`. Nothing here is used at runtime, and the
directory is gitignored — but do not delete it until you are sure you no longer
need what is in it.

| Entry | What it is | How to restore |
| --- | --- | --- |
| `env/` | The bench's original virtualenv. frappe-nix symlinks `env/` to a Nix-built one, and `ln -s` cannot replace a real directory, so it had to move. | Not restorable in place — the Nix venv owns `env/` now. |
| `<app>.git/` | The full git history of an app that had no usable remote and was **vendored** (its source is now committed directly into the bench). | `mv .frappe-nix-backup/<app>.git apps/<app>/.git` — but first `git rm -r --cached apps/<app>` in the bench, or the two will fight. |
| `<app>.json` | That app's original remote / branch / commit, recorded before its `.git` moved. | Reference only. |
| `nested/<app>/*.git` | A nested repository found inside a vendored app. | `mv` it back to the path encoded in the filename (`_` separates path segments). |
| `common_site_config.json.orig` | `sites/common_site_config.json` as it was before reconciliation. | Diff it to see which keys were forced or dropped. |

The right long-term fix for a vendored app is to push it to a real remote and
re-add it as a submodule:

    git rm -r --cached apps/<app>
    rm -rf apps/<app>            # after pushing it somewhere!
    bench-get-app <owner>/<app>
EOF
  info "+ .frappe-nix-backup/README.md"
}

ensure_bench_repo() {
  if [ "$(bench_repo_state)" != own ]; then
    git init -q
    info "+ git repository"
  fi
}

register_submodule() {
  local app=$1 url=$2 br=$3 path="apps/$1" mode

  # Repair-in-place when already registered. Never re-run `submodule add` on a
  # known name: with --force git does not fail on a name clash, it invents
  # `apps/frappe1` for path apps/frappe, and `bench-update`'s `submodule
  # foreach` would then look up a branch under the wrong name.
  if git config -f .gitmodules --get "submodule.$path.url" >/dev/null 2>&1; then
    git config -f .gitmodules "submodule.$path.path" "$path"
    git config -f .gitmodules "submodule.$path.url" "$url"
    git config -f .gitmodules "submodule.$path.branch" "$br"
    git config -f .gitmodules "submodule.$path.shallow" true
    git config --local "submodule.$path.url" "$url"
    git update-index --add --cacheinfo "160000,$(git -C "$path" rev-parse HEAD),$path"
    info "· apps/$app → submodule (repaired) @ $br"
    return 0
  fi

  # An app the bench repo already tracked as ordinary files has to leave the
  # index before it can become a gitlink.
  if git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
    mode="$(git ls-files -s -- "$path" | awk '{print $1; exit}')"
    [ "$mode" = 160000 ] || git rm -r -q --cached -- "$path"
  fi

  # `<path>` already holds a valid repository, so git stages it without
  # cloning: no transport, no network, and the gitlink lands on the app's
  # current HEAD. `-b` is what writes submodule.<name>.branch, which
  # lib/scripts.nix requires; --force is only to bypass .gitignore, which real
  # benches routinely apply to apps/.
  git submodule add -q --force -b "$br" -- "$url" "$path"
  git config -f .gitmodules "submodule.$path.shallow" true
  info "+ apps/$app → submodule @ $br ($(git -C "$path" rev-parse --short HEAD))"
}

vendor_app() {
  local app=$1 path="apps/$1" nested rel fresh=false
  mkdir -p .frappe-nix-backup
  if [ -e "$path/.git" ]; then
    fresh=true
    [ -e ".frappe-nix-backup/$app.git" ] &&
      die "apps/$app has a .git but .frappe-nix-backup/$app.git already exists — resolve by hand"
    # Provenance first: once the gitdir moves, the remote/branch/commit are only
    # recoverable from there.
    jq -n \
      --arg u "$(git -C "$path" remote get-url origin 2>/dev/null || true)" \
      --arg b "$(git -C "$path" symbolic-ref --quiet --short HEAD 2>/dev/null || true)" \
      --arg c "$(git -C "$path" rev-parse HEAD 2>/dev/null || true)" \
      '{url: $u, branch: $b, commit: $c}' > ".frappe-nix-backup/$app.json"
    mv "$path/.git" ".frappe-nix-backup/$app.git"
  fi
  # Nested repos would otherwise be staged as stray gitlinks with no .gitmodules
  # entry, i.e. empty directories in the flake source.
  while IFS= read -r -d '' nested; do
    rel="$(dirname "${nested#"$path"/}")"
    mkdir -p ".frappe-nix-backup/nested/$app"
    mv "$nested" ".frappe-nix-backup/nested/$app/$(printf '%s' "$rel" | tr / _).git"
    warn "apps/$app: moved nested repo $rel aside"
  done < <(find "$path" -mindepth 2 -name .git -print0)

  # Deliberately not `git add -f`: forcing past .gitignore would pull in
  # node_modules and public/dist. Correctness therefore rests on the ignore
  # rules, which verify_not_ignored checks.
  git add -- "$path"
  [ "$(git ls-files -- "$path" | wc -l)" -gt 0 ] ||
    die "vendored apps/$app produced 0 tracked files — check .gitignore"
  VENDORED+=("$app")
  if $fresh || [ ! -e ".frappe-nix-backup/$app.git" ]; then
    info "+ apps/$app → vendored ($(git ls-files -- "$path" | wc -l) files)"
  else
    info "· apps/$app already vendored ($(git ls-files -- "$path" | wc -l) files)"
  fi
}

convert_apps() {
  local app disp
  for app in "${APP_ORDER[@]}"; do
    disp="${APP_DISP[$app]}"
    case "$disp" in
      submodule)
        register_submodule "$app" "${APP_URL[$app]}" "${APP_BRANCH[$app]}"
        # bench-update fetches from `origin`; benches created by `bench get-app`
        # often only have `upstream`.
        if [ "${APP_REMOTE[$app]}" != origin ] &&
          ! git -C "apps/$app" remote get-url origin >/dev/null 2>&1; then
          git -C "apps/$app" remote add origin "${APP_URL[$app]}"
          info "  added an 'origin' alias in apps/$app (remote was '${APP_REMOTE[$app]}')"
        fi
        ;;
      vendor)
        $NO_VENDOR &&
          die "apps/$app has no usable git remote (${APP_FLAGS[$app]#,}) and --no-vendor was given. Push it to a remote and re-run, or drop --no-vendor to commit its source into the bench."
        vendor_app "$app"
        ;;
      skip) warn "apps/$app skipped (${APP_FLAGS[$app]#,}) — it will not be part of the bench" ;;
    esac
  done

  if $ABSORB_GITDIRS && [ "${#APP_ORDER[@]}" -gt 0 ]; then
    git submodule absorbgitdirs || warn "git submodule absorbgitdirs failed"
  fi
}

# Decided after vendoring and shimming, because a vendored setup.py-only app
# may have just been given a pyproject.toml.
compute_membership() {
  local app
  MEMBER_APPS=()
  APPS_TXT_ADD=()
  for app in "${APP_ORDER[@]}"; do
    [ "${APP_DISP[$app]}" = skip ] && continue
    app_has_flag "$app" not-a-frappe-app && continue
    APPS_TXT_ADD+=("$app")
    if [ -f "apps/$app/pyproject.toml" ]; then
      MEMBER_APPS+=("$app")
    else
      warn "apps/$app has no pyproject.toml — kept in apps/ and on PYTHONPATH, but not a uv workspace member (its own dependencies will not be resolved)"
    fi
  done
}

# Apps that shipped only a setup.py cannot be uv workspace members. Vendored
# ones we own, so a shim is committed with the bench. For a submodule the shim
# would live outside the pinned commit, so a clean build on any other machine
# would not see it — the only correct answers there are vendoring or fixing the
# app upstream.
shim_legacy_apps() {
  local app policy
  for app in "${APP_ORDER[@]}"; do
    app_has_flag "$app" no-pyproject || continue
    app_has_flag "$app" setup-py || continue
    policy="$LEGACY_APPS"
    if [ -z "$policy" ]; then
      if [ "${APP_DISP[$app]}" = vendor ]; then policy=shim; else policy=skip; fi
    fi
    case "$policy" in
      abort) die "apps/$app has only a setup.py and --legacy-apps=abort was given" ;;
      shim)
        [ "${APP_DISP[$app]}" = vendor ] ||
          die "apps/$app is a submodule; a generated pyproject.toml would not be part of its pinned commit. Use --vendor $app, or add the file upstream."
        frappe-nix-workspace shim-app --app-dir "apps/$app" --name "$app" \
          --requires-python "$requires_python"
        ;;
      skip) : ;;
      *) die "unknown --legacy-apps policy '$policy' (shim|skip|abort)" ;;
    esac
  done
}

# ── the venv symlink ──────────────────────────────────────────────────────

# modules/devenv.nix reconciles ./env to the Nix-built dev virtualenv with
# `ln -sfn`. Against a classic bench's real env/ directory that does not
# replace it — it creates env/<store-path> *inside* it — so the guard never
# becomes satisfied and `bench` keeps resolving env/bin/python to the stale
# virtualenv. Move it aside rather than delete it.
relocate_legacy_env() {
  [ -e env ] || return 0
  [ -L env ] && return 0
  mkdir -p .frappe-nix-backup
  if [ -e .frappe-nix-backup/env ]; then
    rm -rf env
    info "- env/ (classic virtualenv; .frappe-nix-backup/env already present)"
  else
    mv env .frappe-nix-backup/env
    info "- env/ → .frappe-nix-backup/env (classic virtualenv; frappe-nix symlinks env/ to the Nix venv)"
  fi
}

# ── workspace registration ────────────────────────────────────────────────

# The app's own [project].name, which is what uv resolves the workspace member
# to. Read only from the [project] table — other tables have `name` keys too.
app_dist_name() {
  local f="apps/$1/pyproject.toml" n=""
  if [ -f "$f" ]; then
    n="$(awk -F= '
      /^[[:space:]]*\[project\][[:space:]]*$/ { in_project = 1; next }
      /^[[:space:]]*\[/ { in_project = 0 }
      in_project && $1 ~ /^[[:space:]]*name[[:space:]]*$/ {
        gsub(/[[:space:]"'"'"']/, "", $2); print $2; exit
      }
    ' "$f")"
  fi
  printf '%s' "${n:-$1}"
}

# A bench directory named after its main app (bench_bjs / apps/bjs) is common,
# and uv refuses a workspace whose root shares a name with a member. Resolved
# before the template is rendered, since [project].name is a template token.
resolve_project_name() {
  local app dist
  PROJECT_NAME="$name"
  for app in "${APP_ORDER[@]}"; do
    dist="$(app_dist_name "$app")"
    if [ "$(normalize_dist "$PROJECT_NAME")" = "$(normalize_dist "$dist")" ] ||
      [ "$(normalize_dist "$PROJECT_NAME")" = "$(normalize_dist "$app")" ]; then
      PROJECT_NAME="$name-bench"
      warn "the bench and the app '$app' are both named '$name'; the workspace root is [project].name = '$PROJECT_NAME' so uv can tell them apart"
      return 0
    fi
  done
}

reconcile_workspace() {
  if [ ! -f pyproject.toml ]; then
    cp "$STAGING/pyproject.toml" pyproject.toml
    info "+ pyproject.toml"
  fi
  frappe-nix-workspace ensure-root \
    --pyproject pyproject.toml \
    --name "$PROJECT_NAME" \
    --requires-python "$requires_python" \
    --overrides "$overrides" \
    --preset "$frappe_version" \
    --extra-build-dependencies "$STAGING/pyproject.toml"
  frappe-nix-workspace sync-apps --pyproject pyproject.toml "${MEMBER_APPS[@]}"
  frappe-nix-workspace apps-txt --file sites/apps.txt --add "${APPS_TXT_ADD[@]}"
}
