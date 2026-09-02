# frappe-init — app mode: put a frappe-nix dev environment in an app's own repo.
#
# The bench modes reconcile a directory that *is* a bench. This one does the
# opposite: it writes a flake that says "assemble a bench around me", and touches
# nothing else. In particular it never edits the app's pyproject.toml — that file
# is the app's packaging metadata, and the workspace root frappe-nix generates is
# a different file living in the Nix store.

# A Frappe app is a pyproject.toml whose [project].name names a sibling package
# that holds hooks.py. That is what `bench get-app` looks for, and it is enough to
# tell an app apart from any other Python project.
looks_like_frappe_app() {
  local n
  [ -f pyproject.toml ] || return 1
  n="$(frappe-nix-workspace dist-name --app-dir . 2> /dev/null)" || return 1
  [ -n "$n" ] && [ -f "$n/hooks.py" ]
}

cmd_app_init() {
  if [ -z "$frappe_version" ]; then
    if has_tty; then
      frappe_version="$(choose_frappe_version)"
    else
      die "--frappe-version is required (one of: $(preset_keys | tr '\n' ' '))" 5
    fi
  fi
  preset_exists "$frappe_version" ||
    die "unknown frappe version '$frappe_version' (expected: $(preset_keys | tr '\n' ' '))" 5
  resolve_preset

  app_name="$(frappe-nix-workspace dist-name --app-dir .)"
  [ -n "$app_name" ] || die "cannot read [project].name from pyproject.toml" 5
  [ -f "$app_name/hooks.py" ] ||
    die "'$(pwd -P)' does not look like a Frappe app: [project].name is '$app_name' but there is no $app_name/hooks.py" 6

  # The bench name, which fixes this bench's port range and its container image
  # prefix. Derived rather than asked for: an app repo has exactly one bench and
  # naming it separately is a question with no interesting answer.
  name="$(normalize_dist "$app_name")"
  site="${site:-$name.localhost}"

  step "Plan for $(pwd -P)"
  info "app            : $app_name"
  info "frappe version : $frappe_version (python $pyver / node ${nodejs#nodejs_})"
  info "bench name     : $name"
  info "default site   : $site"
  printf '\n'
  info "flake.nix and .envrc are written here; the bench itself is generated"
  info "into .frappe-nix/ on shell entry and is gitignored."
  printf '\n'

  if $DRY_RUN; then
    printf -- '--dry-run: nothing was changed.\n'
    return 0
  fi

  [ -d .git ] || git rev-parse --git-dir > /dev/null 2>&1 ||
    die "'$(pwd -P)' is not a git repository. A flake's source tree is exactly its tracked files, so frappe-nix cannot see an app that git cannot." 6

  step "Writing the flake"
  TEMPLATE="$APP_TEMPLATE"
  render_template
  install_template keep
  install_gitignore_block

  # Staged, not just written: a flake's source tree is only its tracked files, so
  # an untracked flake.nix is one `nix run` away from "does not provide attribute".
  mkdir -p nix
  git add -- flake.nix .envrc .gitignore

  local p
  for p in flake.nix .envrc; do
    ! git check-ignore -q -- "$p" ||
      die "$p is excluded by .gitignore — the Nix build cannot see it"
  done

  step "Resolving the Python workspace"
  if $SKIP_LOCK; then
    info "skipping (--skip-lock)"
  elif ! command -v nix > /dev/null 2>&1; then
    warn "nix is not on PATH — run 'nix run .#relock' yourself before entering the shell"
  else
    # The first lock has to come from the flake we just wrote, and it cannot come
    # from the dev shell: without nix/uv.lock the shell is exactly what refuses to
    # evaluate. `relock` is wired to be reachable without one.
    nix run --impure .#relock || {
      warn "'nix run .#relock' failed — fix the error above and re-run it; everything else is already written"
    }
  fi

  step "Done — $(pwd -P)"
  cat <<EOF

Next steps:
  git diff --cached --stat     # review; nothing has been committed
  direnv allow                 # or: nix develop --no-pure-eval
  devenv up                    # MariaDB, Redis, web, scheduler, worker, …
  provision-site               # (in another shell) create $site + install $app_name

To add a sibling app (erpnext, hrms, …), declare it as a flake input and list it
under frappe-nix.app.siblings in flake.nix, then re-run 'nix run .#relock'.
Commit nix/ — a flake's source tree is only its tracked files.
EOF
}
