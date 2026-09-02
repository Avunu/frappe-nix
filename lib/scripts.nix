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
  # App mode: this project is one Frappe app and the bench under it is
  # generated. Everything that edits the bench as if it were a checkout — a
  # submodule pull, a workspace member, a scaffolded app — has no meaning here
  # and is replaced by the flake-input equivalent rather than left to fail
  # obscurely, or worse, succeed into a tree the next refresh deletes.
  appMode ? false,
  # App mode: where the generated-but-committed lock files live, relative to
  # the repo root. Only used in messages.
  lockDir ? "nix",
}:

let
  # Every bench command runs from the bench root. Not a convenience: the bench
  # CLI resolves its bench by walking *up* from cwd (bench/cli.py's
  # change_working_directory → find_parent_bench), so from anywhere else it
  # either finds nothing and stops dispatching frappe commands entirely, or — if
  # this repo happens to sit inside another bench's apps/ — finds that one and
  # runs against its database.
  #
  # A relative path argument is therefore resolved from the bench root, not from
  # where you typed it. That is not new — `bench` chdirs to the bench root itself
  # before it runs anything — and every script here that takes a file already
  # cd'd first for the same reason.
  atBench = ''cd "$FRAPPE_BENCH_ROOT"'';

  # Secrets are tracked files in the git worktree, which in app mode is not the
  # bench. A rules file naming a path under the generated bench makes `agenix -e`
  # report "no rule for file" and `agenix -r` skip it as "does not exist,
  # ignored" — exit 0, nothing rekeyed. See lib/secrets-tools.nix.
  atRepo = ''cd "$REPO_ROOT"'';

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
      ${atRepo}

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

      # The rules are rendered here rather than committed, so the recipient
      # list in flake.nix is the only place it is written down. They carry
      # absolute paths and the file below is passed absolute to match — see
      # lib/secrets-tools.nix for why a store-path rules file with relative
      # keys silently does nothing.
      ${secrets.writeRules}

      # ryantm/agenix substitutes `cp -- /dev/stdin` for $EDITOR when stdin is
      # not a terminal, so `edit-secret foo <<EOF … EOF` just works there.
      # ragenix does not — it refuses with "Standard output is not a terminal"
      # — which would make every secret in this bench hand-typed only. Restore
      # the documented behaviour.
      # Unconditional, not "only if EDITOR is unset": EDITOR is nano on most
      # machines, and an interactive editor cannot work without a terminal
      # anyway, so honouring it here just fails. ryantm/agenix makes the same
      # unconditional substitution.
      if [ ! -t 0 ]; then
        EDITOR="cp /dev/stdin"
        export EDITOR
      fi

      if [ -n "''${1:-}" ]; then
        ${lib.getExe' secrets.cli "agenix"} -e "$REPO_ROOT/$REL" -i "$1"
      else
        ${lib.getExe' secrets.cli "agenix"} -e "$REPO_ROOT/$REL"
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
      ${atRepo}

      echo "Re-encrypting every declared secret to the current recipient list…"
      echo "(you need to be able to decrypt them, so this cannot run in CI)"
      echo

      ${secrets.writeRules}
      ${lib.getExe' secrets.cli "agenix"} -r "$@"

      echo
      ${lib.getExe' secrets.agecheck "frappe-nix-agecheck"} check ${secrets.rulesJSON} --root .
      echo
      echo "Commit the changed .age files — until you do, the recipient list in"
      echo "flake.nix and the ciphertext on the branch disagree."
    '';

    check-secrets.exec = ''
      set -euo pipefail
      ${atRepo}
      if [ -n "''${1:-}" ]; then
        exec ${lib.getExe' secrets.agecheck "frappe-nix-agecheck"} explain \
          ${secrets.rulesJSON} "$1" --root .
      fi
      exec ${lib.getExe' secrets.agecheck "frappe-nix-agecheck"} check \
        ${secrets.rulesJSON} --root .
    '';
  } // lib.optionalAttrs ((secrets.enabled or false) && (restore.enable or false)) {
    setup-backup-access.exec = ''
      exec ${secrets.setupBackupAccess restore.fetch}/bin/frappe-nix-setup-backup-access "$@"
    '';
  };
  # App mode replaces the two scripts whose whole job is to edit the bench as if
  # it were a checkout. Replaced rather than dropped: the `bench` umbrella
  # wrapper dispatches `get-app`/`new-app` to them, and a missing script would
  # surface as `command not found` instead of the one sentence that says what to
  # do instead.
  appModeOverrides = {
    bench-get-app = {
      exec = ''
        cat >&2 <<'EOF'
        bench-get-app: this is an app repository, not a bench — there is no apps/
        of yours to add to, and anything written into the generated bench is
        discarded the next time a pin moves.

        Add the app as a flake input and a sibling instead:

            inputs.hrms = { url = "github:frappe/hrms/version-16"; flake = false; };
            ...
            frappe-nix.app.siblings = [ { name = "hrms"; src = inputs.hrms; } ];

        then: nix run .#relock
        EOF
        exit 1
      '';
      description = "Not available in app mode — declare the app as a flake input instead.";
    };

    bench-new-app = {
      exec = ''
        cat >&2 <<'EOF'
        bench-new-app: this is an app repository, not a bench. A new app scaffolded
        into the generated bench would be deleted the next time a pin moves.

        Create it in its own repository — `nix run github:Avunu/frappe-nix` in an
        app's directory sets one up — or add it to a bench.
        EOF
        exit 1
      '';
      description = "Not available in app mode — new apps get their own repository.";
    };
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
      ${atBench}
      exec ${benchBin} "$@"
    fi
    # Before the dispatch, so the specialised scripts and the fall-through both
    # get it — and after the raw guard, which has already run it.
    ${atBench}
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
    ${atBench}
    ${siteFlag}
    bench $SITE_FLAG console "$@"
  '';

  bench-migrate.exec = ''
    export _FRAPPE_BENCH_RAW=1
    ${atBench}
    ${siteFlag}
    bench $SITE_FLAG migrate "$@"
  '';

  bench-clear-cache.exec = ''
    export _FRAPPE_BENCH_RAW=1
    ${atBench}
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

    # In app mode there is nothing here to pull and nothing here to write: the
    # apps are pinned by flake.lock and the hashes belong to the repository, not
    # to a bench directory the next refresh replaces.
    PULL=${if appMode then "false" else "true"}
    MIGRATE=true
    BUILD=true
    FORCE_NODE_HASHES=false

    for arg in "$@"; do
      case "$arg" in
${
      if appMode then
        ''
              --pull | --node-hashes)
                echo "bench-update: $arg has no meaning in app mode — the apps are pinned by" >&2
                echo "flake.lock, not pulled, and ${lockDir}/node-offline-hashes.json is" >&2
                echo "generated from those pins. Use:" >&2
                echo "" >&2
                echo "    nix flake update        # move the pins" >&2
                echo "    nix run .#relock        # re-resolve and rewrite ${lockDir}/" >&2
                exit 1
                ;;''
      else
        ''
              --pull)        MIGRATE=false; BUILD=false ;;
              --node-hashes) PULL=false;   MIGRATE=false; BUILD=false; FORCE_NODE_HASHES=true ;;''
    }
        --migrate)     PULL=false;   BUILD=false  ;;
        --build)       PULL=false;   MIGRATE=false ;;
        --help|-h)
