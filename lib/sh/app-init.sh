# frappe-init — app mode: put a frappe-nix dev environment in an app's own repo.
#
# The bench modes reconcile a directory that *is* a bench. This one does the
# opposite: it puts in the files that say "assemble a bench around me". Only
# .envrc and the .gitignore block come from templates/app; everything else
# (flake.nix and its lock, [tool.ironclad] and the managed keys of
# pyproject.toml, the tool configs and locks) is `ironclad sync --write`, the
# engine `frappe-init --sync` runs later (docs/ironclad/spec.md §3.3), so a new
# app starts in sync. The workspace root frappe-nix generates is a different
# file, living in the Nix store.

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
  info "default site   : $site ([tool.ironclad] site sets another)"
  printf '\n'
  info ".envrc and the .gitignore block come from the app template; every other"
  info "managed file is written by 'ironclad sync --write'. The bench itself is"
  info "generated into .frappe-nix/ on shell entry and is gitignored."
  printf '\n'

  if $DRY_RUN; then
    printf -- '--dry-run: nothing was changed.\n'
    return 0
  fi

  [ -d .git ] || git rev-parse --git-dir > /dev/null 2>&1 ||
    die "'$(pwd -P)' is not a git repository. A flake's source tree is exactly its tracked files, so frappe-nix cannot see an app that git cannot." 6

  # templates/app holds exactly .envrc and .gitignore: install_template copies
  # every file in it, so nothing else may live there (spec §1.2).
  step "Copying the app template"
  TEMPLATE="$APP_TEMPLATE"
  render_template
  install_template keep
  install_gitignore_block
  git add -- .envrc .gitignore

  if git check-ignore -q -- .envrc; then
    die ".envrc is excluded by .gitignore — the Nix build cannot see it"
  fi

  # Staged as it goes: a flake's source tree is only its tracked files, so an
  # untracked flake.nix is one `nix run` away from "does not provide attribute".
  step "Writing the managed files (ironclad sync --write)"
  local -a args=(--write --frappe-version "$frappe_version")
  if $SKIP_LOCK; then args+=(--skip-lock); fi
  local rc=0
  ironclad sync "${args[@]}" || rc=$?
  if [ "$rc" != 0 ]; then
    die "'ironclad sync --write' exited $rc — fix what it reported above and run 'frappe-init --sync'" "$rc"
  fi

  if git check-ignore -q -- flake.nix; then
    die "flake.nix is excluded by .gitignore — the Nix build cannot see it"
  fi

  step "Done — $(pwd -P)"
  cat <<EOF

Next steps:
  git diff --cached --stat     # review; nothing has been committed
  direnv allow                 # or: nix develop --no-pure-eval
  devenv up                    # MariaDB, Redis, web, scheduler, worker, …
  provision-site               # (in another shell) create $site + install $app_name

To add a sibling app (erpnext, hrms, …), list it in [tool.ironclad] siblings in
pyproject.toml and run 'nix run .#frappe-init -- --sync'. Commit nix/ — a flake's
source tree is only its tracked files.
EOF
}
