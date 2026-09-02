# frappe-init — laying down template files without ever clobbering.
#
# The template is rendered into a staging directory and only then installed, so
# token substitution can never touch a file the bench already owns (migrating
# in place with `sed -i README.md` would rewrite the user's README).

STAGING=""

cleanup_staging() { [ -n "$STAGING" ] && rm -rf "$STAGING"; return 0; }

render_template() {
  [ -n "$STAGING" ] && return 0
  STAGING="$(mktemp -d)"
  trap cleanup_staging EXIT
  cp -R "$TEMPLATE"/. "$STAGING"/
  chmod -R u+w "$STAGING"
  (
    cd "$STAGING"
    # Substitute across every file that carries a token, rather than an explicit
    # file list that has to be kept in sync with the template's contents.
    grep -rlZ '@[A-Z_]*@' . 2>/dev/null | xargs -0 -r sed -i \
      -e "s|@BENCH_NAME@|$name|g" \
      -e "s|@PROJECT_NAME@|$PROJECT_NAME|g" \
      -e "s|@SITE_NAME@|$site|g" \
      -e "s|@PYTHON@|$python|g" \
      -e "s|@NODEJS@|$nodejs|g" \
      -e "s|@REQUIRES_PYTHON@|$requires_python|g" \
      -e "s|@PYTAG@|$pytag|g" \
      -e "s|@PYVER@|$pyver|g" \
      -e "s|@APP_NAME@|$app_name|g" \
      -e "s|@FRAPPE_VERSION@|$frappe_version|g" \
      -e "s|@FRAPPE_BRANCH@|$branch|g"
    # The bench template's pyproject.toml only. @OVERRIDES@ is a bare TOML array
    # rather than a quoted string, so it cannot go through the pass above without
    # `|` in an override specifier splitting the sed expression; and the app
    # template has no pyproject.toml of its own to render — an app repo's project
    # file is its packaging metadata, not a workspace root.
    #
    # An `if`, not `[ -f … ] &&`: this is the last command in the subshell, so
    # under `set -e` a false test would exit the whole script — which is exactly
    # what the app template, having no pyproject.toml, would do.
    if [ -f pyproject.toml ]; then
      sed -i -e "s|@OVERRIDES@|$overrides|" pyproject.toml
    fi
  )
}

# Install everything except .gitignore, which is spliced as a managed block
# (install_gitignore_block) so a later run can upgrade it in place.
install_template() {
  local keep=$1 rel
  while IFS= read -r -d '' rel; do
    rel="${rel#./}"
    [ "$rel" = ".gitignore" ] && continue
    if [ "$keep" = keep ] && [ -e "$rel" ]; then
      continue
    fi
    mkdir -p "$(dirname "$rel")"
    cp "$STAGING/$rel" "$rel"
    info "+ $rel"
  done < <(cd "$STAGING" && find . -type f -print0)
}

# ── .gitignore ────────────────────────────────────────────────────────────

GITIGNORE_BEGIN='# >>> frappe-nix >>> (managed block — edits here are overwritten)'
GITIGNORE_END='# <<< frappe-nix <<<'

install_gitignore_block() {
  local body tmp
  body="$(cat "$STAGING/.gitignore")"
  tmp="$(mktemp)"
  if [ -f .gitignore ] && grep -qxF "$GITIGNORE_BEGIN" .gitignore; then
    awk -v b="$GITIGNORE_BEGIN" -v e="$GITIGNORE_END" -v body="$body" '
      $0 == b { print b; print body; print e; skip = 1; next }
      $0 == e { skip = 0; next }
      !skip   { print }
    ' .gitignore > "$tmp"
  else
    {
      if [ -f .gitignore ]; then
        cat .gitignore
        [ -n "$(tail -c1 .gitignore)" ] && echo
      fi
      printf '%s\n%s\n%s\n' "$GITIGNORE_BEGIN" "$body" "$GITIGNORE_END"
    } > "$tmp"
  fi
  if [ -f .gitignore ] && cmp -s "$tmp" .gitignore; then
    rm -f "$tmp"
    info ". .gitignore already current"
  else
    mv "$tmp" .gitignore
    info "+ .gitignore (frappe-nix managed block)"
  fi
}

# The flake source tree is exactly the set of git-tracked files, so anything the
# build needs that .gitignore excludes is a silent, much-later build failure.
# Turn it into an error here instead.
verify_not_ignored() {
  local bad=0 p app
  for p in flake.nix pyproject.toml .envrc sites/apps.txt sites/common_site_config.json apps; do
    [ -e "$p" ] || continue
    if git check-ignore -q -- "$p"; then
      err "$p is excluded by .gitignore"
      bad=1
    fi
  done
  for app in "${VENDORED[@]}"; do
    for p in "apps/$app/$app/hooks.py" "apps/$app/pyproject.toml" "apps/$app/$app/public"; do
      [ -e "$p" ] || continue
      if git check-ignore -q -- "$p"; then
        err "$p is excluded by .gitignore"
        bad=1
      fi
    done
  done
  [ "$bad" = 0 ] ||
    die "the bench .gitignore excludes files the Nix build needs (a flake's source tree is only its git-tracked files)"
}

