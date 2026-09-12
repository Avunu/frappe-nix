# Bench infrastructure: app discovery, node_modules, benchRoot, and builtBench.
#
# benchRoot  — unbuilt /bench tree for dev shells and as a build input. Also
#              where sites/apps.txt and sites/apps.json are generated: the
#              registry is an output of the package, never a copy of what the
#              bench happens to have committed. And where each app's (and each
#              nested frontend's) node_modules, built from the committed
#              node-locks/, are linked in.
# builtBench — benchRoot + compiled assets (`bench build`), the deployable
#              artifact. Exposes passthru.{pythonEnv,nodejs,appsPath,appNames,
#              registeredApps} so the NixOS module can discover interpreters
#              and the registered apps from the package.

{
  pkgs,
  lib,
  prodPythonEnv,
  workspaceRoot,
  nodejs,
  # "app/subdir" keys of nested frontends to leave out entirely: no lock, no
  # node_modules, and the parent app's build/postinstall scripts that drive the
  # subdir are dropped from its package.json (see dropNestedFrontendScripts).
  nodeNestedFrontendExcludes ? [ ],
  # Per target key (an app name, or "app/subdir"): extra `derivationArgs` for
  # importNpmLock.buildNodeModules — npmFlags, npmRebuildFlags (`""` to run
  # lifecycle scripts), postPatch, nativeBuildInputs, or an npmDeps of your own.
  nodeOverrides ? { },
  extraPackages ? [ ],
  # Where node-locks/<target>/ lives. The workspace root's in a bench; the app
  # repository's nix/node-locks in app mode (there is no bench root to commit
  # to there, and the assembled workspace is rebuilt on every pin bump).
  nodeLocksDir ? workspaceRoot + "/node-locks",
  # What to tell the user when a lock is missing or stale — the command
  # differs between a bench and an app repository.
  nodeLocksAdvice ? "run `bench-update --node-locks` and commit node-locks/",
  # App mode hands both of these over instead of letting them be discovered.
  #
  # Discovery is right for a bench, where apps/ is the checkout and readDir is the
  # only thing that knows what is in it. For an assembled workspace the list is
  # known exactly, so the readDir is pure cost — it forces that workspace to be
  # built during evaluation.
  #
  # `appSrcs` matters more, and names the *original* sources rather than anything
  # under `workspaceRoot`. lib/app-workspace.nix mirrors each app — real
  # directories, symlinked files — and a path added to the store as a *source*
  # keeps no references, so copying one of those mirrors into a derivation gives
  # it links whose targets are not in the sandbox. Which fails as a missing file
  # the build can plainly see on disk. Everything below that reaches an app's
  # bytes goes through appSrcOf for that reason.
  appNames ? null,
  appSrcs ? null,
  # The bench root's pyproject.toml, parsed. lib/python.nix reads the same
  # file; the default here is for callers that do not go through it.
  rootPyproject ? builtins.fromTOML (builtins.readFile (workspaceRoot + "/pyproject.toml")),
  # `{ <app> = { commit_hash; branch; is_repo; }; }` — what the caller knows
  # about where each app came from that the source tree cannot say. App mode
  # fills this from its flake inputs; a bench repo has nothing to add (its
  # .gitmodules is in the tree, and the tool reads that itself).
  provenance ? { },
}:

