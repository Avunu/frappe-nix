# frappe-init — migrate mode: convert an existing bench in place.
#
# Assumes the caller has already chdir'd into the bench. Nothing here is
# destructive: superseded files move to .frappe-nix-backup/ rather than being
# deleted, and existing files are merged rather than overwritten.

resolve_migration_preset() {
  local detected key why
  if [ -n "$frappe_version" ]; then
    preset_exists "$frappe_version" ||
      die "unknown frappe version '$frappe_version' (expected: $(preset_keys | tr '\n' ' '))" 5
    detected="$(detect_frappe_version)"
    key="${detected%%$'\t'*}"
    if [ -n "$key" ] && [ "$key" != UNSUPPORTED ] && [ "$key" != "$frappe_version" ]; then
      warn "--frappe-version $frappe_version, but this bench looks like $key — the python/node pins will not match the code"
    fi
    resolve_preset
    return 0
  fi

  detected="$(detect_frappe_version)"
  key="${detected%%$'\t'*}"
  why="${detected#*$'\t'}"

  if [ "$key" = UNSUPPORTED ]; then
    die "apps/frappe is version $why. frappe-nix has no preset below version-15: those apps ship a setup.py with no pyproject.toml and cannot be uv workspace members, and the python/node pins differ. Upgrade the bench first (bench switch-to-branch version-15), or re-run with --frappe-version version-15 to proceed anyway." 5
  fi
  if [ -z "$key" ]; then
    if has_tty; then
      warn "could not detect the frappe version from apps/frappe"
      frappe_version="$(choose_frappe_version)"
    else
      die "could not detect the frappe version; pass --frappe-version (one of: $(preset_keys | tr '\n' ' '))" 5
    fi
  else
    frappe_version="$key"
    info "frappe version : $frappe_version (detected from $why)"
  fi
  resolve_preset
}

# The bench's own default_site is the right answer; fall back to a sole site
# directory before inventing one.
resolve_migration_site() {
  local candidates
  [ -n "$site" ] && return 0
  if [ -f sites/common_site_config.json ]; then
    site="$(jq -r '.default_site // empty' sites/common_site_config.json 2>/dev/null || true)"
  fi
  [ -n "$site" ] && return 0
  mapfile -t candidates < <(
    find sites -maxdepth 2 -name site_config.json -printf '%h\n' 2>/dev/null |
      xargs -r -n1 basename || true
  )
  case "${#candidates[@]}" in
    0) site="frappe.localhost" ;;
    1) site="${candidates[0]}" ;;
    *)
      if has_tty; then
        site="$(printf '%s\n' "${candidates[@]}" | gum choose --header "Default site:")"
      else
        die "this bench has several sites and no default_site; pass --site" 5
      fi
      ;;
  esac
}

cmd_migrate() {
  case "$(bench_repo_state)" in
    nested)
      warn "this bench is inside another git repository ($(git rev-parse --show-toplevel)); frappe-nix needs the bench root to be its own repo"
      $FORCE || die "re-run with --force to create a nested repository anyway" 8
      ;;
    *) : ;;
  esac

  resolve_migration_preset
  resolve_migration_site
  [ -n "$name" ] || name="$(basename "$(pwd -P)")"
  name="$(normalize_name "$name")"

  classify_apps
  print_plan

  if $DRY_RUN; then
    printf '\n--dry-run: nothing was changed.\n'
    return 0
  fi
  if ! $ASSUME_YES; then
    confirm "Migrate this bench to frappe-nix?" ||
      die "aborted. Re-run with --yes (or --dry-run to inspect the plan)." 6
  fi

  if has_foreign_flake; then
    $FORCE || die "flake.nix exists and is not a frappe-nix wrapper. Re-run with --force: the frappe-nix wrapper will be written to flake.nix.frappe-nix for you to merge." 4
    render_template
    cp "$STAGING/flake.nix" flake.nix.frappe-nix
    warn "flake.nix is not a frappe-nix wrapper; the wrapper was written to flake.nix.frappe-nix — merge it by hand"
  fi

  run_pipeline
}
