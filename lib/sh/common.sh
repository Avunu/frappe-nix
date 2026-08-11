# frappe-init — shared helpers.
#
# This file is concatenated with the other lib/sh/*.sh sources into a single
# script by lib/init.nix, so shellcheck sees the whole program at build time.
# Definitions only; `main` runs from main.sh, which must be concatenated last.

# Baked at build time by lib/init.nix.
PRESETS="@PRESETS@"
TEMPLATE="@TEMPLATE@"

# ── globals ───────────────────────────────────────────────────────────────
# Declared up front because the script runs under `set -o nounset`.
MODE="auto"          # auto | init | migrate
FORCE=false
DRY_RUN=false
ASSUME_YES=false
SKIP_LOCK=false
DO_COMMIT=false
COMMIT_MSG="chore: migrate bench to frappe-nix"
STRICT=false
NO_VENDOR=false
ALLOW_FILE_REMOTES=false
ABSORB_GITDIRS=false
KEEP_DB_ROOT_PW=false
LEGACY_APPS=""       # "" = auto (shim when vendored, skip when a submodule)
FORCE_VENDOR=""      # comma-separated app names

frappe_version=""
apps_csv=""
name=""
site=""
target=""

# Preset fields, filled by resolve_preset.
branch=""
python=""
nodejs=""
requires_python=""
overrides=""
pytag=""
pyver=""

# Per-run state.
declare -a WARNINGS=()
declare -a VENDORED=()
declare -a MEMBER_APPS=()
declare -a APPS_TXT_ADD=()

# [project].name for the workspace root. Usually $name, but see
# resolve_project_name: it must not collide with a workspace member.
PROJECT_NAME=""

# ── output ────────────────────────────────────────────────────────────────
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() {
  WARNINGS+=("$*")
  printf '  \033[33m⚠\033[0m  %s\n' "$*" >&2
}
err() { printf '  \033[31m✗\033[0m  %s\n' "$*" >&2; }
die() {
  printf '\033[31mERROR:\033[0m %s\n' "$*" >&2
  exit "${2:-1}"
}

has_tty() { [ -t 0 ] && [ -t 1 ]; }

# Prompt only when we can; never emit a gum call with stdin closed. `gum
# confirm` exits 1 on "no", which would trip `set -e` outside an if.
confirm() {
  has_tty || return 1
  gum confirm "$1"
}

# ── presets ───────────────────────────────────────────────────────────────
preset_keys() { jq -r 'keys_unsorted[]' "$PRESETS"; }
preset_exists() { [ -n "${1:-}" ] && jq -e --arg v "$1" 'has($v)' "$PRESETS" >/dev/null; }
preset_field() { jq -r --arg v "$frappe_version" --arg k "$1" '.[$v][$k]' "$PRESETS"; }

# Fill the preset globals from $frappe_version. python/nodejs feed the generated
# flake.nix; requires_python/overrides feed pyproject.toml; branch is only used
# by the scaffold path (migration records each app's own branch instead).
resolve_preset() {
  local pynum
  branch="$(preset_field branch)"
  python="$(preset_field python)"
  nodejs="$(preset_field nodejs)"
  requires_python="$(preset_field requiresPython)"
  overrides="$(jq -c --arg v "$frappe_version" '.[$v].overrideDependencies' "$PRESETS")"
  pynum="${python#python}"
  pytag="py${pynum}"
  pyver="${pynum:0:1}.${pynum:1}"
}

# ── names / urls ──────────────────────────────────────────────────────────
# Normalize to a safe identifier (used as a Nix string and a container image
# prefix).
normalize_name() {
  local n
  n="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed -E 's/-+/-/g; s/^-//; s/-$//')"
  printf '%s' "${n:-frappe-bench}"
}

# full URL → as-is; owner/repo → github.com/owner/repo; bare name → frappe/<name>.
resolve_app_url() {
  case "$1" in
    *://* | git@*) printf '%s' "$1" ;;
    */*) printf 'https://github.com/%s.git' "$1" ;;
    *) printf 'https://github.com/frappe/%s.git' "$1" ;;
  esac
}

# PEP 503 distribution-name normalization, so `print_designer` and
# `print-designer` compare equal the way uv sees them.
normalize_dist() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[-_.]+/-/g'
}

in_list() {
  local needle=$1 item
  shift
  for item in "$@"; do
    [ "$item" = "$needle" ] && return 0
  done
  return 1
}
