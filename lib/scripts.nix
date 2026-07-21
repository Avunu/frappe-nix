# Portable bench shell scripts for devenv.
#
# All scripts use $FRAPPE_SITE with a guard so they work in both
# single-site and multi-tenancy setups.

{
  lib,
  pkgs,
  appsWithNode,
  # Absolute path to the real bench CLI (devPythonEnv/bin/bench). The umbrella
  # `bench` wrapper and the re-entrancy guard use it to reach the unwrapped bench.
  benchBin ? "bench",
}:

let
  siteFlag = ''
    SITE_FLAG=""
    if [ -n "''${FRAPPE_SITE:-}" ]; then
      SITE_FLAG="--site $FRAPPE_SITE"
    fi
  '';

  # Python with tomlkit for format-preserving pyproject.toml edits.
  pythonToml = pkgs.python3.withPackages (ps: [ ps.tomlkit ]);

  # Shell snippet: register "$APP_NAME" as a uv workspace member — appends
  # apps/$APP_NAME to [tool.uv.workspace].members and sets
  # [tool.uv.sources].$APP_NAME.workspace = true. Idempotent and
  # comment-preserving (tomlkit). Expects $APP_NAME set and cwd at bench root.
  # (dasel is not used: nixpkgs' dasel is the query-only rewrite without `put`.)
  registerWorkspaceMember = ''
    echo "Registering $APP_NAME in pyproject.toml workspace..."
    ${pythonToml}/bin/python3 -c 'import sys, tomlkit; doc = tomlkit.parse(open("pyproject.toml").read()); uv = doc["tool"]["uv"]; m = uv["workspace"]["members"]; e = "apps/" + sys.argv[1]; (e in m) or m.append(e); uv.setdefault("sources", tomlkit.table()); s = uv["sources"]; (sys.argv[1] in s) or s.__setitem__(sys.argv[1], tomlkit.inline_table()); s[sys.argv[1]].setdefault("workspace", True); open("pyproject.toml", "w").write(tomlkit.dumps(doc))' "$APP_NAME"
  '';

  # Shell snippet: add "$APP_NAME" to sites/apps.txt if absent, ensuring the file
  # ends with a newline first (frappe's apps.txt has no trailing newline, so a
  # naive `echo >>` concatenates onto the last app).
  addToAppsTxt = ''
    if ! grep -qx "$APP_NAME" sites/apps.txt 2>/dev/null; then
      echo "Adding $APP_NAME to sites/apps.txt..."
      if [ -s sites/apps.txt ] && [ -n "$(tail -c1 sites/apps.txt)" ]; then
        echo >> sites/apps.txt
      fi
      echo "$APP_NAME" >> sites/apps.txt
    fi
  '';
