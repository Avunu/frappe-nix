# Bench infrastructure: app discovery, node_modules, benchRoot, and builtBench.
#
# benchRoot  — unbuilt /bench tree for dev shells and as a build input.
# builtBench — benchRoot + compiled assets (`bench build`), the deployable
#              artifact. Exposes passthru.{pythonEnv,nodejs,appsPath,appNames}
#              so the NixOS module can discover interpreters from the package.

{
  pkgs,
  lib,
  prodPythonEnv,
  workspaceRoot,
  nodejs,
  nodeOverrides ? { },
  nodeOfflineHashes ? { },
  extraPackages ? [ ],
}:

let
  appNames = builtins.attrNames (builtins.readDir (workspaceRoot + "/apps"));

  appsPath = root: lib.concatMapStringsSep ":" (app: "${root}/apps/${app}") appNames;

  appsWithNode = lib.filter (
    app:
    builtins.pathExists (workspaceRoot + "/apps/${app}/package.json")
    && builtins.pathExists (workspaceRoot + "/apps/${app}/yarn.lock")
  ) appNames;

  # Per-app fetchYarnDeps offline-cache hashes. The committed
  # node-offline-hashes.json (kept current by `bench-update`) is the source of
  # truth; entries in the nodeOfflineHashes option override it.
  offlineHashesFile = workspaceRoot + "/node-offline-hashes.json";
  fileOfflineHashes =
    if builtins.pathExists offlineHashesFile then
      builtins.fromJSON (builtins.readFile offlineHashesFile)
    else
      { };
  offlineHashes = fileOfflineHashes // nodeOfflineHashes;

  # Build immutable node_modules from yarn.lock using the yarn-v1 hooks
  # (yarn2nix / mkYarnPackage was removed from nixpkgs). fetchYarnDeps builds an
  # offline mirror — its hash depends on the app's yarn.lock. Hashes live in
  # node-offline-hashes.json; run `bench-update --node-hashes` to (re)generate
  # them. A missing hash falls back to lib.fakeHash so the build fails with the
  # `got: sha256-…` value to record.
  #
  # postinstall scripts are skipped (the hook passes --ignore-scripts): apps like
  # hrms run nested `yarn install` in frontend/ subdirs needing network access
  # not available in the sandbox.
  nodeModulesForApp =
    app:
    let
      appOverrides = nodeOverrides.${app} or { };
      appSrc = workspaceRoot + "/apps/${app}";
      offlineCache = pkgs.fetchYarnDeps {
        yarnLock = appSrc + "/yarn.lock";
        hash = offlineHashes.${app} or lib.fakeHash;
      };
    in
    pkgs.stdenv.mkDerivation (
      {
        name = "${app}-node-modules";
        src = appSrc;
        nativeBuildInputs = [
          pkgs.yarnConfigHook
          pkgs.yarn
          nodejs
        ];
        yarnOfflineCache = offlineCache;
        dontBuild = true;
        installPhase = ''
          runHook preInstall
          mkdir -p $out
          cp -R node_modules $out/node_modules
          runHook postInstall
        '';
      }
      // appOverrides
    );

  nodeModules = lib.genAttrs appsWithNode nodeModulesForApp;

  # Discover nested frontends: subdirs of apps that have their own yarn.lock
  # (e.g. commit/dashboard, commit/docs). These need separate offline caches
  # because their postinstall-driven `yarn install` is skipped in the sandbox.
  # Hash keys in node-offline-hashes.json use "app/subdir" format.
  nestedFrontends = lib.concatMap (app:
    let
      appDir = workspaceRoot + "/apps/${app}";
      subdirs = builtins.attrNames (
        lib.filterAttrs (_: type: type == "directory")
          (builtins.readDir appDir)
      );
    in
    lib.concatMap (sub:
      let subDir = appDir + "/${sub}"; in
      if builtins.pathExists (subDir + "/yarn.lock")
         && builtins.pathExists (subDir + "/package.json")
         && sub != "node_modules"
      then [{
        app = app;
        subdir = sub;
        path = subDir;
        hashKey = "${app}/${sub}";
      }]
      else []
    ) subdirs
  ) appsWithNode;

  nestedOfflineCaches = lib.listToAttrs (map (nf:
    lib.nameValuePair nf.hashKey (pkgs.fetchYarnDeps {
      yarnLock = nf.path + "/yarn.lock";
      hash = offlineHashes.${nf.hashKey} or lib.fakeHash;
    })
  ) nestedFrontends);

  benchRoot = pkgs.runCommand "bench-root" { } ''
    mkdir -p $out/bench/{sites,logs,config/pids}

    ln -s ${prodPythonEnv} $out/bench/env

    mkdir -p $out/bench/apps
    ${lib.concatStringsSep "\n" (
      map (app: ''
        cp -r ${workspaceRoot + "/apps/${app}"} $out/bench/apps/${app}
        chmod -R u+w $out/bench/apps/${app}
        ${lib.optionalString (builtins.elem app appsWithNode) ''
          rm -rf $out/bench/apps/${app}/node_modules
          ln -s ${nodeModules.${app}}/node_modules $out/bench/apps/${app}/node_modules
        ''}
      '') appNames
    )}

    ${lib.optionalString (builtins.pathExists (workspaceRoot + "/sites/apps.json")) ''
      cp ${workspaceRoot + "/sites/apps.json"} $out/bench/sites/apps.json
    ''}
    ${lib.optionalString (builtins.pathExists (workspaceRoot + "/sites/apps.txt")) ''
      cp ${workspaceRoot + "/sites/apps.txt"} $out/bench/sites/apps.txt
    ''}

    ${lib.optionalString (builtins.pathExists (workspaceRoot + "/config")) ''
      cp -r ${workspaceRoot + "/config"}/* $out/bench/config/ 2>/dev/null || true
      chmod -R u+w $out/bench/config
    ''}
  '';

  # Production-ready bench with compiled assets. Runs `bench build` (frappe's
  # esbuild pipeline) inside the Nix sandbox, producing sites/assets/ with
  # hashed bundles. No network access required — node_modules are pre-built.
  builtBench = pkgs.stdenv.mkDerivation {
    name = "built-bench";

    dontUnpack = true;
    dontConfigure = true;

    nativeBuildInputs = [
      prodPythonEnv
      nodejs
      pkgs.yarn
      pkgs.fixup-yarn-lock
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

      # Install nested frontends (e.g. commit/dashboard, commit/docs) that have
      # their own yarn.lock. The top-level Nix node_modules build skips postinstall,
      # so these never get installed. Replicate yarnConfigHook's approach: set the
      # offline mirror, fixup the lockfile, then install offline.
      ${lib.concatStringsSep "\n" (
        map (nf: ''
          _subdir=$TMPDIR/bench/apps/${nf.app}/${nf.subdir}
          echo "Installing nested frontend: ${nf.hashKey}"
          (
            cd "$_subdir"
            yarn config --offline set yarn-offline-mirror ${nestedOfflineCaches.${nf.hashKey}}
            fixup-yarn-lock yarn.lock
            yarn install \
              --frozen-lockfile \
              --force \
              --production=false \
              --ignore-engines \
              --ignore-platform \
              --ignore-scripts \
              --no-progress \
              --non-interactive \
              --offline
            patchShebangs node_modules
          )
        '') nestedFrontends
      )}

      cd $TMPDIR/bench
      ${prodPythonEnv}/bin/bench build --production 2>&1

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      # Start from benchRoot (preserves store symlinks for env, node_modules),
      # then layer the compiled assets on top.
      mkdir -p $out
      cp -a ${benchRoot}/* $out/
      chmod -R u+w $out/bench/sites

      # Copy per-app dist bundles written by esbuild. The dist files are at
      # apps/<app>/<app>/public/dist/ (esbuild writes through the sites/assets
      # symlinks which point to apps/<app>/<app>/public/).
      ${lib.concatStringsSep "\n" (
        map (app: ''
          if [ -d "$TMPDIR/bench/apps/${app}/${app}/public/dist" ]; then
            chmod -R u+w $out/bench/apps/${app}/${app}/public 2>/dev/null || true
            rm -rf $out/bench/apps/${app}/${app}/public/dist
            cp -a $TMPDIR/bench/apps/${app}/${app}/public/dist $out/bench/apps/${app}/${app}/public/dist
          fi
        '') appNames
      )}

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
      inherit nodejs appNames extraPackages;
      # Function: root -> colon-separated PYTHONPATH of apps under root.
      # Usage: pkg.passthru.appsPath "${pkg}/bench"
      inherit appsPath;
    };
  };

in
{
  inherit
    appNames
    appsWithNode
    appsPath
    nodeModules
    benchRoot
    builtBench
    ;
}
