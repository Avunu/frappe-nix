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
      -e "s|@PYVER@|$pyver|g"
    sed -i -e "s|@OVERRIDES@|$overrides|" pyproject.toml
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

# Forced keys are the ones the devenv shell physically contradicts: it runs a
# single Redis on 13000 and fixed web/socketio/watcher ports, so a bench that
# keeps bench-init's 11000/12000 split hangs its workers on connection refusals.
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
      *) warn "db_host is '$db_host'; the devenv MariaDB is local — set it to 127.0.0.1 or expect connections to the old host" ;;
    esac
  fi

  defaults="$(jq -n --arg site "$site" \
    '{default_site: $site, background_workers: 1, gunicorn_workers: 1, db_host: "127.0.0.1"}')"
  forced="$(jq -n --argjson keep "$KEEP_DB_ROOT_PW" '
    {redis_cache: "redis://localhost:13000",
     redis_queue: "redis://localhost:13000",
     redis_socketio: "redis://localhost:13000",
     use_redis_auth: false,
     webserver_port: 8000,
     socketio_port: 9000,
     file_watcher_port: 6787,
     developer_mode: 1,
     live_reload: true,
     serve_default_site: true,
     shallow_clone: true}
    + (if $keep then {} else {mariadb_root_password: ""} end)')"

  # Production-only keys that are actively harmful in a dev bench: host_name and
  # http_port make Frappe mint absolute URLs pointing at the production host
  # (in emails, in redirects), and the restart_* hooks try to drive a supervisor
  # or systemd that is not there. The original is kept as .orig.
  local dropped
  dropped="$(printf '%s' "$cur" | jq -r '
    [ "host_name", "http_port", "frappe_user",
      "restart_supervisor_on_update", "restart_systemd_on_update" ]
    | map(select(. as $k | $ARGS.named.cur | has($k))) | join(", ")
  ' --argjson cur "$cur")"
  [ -n "$dropped" ] && info "dropping production-only keys: $dropped"

  tmp="$(mktemp)"
  # `$d * . * $f` reads as: defaults, overridden by the bench's own file,
  # overridden by the values frappe-nix owns.
  printf '%s' "$cur" | jq --argjson d "$defaults" --argjson f "$forced" '
    ($d * . * $f)
    | del(.host_name, .http_port, .frappe_user,
          .restart_supervisor_on_update, .restart_systemd_on_update)
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
