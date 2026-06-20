# Bench infrastructure: app discovery, node_modules, and benchRoot derivation.
#
# Builds the production /bench directory structure declaratively from source,
# lock files, and Nix-built environments.

{
  pkgs,
  lib,
  prodPythonEnv,
  workspaceRoot,
  nodejs,
  nodeOverrides ? { },
  nodeOfflineHashes ? { },
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

in
{
  inherit
    appNames
    appsWithNode
    appsPath
    nodeModules
    benchRoot
    ;
}
