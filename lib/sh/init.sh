# frappe-init — scaffold mode: lay down a bench from scratch.
#
# Once the apps are cloned this hands off to the same pipeline migration uses,
# so the two modes cannot drift apart.

# Curated catalog for the interactive picker. "custom" is not needed: the prompt
# also accepts owner/repo or a git URL through --apps.
APP_CATALOG=(erpnext hrms payments helpdesk crm lms builder insights wiki print_designer webshop drive)

choose_frappe_version() {
  local choice
  choice="$(jq -r 'to_entries[] | "\(.key)\t\(.value.label)"' "$PRESETS" |
    gum choose --header "Frappe version:" | cut -f1)"
  printf '%s' "$choice"
}

# Shallow-clone the chosen branch. `git clone --depth 1 -b <branch>` is used
# rather than `submodule add -b`, because the latter only honours the remote's
# default branch; registration happens afterwards, in the shared pipeline.
clone_app() {
  local url=$1 app=$2 path="apps/$2" use_branch=""
  if git ls-remote --heads "$url" "$branch" 2>/dev/null | grep -q .; then
    use_branch="$branch"
  fi
  info "+ $path (${use_branch:-default branch}) — $url"
  if [ -n "$use_branch" ]; then
    git clone -q --depth 1 --branch "$use_branch" -- "$url" "$path"
  else
    git clone -q --depth 1 -- "$url" "$path"
  fi
}

cmd_init() {
  local a url target_abs
  local -a selected_apps=() app_names=() app_urls=()

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

  if [ -n "$apps_csv" ]; then
    IFS=',' read -ra selected_apps <<< "$apps_csv"
  elif has_tty; then
    mapfile -t selected_apps < <(
      printf '%s\n' "${APP_CATALOG[@]}" |
        gum choose --no-limit --header "Apps (space to toggle, enter to confirm; none = frappe only):" || true
    )
  fi
  for a in "${selected_apps[@]}"; do
    [ -n "$a" ] || continue
    url="$(resolve_app_url "$a")"
    app_names+=("$(basename "$url" .git)")
    app_urls+=("$url")
  done

  if [ -z "$target" ]; then
    if has_tty; then
      target="$(gum input --header "Bench directory:" --value "frappe-bench")"
    fi
    target="${target:-frappe-bench}"
  fi
  [ -n "$name" ] || name="$(basename "$target")"
  name="$(normalize_name "$name")"
  site="${site:-frappe.localhost}"

  dir_is_empty "$target" || $FORCE ||
    die "target '$target' already exists and is not empty. Use --migrate to convert an existing bench, or --init --force to scaffold into it anyway."

  if $DRY_RUN; then
    step "Plan"
    info "scaffold bench '$name' ($frappe_version → python $pyver / node ${nodejs#nodejs_}) in $target"
    info "apps           : frappe ${app_names[*]}"
    info "default site   : $site"
    printf '\n--dry-run: nothing was changed.\n'
    return 0
  fi

  step "Creating bench '$name' ($frappe_version → python $pyver / node ${nodejs#nodejs_}) in $target"
  mkdir -p "$target"
  cd "$target" || die "cannot enter $target"
  target_abs="$(pwd -P)"
  info "$target_abs"

  ensure_bench_repo
  mkdir -p apps
  step "Cloning apps"
  [ -d apps/frappe ] || clone_app "https://github.com/frappe/frappe.git" frappe
  for i in "${!app_names[@]}"; do
    [ -d "apps/${app_names[$i]}" ] || clone_app "${app_urls[$i]}" "${app_names[$i]}"
  done

  classify_apps
  run_pipeline
}
