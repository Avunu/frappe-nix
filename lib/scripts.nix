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
  # lib/secrets-tools.nix. `enabled = false` when the bench declares no secrets,
  # in which case the secret scripts are omitted entirely rather than shipped as
  # stubs that fail late.
  secrets ? { enabled = false; },
  # Absolute path to lib/node-modules.nix's tool. Left as a bare name for a
  # consumer that instantiates this file on its own; the dev shell passes the
  # store path.
  nodeModulesBin ? "frappe-nix-node-modules",
  # perSystem.frappe-nix.restore, plus `fetch` (the frappe-nix-backup-fetch
  # binary) and `devguard` (whether this bench's guard rails are on).
  # `enable = false` leaves `bench restore` with its explicit-file behaviour.
  restore ? { enable = false; },
}:

let
  siteFlag = ''
    SITE_FLAG=""
    if [ -n "''${FRAPPE_SITE:-}" ]; then
      SITE_FLAG="--site $FRAPPE_SITE"
    fi
  '';

  # The one implementation of the apps/ ⇄ pyproject.toml ⇄ sites/apps.txt
  # contract, shared with the `frappe-init` scaffolder/migrator so the two
  # cannot drift apart. Idempotent and comment-preserving (tomlkit).
  workspaceTool = import ./workspace-tool.nix { inherit pkgs; };
  workspaceBin = "${workspaceTool}/bin/frappe-nix-workspace";

  # Shell snippet: register "$APP_NAME" as a uv workspace member — appends
  # apps/$APP_NAME to [tool.uv.workspace].members and adds a [tool.uv.sources]
  # entry keyed on the app's own [project].name (which is what uv resolves the
  # member to; a dir-named key is inert when the two differ, e.g.
  # print_designer → print-designer). Expects $APP_NAME set and cwd at bench root.
  registerWorkspaceMember = ''
    echo "Registering $APP_NAME in pyproject.toml workspace..."
    ${workspaceBin} add-app --pyproject pyproject.toml --app "$APP_NAME"
  '';

  # Shell snippet: bring every app's node_modules back in step with its
  # manifests. A no-op when nothing moved, so it is cheap enough to run in front
  # of every build — which is the point: `bench update` pulls the app commit that
  # adds a dependency and then builds in the same breath, and only this stands
  # between those two steps. Expects cwd at the bench root.
  refreshNodeModules = lib.optionalString (appsWithNode != [ ]) ''
    ${nodeModulesBin} . ${lib.escapeShellArgs appsWithNode}
  '';

  # The same, downgraded to a warning — for the paths where node_modules is not
  # what the command is about and a yarn failure should not abort it.
  refreshNodeModulesSoft = lib.optionalString (appsWithNode != [ ]) ''
    ${nodeModulesBin} . ${lib.escapeShellArgs appsWithNode} || true
  '';

  # Shell snippet: add "$APP_NAME" to sites/apps.txt if absent. Frappe writes
  # that file without a trailing newline, so a naive `echo >>` would concatenate
  # onto the last app; the tool rewrites the whole list instead.
  addToAppsTxt = ''
    ${workspaceBin} apps-txt --file sites/apps.txt --add "$APP_NAME"
  '';

  # ── secrets ───────────────────────────────────────────────────────────────
  # Only defined when the bench declares secrets; merged in below. They live
  # here, with every other script, rather than being contributed separately from
  # modules/secrets.nix — modules/devenv.nix assigns `scripts` wholesale as
  # `scripts // cfg.extraScripts`, so a second module defining the same
  # attribute would turn a consumer's `extraScripts.edit-secret` into a
  # *conflict* instead of the documented override.
  secretNames = lib.concatMapStringsSep "\n" (s: "  ${s.name}") (secrets.names or [ ]);

  secretScripts = lib.optionalAttrs (secrets.enabled or false) {
    edit-secret.exec = ''
      set -euo pipefail
      cd "$FRAPPE_BENCH_ROOT"

      if [ -z "''${1:-}" ] || [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        cat <<'EOF'
      Usage: edit-secret <name> [identity-file]

      Decrypts a secret into $EDITOR and re-encrypts it to the recipients
      declared in flake.nix. Creates it if it does not exist.

      Declared secrets:
      ${secretNames}

      The recipient list is generated from `frappe-nix.secrets.recipients`, so
      there is no secrets.nix to keep in step. After changing that list, run
      `rekey-secrets`.
      EOF
        exit 0
      fi

      REL="${secrets.relDir}/$1.age"
      shift

      # RULES is how agenix finds the recipients. Pointing it at the generated
      # store file is what removes the committed rules file — and with it the
      # whole class of "the rules changed but the ciphertext did not".
      export RULES=${secrets.rulesFile}

      if [ -n "''${1:-}" ]; then
        ${lib.getExe' secrets.cli "agenix"} -e "$REL" -i "$1"
      else
        ${lib.getExe' secrets.cli "agenix"} -e "$REL"
      fi

      # A new .age is untracked, and a flake's source tree is only its tracked
      # files — so an untracked secret is invisible to the build and the next
      # `direnv reload` reports it missing. Stage it now rather than letting
      # that happen.
      if ! git ls-files --error-unmatch -- "$REL" >/dev/null 2>&1; then
        git add -- "$REL"
        echo "staged new secret $REL (age ciphertext is meant to be committed)"
      fi

      ${lib.getExe' secrets.agecheck "frappe-nix-agecheck"} check ${secrets.rulesJSON} --root .
    '';

    rekey-secrets.exec = ''
      set -euo pipefail
      cd "$FRAPPE_BENCH_ROOT"

      echo "Re-encrypting every declared secret to the current recipient list…"
      echo "(you need to be able to decrypt them, so this cannot run in CI)"
      echo

      export RULES=${secrets.rulesFile}
      ${lib.getExe' secrets.cli "agenix"} -r

      echo
      ${lib.getExe' secrets.agecheck "frappe-nix-agecheck"} check ${secrets.rulesJSON} --root .
      echo
      echo "Commit the changed .age files — until you do, the recipient list in"
      echo "flake.nix and the ciphertext on the branch disagree."
    '';

    check-secrets.exec = ''
      set -euo pipefail
      cd "$FRAPPE_BENCH_ROOT"
      if [ -n "''${1:-}" ]; then
        exec ${lib.getExe' secrets.agecheck "frappe-nix-agecheck"} explain \
          ${secrets.rulesJSON} "$1" --root .
      fi
      exec ${lib.getExe' secrets.agecheck "frappe-nix-agecheck"} check \
        ${secrets.rulesJSON} --root .
    '';
  };
in
secretScripts
// {
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
      build)       shift; exec bench-build "$@" ;;
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

  # The one entry point to a build, for `bench build` too — the umbrella wrapper
  # redirects it here. frappe's esbuild pipeline shells out to each app's own
  # `yarn build`, so a node_modules that predates the app's current package.json
  # surfaces as a missing-package error from a vite config, several apps deep,
  # naming nothing that would lead you back to the install. Refresh first, and
  # refuse to build if that fails rather than compiling half the assets against
  # the previous install.
  bench-build.exec = ''
    set -euo pipefail
    export _FRAPPE_BENCH_RAW=1
    cd "$FRAPPE_BENCH_ROOT"
    ${refreshNodeModules}
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
      declare -A _before_lock _before_py
      for lock in apps/*/yarn.lock; do
        [ -e "$lock" ] || continue
        _before_lock["$lock"]=$(git hash-object "$lock" 2>/dev/null || echo none)
      done
      for pp in apps/*/pyproject.toml; do
        [ -e "$pp" ] || continue
        _before_py["$pp"]=$(git hash-object "$pp" 2>/dev/null || echo none)
      done

      git submodule foreach '
        branch=$(git config -f "$toplevel/.gitmodules" "submodule.$name.branch") || {
          echo "  ⚠  $name: no branch configured in .gitmodules — skipping."
          echo "     Fix it with: frappe-init --migrate (or: git config -f .gitmodules submodule.$name.branch <branch>)"
          exit 0
        }
        # Benches built with `bench get-app` often have only an `upstream`
        # remote — the bench CLI prefers that name in get_remote() — so origin
        # is not a safe assumption for a migrated bench.
        remote=origin
        git remote | grep -qx origin || remote=$(git remote | head -n1)
        [ -n "$remote" ] || { echo "  ⚠  $name: no git remote — skipping"; exit 0; }
        echo "  → $name ($branch from $remote)"
        git fetch "$remote" --depth 1 "$branch"
        # `checkout -B` discards anything not on the remote branch. Refuse when
        # this submodule carries commits that are not in what we just fetched.
        if ! git merge-base --is-ancestor HEAD FETCH_HEAD 2>/dev/null; then
          if [ -n "''${FRAPPE_BENCH_UPDATE_FORCE:-}" ]; then
            echo "     ⚠  local commits will be discarded (FRAPPE_BENCH_UPDATE_FORCE=1)"
          else
            echo "  ⚠  $name: HEAD is not an ancestor of $remote/$branch — it has local or"
            echo "     unpushed commits that checkout -B would discard. Skipping."
            echo "     Push them, or re-run with FRAPPE_BENCH_UPDATE_FORCE=1 to overwrite."
            exit 0
          fi
        fi
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

      # Re-lock when an app's pyproject.toml moved. The Python half of this used
      # to be missing while the Node half above was not, and the asymmetry is
      # what made a stale uv.lock the routine outcome of a pull: an app that
      # declares a new dependency leaves uv2nix resolving a name that is in no
      # lock, which fails at *evaluation* — so the next shell entry breaks rather
      # than the update that caused it. Do it here, where the cause is on screen.
      py_changed=()
      for pp in apps/*/pyproject.toml; do
        [ -e "$pp" ] || continue
        if [ "''${_before_py["$pp"]:-none}" != "$(git hash-object "$pp" 2>/dev/null || echo none)" ]; then
          py_changed+=("$(basename "$(dirname "$pp")")")
        fi
      done
      if [ ''${#py_changed[@]} -gt 0 ]; then
        echo "── Re-locking the Python workspace (pyproject.toml changed:$(printf ' %s' "''${py_changed[@]}")) ──"
        if ! uv lock; then
          echo "  ⚠  uv lock failed — the dev shell will not evaluate until this resolves." >&2
          echo "     For a conflict between two apps, add a pin to [tool.uv] override-dependencies;" >&2
          echo "     for one against [dependency-groups], relax the pin there. Then re-run 'uv lock'." >&2
          exit 1
        fi
        echo "  commit uv.lock along with the submodule bumps"
        echo ""
      fi

      # `--pull` stops here, so this is its only chance to bring node_modules
      # back in step with what was just pulled. A full update reaches the same
      # refresh through bench-build below, where a failure is fatal because the
      # build is what it would break; here it is only a warning.
      if ! $BUILD; then
        ${refreshNodeModulesSoft}
      fi
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
      # bench-build, not `bench build`: _FRAPPE_BENCH_RAW is exported here, so
      # the latter would go straight to the real bench and skip the
      # node_modules refresh that the pull above is the whole reason for.
      bench-build
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
    # No --db-socket here: unlike `new-site`, `bench restore` has no such option
    # (frappe/commands/site.py), so the socket has to arrive the other way — from
    # the site's own site_config.json, or from FRAPPE_DB_SOCKET via
    # frappe/config.py. Both are set by the dev shell.
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
    # Read the port back rather than trusting an env var: devenv only runs its
    # port allocator for `devenv up`, so anything resolved in a plain shell is
    # the bench's base port, which may not be the one nginx actually took.
    echo "   URL: http://127.0.0.1:$(${pkgs.jq}/bin/jq -r '.webserver_port // 8000' sites/common_site_config.json)"
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
