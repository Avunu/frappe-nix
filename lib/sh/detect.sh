# frappe-init — read-only probes: what is this directory, what frappe is it,
# and what is each app under apps/.
#
# Nothing here writes. The whole resolve phase is pure so `--dry-run` is exact
# rather than a best guess.

# Per-app classification, filled by classify_apps. Parallel maps keyed by app
# directory name; APP_ORDER preserves sites/apps.txt order.
declare -a APP_ORDER=()
declare -A APP_DISP=()    # submodule | vendor | skip
declare -A APP_URL=()
declare -A APP_BRANCH=()
declare -A APP_SHA=()
declare -A APP_REMOTE=()
declare -A APP_FLAGS=()

# The bench's own .git, resolved once. Empty when the bench root is not yet a
# repository; the "\$BENCH_GIT_DIR/modules/*" test then matches nothing, which
# is the right answer.
BENCH_GIT_DIR=""

# ── directory shape ───────────────────────────────────────────────────────

# A bare .git does not make a directory non-empty: `git init` followed by
# nothing is still somewhere we can scaffold.
dir_is_empty() {
  local entry
  [ -e "$1" ] || return 0
  [ -d "$1" ] || return 1
  for entry in "$1"/* "$1"/.[!.]* "$1"/..?*; do
    if [ -e "$entry" ] || [ -L "$entry" ]; then
      if [ "$(basename "$entry")" != ".git" ]; then
        return 1
      fi
    fi
  done
  return 0
}

has_any_frappe_app() { compgen -G 'apps/*/*/hooks.py' >/dev/null 2>&1; }

looks_like_bench() {
  [ -d apps ] && [ -d sites ] &&
    { [ -f apps/frappe/frappe/hooks.py ] || has_any_frappe_app; }
}

# Bench skeleton with no app in it — apps/ and sites/ exist but nothing has
# hooks.py. Migrating that would produce a bench with nothing in it.
looks_like_empty_bench() { [ -d apps ] && [ -d sites ] && ! has_any_frappe_app; }

is_frappe_nix_bench() {
  [ -f flake.nix ] && grep -q 'frappe-nix' flake.nix &&
    [ -f pyproject.toml ] && grep -q '^\[tool\.uv\.workspace\]' pyproject.toml
}

has_foreign_flake() { [ -f flake.nix ] && ! is_frappe_nix_bench; }

# `--show-toplevel`, not `--git-dir`: a bench may sit inside another repo's
# worktree (a monorepo), and that is not a bench repo of its own.
bench_repo_state() {
  local top
  top="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -z "$top" ]; then
    printf 'none'
  elif [ "$top" = "$(pwd -P)" ]; then
    printf 'own'
  else
    printf 'nested'
  fi
}

# ── frappe version → preset ───────────────────────────────────────────────

# Branch names are consulted before __version__ because `develop` and
# `version-16` both report 16.0.0-dev.
detect_frappe_version() {
  local d=apps/frappe b v ref
  [ -d "$d" ] || return 0

  b="$(git -C "$d" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  if preset_exists "$b"; then
    printf '%s\t%s' "$b" "apps/frappe branch"
    return 0
  fi

  # Detached HEAD: any local or remote ref at this commit named like a preset.
  while IFS= read -r ref; do
    ref="${ref#origin/}"
    ref="${ref#upstream/}"
    if preset_exists "$ref"; then
      printf '%s\t%s' "$ref" "ref at apps/frappe HEAD"
      return 0
    fi
  done < <(git -C "$d" for-each-ref --points-at HEAD --format='%(refname:lstrip=2)' \
    refs/heads refs/remotes 2>/dev/null || true)

  # bench's own record. `resolution` degrades to the string "not calculated"
  # in some bench versions, so guard on its type.
  if [ -f sites/apps.json ]; then
    b="$(jq -r 'if (.frappe.resolution? | type) == "object"
                then (.frappe.resolution.branch // empty) else empty end' \
      sites/apps.json 2>/dev/null || true)"
    if preset_exists "$b"; then
      printf '%s\t%s' "$b" "sites/apps.json"
      return 0
    fi
  fi

  v="$(sed -n 's/^__version__[[:space:]]*=[[:space:]]*["'\'']\([0-9][^"'\'']*\).*/\1/p' \
    "$d/frappe/__init__.py" 2>/dev/null | head -n1)"
  [ -z "$v" ] && v="$(jq -r '.frappe.version // empty' sites/apps.json 2>/dev/null || true)"
  case "${v%%.*}" in
    '') : ;;
    1[0-4] | [0-9])
      printf 'UNSUPPORTED\t%s' "$v"
      return 0
      ;;
    *)
      if preset_exists "version-${v%%.*}"; then
        printf 'version-%s\t%s' "${v%%.*}" "apps/frappe __version__ $v"
        return 0
      fi
      ;;
  esac
  return 0
}

