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
}:

let
  appNames = builtins.attrNames (builtins.readDir (workspaceRoot + "/apps"));

  appsPath = root: lib.concatMapStringsSep ":" (app: "${root}/apps/${app}") appNames;

  appsWithNode = lib.filter (
    app:
    builtins.pathExists (workspaceRoot + "/apps/${app}/package.json")
    && builtins.pathExists (workspaceRoot + "/apps/${app}/yarn.lock")
  ) appNames;

  nodeModulesForApp =
    app:
    let
      appOverrides = nodeOverrides.${app} or { };
      pkg = pkgs.mkYarnPackage (
        {
          name = app;
          src = workspaceRoot + "/apps/${app}";
          inherit nodejs;
          version =
            let
              hooksPath = workspaceRoot + "/apps/${app}/hooks.py";
              initPath = workspaceRoot + "/apps/${app}/${app}/__init__.py";
              hooksContent =
                if builtins.pathExists hooksPath then builtins.readFile hooksPath else "";
              initContent =
                if builtins.pathExists initPath then builtins.readFile initPath else "";
              appVersionMatch = builtins.match ".*app_version[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"].*" hooksContent;
              versionMatch = builtins.match ".*__version__[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"].*" initContent;
            in
            if appVersionMatch != null then
              builtins.elemAt appVersionMatch 0
            else if versionMatch != null then
              builtins.elemAt versionMatch 0
            else
              "0.1.0";
          yarn = pkgs.yarn;
        }
        // appOverrides
      );
    in
    pkgs.runCommand "${app}-node-modules" { } ''
      mkdir -p $out
      ln -s ${pkg}/libexec/${app}/node_modules $out/node_modules
    '';

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
