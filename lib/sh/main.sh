# frappe-init — usage, argument parsing and mode dispatch.
#
# Concatenated last by lib/init.nix: everything above is definitions, and this
# file is what actually runs.

RESOLVED_MODE=""

usage() {
  cat <<'EOF'
Usage: frappe-init [options] [target-dir]

Scaffold a new frappe-nix bench, or migrate an existing `bench init` bench in
place. The mode is detected from the target directory (default: the current
directory when it is a bench, otherwise a new one).

Mode:
  --init                   Force scaffold mode
  --migrate                Force migration mode (also re-syncs a frappe-nix bench)
  --force                  Relax the mode guard; never overwrites an existing file
  --dry-run                Print the plan and exit without changing anything
  -y, --yes                Assume yes (required to migrate in a non-TTY)

Common:
  --frappe-version <v>     develop | version-16 | version-15  (overrides detection)
  --name <name>            Bench name (default: existing / target dir basename)
  --site <site>            Default site (default: existing default_site)
  --skip-lock              Do not run `uv lock`
  -h, --help               Show this help

Scaffold only:
  --apps <a,b,c>           Apps to add (names, owner/repo, or git URLs)

Migration only:
  --vendor <a,b>           Force these apps to be vendored (source committed here)
  --no-vendor              Abort instead of vendoring an app with no usable remote
  --allow-file-remotes     Accept filesystem paths as submodule URLs
  --legacy-apps <policy>   shim | skip | abort, for apps with only a setup.py
  --strict                 Treat dirty / unpushed apps as errors
  --absorb-gitdirs         Run `git submodule absorbgitdirs` after registering
  --keep-db-root-password  Do not blank mariadb_root_password before committing
  --commit[=<msg>]         Commit the result instead of only staging it

With a TTY and no flags you are prompted (via gum).
EOF
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --frappe-version) frappe_version="$2"; shift 2 ;;
      --frappe-version=*) frappe_version="${1#*=}"; shift ;;
      --apps) apps_csv="$2"; shift 2 ;;
      --apps=*) apps_csv="${1#*=}"; shift ;;
      --name) name="$2"; shift 2 ;;
      --name=*) name="${1#*=}"; shift ;;
      --site) site="$2"; shift 2 ;;
      --site=*) site="${1#*=}"; shift ;;
      --vendor) FORCE_VENDOR="$2"; shift 2 ;;
      --vendor=*) FORCE_VENDOR="${1#*=}"; shift ;;
      --legacy-apps) LEGACY_APPS="$2"; shift 2 ;;
      --legacy-apps=*) LEGACY_APPS="${1#*=}"; shift ;;
      --commit) DO_COMMIT=true; shift ;;
      --commit=*) DO_COMMIT=true; COMMIT_MSG="${1#*=}"; shift ;;
      --init) MODE=init; shift ;;
      --migrate) MODE=migrate; shift ;;
      --force) FORCE=true; shift ;;
      --dry-run) DRY_RUN=true; shift ;;
      -y | --yes) ASSUME_YES=true; shift ;;
      --skip-lock) SKIP_LOCK=true; shift ;;
      --strict) STRICT=true; shift ;;
      --no-vendor) NO_VENDOR=true; shift ;;
      --allow-file-remotes) ALLOW_FILE_REMOTES=true; shift ;;
      --absorb-gitdirs) ABSORB_GITDIRS=true; shift ;;
      --keep-db-root-password) KEEP_DB_ROOT_PW=true; shift ;;
      -h | --help) usage; exit 0 ;;
      -*) usage >&2; die "unknown flag: $1" ;;
      *) target="$1"; shift ;;
    esac
  done
}

# Runs with the working directory already inside the target. Sets RESOLVED_MODE
# rather than echoing it, so `die` here exits the process instead of a subshell.
decide_mode() {
  case "$MODE" in
    init)
      dir_is_empty . || $FORCE ||
        die "'$(pwd -P)' is not empty. Use --migrate to convert an existing bench, or --init --force to scaffold into it anyway." 2
      RESOLVED_MODE=init
      return 0
      ;;
    migrate)
      looks_like_bench || $FORCE ||
        die "'$(pwd -P)' does not look like a Frappe bench (no apps/ + sites/ with an app's hooks.py). Re-run with --migrate --force to try anyway." 6
      RESOLVED_MODE=migrate
      return 0
      ;;
  esac

  if is_frappe_nix_bench; then
    info "already a frappe-nix bench — reconciling (repair mode)"
    RESOLVED_MODE=migrate
  elif has_foreign_flake && looks_like_bench; then
    die "'$(pwd -P)' is a bench, but its flake.nix is not a frappe-nix wrapper. Re-run with --migrate --force: the wrapper will be written to flake.nix.frappe-nix for you to merge." 4
  elif looks_like_bench; then
    RESOLVED_MODE=migrate
  elif looks_like_empty_bench; then
    die "'$(pwd -P)' has apps/ and sites/ but no Frappe app (nothing matches apps/*/*/hooks.py). Nothing to migrate." 7
  else
    die "'$(pwd -P)' is not empty and is not a Frappe bench. Use --init --force to scaffold into it, or --migrate --force to try anyway." 6
  fi
}

main() {
  # A stray GIT_DIR (from a hook, or from `git submodule foreach`) would send
  # every git command in this script at the wrong repository.
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY

  parse_args "$@"

  # No target given: migrate the current directory when it is a bench, else fall
  # through to scaffolding (which prompts for a directory).
  if [ -z "$target" ] && { looks_like_bench || is_frappe_nix_bench || looks_like_empty_bench; }; then
    target="."
  fi

  if [ -n "$target" ] && [ -d "$target" ] && ! dir_is_empty "$target"; then
    cd "$target" || die "cannot enter $target"
    target="."
    decide_mode
  else
    [ "$MODE" = migrate ] &&
      die "'${target:-.}' is empty or does not exist — there is nothing to migrate." 1
    RESOLVED_MODE=init
  fi

  case "$RESOLVED_MODE" in
    init) cmd_init ;;
    migrate) cmd_migrate ;;
    *) die "internal: unresolved mode" ;;
  esac
}

main "$@"