in
{
  # Umbrella wrapper: shadows devPythonEnv/bin/bench (devenv wraps scripts with
  # hiPrioSet, so this wins on PATH) and transparently redirects the subcommands
  # that need frappe-nix handling, passing everything else through to the real
  # bench. The specialized scripts export _FRAPPE_BENCH_RAW=1, so their own nested
  # `bench …` calls re-enter here and fall through to ${benchBin} rather than
  # recursing — true whether a command is run via `bench update` or `bench-update`.
  bench.exec = ''
    if [ -n "''${_FRAPPE_BENCH_RAW:-}" ]; then
      exec ${benchBin} "$@"
    fi
    case "''${1:-}" in
      update)      shift; exec bench-update "$@" ;;
      get-app)     shift; exec bench-get-app "$@" ;;
      new-app)     shift; exec bench-new-app "$@" ;;
      restore)     shift; exec bench-restore "$@" ;;
      migrate)     shift; exec bench-migrate "$@" ;;
      console)     shift; exec bench-console "$@" ;;
      clear-cache) shift; exec bench-clear-cache "$@" ;;
      new-site)
        # Inject env-specific DB connection flags so site creation isn't
        # interactive; provision-site stays the create-and-install-all flow.
        shift
        exec ${benchBin} new-site --db-socket "$FRAPPE_DB_SOCKET" --db-root-username root "$@" ;;
      *)           exec ${benchBin} "$@" ;;
    esac
  '';

  bench-console.exec = ''
    export _FRAPPE_BENCH_RAW=1
    ${siteFlag}
    bench $SITE_FLAG console "$@"
  '';

  bench-migrate.exec = ''
    export _FRAPPE_BENCH_RAW=1
    ${siteFlag}
    bench $SITE_FLAG migrate "$@"
  '';

  bench-clear-cache.exec = ''
    export _FRAPPE_BENCH_RAW=1
    ${siteFlag}
    bench $SITE_FLAG clear-cache "$@"
  '';

  bench-build.exec = ''
    export _FRAPPE_BENCH_RAW=1
    bench build "$@"
  '';

  bench-update.exec = ''
    set -euo pipefail
    export _FRAPPE_BENCH_RAW=1

    PULL=true
    MIGRATE=true
    BUILD=true
    FORCE_NODE_HASHES=false

    for arg in "$@"; do
      case "$arg" in
        --pull)        MIGRATE=false; BUILD=false ;;
        --migrate)     PULL=false;   BUILD=false  ;;
        --build)       PULL=false;   MIGRATE=false ;;
        --node-hashes) PULL=false;   MIGRATE=false; BUILD=false; FORCE_NODE_HASHES=true ;;
        --help|-h)
          echo "Usage: bench-update [--pull | --migrate | --build | --node-hashes]"
          echo ""
          echo "  (no flags)     Pull apps, refresh node hashes, migrate, build"
          echo "  --pull         Pull latest commits + refresh changed node hashes"
          echo "  --migrate      Run DB migrations only"
          echo "  --build        Build JS/CSS assets only"
          echo "  --node-hashes  Force-regenerate node-offline-hashes.json (all apps)"
          exit 0 ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
      esac
    done

    cd "$FRAPPE_BENCH_ROOT"
    HASHES_FILE="node-offline-hashes.json"

    # Compute the fetchYarnDeps offline-cache hash for one app ($1) by building
    # the real derivation with a fake hash and reading the reported `got:` value.
    # (prefetch-yarn-deps' standalone hash does NOT match fetchYarnDeps' FOD hash,
    # which also embeds the yarn.lock.)
    _offline_hash() {
      nix build --impure --no-link --no-warn-dirty --expr "
        let pkgs = import ${pkgs.path} { system = builtins.currentSystem; };
        in pkgs.fetchYarnDeps { yarnLock = $FRAPPE_BENCH_ROOT/apps/$1/yarn.lock; hash = pkgs.lib.fakeHash; }
      " 2>&1 | awk '/got:/ { print $NF; exit }' || true
    }

    _write_hash() {
      local app="$1" h="$2" tmp
      tmp=$(mktemp)
      { [ -f "$HASHES_FILE" ] && cat "$HASHES_FILE" || echo '{}'; } \
        | ${pkgs.jq}/bin/jq --sort-keys --arg a "$app" --arg h "$h" '.[$a] = $h' > "$tmp"
      mv "$tmp" "$HASHES_FILE"
    }

    _regen_hashes() {
      if [ "$#" -eq 0 ]; then
        echo "  node-offline-hashes.json already up to date"
        return 0
      fi
      echo "── Regenerating node-offline-hashes.json for:$(printf ' %s' "$@") ──"
      for app in "$@"; do
        echo "  prefetching $app (downloads yarn deps)…"
        h=$(_offline_hash "$app")
        if [ -z "$h" ]; then
          echo "  ⚠  could not compute hash for $app" >&2
          continue
        fi
        _write_hash "$app" "$h"
        echo "  $app = $h"
      done
    }

    if $PULL; then
      echo "── Pulling latest commits for all app submodules ────────────"
      declare -A _before_lock
      for lock in apps/*/yarn.lock; do
        [ -e "$lock" ] || continue
        _before_lock["$lock"]=$(git hash-object "$lock" 2>/dev/null || echo none)
      done

      git submodule foreach '
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

      # Refresh node hashes for apps whose yarn.lock changed or are not yet recorded.
      changed=()
      for lock in apps/*/yarn.lock; do
        [ -e "$lock" ] || continue
        app=$(basename "$(dirname "$lock")")
        after=$(git hash-object "$lock" 2>/dev/null || echo none)
        if [ "''${_before_lock["$lock"]:-none}" != "$after" ] \
           || ! ${pkgs.jq}/bin/jq -e --arg a "$app" 'has($a)' "$HASHES_FILE" >/dev/null 2>&1; then
          changed+=("$app")
        fi
      done
      _regen_hashes "''${changed[@]}"
      echo ""
    fi

    if $FORCE_NODE_HASHES; then
      all_apps=()
      for lock in apps/*/yarn.lock; do
        [ -e "$lock" ] || continue
        all_apps+=("$(basename "$(dirname "$lock")")")
      done
      _regen_hashes "''${all_apps[@]}"
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
    export _FRAPPE_BENCH_RAW=1

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
    export _FRAPPE_BENCH_RAW=1
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
      set -euo pipefail
      export _FRAPPE_BENCH_RAW=1

      if [ -z "''${1:-}" ]; then
        echo "Usage: bench-get-app <url-or-alias>"
        echo ""
        echo "Adds a Frappe app as a git submodule and integrates it into the workspace."
        echo ""
        echo "Examples:"
        echo "  bench-get-app helpdesk                              # → frappe/helpdesk"
        echo "  bench-get-app frappe/payments                      # owner/repo on GitHub"
        echo "  bench-get-app https://github.com/frappe/hrms.git   # full URL"
        exit 1
      fi

      INPUT="$1"
      cd "$FRAPPE_BENCH_ROOT"

      # Resolve the app source URL:
      #   full URL (scheme:// or git@…)  → used as-is
      #   owner/repo                     → https://github.com/owner/repo.git
      #   bare name                      → https://github.com/frappe/<name>.git
      if [[ "$INPUT" == *://* ]] || [[ "$INPUT" == git@* ]]; then
        URL="$INPUT"
      elif [[ "$INPUT" == */* ]]; then
        URL="https://github.com/$INPUT.git"
      else
        URL="https://github.com/frappe/$INPUT.git"
      fi

      APP_NAME=$(basename "$URL" .git)
      APP_DIR="apps/$APP_NAME"

      if [ -d "$APP_DIR" ]; then
        echo "Error: App '$APP_NAME' already exists in $APP_DIR"
        exit 1
      fi

      echo "Adding git submodule: $URL -> $APP_DIR"
      git submodule add "$URL" "$APP_DIR"
      # Not --recursive: Frappe apps often ship broken nested submodules that
      # have no production role and would fail init.
      git submodule update --init "$APP_DIR"

      ${registerWorkspaceMember}

      ${addToAppsTxt}

      echo "Syncing Python dependencies..."
      uv sync

      echo ""
      echo "✅ App '$APP_NAME' added successfully!"
      echo ""
      echo "Next steps:"
      echo "  1. Restart devenv: direnv reload --no-eval-cache"
      echo "  2. Install the app: bench --site ''${FRAPPE_SITE:-<site>} install-app $APP_NAME"
    '';
    description = "Add a Frappe app from a git URL/alias as a submodule and register it in the uv workspace.";
  };

  # Scaffold a brand-new app and register it in the uv workspace. Wraps
  # `bench new-app`, whose trailing pip-install step fails in the read-only Nix
  # env (expected/ignored).
  bench-new-app = {
    exec = ''
      set -euo pipefail
      export _FRAPPE_BENCH_RAW=1

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

      ${registerWorkspaceMember}

      ${addToAppsTxt}

      echo "Syncing Python dependencies..."
      uv sync

      command -v direnv >/dev/null 2>&1 && direnv reload || true

      echo ""
      echo "✅ App '$APP_NAME' created and integrated!"
      echo "   Install it with: bench --site ''${FRAPPE_SITE:-<site>} install-app $APP_NAME"
    '';
    description = "Scaffold a new Frappe app and integrate it into the uv workspace.";
  };
}