# ── per-app classification ────────────────────────────────────────────────

# Always yields a branch: lib/scripts.nix's `bench-update --pull` skips any
# submodule with no `submodule.<name>.branch`, so an empty value here would
# quietly freeze the app forever.
resolve_app_branch() {
  local d=$1 remote=$2 sha=$3 b upstream
  b="$(git -C "$d" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  if [ -n "$b" ] && git -C "$d" rev-parse --verify -q "refs/remotes/$remote/$b" >/dev/null 2>&1; then
    printf '%s' "$b"
    return 0
  fi
  # Detached, or a branch that exists only locally: the first remote-tracking
  # ref pointing at this commit.
  b="$(git -C "$d" for-each-ref --points-at "$sha" --format='%(refname:lstrip=3)' \
    "refs/remotes/$remote" 2>/dev/null | grep -vx HEAD | head -n1 || true)"
  [ -n "$b" ] && { printf '%s' "$b"; return 0; }
  # A `clone --depth 1 -b X` has exactly one ref; its config records X.
  upstream="$(git -C "$d" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  if [ -n "$upstream" ]; then
    b="$(git -C "$d" config --get "branch.$upstream.merge" 2>/dev/null || true)"
    b="${b#refs/heads/}"
    [ -n "$b" ] && { printf '%s' "$b"; return 0; }
  fi
  b="$(jq -r --arg a "$(basename "$d")" \
    'if (.[$a].resolution? | type) == "object" then (.[$a].resolution.branch // empty) else empty end' \
    sites/apps.json 2>/dev/null || true)"
  [ -n "$b" ] && { printf '%s' "$b"; return 0; }
  printf '%s' "$branch"
}

