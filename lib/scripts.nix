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
  # Absolute path to lib/build-deps.nix's `sync`, run after each `uv lock` to
  # derive the managed [tool.uv.extra-build-dependencies] block; null runs
  # nothing (a consumer rendering these scripts on their own).
  buildDepsSyncBin ? null,
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
  # frappe-nix-sources (python-devops-nix's lib.sources, lib/shell-tools.nix):
  # bench-get-app's clone. Left as a bare name, same convention as the tools
  # above.
  sources ? "frappe-nix-sources",
  # perSystem.frappe-nix.renamedApps and replacedApps (OLD = NEW), which
  # reconcile-apps applies around its installs.
  renamedApps ? { },
  replacedApps ? { },
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
  # shell so a caller's own FRAPPE_NIX_OFFLINE_MIGRATE_* — `=0` for one migrate
  # that must run stock — still wins. So does one under its old name
  # (FRAPPE_OFFLINE_MIGRATE_*), which the tool reads with a warning through 1.x,
  # until frappe-nix 2.0.0.
  offlineMigrateEnv =
    let
      default = name: value: ''
        if [ -z "''${FRAPPE_NIX_${name}+x}" ] && [ -z "''${FRAPPE_${name}+x}" ]; then
          export FRAPPE_NIX_${name}=${lib.escapeShellArg value}
        fi
      '';
    in
    lib.concatStrings [
      (lib.optionalString (offlineMigrate ? ptOsc) (
        default "OFFLINE_MIGRATE_PT_OSC" offlineMigrate.ptOsc
      ))
      (lib.optionalString (offlineMigrate ? rowThreshold) (
        default "OFFLINE_MIGRATE_ROW_THRESHOLD" (toString offlineMigrate.rowThreshold)
      ))
    ];

  # The one implementation of the apps/ ⇄ pyproject.toml ⇄ sites/apps.{txt,json}
  # contract, shared with the `frappe-init` scaffolder/migrator and the bench
  # package build so none of them can drift apart. Idempotent and
  # comment-preserving (tomlkit).
  workspaceTool = import ./workspace-tool.nix { inherit pkgs; };
  workspaceBin = "${workspaceTool}/bin/frappe-nix-workspace";

  # Shell snippet: after a `uv lock`, the managed build-dependency block in step
  # with the new lock (lib/build-deps.nix). Expects cwd at the bench root.
  syncBuildDeps = lib.optionalString (buildDepsSyncBin != null) ''
    ${buildDepsSyncBin}
  '';

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
  # wrapper dispatches `get-app`/`new-app`/`remove-app`/`rename-app` to them, and a missing
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

    bench-rename-app = {
      exec = ''
        cat >&2 <<'EOF'
        bench-rename-app: this is an app repository, not a bench — there is no
        apps/ submodule or uv workspace membership of yours to move here.

        Rename the sibling in frappe-nix.app.siblings instead (and the flake input
        it comes from), then: nix run .#relock
        EOF
        exit 1
      '';
      description = "Not available in app mode — rename the sibling declaration instead.";
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
  # What the Python bench commands read (frappe_nix_tools/bench_config.py):
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
      # get-app, remove-app, rename-app, new-app and reconcile-apps
      # (frappe_nix_tools/apps/config.py).
      apps =
        let
          pairs = lib.mapAttrsToList (old: new: { inherit old new; });
        in
        {
          bench = benchBin;
          python = pythonBin;
          workspace = workspaceBin;
          inherit sources;
          buildDepsSync = buildDepsSyncBin;
          renameTool = "${./rename/frappe_rename_app.py}";
          renamedApps = pairs renamedApps;
          replacedApps = pairs replacedApps;
        };
      # bench setup requirements (frappe_nix_tools/deps/config.py).
      deps = {
        inherit appsWithNode appMode;
        nodeVerify = nodeVerifyBin;
        nodeModules = nodeModulesBin;
        requirementsCheck = "${./requirements-check.py}";
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
      rename-app)  shift; exec bench-rename-app "$@" ;;
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
  #
  # py/frappe_nix_tools/frappe_nix_tools/deps/setup_requirements.py.
  bench-setup-requirements.exec = ''
    FRAPPE_NIX_CLI_CONFIG=${cliConfig} exec ${benchCli} setup-requirements "$@"
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
                # (python-devops-nix's lib.sources); a pull is where any other gets its
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
              # A partial clone (shell entry's lib.sources makes them) keeps its
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
                if [ -z "''${FRAPPE_NIX_BENCH_UPDATE_FORCE:-}" ] && [ -n "''${FRAPPE_BENCH_UPDATE_FORCE:-}" ]; then
                  echo "     ⚠  FRAPPE_BENCH_UPDATE_FORCE is deprecated; set FRAPPE_NIX_BENCH_UPDATE_FORCE instead (the old name goes in frappe-nix 2.0.0)" >&2
                  FRAPPE_NIX_BENCH_UPDATE_FORCE=$FRAPPE_BENCH_UPDATE_FORCE
                fi
                if [ -n "''${FRAPPE_NIX_BENCH_UPDATE_FORCE:-}" ]; then
                  echo "     ⚠  local commits will be discarded (FRAPPE_NIX_BENCH_UPDATE_FORCE=1)"
                else
                  echo "  ⚠  $app: HEAD is not an ancestor of $remote/$branch — it has local or"
                  echo "     unpushed commits that checkout -B would discard. Skipping."
                  echo "     Push them, or re-run with FRAPPE_NIX_BENCH_UPDATE_FORCE=1 to overwrite."
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
            ${syncBuildDeps}
            echo "  commit uv.lock (and pyproject.toml) along with the submodule bumps"
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
          uv lock || exit
          ${syncBuildDeps}
          uv sync
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
  # in app mode with a fixed defaultSite, `frappe:apps-reconcile` runs it
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
    # Read the port back rather than trusting an env var: `devenv up` leases
    # the ports (frappe-ports), so anything resolved in a plain shell is the
    # bench's preferred port, which may not be the one nginx actually took.
    # `frappe-nix-ports web` names it while the bench runs; the config's
    # webserver_port, which frappe:config writes, is the fallback.
    _url_port="$(frappe-nix-ports web 2>/dev/null)" \
      || _url_port="$(${pkgs.jq}/bin/jq -r '.webserver_port // 8000' sites/common_site_config.json)"
    echo "   URL: http://127.0.0.1:$_url_port"
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
  #
  # With frappe-nix.renamedApps and replacedApps around it: renames first,
  # replacements after (docs/app-standards/spec.md §5.10).
  # py/frappe_nix_tools/frappe_nix_tools/apps/reconcile.py.
  reconcile-apps.exec = ''
    FRAPPE_NIX_CLI_CONFIG=${cliConfig} exec ${benchCli} reconcile-apps "$@"
  '';

  # Add an existing app as a git submodule and register it in the uv workspace
  # ([tool.uv.workspace] members + [tool.uv.sources]) and sites/apps.txt.
  #
  # py/frappe_nix_tools/frappe_nix_tools/apps/get_app.py. Its clone is
  # python-devops-nix's lib.sources (`frappe-nix-sources clone`), the shape
  # shell entry gives every app.
  bench-get-app = {
    exec = ''
      FRAPPE_NIX_CLI_CONFIG=${cliConfig} exec ${benchCli} get-app "$@"
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
  #
  # py/frappe_nix_tools/frappe_nix_tools/apps/remove_app.py.
  bench-remove-app = {
    exec = ''
      FRAPPE_NIX_CLI_CONFIG=${cliConfig} exec ${benchCli} remove-app "$@"
    '';
    description = "Remove a Frappe app from the bench — submodule, .gitmodules, uv workspace, registry, locks — once no site has it installed.";
  };

  # The bench's half of renaming an app: apps/<old> to apps/<new> with its
  # submodule, clone, .gitmodules entry, workspace member and registry, moved
  # rather than removed and added again. `frappe-rename-app` does the other two
  # halves, the package in the app's repository and the sites that have it
  # installed; a `git pull` of the renamed repository does none of this one.
  #
  # py/frappe_nix_tools/frappe_nix_tools/apps/rename_app.py.
  bench-rename-app = {
    exec = ''
      FRAPPE_NIX_CLI_CONFIG=${cliConfig} exec ${benchCli} rename-app "$@"
    '';
    description = "Rename a Frappe app in the bench — move its submodule, clone, .gitmodules entry, uv workspace member and registry — the bench's half of frappe-rename-app.";
  };

  # Scaffold a brand-new app and register it in the uv workspace. Wraps
  # `bench new-app`, whose trailing pip-install step fails in the read-only Nix
  # env (expected/ignored).
  #
  # py/frappe_nix_tools/frappe_nix_tools/apps/new_app.py.
  bench-new-app = {
    exec = ''
      FRAPPE_NIX_CLI_CONFIG=${cliConfig} exec ${benchCli} new-app "$@"
    '';
    description = "Scaffold a new Frappe app and integrate it into the uv workspace.";
  };
}
// lib.optionalAttrs appMode appModeOverrides
// dropInScripts
