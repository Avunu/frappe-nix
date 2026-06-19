# Portable bench shell scripts for devenv.
#
# All scripts use $FRAPPE_SITE with a guard so they work in both
# single-site and multi-tenancy setups.

{
  lib,
  pkgs,
  appsWithNode,
}:

let
  siteFlag = ''
    SITE_FLAG=""
    if [ -n "''${FRAPPE_SITE:-}" ]; then
      SITE_FLAG="--site $FRAPPE_SITE"
    fi
  '';
in
{
  bench-console.exec = ''
    ${siteFlag}
    bench $SITE_FLAG console
  '';

  bench-migrate.exec = ''
    ${siteFlag}
    bench $SITE_FLAG migrate
  '';

  bench-clear-cache.exec = ''
    ${siteFlag}
    bench $SITE_FLAG clear-cache
  '';

  bench-build.exec = ''
    bench build
  '';

  bench-update.exec = ''
    set -euo pipefail

    PULL=true
    MIGRATE=true
    BUILD=true

    for arg in "$@"; do
      case "$arg" in
        --pull)    MIGRATE=false; BUILD=false ;;
        --migrate) PULL=false;   BUILD=false  ;;
        --build)   PULL=false;   MIGRATE=false;;
        --help|-h)
          echo "Usage: bench-update [--pull | --migrate | --build]"
          echo ""
          echo "  (no flags)  Pull all apps, run migrations, build assets"
          echo "  --pull      Pull latest commits for each app only"
          echo "  --migrate   Run DB migrations only"
          echo "  --build     Build JS/CSS assets only"
          exit 0 ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
      esac
    done

    if $PULL; then
      echo "── Pulling latest commits for all app submodules ────────────"
      git submodule foreach --recursive '
        branch=$(git config -f "$toplevel/.gitmodules" "submodule.$name.branch") || {
          echo "  ⚠  $name: no branch configured in .gitmodules — skipping"
          exit 0
        }
        echo "  → $name ($branch)"
        git fetch origin --depth 1 "$branch"
        git checkout -B "$branch" "FETCH_HEAD"
        find . -name "*.pyc" -delete
      '
      echo ""
    fi

    if $MIGRATE; then
      echo "── Running migrations ───────────────────────────────────────"
      ${siteFlag}
      bench $SITE_FLAG migrate
      echo ""
    fi

    if $BUILD; then
      echo "── Building assets ──────────────────────────────────────────"
      bench build
      echo ""
    fi

    echo "✅ bench-update complete"
  '';

  bench-restore.exec = ''
    set -euo pipefail

    if [ -z "''${1:-}" ]; then
      echo "Usage: bench-restore <sql-file-path> [options]"
      echo ""
      echo "Restores the Frappe site from a SQL backup file."
      echo ""
      echo "Options (passed to bench restore):"
      echo "  --with-public-files <path>   Restore public files from tar"
      echo "  --with-private-files <path>  Restore private files from tar"
      echo "  --encryption-key <key>       Backup encryption key"
      echo "  --force                      Ignore validations and warnings"
      exit 1
    fi

    SQL_FILE="$1"
    shift

    ${siteFlag}
    echo "Restoring site ''${FRAPPE_SITE:-all sites} from $SQL_FILE..."
    exec bench $SITE_FLAG restore "$SQL_FILE" \
      --db-root-username "root" \
      --db-root-password "" \
      "$@"
  '';

  update-deps.exec = ''
    echo "Updating Python dependencies..."
    uv lock && uv sync
    echo ""
    echo "Updating Node dependencies..."
    ${lib.concatStringsSep "\n" (
      map (app: ''
        echo "  yarn install: ${app}"
        (cd "apps/${app}" && yarn install)
      '') appsWithNode
    )}
    echo ""
    echo "Done! Lock files updated. Commit uv.lock and yarn.lock files."
  '';

  provision-site.exec = ''
    set -euo pipefail
    cd "$FRAPPE_BENCH_ROOT"

    if [ -z "''${FRAPPE_SITE:-}" ]; then
      echo "ERROR: FRAPPE_SITE is not set. Set it in your .env or devenv config." >&2
      exit 1
    fi

    echo "⚠  When prompted for the MySQL root password, leave it blank and press Enter."

    ADMIN_PASS="''${1:-admin}"

    echo "Creating site $FRAPPE_SITE..."
    bench new-site "$FRAPPE_SITE" \
      --db-type mariadb \
      --db-socket "$FRAPPE_DB_SOCKET" \
      --db-root-username root \
      --admin-password "$ADMIN_PASS" \
      --set-default \
      --force

    while IFS= read -r app; do
      [ -z "$app" ] && continue
      [ "$app" = "frappe" ] && continue
      echo "Installing app: $app"
      bench --site "$FRAPPE_SITE" install-app "$app"
    done < sites/apps.txt

    echo ""
    echo "✅ Site $FRAPPE_SITE provisioned!"
    echo "   Admin password: $ADMIN_PASS"
    echo "   URL: http://localhost:''${FRAPPE_WEBSERVER_PORT:-8000}"
  '';

  # Add an existing app as a git submodule and register it in the uv workspace
  # ([tool.uv.workspace] members + [tool.uv.sources]) and sites/apps.txt.
  bench-get-app = {
    exec = ''
      if [ -z "''${1:-}" ]; then
        echo "Usage: bench-get-app <url-or-alias>"
        echo ""
        echo "Adds a Frappe app as a git submodule and integrates it into the workspace."
        echo ""
        echo "Examples:"
        echo "  bench-get-app frappe/payments"
        echo "  bench-get-app https://github.com/frappe/payments.git"
        exit 1
      fi

      INPUT="$1"
      cd "$FRAPPE_BENCH_ROOT"

      if [[ "$INPUT" == */* ]] && [[ "$INPUT" != *://* ]]; then
        URL="https://github.com/$INPUT.git"
      else
        URL="$INPUT"
      fi

      APP_NAME=$(basename "$URL" .git)
      APP_DIR="apps/$APP_NAME"

      if [ -d "$APP_DIR" ]; then
        echo "Error: App '$APP_NAME' already exists in $APP_DIR"
        exit 1
      fi

      echo "Adding git submodule: $URL -> $APP_DIR"
      git submodule add "$URL" "$APP_DIR"
      git submodule update --init --recursive "$APP_DIR"

      echo "Registering $APP_NAME in pyproject.toml workspace..."
      dasel put -f pyproject.toml -t string 'tool.uv.workspace.members.append()' "apps/$APP_NAME"
      dasel put -f pyproject.toml -t bool "tool.uv.sources.$APP_NAME.workspace" true

      echo "Adding $APP_NAME to sites/apps.txt..."
      if ! grep -q "^$APP_NAME$" sites/apps.txt 2>/dev/null; then
        echo "$APP_NAME" >> sites/apps.txt
      fi

      echo "Syncing Python dependencies..."
      uv sync

      echo ""
      echo "✅ App '$APP_NAME' added successfully!"
      echo ""
      echo "Next steps:"
      echo "  1. Restart devenv: direnv reload --no-eval-cache"
      echo "  2. Install the app: bench --site ''${FRAPPE_SITE:-<site>} install-app $APP_NAME"
    '';
    packages = [ pkgs.dasel ];
    description = "Add a Frappe app from a git URL/alias as a submodule and register it in the uv workspace.";
  };

  # Scaffold a brand-new app and register it in the uv workspace. Wraps
  # `bench new-app`, whose trailing pip-install step fails in the read-only Nix
  # env (expected/ignored).
  bench-new-app = {
    exec = ''
      if [ -z "''${1:-}" ]; then
        echo "Usage: bench-new-app <app-name>"
        echo ""
        echo "Creates a new Frappe app and integrates it into the uv workspace."
        exit 1
      fi

      APP_NAME="$1"
      APP_DIR="apps/$APP_NAME"
      cd "$FRAPPE_BENCH_ROOT"

      if [ -d "$APP_DIR" ] && [ -f "$APP_DIR/pyproject.toml" ]; then
        echo "Error: App '$APP_NAME' already exists in $APP_DIR"
        exit 1
      fi

      echo "Creating app scaffold with 'bench new-app --no-git $APP_NAME'..."
      echo "⚠  The pip-install step will fail (read-only Nix env) — this is expected."
      bench new-app --no-git "$APP_NAME" || true

      if [ ! -f "$APP_DIR/pyproject.toml" ]; then
        echo "Error: App scaffold was not created at $APP_DIR"
        exit 1
      fi

      echo "Registering $APP_NAME in pyproject.toml workspace..."
      dasel put -f pyproject.toml -t string 'tool.uv.workspace.members.append()' "apps/$APP_NAME"
      dasel put -f pyproject.toml -t bool "tool.uv.sources.$APP_NAME.workspace" true

      echo "Adding $APP_NAME to sites/apps.txt..."
      if ! grep -q "^$APP_NAME$" sites/apps.txt 2>/dev/null; then
        echo "$APP_NAME" >> sites/apps.txt
      fi

      echo "Syncing Python dependencies..."
      uv sync

      command -v direnv >/dev/null 2>&1 && direnv reload || true

      echo ""
      echo "✅ App '$APP_NAME' created and integrated!"
      echo "   Install it with: bench --site ''${FRAPPE_SITE:-<site>} install-app $APP_NAME"
    '';
    packages = [ pkgs.dasel ];
    description = "Scaffold a new Frappe app and integrate it into the uv workspace.";
  };
}