${
      if appMode then
        ''
              echo "Usage: bench-update [--migrate | --build]"
              echo ""
              echo "  (no flags)     Migrate, then build"
              echo "  --migrate      Run DB migrations only"
              echo "  --build        Build JS/CSS assets only"
              echo ""
              echo "To move the pinned apps: nix flake update && nix run .#relock"''
      else
        ''
              echo "Usage: bench-update [--pull | --migrate | --build | --node-hashes]"
              echo ""
              echo "  (no flags)     Pull apps, refresh node hashes, migrate, build"
              echo "  --pull         Pull latest commits + refresh changed node hashes"
              echo "  --migrate      Run DB migrations only"
              echo "  --build        Build JS/CSS assets only"
              echo "  --node-hashes  Force-regenerate node-offline-hashes.json (all apps)"''
    }
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

  # Restore this bench from a Frappe backup — an explicit file, or the latest
  # one in the object store.
  #
  # Reached both directly and as `bench restore`, via the umbrella wrapper
  # above. Overridable wholesale through `extraScripts.bench-restore`, which is
  # how the consuming repos kept their own version while this one was built.
  bench-restore.exec = ''
    set -euo pipefail
    export _FRAPPE_BENCH_RAW=1
    cd "$FRAPPE_BENCH_ROOT"

    FETCH_ENABLED=${if restore.enable or false then "true" else "false"}
    WANT_PUBLIC=${if (restore.withFiles or "none") == "all" then "true" else "false"}
    WANT_PRIVATE=${if builtins.elem (restore.withFiles or "none") [ "private" "all" ] then "true" else "false"}
    DO_MIGRATE=${if restore.migrate or false then "true" else "false"}
    SEED_CONFIG=true
    FORCE=true
    AT=""
    NO_CACHE=false
    SQL_FILE=""
    CARRY_KEYS=${lib.escapeShellArg (lib.concatStringsSep " " (restore.carryConfigKeys or [ ]))}
    declare -a PASSTHRU=()

    usage() {
      cat <<'USAGE'
    Usage: bench restore [<sql-file>] [options]

    With no file, fetches the most recent backup from the object store and
    restores it into $FRAPPE_SITE, creating the site if it does not exist.

      --list                 Show available backups and exit
      --at <YYYYMMDD_HHMMSS> Restore a specific backup instead of the newest
      --files                Also restore the public files archive
      --private-files        Also restore the private files archive
      --no-cache             Re-download even if the cached copy is intact
      --no-site-config       Do not carry any key from production's site config
      --no-migrate           Skip `bench migrate` afterwards
      --no-force             Fail instead of replacing an existing database
      --encryption-key <k>   Override the backup encryption key

    Anything else is passed through to `bench restore`.
    USAGE
    }

    while [ "$#" -gt 0 ]; do
      case "$1" in
        --list)            LIST=true; shift ;;
        --at)              AT="$2"; shift 2 ;;
        --files)           WANT_PUBLIC=true; shift ;;
        --private-files)   WANT_PRIVATE=true; shift ;;
        --no-cache)        NO_CACHE=true; shift ;;
        --no-site-config)  SEED_CONFIG=false; shift ;;
        --no-migrate)      DO_MIGRATE=false; shift ;;
        --no-force)        FORCE=false; shift ;;
        -h|--help)         usage; exit 0 ;;
        --)                shift; PASSTHRU+=("$@"); break ;;
        -*)                PASSTHRU+=("$1"); shift ;;
        *)
          if [ -n "$SQL_FILE" ]; then
            echo "bench restore: unexpected argument '$1'" >&2; exit 1
          fi
          SQL_FILE="$1"; shift ;;
      esac
    done

    ${siteFlag}

    # ── an explicit file: unchanged behaviour ──────────────────────────────
    # No --db-socket: unlike `new-site`, `bench restore` has no such option
    # (frappe/commands/site.py), so the socket arrives the other way — from the
    # site's own site_config.json, or from FRAPPE_DB_SOCKET via frappe/config.py.
    # Both are set by the dev shell.
    if [ -n "$SQL_FILE" ]; then
      echo "Restoring ''${FRAPPE_SITE:-all sites} from $SQL_FILE…"
      exec bench $SITE_FLAG restore "$SQL_FILE" \
        --db-root-username "root" --db-root-password "" "''${PASSTHRU[@]}"
    fi

    if [ "$FETCH_ENABLED" != true ]; then
      echo "bench restore: no backup source is configured for this bench." >&2
      echo >&2
      echo "  Pass a file:   bench restore <dump.sql.gz>" >&2
      echo "  Or enable fetching by declaring the backup-access secret:" >&2
      echo "      frappe-nix.secrets.recipients.<you> = \"ssh-ed25519 …\";" >&2
      echo "  then: edit-secret backup-access" >&2
      exit 1
    fi

    if [ -z "''${FRAPPE_SITE:-}" ]; then
      echo "bench restore: this bench is multi-tenant (siteName = \"\") and" >&2
      echo "FRAPPE_SITE is unset, so there is no site to restore into." >&2
      echo "Set FRAPPE_SITE in .env, or pass a file to restore explicitly." >&2
      exit 1
    fi

    # ── credentials, on demand ─────────────────────────────────────────────
    # Sourced here rather than in enterShell: agenix-shell re-runs rage on every
    # source and enterShell runs on every direnv reload, so a passphrased key
    # would prompt on every file save; and it exports the plaintext itself, not
    # just a path, which at shell entry would put credentials in the environment
    # of every process in the session — devenv up's children included.
    ${restore.loadSecrets or ""}

    if [ "''${LIST:-false}" = true ]; then
      exec ${restore.fetch} list
    fi

    # ── fetch ──────────────────────────────────────────────────────────────
    declare -a FETCH_ARGS=(fetch)
    [ -n "$AT" ] && FETCH_ARGS+=(--at "$AT")
    [ "$WANT_PUBLIC" = true ] && FETCH_ARGS+=(--files)
    [ "$WANT_PRIVATE" = true ] && FETCH_ARGS+=(--private-files)
    [ "$NO_CACHE" = true ] && FETCH_ARGS+=(--no-cache)
    export FRAPPE_BACKUP_CACHE="''${FRAPPE_BACKUP_CACHE:-$DEVENV_STATE/frappe-nix/restore}"
    ${lib.optionalString ((restore.prefix or "") != "") ''
      export BACKUPS_PREFIX="''${BACKUPS_PREFIX:-${restore.prefix}}"
    ''}

    MANIFEST="$(${restore.fetch} "''${FETCH_ARGS[@]}")"
    DB_PATH="$(printf '%s' "$MANIFEST"   | ${pkgs.jq}/bin/jq -r '.database')"
    CONF_PATH="$(printf '%s' "$MANIFEST" | ${pkgs.jq}/bin/jq -r '.site_config // empty')"
    PUB_PATH="$(printf '%s' "$MANIFEST"  | ${pkgs.jq}/bin/jq -r '.files // empty')"
    PRIV_PATH="$(printf '%s' "$MANIFEST" | ${pkgs.jq}/bin/jq -r '.private_files // empty')"
    ENCRYPTED="$(printf '%s' "$MANIFEST" | ${pkgs.jq}/bin/jq -r '.encrypted')"
    FOLDER="$(printf '%s' "$MANIFEST"    | ${pkgs.jq}/bin/jq -r '.folder')"
    SLUG="$(printf '%s' "$MANIFEST"      | ${pkgs.jq}/bin/jq -r '.slug')"

    DEV_SLUG="$(printf '%s' "$FRAPPE_SITE" | tr '.:-' '___')"
    if [ "$SLUG" != "$DEV_SLUG" ]; then
      echo "  note: the backup is of '$SLUG', this bench is '$DEV_SLUG'."
      echo "        Restoring across sites is fine — this site keeps its own"
      echo "        db_name and db_password, and file archives are extracted"
      echo "        with 'tar --strip 2', which discards the archive's own"
      echo "        site directory."
    fi

    # ── make sure the site exists ──────────────────────────────────────────
    # `bench restore` cannot create one: frappe.init raises IncorrectSitePath
    # when sites/<site>/site_config.json is absent (frappe/__init__.py), and it
    # does that before reading the dump.
    #
    # But that file is *all* it needs. restore_backup calls _new_site(force=True),
    # which runs make_site_dirs() and install_db() itself, and make_site_config
    # only writes when the file does not already exist (frappe/installer.py) —
    # so a four-key seed both unblocks a fresh clone and survives untouched. A
    # full `bench new-site` here would install every app into a database that is
    # dropped seconds later.
    SITE_CFG="sites/$FRAPPE_SITE/site_config.json"
    if [ ! -f "$SITE_CFG" ]; then
      echo "  creating $FRAPPE_SITE (it does not exist yet)"
      # frappe's own scheme when it picks a database name itself: "_" plus 16
      # hex, which fits MariaDB's identifier limits and doubles as the DB user.
      DB_NAME="_$(printf '%s' "$FRAPPE_SITE" | ${pkgs.coreutils}/bin/sha1sum | cut -c1-16)"
      DB_PASS="$(${pkgs.coreutils}/bin/head -c 24 /dev/urandom | ${pkgs.coreutils}/bin/base64 | tr -dc 'A-Za-z0-9' | cut -c1-16)"
      mkdir -p "sites/$FRAPPE_SITE/locks"
      (
        umask 0077
        ${pkgs.jq}/bin/jq -n \
          --arg n "$DB_NAME" --arg p "$DB_PASS" --arg s "''${FRAPPE_DB_SOCKET:-}" \
          '{db_type: "mariadb", db_name: $n, db_password: $p}
           + (if $s == "" then {} else {db_socket: $s} end)' > "$SITE_CFG"
      )
    else
      # filelock() runs before _new_site and will not create its own parent
      # (frappe/utils/synchronization.py), so an older site directory that
      # predates locks/ would fail there rather than in the restore.
      mkdir -p "sites/$FRAPPE_SITE/locks"
    fi

    # ── the encryption keys ────────────────────────────────────────────────
    # Two different keys, and both matter:
    #   encryption_key         frappe/utils/password.py — decrypts every stored
    #                          Password and API-secret field in the dump
    #   backup_encryption_key  frappe/utils/backups.py  — the gpg passphrase for
    #                          this dump and the next one
    # Both live in the backup's own site_config_backup.json, which is readable
    # even for an "-enc" backup: backup_encryption() encrypts the database and
    # the two archives, never the config.
    ENC_KEY=""
    if [ -n "$CONF_PATH" ] && [ "$SEED_CONFIG" = true ] && [ -n "$CARRY_KEYS" ]; then
      ${lib.optionalString (!(restore.devguard or true)) ''
        if [ "''${FRAPPE_RESTORE_ALLOW_UNGUARDED:-0}" != 1 ]; then
          echo "bench restore: refusing to write production's encryption key into a" >&2
          echo "bench with frappe-nix.devguard.enable = false." >&2
          echo >&2
          echo "That key decrypts every stored production credential in this dump —" >&2
          echo "mail passwords, payment secrets, API tokens. The guard rails are what" >&2
          echo "keep a bench holding those from mailing customers or deleting objects" >&2
          echo "out of the production bucket. They are a large reduction in blast" >&2
          echo "radius, not an airgap, and turning them off makes this a real risk." >&2
          echo >&2
          echo "  re-enable devguard, or" >&2
          echo "  bench restore --no-site-config     (stored credentials stay opaque), or" >&2
          echo "  FRAPPE_RESTORE_ALLOW_UNGUARDED=1 bench restore" >&2
          exit 1
        fi
      ''}
      echo "  carrying from the backup's site config: $CARRY_KEYS"
      TMP_CFG="$(mktemp)"
      ${pkgs.jq}/bin/jq -s --arg keys "$CARRY_KEYS" '
        ($keys | split(" ") | map(select(length > 0))) as $allow
        | .[0] + (.[1] | with_entries(select(.key as $k | $allow | index($k))))
      ' "$SITE_CFG" "$CONF_PATH" > "$TMP_CFG"
      ${pkgs.coreutils}/bin/install -m 0600 "$TMP_CFG" "$SITE_CFG"
      rm -f "$TMP_CFG"
    fi

    # Always resolve the key ourselves for an encrypted dump. Left to frappe,
    # _restore falls back to get_or_generate_backup_encryption_key(), which
    # *generates a fresh key and writes it into this site's config* before
    # failing to decrypt — leaving the restore broken and the config wrong.
    if [ "$ENCRYPTED" = true ]; then
      for arg in "''${PASSTHRU[@]:-}"; do
        [ "$arg" = "--encryption-key" ] && ENC_KEY="provided"
      done
      if [ "$ENC_KEY" != provided ]; then
        ENC_KEY="$(${pkgs.jq}/bin/jq -r '.backup_encryption_key // .encryption_key // empty' "$SITE_CFG")"
        if [ -z "$ENC_KEY" ] && [ -n "$CONF_PATH" ]; then
          ENC_KEY="$(${pkgs.jq}/bin/jq -r '.backup_encryption_key // .encryption_key // empty' "$CONF_PATH")"
        fi
        if [ -z "$ENC_KEY" ]; then
          echo "bench restore: this backup is encrypted and no backup_encryption_key" >&2
          echo "is available — not from the fetched site config, not from this site." >&2
          echo "Pass one with --encryption-key, or restore an unencrypted backup." >&2
          exit 1
        fi
        PASSTHRU+=(--encryption-key "$ENC_KEY")
      fi
    fi

    # ── restore ────────────────────────────────────────────────────────────
    # --force by default: without it setup_database refuses with "Database …
    # already exists" (frappe/database/mariadb/setup_db.py), so every restore
    # after the first one fails. This is the one destructive default here — it
    # drops and recreates the site's database, which is what "clone production"
    # means. --no-force opts out.
    [ "$FORCE" = true ] && PASSTHRU+=(--force)
    [ -n "$PUB_PATH" ]  && PASSTHRU+=(--with-public-files "$PUB_PATH")
    [ -n "$PRIV_PATH" ] && PASSTHRU+=(--with-private-files "$PRIV_PATH")

    echo "  restoring $FRAPPE_SITE from $FOLDER…"
    bench $SITE_FLAG restore "$DB_PATH" \
      --db-root-username "root" --db-root-password "" "''${PASSTHRU[@]}"

    # ── after ──────────────────────────────────────────────────────────────
    # The dump carries production's Installed Applications. remove_missing_apps()
    # only knows two legacy names, so an app production has and this bench does
    # not survives into the restored database and takes `bench migrate` down with
    # a bare ModuleNotFoundError. Name them instead.
    MISSING=""
    if [ -f sites/apps.txt ]; then
      for app in $(bench $SITE_FLAG list-apps --format json 2>/dev/null \
                   | ${pkgs.jq}/bin/jq -r --arg s "$FRAPPE_SITE" '.[$s][]? // empty' || true); do
        grep -qxF "$app" sites/apps.txt || MISSING="$MISSING $app"
      done
    fi
    if [ -n "$MISSING" ]; then
      echo >&2
      echo "  warning: the restored database has apps this bench does not:$MISSING" >&2
      echo "           \`bench migrate\` will fail on them. Add each with:" >&2
      for app in $MISSING; do echo "               bench get-app $app" >&2; done
      DO_MIGRATE=false
    fi

    if [ "$DO_MIGRATE" = true ]; then
      echo "  running bench migrate…"
      bench $SITE_FLAG migrate
    fi

    echo "✅ restored $FRAPPE_SITE from $FOLDER"
  '';

  update-deps.exec = ''
    ${atBench}
    ${
      if appMode then
        ''
          echo "Python dependencies are resolved from the flake's pins, not from"
          echo "this bench — the workspace root it would lock lives in the Nix store."
          echo "Use:  nix run .#relock"
        ''
      else
        ''
          echo "Updating Python dependencies..."
          uv lock && uv sync
        ''
    }
    echo ""
    echo "Updating Node dependencies..."
    ${lib.concatStringsSep "\n" (
      map (app: ''
        echo "  yarn install: ${app}"
        (cd "apps/${app}" && yarn install)
      '') appsWithNode
    )}
    echo ""
    ${
      if appMode then
        ''echo "Done! Commit any changed yarn.lock, then: nix run .#relock"''
      else
        ''echo "Done! Lock files updated. Commit uv.lock and yarn.lock files."''
    }
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
// lib.optionalAttrs appMode appModeOverrides