let
  # Directories only: readDir also reports files, and a stray tracked file under
  # apps/ would otherwise become an "app" on PYTHONPATH and in benchRoot.
  discoveredAppNames = builtins.attrNames (
    lib.filterAttrs (_: type: type == "directory") (builtins.readDir (workspaceRoot + "/apps"))
  );

  names = if appNames != null then appNames else discoveredAppNames;

  # Where an app's source actually lives, which is not necessarily where the
  # workspace says it does. See the argument comment above.
  appSrcOf = app: if appSrcs != null then appSrcs.${app} else workspaceRoot + "/apps/${app}";

  appsPath = root: lib.concatMapStringsSep ":" (app: "${root}/apps/${app}") names;

  # ── the registry ────────────────────────────────────────────────────────
  #
  # sites/apps.txt is what frappe.get_all_apps() returns and what `install-app`
  # checks a name against; sites/apps.json is bench's record of each app's
  # version and pin. Both are written by `frappe-nix-workspace sync-registry`
  # (lib/frappe-workspace.py) — the same tool the dev shell and frappe-init run
  # — from one rule: the registered apps are the [tool.uv.workspace].members,
  # in declared order, frappe first. Members, because that is what the
  # virtualenv actually installs; a directory under apps/ that is not one is
  # on PYTHONPATH and nothing more.
  #
  # The rule is mirrored here, in Nix, for two things the build cannot do: an
  # evaluation-time warning about an app that will silently not be registered,
  # and passthru.registeredApps for the NixOS module and tests. benchRoot diffs
  # this list against what the tool wrote, so the two cannot drift.
  workspaceTool = import ./workspace-tool.nix { inherit pkgs; };

  # Explicit `apps/<x>` entries only, like the tool: a glob would need a second
  # matcher here that agrees with Python's, so neither side accepts one.
  memberNames = lib.unique (
    lib.concatMap (
      member:
      let
        parts = lib.splitString "/" (lib.removeSuffix "/" member);
      in
      if builtins.length parts == 2 && builtins.head parts == "apps" && builtins.match ".*[*?[].*" member == null then
        [ (lib.last parts) ]
      else
        [ ]
    ) (rootPyproject.tool.uv.workspace.members or [ ])
  );

  isFrappeApp = app: builtins.pathExists (appSrcOf app + "/${app}/hooks.py");

  presentMembers = lib.filter (app: lib.elem app names && isFrappeApp app) memberNames;

  registeredApps =
    let
      apps = lib.optional (lib.elem "frappe" presentMembers) "frappe"
        ++ lib.filter (app: app != "frappe") presentMembers;
      unregistered = lib.filter (app: isFrappeApp app && !(lib.elem app apps)) names;
    in
    lib.warnIf (unregistered != [ ]) (
      "frappe-nix: not registered — a hooks.py but no [tool.uv.workspace] member, "
      + "so on PYTHONPATH only and invisible to frappe: "
      + lib.concatMapStringsSep ", " (a: "apps/${a}") unregistered
      + ". Register with bench-get-app / `frappe-nix-workspace add-app`, or remove the directory."
    ) apps;

  registryExpected = pkgs.writeText "apps.txt.expected" (
    lib.concatMapStrings (app: app + "\n") registeredApps
  );

  provenanceFile = pkgs.writeText "apps-provenance.json" (builtins.toJSON provenance);

  # ── node_modules ────────────────────────────────────────────────────────
  #
  # Every node target — an app with a package.json, and each nested frontend
  # under it (lib/node-targets.nix) — gets a node_modules built by
  # pkgs.importNpmLock from node-locks/<target>/package-lock.json, committed
  # to the bench and written by `frappe-nix-node-locks` (lib/node-locks.nix)
  # with npm: pinned to the app's yarn.lock when it ships one, resolved from
  # package.json when it does not. No hash is computed anywhere. Each package
  # is fetched by the integrity the lock already carries, a git dependency by
  # its commit — so an upstream yarn.lock bump costs a lock regeneration,
  # never a hash hunt, and a nested frontend gets the same treatment as its
  # parent rather than a hand-rolled offline install inside builtBench.
  #
  # The dev shell does not use these: it installs with a plain online
  # `yarn install`, as upstream tooling expects (lib/node-modules.nix).

  # Dev-shell contract only (scripts.nix, enterShell): `yarn install
  # --frozen-lockfile` wants a yarn.lock, so this is the apps that have one.
  appsWithNode = lib.filter (
    app:
    builtins.pathExists (appSrcOf app + "/package.json")
    && builtins.pathExists (appSrcOf app + "/yarn.lock")
  ) names;

  nodeTargets = (import ./node-targets.nix { inherit lib; }).discover {
    inherit names appSrcOf;
    excludes = nodeNestedFrontendExcludes;
  };
  nodeTargetKeys = map (t: t.key) nodeTargets;

  lockDirOf = t: nodeLocksDir + "/${t.key}";
  hasLock =
    t:
    builtins.pathExists (lockDirOf t + "/package-lock.json")
    && builtins.pathExists (lockDirOf t + "/package.json");

  # The same manifests, hashed the same way, as the generator's source.json.
  sourceStamp =
    t:
    lib.listToAttrs (
      lib.concatMap (
        f:
        lib.optional (builtins.pathExists (t.src + "/${f}")) (
          lib.nameValuePair f (builtins.hashFile "sha256" (t.src + "/${f}"))
        )
      ) [ "package.json" "yarn.lock" "package-lock.json" ]
    );
  lockIsStale =
    t:
    builtins.pathExists (lockDirOf t + "/source.json")
    && lib.importJSON (lockDirOf t + "/source.json") != sourceStamp t;

  nodeModulesFor =
    t:
    let
      dir = lockDirOf t;
    in
    lib.warnIf (lockIsStale t)
      "frappe-nix: node-locks/${t.key} is older than apps/${t.key}'s package.json/yarn.lock — ${nodeLocksAdvice}"
      (
        pkgs.importNpmLock.buildNodeModules {
          # The normalized manifest npm resolved against (yarn `resolutions`
          # turned into npm `overrides`), not the app's own: the lock only
          # makes sense next to the manifest it was resolved from.
          package = lib.importJSON (dir + "/package.json");
          packageLock = lib.importJSON (dir + "/package-lock.json");
          inherit nodejs;
          derivationArgs = {
            pname = "${lib.replaceStrings [ "/" ] [ "-" ] t.key}-node-modules";
            # Strings, not lists: buildNodeModules forces structured attrs, under
            # which the hook's `$npmInstallFlags` is a bash array's first element.
            #
            # --legacy-peer-deps mirrors the generator: yarn v1 never installed
            # peers, the lock was resolved without them, and an install that
            # disagreed would re-resolve — offline, so it would fail instead.
            npmInstallFlags = "--legacy-peer-deps";
            # No lifecycle scripts, as before. Every native piece a Frappe
            # frontend needs is a platform package or a prebuilt binary; a
            # consumer whose app is different sets npmRebuildFlags = "".
            npmRebuildFlags = "--ignore-scripts";
          }
          // (nodeOverrides.${t.key} or { });
        }
      );

  lockedTargets = lib.filter hasLock nodeTargets;
  missingLocks = lib.filter (t: !(hasLock t)) nodeTargets;

  nodeModules = lib.listToAttrs (map (t: lib.nameValuePair t.key (nodeModulesFor t)) lockedTargets);

  # A missing lock is not fatal here — the dev shell never needs it — but the
  # package it produces has no node_modules for that target, and `bench build`
  # runs `yarn install` (online) the moment it finds an app without one.
  warnMissingLocks = lib.warnIf (missingLocks != [ ]) (
    "frappe-nix: no node lock for ${lib.concatMapStringsSep ", " (t: t.key) missingLocks} — "
    + nodeLocksAdvice
    + ". Until then the package carries no node_modules for it, and builtBench fails on any of them with a build script."
  );

  # The excluded subdirs belonging to one app, from the flat "app/subdir" list.
  excludedSubdirsOf =
    app:
    map (lib.removePrefix "${app}/") (
      lib.filter (lib.hasPrefix "${app}/") nodeNestedFrontendExcludes
    );

  # Excluding a nested frontend also has to disarm whatever in the *parent* app
  # builds it -- see lib/js/drop-nested-frontend-scripts.js for why skipping the
  # install alone is not enough. Done here, in benchRoot, rather than in
  # builtBench's buildPhase, so a later `bench build` over a dev bench or the
  # deployed tree walks into the same repaired package.json.
  dropNestedFrontendScripts = ./js/drop-nested-frontend-scripts.js;

  benchRoot = warnMissingLocks (pkgs.runCommand "bench-root" {
    # Not pkgs.git: sync-registry reads a checkout's .git when there is one,
    # and a store copy that happened to carry one must not change the output.
    # Without git on PATH the tool falls through to .gitmodules and the seed.
    nativeBuildInputs = [ workspaceTool ] ++ lib.optionals (nodeNestedFrontendExcludes != [ ]) [ nodejs ];
  } ''
    mkdir -p $out/bench/{sites,logs,config/pids}

    ln -s ${prodPythonEnv} $out/bench/env

    mkdir -p $out/bench/apps
    ${lib.concatStringsSep "\n" (
      map (app: ''
        cp -r ${appSrcOf app} $out/bench/apps/${app}
        chmod -R u+w $out/bench/apps/${app}
        ${lib.optionalString (excludedSubdirsOf app != [ ]) ''
          node ${dropNestedFrontendScripts} \
            $out/bench/apps/${app}/package.json \
            ${lib.escapeShellArgs (excludedSubdirsOf app)}
        ''}
      '') names
    )}

    # node_modules, for every target with a lock: a real directory of links
    # to the store's packages, not one link to the store's node_modules. Vite
    # (8, via rolldown) mkdirs node_modules/.vite-temp while it bundles an ESM
    # vite.config, and only an EACCES is tolerated — the sandbox's read-only
    # store answers EROFS and the build dies. Node, esbuild and vite realpath
    # through the per-entry links; the directory itself is writable wherever
    # the tree is copied to (builtBench's $TMPDIR).
    _link_node_modules() { # <store node_modules> <destination>
      mkdir -p "$2"
      for entry in "$1"/* "$1"/.[!.]*; do
        { [ -e "$entry" ] || [ -L "$entry" ]; } || continue
        ln -s "$entry" "$2/''${entry##*/}"
      done
    }
    ${lib.concatMapStrings (t: ''
      rm -rf $out/bench/apps/${t.key}/node_modules
      _link_node_modules ${nodeModules.${t.key}}/node_modules $out/bench/apps/${t.key}/node_modules
    '') lockedTargets}

    # The registry. The committed apps.json, if any, is only a *seed*: the
    # lowest-ranked source, consulted for a commit hash the sandbox cannot
    # read (a bench repo's apps/ are submodules, and the flake's source tree
    # carries their files but not their .git). Versions, order and required
    # apps are always recomputed from the sources.
    frappe-nix-workspace sync-registry \
      --pyproject ${workspaceRoot + "/pyproject.toml"} \
      --apps-dir $out/bench/apps \
      --sites-dir $out/bench/sites \
      ${lib.optionalString (builtins.pathExists (workspaceRoot + "/.gitmodules"))
        "--gitmodules ${workspaceRoot + "/.gitmodules"}"} \
      ${lib.optionalString (builtins.pathExists (workspaceRoot + "/sites/apps.json"))
        "--seed ${workspaceRoot + "/sites/apps.json"}"} \
      ${lib.optionalString (provenance != { }) "--provenance ${provenanceFile}"}
    # The Nix mirror of the membership rule (registeredApps) must agree with
    # what the tool wrote, or passthru would describe a different bench.
    diff ${registryExpected} $out/bench/sites/apps.txt

    ${lib.optionalString (builtins.pathExists (workspaceRoot + "/config")) ''
      cp -r ${workspaceRoot + "/config"}/* $out/bench/config/ 2>/dev/null || true
      chmod -R u+w $out/bench/config
    ''}
  '');

  # Production-ready bench with compiled assets. Runs `bench build` (frappe's
  # esbuild pipeline) inside the Nix sandbox, producing sites/assets/ with
  # hashed bundles. No network access required — node_modules are pre-built.
  builtBench = pkgs.stdenv.mkDerivation {
    name = "built-bench";

    dontUnpack = true;
    dontConfigure = true;

    # yarn stays: `bench build` is `yarn run production` in apps/frappe, and
    # every app with a build script is built with `yarn build`. It only runs
    # scripts against the node_modules already linked in; nothing is installed.
    nativeBuildInputs = [
      prodPythonEnv
      nodejs
      pkgs.yarn
      pkgs.git
    ];

    buildPhase = ''
      runHook preBuild

      # Start from the unbuilt benchRoot — copy so we can write into it.
      cp -a ${benchRoot}/bench $TMPDIR/bench
      chmod -R u+w $TMPDIR/bench

      # bench build writes into sites/assets and apps/*/public/dist.
      mkdir -p $TMPDIR/bench/sites/assets
      mkdir -p $TMPDIR/bench/config/pids

      export FRAPPE_BENCH_ROOT=$TMPDIR/bench
      export SITES_PATH=$TMPDIR/bench/sites
      export PYTHONPATH=${appsPath "$TMPDIR/bench"}
      export NODE_OPTIONS="--max-old-space-size=4096"
      export HOME=$TMPDIR/home
      mkdir -p $HOME

      # A classic bench always has sites/common_site_config.json, and some
      # vite configs read it unconditionally on load (erpnext/banking's
      # proxyOptions.ts, for the dev-server proxy port) — a missing file is an
      # ENOENT in the middle of `yarn build`. Build-time only: installPhase
      # takes sites/ from benchRoot, and the real file is the operator's.
      [ -f $TMPDIR/bench/sites/common_site_config.json ] \
        || echo '{}' > $TMPDIR/bench/sites/common_site_config.json

      cd $TMPDIR/bench
      ${prodPythonEnv}/bin/bench build --production 2>&1

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      # Start from benchRoot (preserves the store symlink for env), then take
      # apps/ as the build left it. The whole tree, not just esbuild's
      # apps/<app>/<app>/public/dist: a nested frontend's `vite build` writes
      # wherever its config says (erpnext/public/banking, hrms/public/frontend)
      # and its `copy-html-entry` writes the page under <app>/www/ — cherry-
      # picking dist/ shipped a package whose frontends had built and were
      # missing. node_modules travel as they are: directories of links into
      # the store (benchRoot), which cp -a preserves.
      mkdir -p $out/bench
      for entry in ${benchRoot}/bench/* ${benchRoot}/bench/.[!.]*; do
        { [ -e "$entry" ] || [ -L "$entry" ]; } || continue
        [ "''${entry##*/}" = apps ] && continue
        cp -a "$entry" $out/bench/
      done
      cp -a $TMPDIR/bench/apps $out/bench/apps
      chmod -R u+w $out/bench/sites

      # `bench build` links each app's <app>/public/node_modules at
      # apps/<app>/node_modules by absolute path — the build tree's. Point
      # those at $out, as the sites/assets links are below.
      find $out/bench/apps -type l | while IFS= read -r link; do
        target=$(readlink "$link")
        case "$target" in
          "$TMPDIR/bench"/*|/build/bench/*)
            newtarget=$(echo "$target" | sed "s|$TMPDIR/bench|$out/bench|; s|^/build/bench|$out/bench|")
            ln -sfn "$newtarget" "$link"
            ;;
        esac
      done

      # bench build creates sites/assets/ with symlinks to each app's public dir
      # and compiled files (locale .mo files, etc.). The symlinks point into the
      # build tree ($TMPDIR) which won't exist in the store. Replace them with
      # links to $out and copy any real files.
      rm -rf $out/bench/sites/assets
      mkdir -p $out/bench/sites/assets
      for item in $TMPDIR/bench/sites/assets/*; do
        name=$(basename "$item")
        if [ -L "$item" ]; then
          # Rewrite symlink: /build/bench/apps/foo/... → $out/bench/apps/foo/...
          target=$(readlink "$item")
          newtarget=$(echo "$target" | sed "s|$TMPDIR/bench|$out/bench|g; s|/build/bench|$out/bench|g")
          ln -s "$newtarget" "$out/bench/sites/assets/$name"
        elif [ -d "$item" ]; then
          cp -a "$item" "$out/bench/sites/assets/$name"
        else
          cp -a "$item" "$out/bench/sites/assets/$name"
        fi
      done

      runHook postInstall
    '';

    passthru = {
      pythonEnv = prodPythonEnv;
      inherit nodejs extraPackages;
      appNames = names;
      # The contents of sites/apps.txt, in order: the workspace members that
      # are Frappe apps. A subset of appNames, which is every apps/ directory.
      inherit registeredApps;
      # Every app and nested frontend that has (or should have) a node lock.
      nodeTargets = nodeTargetKeys;
      # Function: root -> colon-separated PYTHONPATH of apps under root.
      # Usage: pkg.passthru.appsPath "${pkg}/bench"
      inherit appsPath;
    };
  };

in
{
  appNames = names;
  nodeTargets = nodeTargetKeys;
  inherit
    registeredApps
    appsWithNode
    appsPath
    nodeModules
    benchRoot
    builtBench
    ;
}
