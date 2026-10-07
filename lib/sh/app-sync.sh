# frappe-init — `--sync` and `--check`: an app's managed files (docs/ironclad/spec.md §3.3).
#
# Both are app mode and both run in the current directory. Everything else is
# `ironclad sync`, which owns the templates, the two phases and the exit codes
# (0 clean, 1 drift, 2 invalid configuration, 3 environment): this file only
# turns frappe-init's flags into its arguments and execs it.
#
#   frappe-init --sync  [--dry-run] [--skip-lock] [--only <path>[,<path>…]] [--init-listing] [--frappe-version version-16]
#   frappe-init --check [--format text|json|github] [--only …] [--expect-rev <sha>]

APP_ACTION=""             # sync | check
declare -a SYNC_ARGS=()   # --format, --only, --expect-rev, --init-listing, passed through

cmd_app_sync() {
  local -a args=()
  case "$APP_ACTION" in
    sync) args+=(--write) ;;
    check) args+=(--check) ;;
    *) die "internal: unknown app action '$APP_ACTION'" ;;
  esac
  if $DRY_RUN; then args+=(--dry-run); fi
  if $SKIP_LOCK; then args+=(--skip-lock); fi
  if [ -n "$frappe_version" ]; then args+=(--frappe-version "$frappe_version"); fi
  exec ironclad sync "${args[@]}" "${SYNC_ARGS[@]}"
}
