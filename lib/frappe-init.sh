# frappe-init — scaffold a new frappe-nix bench.
# Baked at build time:
PRESETS="@PRESETS@"
TEMPLATE="@TEMPLATE@"

# Curated app catalog (name → github.com/frappe/<name>). "custom" lets the user
# enter any owner/repo or git URL.
APP_CATALOG=(erpnext hrms payments helpdesk crm lms builder insights wiki print_designer webshop drive)

usage() {
  cat <<'EOF'
Usage: frappe-init [options] [target-dir]

Scaffold a new frappe-nix-managed Frappe bench.

Options:
  --frappe-version <v>   Preset: develop | version-16 | version-15
  --apps <a,b,c>         Comma-separated apps (names, owner/repo, or git URLs)
  --name <name>          Bench name (default: target dir basename)
  --site <site>          Default site (default: frappe.localhost)
  -h, --help             Show this help

With a TTY and no flags, you'll be prompted interactively (via gum).
EOF
}

frappe_version=""
apps_csv=""
name=""
site=""
target=""

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
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown flag: $1" >&2; usage >&2; exit 1 ;;
    *) target="$1"; shift ;;
  esac
done

has_tty() { [ -t 0 ] && [ -t 1 ]; }

# ── frappe version ────────────────────────────────────────────────────────
mapfile -t preset_keys < <(jq -r 'keys_unsorted[]' "$PRESETS")
if [ -z "$frappe_version" ]; then
  if has_tty; then
    frappe_version="$(printf '%s\n' "${preset_keys[@]}" | gum choose --header "Frappe version:")"
  else
    echo "ERROR: --frappe-version is required (one of: ${preset_keys[*]})" >&2
    exit 1
  fi
fi
if ! jq -e --arg v "$frappe_version" 'has($v)' "$PRESETS" >/dev/null; then
  echo "ERROR: unknown frappe version '$frappe_version' (expected: ${preset_keys[*]})" >&2
  exit 1
fi

preset_field() { jq -r --arg v "$frappe_version" --arg k "$1" '.[$v][$k]' "$PRESETS"; }
branch="$(preset_field branch)"
python="$(preset_field python)"
nodejs="$(preset_field nodejs)"
requires_python="$(preset_field requiresPython)"
overrides="$(jq -c --arg v "$frappe_version" '.[$v].overrideDependencies' "$PRESETS")"

pynum="${python#python}"
pytag="py${pynum}"
pyver="${pynum:0:1}.${pynum:1}"

# ── apps ──────────────────────────────────────────────────────────────────
selected_apps=()
if [ -n "$apps_csv" ]; then
  IFS=',' read -ra selected_apps <<< "$apps_csv"
elif has_tty; then
  mapfile -t selected_apps < <(
    printf '%s\n' "${APP_CATALOG[@]}" \
      | gum choose --no-limit --header "Apps (space to toggle, enter to confirm; none = frappe only):" || true
  )
fi

