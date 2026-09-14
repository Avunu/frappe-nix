# The one discovery rule for "what needs a node lock": which directories under
# apps/ get their own node_modules in the bench package.
#
# A target is an app whose root has a package.json, plus each immediate
# subdirectory of it that has one too — a nested frontend (erpnext/banking,
# hrms/frontend, commit/dashboard), built by the parent app's own build script.
# One level deep on purpose: deeper package.json files are fixtures and
# DocType JSON (frappe/frappe/core/doctype/package/package.json is one), not
# projects.
#
# Not a target: node_modules itself; a subdirectory the app tracks as a git
# submodule (hrms/frappe-ui is a whole other project nothing in the bench
# builds); anything the consumer excludes by "app/subdir" key.
#
# Mirrored by lib/node-locks.nix's shell discovery — the dev-shell tool must
# find a new app before the shell has re-evaluated — and both are checked
# against the same fixture tree (tests/node-targets.nix, tests/node-locks.sh).
{ lib }:
{
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

        subdirs = builtins.attrNames (
          lib.filterAttrs (_: type: type == "directory") (builtins.readDir src)
        );

        nested = lib.concatMap (
          sub:
          let
            dir = src + "/${sub}";
            key = "${app}/${sub}";
          in
          lib.optional (
            sub != "node_modules"
            && builtins.pathExists (dir + "/package.json")
            && !(lib.elem sub submodulePaths)
            && !(lib.elem key excludes)
          ) {
            inherit key app;
            subdir = sub;
            src = dir;
          }
        ) subdirs;
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
      )
    ) names;
}
