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
  # lib/secrets.nix (python-devops-nix's lib.secrets.tools). `enabled = false`
  # when the bench declares no secrets.
  secrets ? {
    enabled = false;
  },
  # The secrets toolkit's dev-shell commands (edit-secret, rekey-secrets,
  # check-secrets, setup-backup-access): `secrets.scripts { fetch = …; }`. Empty
  # when the bench declares no secrets, rather than stubs that fail late. Merged
  # in here, under every other script, rather than contributed separately:
  # modules/devenv.nix assigns `scripts` wholesale as `scripts //
  # cfg.extraScripts`, so a second module defining the same attribute would turn
  # a consumer's `extraScripts.edit-secret` into a *conflict* instead of the
  # documented override.
  secretScripts ? { },
  # Absolute path to lib/node-modules.nix's tool. Left as a bare name for a
  # consumer that instantiates this file on its own; the dev shell passes the
  # store path.
  nodeModulesBin ? "frappe-nix-node-modules",
  # Absolute path to lib/node-verify.nix's tool, same convention.
  nodeVerifyBin ? "frappe-nix-node-verify",
  # The interpreter of the environment the apps are installed in.
  pythonBin ? "python3",
  # Absolute path to lib/node-locks.nix's tool, same convention.
  nodeLocksBin ? "frappe-nix-node-locks",
  # perSystem.frappe-nix.nodeNestedFrontendExcludes: the nested frontends the
  # lock generator must leave alone (and prune).
  nodeNestedFrontendExcludes ? [ ],
  # perSystem.frappe-nix.restore, plus `fetch` (the frappe-nix-backup-fetch
  # command, lib/backup-fetch.nix), `loadSecrets` (the snippet that decrypts the
  # bench's secrets) and `devguard` (whether this bench's guard rails are on).
  # `enable = false` leaves `bench restore` with its explicit-file behaviour.
  restore ? {
    enable = false;
  },
  # The `frappe-nix-bench` command (py/frappe_nix_tools, bench_cli.py): the bench
  # subcommands written in Python, which the `bench-*` shims below exec. Left
  # as a bare name for a consumer that instantiates this file on its own.
  benchCli ? "frappe-nix-bench",
  # perSystem.frappe-nix.offlineMigrate, plus `ptOsc` (the lib/offline-migrate.nix
  # binary). `enable = false` leaves `bench migrate` to alter every table itself;
  # `bench-offline-migrate` is there either way, to run by hand.
  offlineMigrate ? {
    enable = false;
  },
  # App mode: this project is one Frappe app and the bench under it is
  # generated. Everything that edits the bench as if it were a checkout — a
  # submodule pull, a workspace member, a scaffolded app — has no meaning here
  # and is replaced by the flake-input equivalent rather than left to fail
  # obscurely, or worse, succeed into a tree the next refresh deletes.
  appMode ? false,
  # App mode: where the generated-but-committed lock files live, relative to
  # the repo root. Only used in messages.
  lockDir ? "nix",
  # Scripts kept in files of their own, merged over the ones below; see
  # lib/scripts.d/README.md. A parameter so tests can point it at a fixture;
  # null merges none.
  scriptsDir ? ./scripts.d,
}@args:

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
  # ignored" — exit 0, nothing rekeyed. See writeRules in python-devops-nix's
  # lib.secrets.tools.
  atRepo = ''cd "$REPO_ROOT"'';

  siteFlag = ''
    SITE_FLAG=""
    if [ -n "''${FRAPPE_SITE:-}" ]; then
      SITE_FLAG="--site $FRAPPE_SITE"
    fi
  '';

  # What lib/offline-migrate.py reads, defaulted rather than exported by the
  # shell so a caller's own FRAPPE_OFFLINE_MIGRATE_* — `=0` for one migrate that
  # must run stock — still wins.
  offlineMigrateEnv = lib.concatStrings [
    (lib.optionalString (offlineMigrate ? ptOsc) ''
      export FRAPPE_OFFLINE_MIGRATE_PT_OSC="''${FRAPPE_OFFLINE_MIGRATE_PT_OSC:-${offlineMigrate.ptOsc}}"
    '')
    (lib.optionalString (offlineMigrate ? rowThreshold) ''
      export FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD="''${FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD:-${toString offlineMigrate.rowThreshold}}"
    '')
  ];

  # The one implementation of the apps/ ⇄ pyproject.toml ⇄ sites/apps.{txt,json}
  # contract, shared with the `frappe-init` scaffolder/migrator and the bench
  # package build so none of them can drift apart. Idempotent and
  # comment-preserving (tomlkit).
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
  #
  # Repairing first: a damaged cache or node_modules passes for installed (see
  # lib/node-verify.py), so the install below would skip right over it.
  refreshNodeModules = lib.optionalString (appsWithNode != [ ]) ''
    ${nodeVerifyBin} . ${lib.escapeShellArgs appsWithNode} || true
    ${nodeModulesBin} . ${lib.escapeShellArgs appsWithNode}
  '';

  # The same, downgraded to a warning — for the paths where node_modules is not
  # what the command is about and a yarn failure should not abort it.
  refreshNodeModulesSoft = lib.optionalString (appsWithNode != [ ]) ''
    ${nodeVerifyBin} . ${lib.escapeShellArgs appsWithNode} || true
    ${nodeModulesBin} . ${lib.escapeShellArgs appsWithNode} || true
  '';

  # Shell snippet: (re)generate node-locks/ — the fallback yarn.lock per app
  # and nested frontend that ships none of its own (and the forced ones), which
  # `nix build` installs that target's node_modules from. Discovers its own
  # targets under apps/, so an app added a moment ago by bench-get-app is
  # covered before the shell has re-evaluated; its stamp makes a target whose
  # manifests did not move free. Expects cwd at the bench root. Arguments name
  # targets to resolve regardless — over the app's own yarn.lock, if it has one.
  regenNodeLocks = "${nodeLocksBin} ${
    lib.escapeShellArgs (map (k: "--exclude=${k}") nodeNestedFrontendExcludes)
  } . node-locks";

  # The same, downgraded to a warning: a resolve failure must not abort a pull
  # (the previous locks stay in place, and nix build keeps working from them).
  regenNodeLocksSoft = ''
    ${regenNodeLocks} || echo "  ⚠  node-locks/ not fully regenerated — nix build keeps the previous locks; re-run: bench-update --node-locks" >&2
  '';

  # Shell snippet: regenerate sites/apps.txt and sites/apps.json from the
  # workspace members. Run from the bench root, after registerWorkspaceMember
  # or a submodule pull — the registry is derived from pyproject.toml and the
  # checkouts, never edited in place.
  syncRegistry = ''
    ${workspaceBin} sync-registry --pyproject pyproject.toml --apps-dir apps --sites-dir sites
  '';

  # App mode replaces the two scripts whose whole job is to edit the bench as if
  # it were a checkout. Replaced rather than dropped: the `bench` umbrella
  # wrapper dispatches `get-app`/`new-app`/`remove-app` to them, and a missing
  # script would surface as `command not found` instead of the one sentence
  # that says what to do instead.
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

    bench-remove-app = {
      exec = ''
        cat >&2 <<'EOF'
        bench-remove-app: this is an app repository, not a bench — there is no
        apps/ submodule or uv workspace membership of yours to tear down here.

        Drop the sibling from frappe-nix.app.siblings instead, then: nix run .#relock
        EOF
        exit 1
      '';
      description = "Not available in app mode — remove the sibling declaration instead.";
    };
  };
  # lib/scripts.d/*.nix, in name order: each a function of this file's
  # arguments and snippets, returning scripts (lib/scripts.d/README.md). A file
  # that redefines a script, this file's own or another file's, fails
  # evaluation; this file's own names come from evaluating it with none.
  dropInArgs = {
    inherit
      lib
      pkgs
      appsWithNode
      benchBin
      secrets
      secretScripts
      benchCli
      nodeModulesBin
      nodeVerifyBin
      pythonBin
      nodeLocksBin
      nodeNestedFrontendExcludes
      restore
      offlineMigrate
      appMode
      lockDir
      atBench
      atRepo
      siteFlag
      offlineMigrateEnv
      workspaceBin
      registerWorkspaceMember
      refreshNodeModules
      refreshNodeModulesSoft
      regenNodeLocks
      regenNodeLocksSoft
      syncRegistry
      ;
  };
  dropInFiles = lib.optionals (scriptsDir != null) (
    builtins.attrNames (
      lib.filterAttrs (name: type: type == "regular" && lib.hasSuffix ".nix" name) (
        builtins.readDir scriptsDir
      )
    )
  );
  # What the Python bench commands read (frappe_nix_tools/restore/config.py):
  # the options and store paths the bash once had interpolated into it, as one
  # generated file. Every key is written; the reader refuses a missing or an
  # unknown one.
  restoreSecretsEnv = pkgs.writeShellScript "frappe-nix-restore-secrets" ''
    # Decrypt the bench's secrets the way the dev shell does, then hand the
    # environment that made back to bench restore, NUL-separated. Anything the
    # snippet prints goes to stderr, so stdout is the environment alone.
    {
      :
      ${restore.loadSecrets or ""}
    } >&2
    exec ${pkgs.coreutils}/bin/env -0
  '';
  cliConfig = pkgs.writeText "frappe-nix-cli.json" (
    builtins.toJSON {
      restore = {
        enable = restore.enable or false;
        fetch = restore.fetch or "frappe-nix-backup-fetch";
        secretsEnv = if (restore.loadSecrets or "") != "" then "${restoreSecretsEnv}" else null;
        prefix = restore.prefix or "";
        # true and false are "all" and "none" (modules/devenv.nix, restore.files).
        files =
          let
            files = restore.files or "none";
          in
          if files == true then
            "all"
          else if files == false then
            "none"
          else
            files;
        carryConfigKeys = restore.carryConfigKeys or [ ];
        migrate = restore.migrate or false;
        neutralize = restore.neutralize or false;
        requireDevguard = restore.requireDevguard or true;
        devguard = restore.devguard or true;
      };
    }
  );
  ownScripts = lib.genAttrs (builtins.attrNames (
    import ./scripts.nix (args // { scriptsDir = null; })
  )) (_: null);
  dropInScripts = lib.foldl' (
    acc: file:
    let
      added = import (scriptsDir + "/${file}") dropInArgs;
      clash = builtins.attrNames (builtins.intersectAttrs added (ownScripts // acc));
    in
    lib.throwIf (clash != [ ]) "lib/scripts.d/${file} redefines ${lib.concatStringsSep ", " clash}" (
      acc // added
    )
  ) { } dropInFiles;
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
      remove-app)  shift; exec bench-remove-app "$@" ;;
      restore)     shift; exec bench-restore "$@" ;;
      setup)
        # Only `setup requirements` is ours; the rest of `bench setup` (nginx,
        # supervisor, production, …) configures a conventional bench and is left
        # to upstream.
        if [ "''${2:-}" = requirements ]; then shift 2; exec bench-setup-requirements "$@"; fi
        exec ${benchBin} "$@" ;;
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

  # `bench setup requirements`, for a bench whose Python comes from uv.lock by
  # way of Nix and whose node_modules are installed by frappe-nix — not the
  # conventional bench's `pip install -e` and `yarn install` of every app, which
  # here would either fight the read-only environment or skip the checks that
  # matter. See lib/node-verify.py and lib/requirements-check.py.
  bench-setup-requirements.exec = ''
        set -uo pipefail
        export _FRAPPE_BENCH_RAW=1
        cd "$FRAPPE_BENCH_ROOT" || exit 1

        DO_NODE=true
        DO_PYTHON=true
        CHECK=false
        for arg in "$@"; do
          case "$arg" in
            --node)   DO_PYTHON=false ;;
            --python) DO_NODE=false ;;
            --check)  CHECK=true ;;
            # Upstream's flag for the dev-only requirements; the workspace's are
            # already part of the environment.
            --dev)    ;;
            -h | --help)
              cat <<'HELP'
    Usage: bench setup requirements [--node | --python] [--check]

    Verifies and repairs what the apps need installed, for a frappe-nix bench:

      node     the yarn cache and every app's node_modules (nested frontends
               included) for truncated native binaries, empty or partial packages
               and cache records that contradict their package; deletes what is
               damaged, then installs what is missing or out of date. Also lists
               lockfiles that differ from their commit.
      python   every requirement the apps declare is in uv.lock, and every app in
               sites/apps.txt imports. The environment is built by Nix from
               uv.lock, so this reports — `uv lock`, then re-enter the shell.

      --check  report only: change nothing, exit 1 if anything is wrong.
    HELP
              exit 0
              ;;
            *) echo "bench setup requirements: unknown option '$arg' (see --help)" >&2; exit 2 ;;
          esac
        done

        RC=0
        ${lib.optionalString (appsWithNode != [ ]) ''
          if $DO_NODE; then
            echo "── node ──"
            VERIFY=(--full --lockfiles)
            $CHECK && VERIFY+=(--check)
            ${nodeVerifyBin} "''${VERIFY[@]}" . ${lib.escapeShellArgs appsWithNode} || RC=1
            if ! $CHECK; then
              ${nodeModulesBin} . ${lib.escapeShellArgs appsWithNode} || RC=1
            fi
            [ "$RC" -eq 0 ] && echo "  ✓ node: ${toString (builtins.length appsWithNode)} app(s) verified"
          fi
        ''}
        ${lib.optionalString (!appMode) ''
          if $DO_PYTHON; then
            echo "── python ──"
            ${pythonBin} ${./requirements-check.py} . || RC=1
          fi
        ''}
        exit "$RC"
  '';

  bench-console.exec = ''
    export _FRAPPE_BENCH_RAW=1
    ${atBench}
    ${siteFlag}
    bench $SITE_FLAG console "$@"
  '';

  # Alters the large tables a migrate is about to alter without locking them, and
  # leaves the rest to `bench migrate`. See lib/offline-migrate.py.
  bench-offline-migrate.exec = ''
    export _FRAPPE_BENCH_RAW=1
    ${atBench}
    ${offlineMigrateEnv}
    ${pythonBin} ${./offline-migrate.py} "$@"
  '';

  bench-migrate.exec = ''
    export _FRAPPE_BENCH_RAW=1
    ${atBench}
    ${siteFlag}
    ${lib.optionalString offlineMigrate.enable ''
      # Not for --help, and not without a site to look at: the tool takes $FRAPPE_SITE.
      case " $* " in
        *" --help "* | *" -h "*) ;;
        *) if [ -n "''${FRAPPE_SITE:-}" ]; then bench-offline-migrate || exit $?; fi ;;
      esac
    ''}
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
        # apps are pinned by flake.lock and the locks belong to the repository, not
        # to a bench directory the next refresh replaces.
        PULL=${if appMode then "false" else "true"}
        MIGRATE=true
        BUILD=true
        NODE_LOCKS=false
        NODE_LOCK_TARGETS=()
        _skipped_apps=()

        for arg in "$@"; do
          case "$arg" in
    ${
      if appMode then
        ''
          --pull | --node-locks | --node-hashes)
            echo "bench-update: $arg has no meaning in app mode — the apps are pinned by" >&2
            echo "flake.lock, not pulled, and ${lockDir}/node-locks/ is generated from" >&2
            echo "those pins. Use:" >&2
            echo "" >&2
            echo "    nix flake update        # move the pins" >&2
            echo "    nix run .#relock        # re-resolve and rewrite ${lockDir}/" >&2
            exit 1
            ;;''
      else
        ''
          --pull)        MIGRATE=false; BUILD=false ;;
          --node-locks)  PULL=false;   MIGRATE=false; BUILD=false; NODE_LOCKS=true ;;
          --node-hashes)
            echo "bench-update: --node-hashes is now --node-locks (node-offline-hashes.json was replaced by node-locks/)" >&2
            PULL=false; MIGRATE=false; BUILD=false; NODE_LOCKS=true ;;''
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
          echo "Usage: bench-update [--pull | --migrate | --build | --node-locks [<app>[/<subdir>]…]]"
          echo ""
          echo "  (no flags)     Pull apps, refresh node-locks/, migrate, build"
          echo "  --pull         Pull latest commits + refresh node-locks/ for what moved"
          echo "  --migrate      Run DB migrations only"
          echo "  --build        Build JS/CSS assets only"
          echo "  --node-locks   (Re)generate node-locks/: a fallback yarn.lock for every app and nested"
          echo "                 frontend without one of its own (needs the network). Name a target to"
          echo "                 force a lock over the yarn.lock it ships — for one that cannot resolve offline."''
    }
              exit 0 ;;
            -*) echo "Unknown flag: $arg" >&2; exit 1 ;;
            *)
              if $NODE_LOCKS; then
                NODE_LOCK_TARGETS+=("$arg")
              else
                echo "Unexpected argument: $arg (targets only follow --node-locks)" >&2
                exit 1
              fi ;;
          esac
        done

        cd "$FRAPPE_BENCH_ROOT"

        if $PULL; then
          echo "── Pulling latest commits for all app submodules ────────────"
          declare -A _before_py
          for pp in apps/*/pyproject.toml; do
            [ -e "$pp" ] || continue
            _before_py["$pp"]=$(git hash-object "$pp" 2>/dev/null || echo none)
          done

          # Not `git submodule foreach`: it dies on the first gitlink that has no
          # .gitmodules entry (a nested repo someone `git add`ed as-is), taking
          # every other app's pull with it. The classifier names what each
          # apps/<x> is; only registered submodules are pulled, and the rest are
          # said out loud — a local app has nothing to pull, a stray repo is
          # invisible to `nix build` and needs the user.
          # Which remote of a submodule to pull from: the one whose URL is what
          # .gitmodules declares. That URL is the app's source of truth — it is
          # what `nix build` fetches the pinned commit from — and `origin` is not
          # a safe stand-in for it: a developer's checkout commonly has origin on
          # a fork that does not carry the release branch at all, and upstream on
          # the real thing. Compared with the trailing `.git` and `/` ignored,
          # which is as far as two spellings of one GitHub URL usually differ.
          _remote_for_url() { # <url> → remote name, or nothing
            local want="$1" r u
            want="''${want%/}"; want="''${want%.git}"
            for r in $(git remote); do
              u="$(git remote get-url "$r" 2>/dev/null || true)"
              u="''${u%/}"; u="''${u%.git}"
              if [ -n "$u" ] && [ "$u" = "$want" ]; then
                printf '%s' "$r"
                return 0
              fi
            done
            return 1
          }

          # Regenerable lockfile basenames — the one place to add one.
          _regen_locks=(yarn.lock package-lock.json)

          # `checkout -B` refuses over a tracked file with local changes that the
          # target commit also changes, and over an untracked file it would
          # create. Lockfiles in an app checkout are tool output — a postinstall's
          # non-frozen `yarn install`, a manual `yarn`, whatever else — and the
          # install that follows regenerates them from the commit's own, so clear
          # exactly those obstacles and nothing else. What git would carry across
          # untouched (a path the pull does not change) is left alone, which keeps
          # node_modules' sentinel valid. Run in the app dir, after FETCH_HEAD is
          # fetched and the ancestry guard has passed: only uncommitted changes
          # are discarded here, never commits.
          _clear_lock_obstacles() {
            local n p st manifest ign obstacle specs=()
            for n in "''${_regen_locks[@]}"; do specs+=(":(glob)**/$n"); done
            # fd 3, not stdin: see the `< /dev/null` below.
            while IFS= read -r -d "" -u 3 st && IFS= read -r -d "" -u 3 p; do
              obstacle=false
              if [ "$st" = A ]; then
                # Not in HEAD: a file (untracked, or staged new) where upstream adds
                # one. git overwrites an ignored one by itself; check-ignore says 1
                # for "not ignored" (0 ignored, 128 error — both leave it alone).
                if [ -e "$p" ] || [ -L "$p" ]; then
                  ign=0
                  git check-ignore -q -- "$p" || ign=$?
                  if [ "$ign" -eq 1 ]; then obstacle=true; fi
                fi
              elif ! git --literal-pathspecs diff --quiet HEAD -- "$p"; then
                obstacle=true
              fi
              "$obstacle" || continue
              # A lock beside a package.json that is edited too may be the other
              # half of a hand edit (`yarn add`), not tool output: leave it, and
              # let the checkout below say so.
              manifest="''${p%"''${p##*/}"}package.json"
              if ! git --literal-pathspecs diff --quiet HEAD -- "$manifest"; then
                echo "     keeping $p: the package.json beside it has local edits too"
                continue
              fi
              if [ "$st" = A ]; then
                git --literal-pathspecs rm -q -f --cached --ignore-unmatch -- "$p" || true
                rm -f -- "$p"
                echo "     discarding local $p (regenerated by yarn install; upstream adds one)"
              elif git --literal-pathspecs checkout -q HEAD -- "$p"; then
                echo "     discarding local changes to $p (regenerated by yarn install; upstream changes it)"
              fi
            done 3< <(git diff-tree -r -z --no-renames --name-status HEAD FETCH_HEAD -- "''${specs[@]}")
          }

          # The subshells below cannot return anything but an exit status, and a
          # non-zero one is fatal under `set -e`; apps left un-updated go to a file.
          _skipped_file="$(mktemp)"
          trap 'rm -f -- "$_skipped_file"' EXIT
          _skip() { printf '%s\n' "$1" >> "$_skipped_file"; }

          # Through tr, because tab is IFS *whitespace*: `read` collapses a run of
          # tabs into one delimiter, so a submodule with no branch — an empty third
          # field — had the URL land in $branch and git was handed it as a refspec
          # ("fatal: invalid refspec 'https://…'"). Unit separator is not whitespace,
          # so an empty field stays empty and the guard below sees it.
          while IFS=$'\037' read -r app kind branch url; do
            case "$kind" in
              local)
                echo "  · $app: local app (source committed with the bench) — nothing to pull"
                continue
                ;;
              nested-repo)
                echo "  ⚠  $app: a git repository that is not a registered submodule — not pulled,"
                echo "     and NOT in the flake's source tree, so 'nix build' leaves it out. Either"
                echo "     vendor it (frappe-init --migrate commits its source into the bench and"
                echo "     keeps its history in .frappe-nix-backup/) or push it somewhere and"
                echo "     re-add it: git rm --cached apps/$app && rm -rf apps/$app && bench-get-app <url>"
                continue
                ;;
              submodule-uninitialized)
                # Shell entry checks out only what a fresh clone has never had
                # (lib/apps-report.nix); a pull is where any other gets its
                # checkout — a deinitialized one's, say, or a fresh clone's
                # pulled before the shell was ever entered — and then moves with
                # the rest. Not --recursive, as ever: Frappe apps ship nested
                # submodules with broken refs. Without a gitlink there is no
                # commit to check out: .gitmodules outlived a removal, which
                # remove-app finishes.
                if ! git ls-files -s -- "apps/$app" \
                  | awk -v p="apps/$app" '$1 == "160000" && $4 == p { f = 1 } END { exit !f }'; then
                  echo "  ⚠  $app: .gitmodules registers it, but the bench records no commit for it — skipping."
                  echo "     A removal that stopped halfway; finish it with: bench remove-app $app"
                  continue
                fi
                echo "  → $app: registered but not checked out — checking out its pinned commit"
                git submodule update --init -- "apps/$app" < /dev/null || {
                  echo "  ✗ $app: could not check it out — skipping" >&2
                  continue
                }
                # The pinned commit is what uv.lock was resolved against, so it —
                # not the empty directory before it — is what the re-lock below
                # compares the pulled pyproject.toml with.
                _before_py["apps/$app/pyproject.toml"]=$(git hash-object "apps/$app/pyproject.toml" 2>/dev/null || echo none)
                ;;
            esac
            [ -n "$branch" ] || {
              echo "  ⚠  $app: no branch configured in .gitmodules — skipping."
              echo "     Fix it with: frappe-init --migrate (or: git config -f .gitmodules submodule.apps/$app.branch <branch>)"
              continue
            }
            (
              cd "apps/$app"
              # The declared URL's remote when there is one (see _remote_for_url);
              # else the URL itself, which git fetches just as well — only the
              # remote-tracking ref goes unrefreshed. The name-based guesses are a
              # last resort for a submodule registered without a URL.
              remote="$(_remote_for_url "$url" || true)"
              if [ -z "$remote" ] && [ -n "$url" ]; then
                remote="$url"
                echo "  · $app: no remote has the .gitmodules URL ($url); fetching it directly"
              fi
              if [ -z "$remote" ]; then
                remote=origin
                git remote | grep -qx origin || remote=$(git remote | head -n1)
              fi
              [ -n "$remote" ] || { echo "  ⚠  $app: no git remote — skipping"; _skip "$app"; exit 0; }
              echo "  → $app ($branch from $remote)"
              # No --depth here. A depth-1 fetch grafts the new tip with no parents,
              # so the ancestry check below could never pass once the remote had
              # moved — every pull was "skipped" for phantom local commits — and on
              # a full clone it cut the history down to that tip as a side effect.
              # A plain fetch on a shallow clone stops at what the clone already
              # has, so it costs only the new commits and stays shallow.
              #
              # A partial clone (lib/apps-report.nix makes them) keeps its
              # paired branch with the folders of every commit, and fetches
              # anything else as commits only — its configured filter. The
              # branch this pulls is the paired one, so ask for its folders.
              _filter=()
              if [ "$(git config --get "remote.$remote.promisor" 2>/dev/null)" = true ]; then
                _filter=(--filter=blob:none)
              fi
              git fetch "''${_filter[@]}" "$remote" "$branch" || {
                echo "  ✗ $app: could not fetch '$branch' from $remote" >&2
                echo "     .gitmodules says apps/$app is $url @ $branch; fix either the entry" >&2
                echo "     (git config -f .gitmodules submodule.apps/$app.branch <branch>) or the remote." >&2
                exit 1
              }
              # A shallow clone can be unable to answer the ancestry question at all.
              # Every depth-limited fetch grafts its tip with no parents — and this
              # script fetched with --depth 1 until 2026-09-11 — so a clone that
              # went through one has the pinned commit and the branch tip as two
              # disconnected islands, even though upstream is linear. The plain fetch
              # above never repairs that: negotiation starts from the tips the clone
              # already has, so the gap between them is never asked for. Left alone,
              # such a clone fails the check below on every pull and the app silently
              # stops updating.
              #
              # "No merge-base whatsoever" is the exact signature, and it is only
              # ever a shallow artefact: a real local commit shares its parent (the
              # pin) with the tip. So in that case, and only that case, deepen the
              # branch back to HEAD's own commit date — the gap and nothing older —
              # and ask again. The margin covers a backport whose committer date
              # runs a little behind the pin's.
              if [ "$(git rev-parse --is-shallow-repository)" = true ] &&
                [ -z "$(git merge-base HEAD FETCH_HEAD 2>/dev/null)" ]; then
                echo "     shallow clone with no path between HEAD and $remote/$branch — deepening to check"
                git fetch -q "$remote" "$branch" \
                  --shallow-since="@$(( $(git log -1 --format=%ct HEAD) - 86400 ))" || true
              fi
              # `checkout -B` discards anything not on the remote branch. Refuse when
              # this submodule carries commits that are not in what we just fetched.
              if ! git merge-base --is-ancestor HEAD FETCH_HEAD 2>/dev/null; then
                if [ -n "''${FRAPPE_BENCH_UPDATE_FORCE:-}" ]; then
                  echo "     ⚠  local commits will be discarded (FRAPPE_BENCH_UPDATE_FORCE=1)"
                else
                  echo "  ⚠  $app: HEAD is not an ancestor of $remote/$branch — it has local or"
                  echo "     unpushed commits that checkout -B would discard. Skipping."
                  echo "     Push them, or re-run with FRAPPE_BENCH_UPDATE_FORCE=1 to overwrite."
                  if [ "$(git rev-parse --is-shallow-repository)" = true ]; then
                    echo "     (This clone is shallow. If you made no commits here, 'git fetch --unshallow'"
                    echo "     in apps/$app settles it.)"
                  fi
                  _skip "$app"
                  exit 0
                fi
              fi
              _clear_lock_obstacles
              # Not a bare `git checkout ... || exit`: under `set -e` that would end
              # the whole bench-update for one app's dirty tree, with every later
              # app, the registry, the locks, migrate and build unrun. Whatever else
              # is in the way — git names it — is the user's to decide; the app is
              # skipped like one with local commits, and listed at the end.
              if ! _out="$(git checkout -B "$branch" "FETCH_HEAD" 2>&1)"; then
                echo "  ⚠  $app: git could not check out $remote/$branch — skipping. git says:"
                while IFS= read -r _l; do echo "       $_l"; done <<< "$_out"
                echo "     If that names local changes: commit them, stash them (git -C apps/$app stash),"
                echo "     or discard them (git -C apps/$app checkout -- <path>), then re-run."
                _skip "$app"
                exit 0
              fi
              [ -z "$_out" ] || echo "$_out"
              find . -name "*.pyc" -delete
            ) < /dev/null  # git must not eat the classifier's remaining lines
          done < <(${workspaceBin} apps --apps-dir apps | tr '\t' '\037')
          mapfile -t _skipped_apps < "$_skipped_file"
          if [ ''${#_skipped_apps[@]} -gt 0 ]; then
            echo "  ⚠  not updated:$(printf ' %s' "''${_skipped_apps[@]}") — see above"
          fi
          echo ""

          # The pins just moved; sites/apps.json records them. Before the node
          # hashes, which can be slow or fail on a network hiccup — the registry
          # must not be left describing the old commits because of that.
          ${syncRegistry}
          echo "  commit sites/apps.json along with the submodule bumps"
          echo ""

          # The fallback locks, for whatever the pull moved: an app without a
          # yarn.lock of its own whose package.json changed, a forced lock whose
          # upstream yarn.lock changed. The generator's own stamp decides what that
          # was — anything else costs nothing — and an app that arrived with the
          # pull lacking a lock gets its fallback in the same run. Apps that ship
          # a yarn.lock are not its business: they build from that.
          echo "── Refreshing node-locks/ ──────────────────────────────────"
          ${regenNodeLocksSoft}
          echo "  commit node-locks/ along with the submodule bumps, if it changed"
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
            : # the snippet above is empty on a bench with no node apps, and bash
              # refuses an empty then-clause
          fi
        fi

        if $NODE_LOCKS; then
          echo "── Regenerating node-locks/ ────────────────────────────────"
          ${regenNodeLocks} "''${NODE_LOCK_TARGETS[@]}"
        fi

        if $MIGRATE; then
          echo "── Running migrations ───────────────────────────────────────"
          # bench-migrate, not `bench migrate`: _FRAPPE_BENCH_RAW is exported here,
          # so the latter would skip the offline step in front of it.
          bench-migrate
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

        if [ ''${#_skipped_apps[@]} -gt 0 ]; then
          echo "⚠  bench-update complete, but not updated:$(printf ' %s' "''${_skipped_apps[@]}") (see above)"
        else
          echo "✅ bench-update complete"
        fi
  '';

  # Restore this bench from a Frappe backup — an explicit file, or the latest
  # one in the object store. py/frappe_nix_tools/frappe_nix_tools/restore/.
  #
  # Reached both directly and as `bench restore`, via the umbrella wrapper
  # above. Overridable wholesale through `extraScripts.bench-restore`, which is
  # how the consuming repos kept their own version while this one was built.
  bench-restore.exec = ''
    FRAPPE_NIX_CLI_CONFIG=${cliConfig} exec ${benchCli} restore "$@"
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
    # In a bench the apps are other people's repositories, and a yarn.lock
    # rewritten here is a modified file their next `git checkout` refuses over
    # (`bench update` then dies on the app). --pure-lockfile still installs from
    # package.json — a lock that has drifted from it is no obstacle, unlike under
    # --frozen-lockfile — but never writes the lock. In app mode apps/<app> is
    # the developer's own repository, and the lock is theirs to commit.
    echo "Updating Node dependencies..."
    ${lib.concatStringsSep "\n" (
      map (app: ''
        echo "  yarn install: ${app}"
        (cd "apps/${app}" && yarn install${lib.optionalString (!appMode) " --pure-lockfile"})
      '') appsWithNode
    )}
    echo ""
    ${
      if appMode then
        ''echo "Done! Commit any changed yarn.lock, then: nix run .#relock"''
      else
        ''
          echo "Refreshing node-locks/ for the apps without a yarn.lock of their own…"
          ${regenNodeLocksSoft}
          echo "Done! Commit uv.lock and node-locks/."
          echo "The apps' own yarn.lock files were not rewritten; to update one on purpose,"
          echo "run \`yarn install\` in that app yourself. (An app's postinstall can still"
          echo "rewrite a nested frontend's lock; \`bench-update --pull\` discards such a change"
          echo "when the pull would overwrite it.)"
        ''
    }
  '';

  # One-shot: creates $FRAPPE_SITE and installs every sites/apps.txt entry.
  # Not safe to re-run to pick up a later-pinned sibling — `--force` below
  # drops the site's database. That case is `reconcile-apps` instead (and,
  # in app mode with a fixed siteName, `frappe:apps-reconcile` runs it
  # automatically on every `devenv up` — see `appsReconcile.enable`).
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

  # Diffs sites/apps.txt — the candidate list frappe-nix regenerates from the
  # flake's pinned apps — against a site's actually-installed apps
  # (frappe.get_installed_apps(), DB-backed, what `bench migrate` consults,
  # not apps.txt) and installs whatever is missing. install-app is
  # idempotent (a no-op, unless --force, which this never passes), so
  # re-running this against an already-reconciled site costs one
  # `bench list-apps` and nothing else.
  #
  # Uses ${benchBin} directly rather than the bare `bench` this file's other
  # scripts call through the interactive wrapper: this also runs unattended
  # from tasks."frappe:apps-reconcile", which has no wrapper on PATH — so the
  # one rendering has to be correct in both contexts.
  #
  # This is what provision-site leaves undone afterwards: a sibling pinned
  # into the flake *after* a site already exists never gets installed by
  # anything else. See https://github.com/Avunu/frappe-nix/issues/32.
  reconcile-apps.exec = ''
    set -euo pipefail
    export _FRAPPE_BENCH_RAW=1
    cd "$FRAPPE_BENCH_ROOT"

    SITE="''${1:-''${FRAPPE_SITE:-}}"
    if [ -z "$SITE" ]; then
      echo "reconcile-apps: FRAPPE_SITE is not set and no site was given." >&2
      echo "  Usage: reconcile-apps <site>   (or set FRAPPE_SITE in .env)" >&2
      exit 1
    fi

    if [ ! -d "sites/$SITE" ]; then
      echo "reconcile-apps: sites/$SITE does not exist yet — run provision-site first."
      exit 0
    fi
    [ -f sites/apps.txt ] || exit 0

    # One at a time. On `devenv up` every process that waits on this task —
    # runtime, watch, … — runs it from its own `devenv-tasks` wrapper, so
    # several copies start in the same second, each sees the same missing apps
    # and each installs them: concurrent `install-app`s fighting over the
    # site's install_app lock, and their failures land in the Error Log. Held
    # from before the installed-apps query, so a copy that waited finds the
    # first one's work done and has nothing left to install. The lock is the
    # kernel's, not the file's: it goes when the holder does, however it dies.
    mkdir -p "sites/$SITE/locks"
    exec 9>"sites/$SITE/locks/reconcile-apps.lock"
    ${pkgs.util-linux}/bin/flock 9

    installed="$(${benchBin} --site "$SITE" list-apps --format json 2>/dev/null \
                 | ${pkgs.jq}/bin/jq -r --arg s "$SITE" '.[$s][]? // empty' 2>/dev/null || true)"

    MISSING=""
    while IFS= read -r app; do
      [ -z "$app" ] && continue
      [ "$app" = "frappe" ] && continue
      # An app frappe-nix.replacedApps is replacing: still on the bench until
      # every site has dropped it, but never to be installed again
      # (lib/rename/reconcile.nix sets this; unset, nothing is skipped).
      case " ''${FRAPPE_NIX_RECONCILE_SKIP:-} " in *" $app "*) continue ;; esac
      grep -qxF "$app" <<<"$installed" || MISSING="$MISSING $app"
    done < sites/apps.txt

    [ -n "$MISSING" ] || exit 0

    echo "reconcile-apps: sites/apps.txt names app(s) missing from $SITE's installed apps:$MISSING"
    FAILED=""
    for app in $MISSING; do
      echo "  installing $app…"
      ${benchBin} --site "$SITE" install-app "$app" || FAILED="$FAILED $app"
    done

    if [ -n "$FAILED" ]; then
      echo "reconcile-apps: failed to install:$FAILED — see the error above; continuing." >&2
      exit 0
    fi
    echo "reconcile-apps: $SITE now has every app in sites/apps.txt installed."
  '';

  # Add an existing app as a git submodule and register it in the uv workspace
  # ([tool.uv.workspace] members + [tool.uv.sources]) and sites/apps.txt.
  bench-get-app = {
    exec = ''
      set -euo pipefail
      export _FRAPPE_BENCH_RAW=1

      usage() {
        echo "Usage: bench-get-app [--branch <name>] <url-or-alias>"
        echo ""
        echo "Adds a Frappe app as a git submodule and integrates it into the workspace."
        echo "Without --branch the remote's default branch is used; either way the branch"
        echo "is recorded in .gitmodules, which is what 'bench-update --pull' follows."
        echo ""
        echo "Examples:"
        echo "  bench-get-app helpdesk                              # → frappe/helpdesk"
        echo "  bench-get-app frappe/payments                      # owner/repo on GitHub"
        echo "  bench-get-app https://github.com/frappe/hrms.git   # full URL"
        echo "  bench-get-app --branch version-16 hrms             # a release branch"
      }

      INPUT=""
      BRANCH=""
      while [ "$#" -gt 0 ]; do
        case "$1" in
          -b | --branch)
            [ -n "''${2:-}" ] || { usage >&2; echo "" >&2; echo "error: $1 needs a branch name" >&2; exit 1; }
            BRANCH="$2"; shift 2 ;;
          --branch=*) BRANCH="''${1#*=}"; shift ;;
          -h | --help) usage; exit 0 ;;
          -*) usage >&2; echo "" >&2; echo "error: unknown flag: $1" >&2; exit 1 ;;
          *)
            [ -z "$INPUT" ] || { usage >&2; echo "" >&2; echo "error: one app at a time" >&2; exit 1; }
            INPUT="$1"; shift ;;
        esac
      done
      if [ -z "$INPUT" ]; then
        usage
        exit 1
      fi

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

      REPO_NAME=$(basename "$URL" .git)
      if [ -d "apps/$REPO_NAME" ]; then
        echo "Error: App '$REPO_NAME' already exists in apps/$REPO_NAME"
        exit 1
      fi

      # The directory under apps/ is the Frappe app's name — the package holding
      # hooks.py, which is what frappe imports (`<app>.hooks`) and what
      # sites/apps.txt lists — and that is not always the repository's:
      # frappe/flow_client ships the app `flow`. Stock `bench get-app` renames
      # its clone to match; a submodule's path is fixed when it is added, so the
      # name is read first, from a throwaway clone with no checkout and no file
      # contents (the folder listing is all this needs).
      probe="$(mktemp -d)"
      trap 'rm -rf "$probe"' EXIT
      probe_args=(--quiet --depth 1 --filter=blob:none --no-checkout)
      if [ -n "$BRANCH" ]; then
        probe_args+=(-b "$BRANCH")
      fi
      git clone "''${probe_args[@]}" "$URL" "$probe"
      mapfile -t PACKAGES < <(git -C "$probe" ls-tree -r --name-only HEAD | sed -n 's|^\([^/]*\)/hooks\.py$|\1|p')
      rm -rf "$probe"

      APP_NAME="$REPO_NAME"
      if [ "''${#PACKAGES[@]}" -eq 0 ]; then
        echo "  ⚠  $REPO_NAME has no <package>/hooks.py — it does not look like a Frappe app; keeping the name '$REPO_NAME'" >&2
      elif [ "''${#PACKAGES[@]}" -eq 1 ]; then
        APP_NAME="''${PACKAGES[0]}"
      elif ! printf '%s\n' "''${PACKAGES[@]}" | grep -qxF "$REPO_NAME"; then
        echo "  ⚠  $REPO_NAME has several packages with a hooks.py (''${PACKAGES[*]}) and none is named for it; keeping the name '$REPO_NAME'" >&2
      fi
      APP_DIR="apps/$APP_NAME"

      if [ -d "$APP_DIR" ]; then
        echo "Error: App '$APP_NAME' already exists in $APP_DIR"
        exit 1
      fi
      if [ "$APP_NAME" != "$REPO_NAME" ]; then
        echo "  $REPO_NAME is the Frappe app '$APP_NAME' ($APP_NAME/hooks.py) — adding it as $APP_DIR"
      fi

      echo "Adding git submodule: $URL -> $APP_DIR''${BRANCH:+ ($BRANCH)}"
      # -b both checks the branch out and records it in .gitmodules. That record
      # is load-bearing: `bench-update --pull` follows it, and a submodule
      # registered without one is never pulled — this script left every app it
      # added in that state until 2026-09-14. Without --branch git clones the
      # remote's default branch; record whatever that turned out to be, so the
      # checkout and .gitmodules cannot disagree.
      add_args=()
      if [ -n "$BRANCH" ]; then
        add_args+=(-b "$BRANCH")
      fi
      git submodule add "''${add_args[@]}" "$URL" "$APP_DIR"
      # Not --recursive: Frappe apps often ship broken nested submodules that
      # have no production role and would fail init.
      git submodule update --init "$APP_DIR"
      if [ -z "$BRANCH" ]; then
        BRANCH="$(git -C "$APP_DIR" symbolic-ref --short HEAD)"
        git config -f .gitmodules "submodule.$APP_DIR.branch" "$BRANCH"
      fi
      echo "  recorded branch '$BRANCH' in .gitmodules"

      ${registerWorkspaceMember}

      ${syncRegistry}

      echo "Syncing Python dependencies..."
      uv sync

      echo ""
      echo "✅ App '$APP_NAME' added successfully!"
      echo ""
      echo "Next steps:"
      echo "  1. Restart devenv: direnv reload --no-eval-cache"
      echo "  2. Install the app: bench --site ''${FRAPPE_SITE:-<site>} install-app $APP_NAME"
      echo "  3. If it ships a package.json but no yarn.lock, resolve one for nix build: bench-update --node-locks"
    '';
    description = "Add a Frappe app from a git URL/alias as a submodule and register it in the uv workspace.";
  };

  # The inverse of bench-get-app, reached both directly and as `bench
  # remove-app`, via the umbrella wrapper above. It replaces the real
  # subcommand, which moves apps/<app> aside, edits sites/apps.txt in place and
  # pip-uninstalls into a read-only venv — none of which fits a bench whose
  # apps are submodules and whose registry is derived from pyproject.toml.
  #
  # Bench-level only, like the real one. `bench uninstall-app` — untouched, it
  # passes straight through — takes an app out of one site's database; this
  # takes it out of the bench, and refuses while any site still has it
  # installed.
  bench-remove-app = {
    exec = ''
      set -euo pipefail
      export _FRAPPE_BENCH_RAW=1

      usage() {
        cat <<'EOF'
      Usage: bench-remove-app [--force] [--no-backup] <app>

      Removes <app> from this bench: deinits its git submodule and drops it from
      .gitmodules (staging only that — any other uncommitted .gitmodules edit
      stays as it was), removes it from the uv workspace (pyproject.toml),
      regenerates sites/apps.txt and sites/apps.json, drops node-locks/<app>
      and sites/assets/<app>, and relocks (uv lock).

      Refuses while any site in this bench has <app> installed — take it out of
      each first with `bench --site <site> uninstall-app <app>` — and while the
      submodule's work tree has uncommitted changes.

        --force      skip both checks
        --no-backup  do not move a vendored app to .frappe-nix-backup/ first
                     (a submodule is never backed up: `bench get-app` re-adds it)
      EOF
      }

      APP=""
      FORCE=false
      BACKUP=true
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --force) FORCE=true; shift ;;
          --no-backup) BACKUP=false; shift ;;
          -h | --help) usage; exit 0 ;;
          -*) usage >&2; echo "" >&2; echo "error: unknown flag: $1" >&2; exit 1 ;;
          *)
            [ -z "$APP" ] || { usage >&2; echo "" >&2; echo "error: one app at a time" >&2; exit 1; }
            APP="$1"; shift ;;
        esac
      done
      if [ -z "$APP" ]; then
        usage >&2
        exit 1
      fi
      case "$APP" in
        frappe)
          echo "bench-remove-app: frappe is the framework every other app runs on — it cannot be removed." >&2
          exit 1 ;;
        . | .. | */*)
          echo "bench-remove-app: '$APP' is not an app name." >&2
          exit 1 ;;
      esac

      cd "$FRAPPE_BENCH_ROOT"
      APP_DIR="apps/$APP"

      # ── identity, before anything moves ─────────────────────────────────
      # KIND is empty when frappe-nix-workspace doesn't know the name — not
      # under apps/, not in .gitmodules. DIST_NAME is the [tool.uv.sources]
      # key, which can differ from the directory name (a fork's own
      # [project].name) and is unreadable once the directory is gone.
      KIND="$(${workspaceBin} apps --apps-dir apps | awk -F'\t' -v a="$APP" '$1==a{print $2; exit}')"
      if [ -z "$KIND" ] && ! grep -qF "\"$APP_DIR\"" pyproject.toml; then
        echo "bench-remove-app: no app '$APP' in this bench — not under apps/, not in" >&2
        echo ".gitmodules, not a uv workspace member." >&2
        exit 1
      fi
      DIST_NAME=""
      if [ -d "$APP_DIR" ]; then
        DIST_NAME="$(${workspaceBin} dist-name --app-dir "$APP_DIR")"
      fi

      if ! $FORCE; then
        # ── refuse while a site still runs it ─────────────────────────────
        # Asked of every site's actual installed apps, the query reconcile-apps
        # and bench-restore use. A site whose list cannot be read (database
        # down, broken site) does not count as "not installed": that is exactly
        # when pulling the code out from under it would go unnoticed until its
        # next `bench migrate` dies on the import.
        INSTALLED=""
        UNREADABLE=""
        for cfg in sites/*/site_config.json; do
          [ -e "$cfg" ] || continue
          s="$(basename "$(dirname "$cfg")")"
          hit="$(${benchBin} --site "$s" list-apps --format json 2>/dev/null \
            | ${pkgs.jq}/bin/jq -r --arg s "$s" --arg a "$APP" \
              '(.[$s] // []) | any(. == $a)' 2>/dev/null)" || hit=""
          case "$hit" in
            true) INSTALLED="$INSTALLED $s" ;;
            false) ;;
            *) UNREADABLE="$UNREADABLE $s" ;;
          esac
        done
        if [ -n "$INSTALLED" ]; then
          echo "bench-remove-app: $APP is still installed on:$INSTALLED" >&2
          echo "Uninstall it from each site first:" >&2
          for s in $INSTALLED; do
            echo "    bench --site $s uninstall-app $APP" >&2
          done
          echo "or pass --force to remove it anyway." >&2
          exit 1
        fi
        if [ -n "$UNREADABLE" ]; then
          echo "bench-remove-app: could not read the installed apps of:$UNREADABLE" >&2
          echo "(is the database up? devenv up) — not removing $APP without knowing" >&2
          echo "no site still runs it. Pass --force to remove it anyway." >&2
          exit 1
        fi

        # deinit -f below discards the submodule's work tree wholesale. Its own
        # check for local changes is a dry-run `git rm`, which dies on any
        # unstaged .gitmodules edit (see below) — so ask git status directly.
        if [ "$KIND" = submodule ]; then
          dirty="$(git -C "$APP_DIR" status --porcelain 2>/dev/null || true)"
          if [ -n "$dirty" ]; then
            echo "bench-remove-app: $APP_DIR has uncommitted changes that removing it would discard:" >&2
            printf '%s\n' "$dirty" | head -n 20 >&2
            echo "Commit or discard them, or pass --force to remove it anyway." >&2
            exit 1
          fi
        fi
      fi

      if [ -z "$KIND" ]; then
        echo "  $APP_DIR is not on disk and not in .gitmodules — only its workspace registration is left."
      fi

      # ── out of git ──────────────────────────────────────────────────────
      case "$KIND" in
        submodule | submodule-uninitialized)
          # The submodule's name, which .git/config and .git/modules/ are keyed
          # on — not always its path (lib/sh/detect.sh resolves it the same way).
          NAME="$(git config -f .gitmodules --get-regexp '^submodule\..*\.path$' 2>/dev/null \
            | awk -v p="$APP_DIR" '$2 == p { sub(/^submodule\./, "", $1); sub(/\.path$/, "", $1); print $1; exit }')" || true
          NAME="''${NAME:-$APP_DIR}"
          echo "Removing the $APP_DIR submodule…"
          # Plumbing, not `git rm`: git rm — --cached included — refuses
          # outright while .gitmodules has any unstaged edit ("please stage your
          # changes to .gitmodules or stash them to proceed"), the usual state
          # of a bench mid-work. vendor_app (lib/sh/apps.sh) sidesteps it the
          # same way.
          if [ "$KIND" = submodule ]; then
            git submodule deinit -q -f -- "$APP_DIR"
          fi
          git config --local --remove-section "submodule.$NAME" 2>/dev/null || true
          git update-index --force-remove -- "$APP_DIR"
          rmdir "$APP_DIR" 2>/dev/null || true
          # .gitmodules' working copy and index copy each lose the section on
          # their own, so exactly this removal is staged next to the gitlink's —
          # what `git rm` would stage — and every other edit stays where it was.
          git config -f .gitmodules --remove-section "submodule.$NAME" 2>/dev/null || true
          if git cat-file -e :.gitmodules 2>/dev/null; then
            staged="$(mktemp)"
            git show :.gitmodules > "$staged"
            if git config -f "$staged" --remove-section "submodule.$NAME" 2>/dev/null; then
              git update-index --cacheinfo "100644,$(git hash-object -w "$staged"),.gitmodules"
            fi
            rm -f "$staged"
          fi
          # Its clone. Left behind, it also makes a later `bench get-app` of the
          # same app refuse ("A git directory for … is found locally").
          rm -rf "$(git rev-parse --git-path "modules/$NAME")"
          if [ -e "$APP_DIR" ]; then
            echo "  ⚠  $APP_DIR still holds files git never tracked; left in place" >&2
          fi
          ;;
        local)
          # A local app has no .git of its own — that is what tells it apart
          # from nested-repo below — so its content lives nowhere but here and,
          # the committed part of it only, in the bench's history.
          if $BACKUP; then
            mkdir -p .frappe-nix-backup
            dest=".frappe-nix-backup/$APP-$(date +%Y%m%d-%H%M%S)"
            git rm -r -q -f --cached --ignore-unmatch -- "$APP_DIR"
            mv "$APP_DIR" "$dest"
            echo "  moved to $dest — restore with: mv $dest $APP_DIR && git add $APP_DIR"
          else
            git rm -r -q -f --ignore-unmatch -- "$APP_DIR"
            rm -rf "$APP_DIR"
          fi
          ;;
        nested-repo)
          echo "bench-remove-app: $APP_DIR is a git repository but not a registered submodule —" >&2
          echo "not removing it automatically. By hand:" >&2
          echo "    git rm --cached $APP_DIR && rm -rf $APP_DIR" >&2
          exit 1
          ;;
      esac

      # ── out of the workspace, the registry and the build inputs ─────────
      echo "Removing $APP from the uv workspace…"
      ${workspaceBin} remove-app --pyproject pyproject.toml --app "$APP" --source-name "$DIST_NAME"

      ${syncRegistry}

      if [ -d "node-locks/$APP" ]; then
        rm -rf "node-locks/$APP"
        echo "  - node-locks/$APP"
      fi
      # A symlink into apps/<app>/<app>/public — dangling now.
      if [ -L "sites/assets/$APP" ]; then
        rm -f "sites/assets/$APP"
        echo "  - sites/assets/$APP"
      fi

      echo "Re-locking the Python workspace…"
      if ! uv lock; then
        echo "  ⚠  uv lock failed — the dev shell will not evaluate until it resolves. Re-run: uv lock" >&2
        exit 1
      fi

      echo ""
      echo "✅ $APP removed from the bench."
      echo "   Commit the staged removal with pyproject.toml, uv.lock and sites/apps.json,"
      echo "   then: direnv reload --no-eval-cache"
    '';
    description = "Remove a Frappe app from the bench — submodule, .gitmodules, uv workspace, registry, locks — once no site has it installed.";
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

      ${syncRegistry}

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
// dropInScripts