# ── sites/common_site_config.json ─────────────────────────────────────────

# Per-bench web port, 8000..8899. Must stay byte-identical to portOffsetFor in
# modules/devenv.nix — Nix's builtins.hashString "sha256" is the same digest of
# the same bytes — so a bench scaffolded here is already correct before its
# first `devenv up` and the shell's config task is a no-op.
#
# Derived from the bench *name* rather than its path precisely because this file
# is committed: a path-derived port would differ in every clone.
frappe_web_port() {
  local hex
  hex="$(printf '%s' "$1" | sha256sum | cut -c1-4)"
  echo $(( 8000 + 0x$hex % 900 ))
}

# Forced keys are the ones the devenv shell physically contradicts. Two classes:
#
#   - the web/socketio ports, which are per-bench so several benches can run at
#     once, and which have to live here because realtime/utils.js reads
#     webserver_port straight out of this JSON with no env override available;
#   - the redis URLs, which are *deleted* rather than set. In the dev shell Redis
#     is on a unix socket under $DEVENV_RUNTIME, and that path is a hash of the
#     project directory — it cannot be committed, because it differs in every
#     clone. FRAPPE_REDIS_CACHE/_QUEUE carry it instead, and a bench that keeps
#     bench-init's 11000/12000 split would otherwise hang its workers on
#     connection refusals.
#
# Everything else the bench had is preserved.
reconcile_common_site_config() {
  local f=sites/common_site_config.json cur='{}' defaults forced tmp
  if [ -f "$f" ]; then
    jq -e 'type == "object"' "$f" >/dev/null 2>&1 ||
      die "$f is not a JSON object — fix or remove it and re-run"
    cur="$(cat "$f")"
    if [ "$(jq -r '.mariadb_root_password // ""' "$f")" != "" ] && ! $KEEP_DB_ROOT_PW; then
      warn "mariadb_root_password was set in $f and has been blanked (this file is committed to git); --keep-db-root-password overrides"
    fi
    local db_host
    db_host="$(jq -r '.db_host // ""' "$f")"
    case "$db_host" in
      '' | localhost | 127.0.0.1) : ;;
      *) warn "db_host was '$db_host' and has been dropped; the devenv MariaDB is local and reached over the unix socket in FRAPPE_DB_SOCKET" ;;
    esac
  fi

  local web_port
  web_port="$(frappe_web_port "$name")"

  defaults="$(jq -n --arg site "$site" \
    '{default_site: $site, background_workers: 1, gunicorn_workers: 1}')"
  forced="$(jq -n --argjson keep "$KEEP_DB_ROOT_PW" --argjson port "$web_port" '
    {use_redis_auth: false,
     webserver_port: $port,
     socketio_port: $port,
     developer_mode: 1,
     live_reload: true,
     serve_default_site: true,
     shallow_clone: true}
    + (if $keep then {} else {mariadb_root_password: ""} end)')"

  # Keys that are actively harmful in a dev bench. Two groups:
  #
  #   - production-only: host_name and http_port make Frappe mint absolute URLs
  #     pointing at the production host (in emails, in redirects), and the
  #     restart_* hooks try to drive a supervisor or systemd that is not there;
  #   - stale transport: db_host/db_port are superseded by the unix socket in
  #     FRAPPE_DB_SOCKET, the redis URLs by FRAPPE_REDIS_* (see above), and
  #     redis_socketio/file_watcher_port are read by nothing at all — realtime
  #     publishes over redis_queue now, and the file watcher has no listener.
  #
  # The original is kept as .orig.
  local dropped
  dropped="$(printf '%s' "$cur" | jq -r '
    [ "host_name", "http_port", "frappe_user",
      "restart_supervisor_on_update", "restart_systemd_on_update",
      "db_host", "db_port",
      "redis_cache", "redis_queue", "redis_socketio", "file_watcher_port" ]
    | map(select(. as $k | $ARGS.named.cur | has($k))) | join(", ")
  ' --argjson cur "$cur")"
  [ -n "$dropped" ] && info "dropping superseded keys: $dropped"

  tmp="$(mktemp)"
  # `$d * . * $f` reads as: defaults, overridden by the bench's own file,
  # overridden by the values frappe-nix owns.
  printf '%s' "$cur" | jq --argjson d "$defaults" --argjson f "$forced" '
    ($d * . * $f)
    | del(.host_name, .http_port, .frappe_user,
          .restart_supervisor_on_update, .restart_systemd_on_update,
          .db_host, .db_port,
          .redis_cache, .redis_queue, .redis_socketio, .file_watcher_port)
  ' > "$tmp"
  mkdir -p sites
  if [ -f "$f" ] && cmp -s "$tmp" "$f"; then
    rm -f "$tmp"
    info ". $f already current"
  else
    [ -f "$f" ] && cp "$f" .frappe-nix-backup/common_site_config.json.orig
    mv "$tmp" "$f"
    info "+ $f"
  fi
}
