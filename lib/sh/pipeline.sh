# frappe-init — the phases scaffolding and migration share.
#
# Scaffolding is migration over an empty directory: once the apps are on disk,
# both modes run exactly the same reconcile → convert → register → stage
# sequence. Every step is check-then-act, so an interrupted run is resumed by
# re-running it.

print_plan() {
  local app disp flags
  step "Plan for $(pwd -P)"
  info "frappe version : $frappe_version (python $pyver / node ${nodejs#nodejs_})"
  info "bench name     : $name"
  info "default site   : $site"
  printf '\n'
  for app in "${APP_ORDER[@]}"; do
    disp="${APP_DISP[$app]}"
    flags="${APP_FLAGS[$app]#,}"
    case "$disp" in
      submodule) printf '  %-24s submodule  %s @ %s%s\n' "$app" "${APP_BRANCH[$app]}" \
        "${APP_SHA[$app]:0:10}" "${flags:+  [$flags]}" ;;
      vendor) printf '  %-24s vendor     (source committed to the bench)%s\n' "$app" "${flags:+  [$flags]}" ;;
      skip) printf '  %-24s SKIP       %s\n' "$app" "$flags" ;;
    esac
  done
  printf '\n'
  [ -e env ] && [ ! -L env ] && info "env/ will move to .frappe-nix-backup/env"
  for app in "${APP_ORDER[@]}"; do
    app_has_flag "$app" unpushed &&
      warn "apps/$app is pinned at a commit that is on no remote branch — the bench will build here and nowhere else"
    app_has_flag "$app" dirty &&
      warn "apps/$app has uncommitted changes; the submodule pin records HEAD, so they are not captured"
    app_has_flag "$app" local-remote &&
      warn "apps/$app's remote is a filesystem path; recording it as a submodule URL breaks other clones"
    app_has_flag "$app" branch-unverified &&
      warn "apps/$app: '${APP_BRANCH[$app]}' is a guess — no such branch on remote '${APP_REMOTE[$app]}'. Check .gitmodules before running 'bench-update --pull'."
  done
  if $STRICT; then
    for app in "${APP_ORDER[@]}"; do
      if app_has_flag "$app" unpushed || app_has_flag "$app" dirty; then
        die "--strict: apps/$app is dirty or unpushed"
      fi
    done
  fi
}

run_pipeline() {
  step "Preparing the bench tree"
  mkdir -p .frappe-nix-backup logs config/pids
  ensure_bench_repo
  resolve_project_name
  render_template
  install_template keep
  install_gitignore_block
  relocate_legacy_env
  reconcile_common_site_config
  grep -q 'use flake' .envrc 2>/dev/null ||
    warn ".envrc does not 'use flake' — direnv will not load the frappe-nix dev shell"

  step "Apps"
  write_backup_readme
  convert_apps
  shim_legacy_apps
  compute_membership
  verify_not_ignored

  step "Workspace"
  reconcile_workspace

  finalize
}

finalize() {
  local staged app

  if $SKIP_LOCK; then
    info "skipping uv lock (--skip-lock)"
  else
    step "Resolving the Python workspace (uv lock)"
    if ! uv lock; then
      # An existing bench pins whatever its apps happened to need, so conflicts
      # here are common and are the user's to resolve — see uv's own diagnosis
      # printed just above.
      warn "uv lock failed. Resolve it in pyproject.toml and re-run 'uv lock': for a conflict between two apps, add a pin to [tool.uv] override-dependencies; for one against the [dependency-groups] dev tools, relax the pin there. Everything else about the migration is already done."
    fi
  fi

  step "Staging"
  staged="$(git add -An 2>/dev/null | wc -l || true)"
  if [ "$staged" -gt 20000 ]; then
    warn "$staged files would be staged — that usually means .gitignore is wrong"
    $ASSUME_YES || confirm "Stage $staged files anyway?" ||
      die "aborted; nothing was staged (the working tree changes above are already applied)"
  fi
  git add -A
  git ls-files --error-unmatch -- flake.nix pyproject.toml sites/apps.txt >/dev/null ||
    die "flake.nix / pyproject.toml / sites/apps.txt did not reach the git index"
  for app in "${VENDORED[@]}"; do
    [ "$(git ls-files -- "apps/$app" | wc -l)" -gt 0 ] ||
      die "vendored app $app has no tracked files"
  done

  if $DO_COMMIT; then
    git commit -q -m "$COMMIT_MSG"
    info "committed: $COMMIT_MSG"
  fi

  print_report
}

print_report() {
  local app w

  step "Done — $(pwd -P)"
  info "frappe version : $frappe_version (python $pyver / node ${nodejs#nodejs_})"
  info "apps           : ${APP_ORDER[*]}"
  if [ "${#VENDORED[@]}" -gt 0 ]; then
    info "vendored       : ${VENDORED[*]}"
    info "                 history preserved in .frappe-nix-backup/<app>.git (gitignored — do not delete it before you have pushed the app somewhere)"
  fi

  if [ "${#WARNINGS[@]}" -gt 0 ]; then
    printf '\n\033[33m%s warning(s):\033[0m\n' "${#WARNINGS[@]}"
    for w in "${WARNINGS[@]}"; do
      printf '  ⚠  %s\n' "$w"
    done
  fi

  cat <<EOF

Next steps:
  git diff --cached --stat     # review; nothing has been committed
  direnv allow                 # or: nix develop --no-pure-eval
  devenv up                    # MariaDB, Redis, web, scheduler, worker, …
  bench-update --node-hashes   # generate node-offline-hashes.json before 'nix build'
EOF
  if [ ! -e sites/"$site" ]; then
    printf '  provision-site               # (in another shell) create %s + install apps\n' "$site"
  fi
  for app in "${APP_ORDER[@]}"; do
    if [ "${APP_DISP[$app]}" = skip ]; then
      printf '\nNote: apps/%s was skipped and is NOT part of the bench.\n' "$app"
    fi
  done
}