classify_app() {
  local app=$1 dir="apps/$1" real top url="" remote="" br sha flags="" r gitdir
  local -a remotes=()
  real="$(cd "$dir" && pwd -P)"
  top="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || true)"

  [ -f "$dir/$app/hooks.py" ] || flags="$flags,not-a-frappe-app"
  [ -f "$dir/pyproject.toml" ] || flags="$flags,no-pyproject"
  [ -f "$dir/setup.py" ] && flags="$flags,setup-py"

  if [ "$top" != "$real" ]; then
    APP_DISP[$app]=vendor
    APP_FLAGS[$app]="$flags,no-git"
    return 0
  fi
  # `.git` as a file is a gitlink. That is the normal shape for a submodule
  # whose gitdir has been absorbed into the superproject (.git/modules/<name>),
  # which is exactly what a half-converted bench looks like — so accept it when
  # it points inside this bench, and reject it only when the real repository
  # lives somewhere else (a linked worktree, or another superproject), because
  # then it would not travel with the bench.
  if [ -f "$dir/.git" ]; then
    gitdir="$(git -C "$dir" rev-parse --absolute-git-dir 2>/dev/null || true)"
    case "${gitdir:-}" in
      "$BENCH_GIT_DIR"/modules/*) : ;;
      *)
        APP_DISP[$app]=skip
        APP_FLAGS[$app]="$flags,external-gitdir"
        return 0
        ;;
    esac
  fi
  if ! git -C "$dir" rev-parse --verify -q HEAD >/dev/null 2>&1; then
    APP_DISP[$app]=skip
    APP_FLAGS[$app]="$flags,no-commits"
    return 0
  fi

  # origin → upstream → sole remote. bench's own get_remote() prefers
  # "upstream", so an origin-only lookup misses real benches.
  for r in origin upstream; do
    if url="$(git -C "$dir" remote get-url "$r" 2>/dev/null)"; then
      remote="$r"
      break
    fi
    url=""
  done
  if [ -z "$remote" ]; then
    mapfile -t remotes < <(git -C "$dir" remote)
    if [ "${#remotes[@]}" -eq 1 ]; then
      remote="${remotes[0]}"
      url="$(git -C "$dir" remote get-url "$remote")"
    fi
  fi

  # A filesystem path as the recorded submodule URL breaks every other clone of
  # the bench and trips protocol.file.allow on `submodule update --init`.
  case "$url" in
    '')
      APP_DISP[$app]=vendor
      APP_FLAGS[$app]="$flags,no-remote"
      return 0
      ;;
    /* | ./* | ../* | file://*)
      flags="$flags,local-remote"
      if ! $ALLOW_FILE_REMOTES; then
        APP_DISP[$app]=vendor
        APP_URL[$app]="$url"
        APP_FLAGS[$app]="$flags"
        return 0
      fi
      ;;
  esac

  sha="$(git -C "$dir" rev-parse HEAD)"
  br="$(resolve_app_branch "$dir" "$remote" "$sha")"
  # A branch that is not a known remote ref is a guess (the preset default, or a
  # stale sites/apps.json). `bench-update --pull` fetches it by name, so an
  # unverified guess means the first pull fails or lands somewhere unexpected.
  git -C "$dir" rev-parse --verify -q "refs/remotes/$remote/$br" >/dev/null 2>&1 ||
    flags="$flags,branch-unverified"
  git -C "$dir" diff --quiet 2>/dev/null && git -C "$dir" diff --cached --quiet 2>/dev/null ||
    flags="$flags,dirty"
  if [ -f "$dir/.git/shallow" ]; then
    flags="$flags,shallow"
  elif [ -z "$(git -C "$dir" branch -r --contains "$sha" 2>/dev/null || true)" ]; then
    # Meaningless on a shallow clone, which has no remote history to search.
    flags="$flags,unpushed"
  fi

  APP_DISP[$app]=submodule
  APP_URL[$app]="$url"
  APP_BRANCH[$app]="$br"
  APP_SHA[$app]="$sha"
  APP_REMOTE[$app]="$remote"
  APP_FLAGS[$app]="$flags"
}

# Order apps the way sites/apps.txt does (that is the install order the site was
# built with), then anything else on disk.
classify_apps() {
  local app line
  local -a seen=() forced=()
  IFS=',' read -ra forced <<< "$FORCE_VENDOR"
  if [ "$(bench_repo_state)" = own ]; then
    BENCH_GIT_DIR="$(git rev-parse --absolute-git-dir 2>/dev/null || true)"
  fi
  APP_ORDER=()
  if [ -f sites/apps.txt ]; then
    while IFS= read -r line; do
      app="$(printf '%s' "$line" | tr -d '[:space:]')"
      [ -n "$app" ] || continue
      if [ -d "apps/$app" ]; then
        APP_ORDER+=("$app")
        seen+=("$app")
      else
        warn "sites/apps.txt lists '$app' but apps/$app does not exist"
      fi
    done < sites/apps.txt
  fi
  for app in apps/*/; do
    [ -d "$app" ] || continue
    app="$(basename "$app")"
    in_list "$app" "${seen[@]}" || APP_ORDER+=("$app")
  done
  # frappe installs first and is the workspace anchor.
  if in_list frappe "${APP_ORDER[@]}" && [ "${APP_ORDER[0]}" != frappe ]; then
    local -a rest=()
    for app in "${APP_ORDER[@]}"; do
      [ "$app" = frappe ] || rest+=("$app")
    done
    APP_ORDER=(frappe "${rest[@]}")
  fi

  for app in "${APP_ORDER[@]}"; do
    classify_app "$app"
    if in_list "$app" "${forced[@]}"; then
      APP_DISP[$app]=vendor
      APP_FLAGS[$app]="${APP_FLAGS[$app]},forced-vendor"
    fi
  done
}

app_has_flag() { case ",${APP_FLAGS[$1]}," in *",$2,"*) return 0 ;; *) return 1 ;; esac; }
