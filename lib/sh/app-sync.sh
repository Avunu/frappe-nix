# frappe-init — `--sync`, `--check` and `--standards`: an app's managed files
# (docs/app-standards/spec.md §3.3).
#
# Both are app mode and both run in the current directory. Everything else is
# `frappe-nix sync`, which owns the templates, the profiles, the two phases and
# the exit codes (0 clean, 1 drift, 2 invalid configuration or not opted in,
# 3 environment): this file only turns frappe-init's flags into its arguments.
#
#   frappe-init --sync  [--standards <profile>] [--force] [--dry-run] [--skip-lock]
#                       [--only <path>[,<path>…]] [--init-listing] [--frappe-version version-16]
#                       [--profile-path <dir>]
#   frappe-init --check [--format text|json|github] [--only …] [--expect-rev <sha>] [--profile-path <dir>]

APP_ACTION=""             # sync | check
STANDARDS=""              # --standards <profile>: opt in (create [tool.frappe-nix])
declare -a SYNC_ARGS=()   # --format, --only, --expect-rev, --init-listing, --profile-path, passed through

# Whether the app in the current directory opted in to the app standards: its
# pyproject.toml has a [tool.frappe-nix] table (a header line, as
# lib/standards/shell.nix tests it; S35).
app_opted_in() {
  [ -f pyproject.toml ] && grep -qE '^[[:space:]]*\[{1,2}tool\.frappe-nix[].]' pyproject.toml
}

# The arguments every `frappe-nix sync --write` from frappe-init gets.
sync_write_args() {
  SYNC_WRITE=(--write)
  if [ -n "$STANDARDS" ]; then SYNC_WRITE+=(--standards "$STANDARDS"); fi
  if [ -n "$frappe_version" ]; then SYNC_WRITE+=(--frappe-version "$frappe_version"); fi
  if [ -n "$site" ]; then SYNC_WRITE+=(--site "$site"); fi
  if $FORCE; then SYNC_WRITE+=(--force); fi
  if $DRY_RUN; then SYNC_WRITE+=(--dry-run); fi
  if $SKIP_LOCK; then SYNC_WRITE+=(--skip-lock); fi
}
declare -a SYNC_WRITE=()

cmd_app_sync() {
  local -a args=()
  case "$APP_ACTION" in
    sync)
      sync_write_args
      args=("${SYNC_WRITE[@]}")
      ;;
    check)
      args=(--check)
      [ -z "$STANDARDS" ] || die "--standards opts an app in, which --check never does: run frappe-init --sync --standards $STANDARDS" 2
      ;;
    *) die "internal: unknown app action '$APP_ACTION'" ;;
  esac
  exec frappe-nix sync "${args[@]}" "${SYNC_ARGS[@]}"
}
