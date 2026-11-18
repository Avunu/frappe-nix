# The one discovery rule for "what needs a node lock": which directories under
# apps/ get their own node_modules in the bench package.
#
# A target is an app whose root has a package.json, plus each immediate
# subdirectory of it that has one too — a nested frontend (erpnext/banking,
# hrms/frontend, commit/dashboard), built by the parent app's own build script.
# One level deep by default: deeper package.json files are fixtures and
# DocType JSON (frappe/frappe/core/doctype/package/package.json is one), not
# projects. A directory one level further down counts only when it ships a
# lockfile of its own beside its package.json (frappe/studio/frontend): a
# lock is what says "this is installed here", and Frappe's build installs
# and builds such a frontend where it stands.
#
# Not a target: node_modules itself; a subdirectory the app tracks as a git
# submodule (hrms/frappe-ui is a whole other project nothing in the bench
# builds); anything the consumer excludes by "app/subdir" key — including,
# in app mode, each of `frappe-nix.app.excludeNodeTargets` (appExcludes).
#
# Mirrored by frappe-nix-node-locks' discovery (lib/node-locks.nix,
# py/frappe_nix_tools/frappe_nix_tools/deps/node_locks.py) — the dev-shell tool must
# find a new app before the shell has re-evaluated — and both are checked
# against the same fixture tree (tests/node-targets.nix, tests/node-locks.sh).
{ lib }:
{
  # frappe-nix.app.excludeNodeTargets as "app/subdir" keys: top-level
  # directories of the app under development that are never node targets
  # (docs-site, while an opted-in app's docs-site module is on; spec §5.11).
  # The same keys reach the node-locks tool as --exclude flags, so both
  # discoveries leave them out.
  appExcludes = { app, dirs }: map (dir: "${app}/${dir}") dirs;

  # names:    ordered app directory names
  # appSrcOf: app -> path to that app's bytes
  # excludes: "app/subdir" keys to leave out
  discover =
    {
      names,
      appSrcOf,
      excludes ? [ ],
    }:
    lib.concatMap (
      app:
      let
        src = appSrcOf app;

        # `path = <subdir>` lines of the app's own .gitmodules.
        submodulePaths =
          if builtins.pathExists (src + "/.gitmodules") then
            lib.concatMap (
              line:
              let
                m = builtins.match "[[:space:]]*path[[:space:]]*=[[:space:]]*([^[:space:]]+)[[:space:]]*" line;
              in
              if m == null then [ ] else m
            ) (lib.splitString "\n" (builtins.readFile (src + "/.gitmodules")))
          else
            [ ];

        subdirs = dirsOf src;

        dirsOf =
          dir: builtins.attrNames (lib.filterAttrs (_: type: type == "directory") (builtins.readDir dir));

        # Neither a target nor searched for deeper ones.
        skipped =
          sub: sub == "node_modules" || lib.elem sub submodulePaths || lib.elem "${app}/${sub}" excludes;

        entry = sub: {
          key = "${app}/${sub}";
          inherit app;
          subdir = sub;
          src = src + "/${sub}";
        };

        nested = map entry (
          lib.filter (sub: !(skipped sub) && builtins.pathExists (src + "/${sub}/package.json")) subdirs
        );

        hasOwnLock =
          dir: builtins.pathExists (dir + "/yarn.lock") || builtins.pathExists (dir + "/pnpm-lock.yaml");

        deeper = lib.concatMap (
          sub:
          map entry (
            lib.filter (
              deep:
              !(skipped deep)
              && builtins.pathExists (src + "/${deep}/package.json")
              && hasOwnLock (src + "/${deep}")
            ) (map (d: "${sub}/${d}") (lib.filter (d: d != "node_modules") (dirsOf (src + "/${sub}"))))
          )
        ) (lib.filter (sub: !(skipped sub)) subdirs);
      in
      lib.optionals (builtins.pathExists (src + "/package.json")) (
        [
          {
            key = app;
            inherit app src;
            subdir = null;
          }
        ]
        ++ nested
        ++ deeper
      )
    ) names;
}