# Normalize each selection into a (name, url) pair.
app_names=()
app_urls=()
for a in "${selected_apps[@]}"; do
  [ -z "$a" ] && continue
  if [[ "$a" == *://* ]] || [[ "$a" == git@* ]]; then
    url="$a"
  elif [[ "$a" == */* ]]; then
    url="https://github.com/$a.git"
  else
    url="https://github.com/frappe/$a.git"
  fi
  app_names+=("$(basename "$url" .git)")
  app_urls+=("$url")
done

# ── name / site / target dir ──────────────────────────────────────────────
if [ -z "$target" ]; then
  if has_tty; then
    target="$(gum input --header "Bench directory:" --value "frappe-bench")"
  fi
  target="${target:-frappe-bench}"
fi
if [ -z "$name" ]; then
  name="$(basename "$target")"
fi
# Normalize the bench name to a safe identifier (used as a Nix string + image prefix).
name="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed -E 's/-+/-/g; s/^-//; s/-$//')"
[ -z "$name" ] && name="frappe-bench"
site="${site:-frappe.localhost}"

if [ -e "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
  echo "ERROR: target '$target' already exists and is not empty" >&2
  exit 1
fi

echo "Creating bench '$name' ($frappe_version → python $pyver / ${nodejs#nodejs_} node) in $target"

# ── lay down the template ─────────────────────────────────────────────────
mkdir -p "$target"
cp -R "$TEMPLATE"/. "$target"/
chmod -R u+w "$target"
cd "$target" || { echo "ERROR: cannot enter $target" >&2; exit 1; }
target_abs="$(pwd)"

# Scalar token substitution.
sed -i \
  -e "s|@BENCH_NAME@|$name|g" \
  -e "s|@SITE_NAME@|$site|g" \
  -e "s|@PYTHON@|$python|g" \
  -e "s|@NODEJS@|$nodejs|g" \
  -e "s|@REQUIRES_PYTHON@|$requires_python|g" \
  -e "s|@PYTAG@|$pytag|g" \
  -e "s|@PYVER@|$pyver|g" \
  flake.nix pyproject.toml sites/common_site_config.json README.md
sed -i -e "s|@OVERRIDES@|$overrides|" pyproject.toml

# Workspace members / sources / apps.txt (frappe is always first).
members="$(printf '    "apps/%s",\n' frappe "${app_names[@]}")"
members="${members%$'\n'}"
sources=""
for n in frappe "${app_names[@]}"; do
  sources+="$n = { workspace = true }"$'\n'
done
sources="${sources%$'\n'}"

awk -v members="$members" -v sources="$sources" '
  $0 == "@MEMBERS@" { print members; next }
  $0 == "@SOURCES@" { print sources; next }
  { print }
' pyproject.toml > pyproject.toml.tmp && mv pyproject.toml.tmp pyproject.toml

printf '%s\n' frappe "${app_names[@]}" > sites/apps.txt

# ── git init + submodules ─────────────────────────────────────────────────
git init -q

add_app_submodule() {
  local url="$1" app="$2" use_branch=""
  local path="apps/$app"
  if git ls-remote --heads "$url" "$branch" 2>/dev/null | grep -q .; then
    use_branch="$branch"
  fi
  echo "  + $path (${use_branch:-default branch}) — $url"
  # Shallow-clone the chosen branch, then register the checkout as a submodule.
  # (`git submodule add --depth 1 -b <branch>` only works for the remote's
  # default branch, so we clone first and add the existing repo with --force.)
  if [ -n "$use_branch" ]; then
    git clone -q --depth 1 --branch "$use_branch" -- "$url" "$path"
    git submodule add -q --force -b "$use_branch" -- "$url" "$path"
  else
    git clone -q --depth 1 -- "$url" "$path"
    git submodule add -q --force -- "$url" "$path"
  fi
  git config -f .gitmodules "submodule.$path.shallow" true
}

echo "Adding app submodules…"
add_app_submodule "https://github.com/frappe/frappe.git" frappe
for i in "${!app_names[@]}"; do
  add_app_submodule "${app_urls[$i]}" "${app_names[$i]}"
done
# Initialize only the direct app submodules. Frappe apps often carry broken
# nested submodules (missing/incorrect .gitmodules refs) with no production
# role, so we deliberately do NOT recurse into them.
git submodule update --init

# ── resolve the python workspace ──────────────────────────────────────────
echo "Resolving Python workspace (uv lock)…"
if ! uv lock; then
  echo "" >&2
  echo "⚠  uv lock failed. This is usually a version conflict — add the offending" >&2
  echo "   pin to [tool.uv] override-dependencies in pyproject.toml and re-run 'uv lock'." >&2
fi

git add -A

cat <<EOF

✅ Bench '$name' created in $target_abs
   frappe version : $frappe_version (python $pyver / node ${nodejs#nodejs_})
   apps           : frappe ${app_names[*]}

Next steps:
  cd $target
  direnv allow            # or: nix develop --no-pure-eval
  devenv up               # start MariaDB, Redis, web, scheduler, worker, …
  provision-site          # (in another shell) create the site + install apps
EOF
