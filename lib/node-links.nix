# `link:` dependencies of a node target: a package.json entry such as
# "@scope/ui": "link:../ui", which yarn installs as a symlink to a directory
# of the source tree rather than as a package from the lock.
#
# The offline install (lib/bench.nix, nodeModulesFor) sees only manifests, so
# such a link has nothing to point at in the store. What is read here tells
# the install which other package.json files it needs beside the target's
# (yarn installs the linked package's dependencies into the host), and tells
# benchRoot where each link goes in the bench tree, where its destination is.
#
# `file:` dependencies are not handled: yarn copies those, and a copy is
# source the manifests-only install does not have.
{ lib }:
let
  # "a/b/../c/./d" -> [ "a" "c" "d" ]; null when it climbs above its start.
  fold =
    path:
    builtins.foldl' (
      acc: part:
      if acc == null then
        null
      else if part == "" || part == "." then
        acc
      else if part == ".." then
        (if acc == [ ] then null else lib.init acc)
      else
        acc ++ [ part ]
    ) [ ] (lib.splitString "/" path);
in
rec {
  # t:        a node target ({ key; app; subdir; src; }, lib/node-targets.nix)
  # appNames: the apps of the bench
  #
  # One record per link: dependency, by name.
  linksOf =
    { t, appNames }:
    let
      manifest = lib.importJSON (t.src + "/package.json");
      deps =
        (manifest.dependencies or { })
        // (manifest.devDependencies or { })
        // (manifest.optionalDependencies or { });
      linked = lib.filterAttrs (_: spec: builtins.isString spec && lib.hasPrefix "link:" spec) deps;
    in
    lib.mapAttrsToList (
      name: spec:
      let
        rel = lib.removePrefix "link:" spec;
        parts = fold "${t.key}/${rel}";
        scoped = lib.hasPrefix "@" name;
        outside = "frappe-nix: apps/${t.key}/package.json: \"${name}\": \"${spec}\" points outside apps/ — a link: dependency has to be a directory of an app in the bench";
      in
      if lib.hasPrefix "/" rel || parts == null || parts == [ ] then
        throw outside
      else if !(lib.elem (builtins.head parts) appNames) then
        throw "frappe-nix: apps/${t.key}/package.json: \"${name}\": \"${spec}\" links into apps/${builtins.head parts}, which is not in this bench — ${t.key} needs the app ${builtins.head parts}"
      else
        {
          inherit name rel;
          scope = if scoped then builtins.head (lib.splitString "/" name) else null;
          # Relative to the bench's apps/ directory.
          dest = lib.concatStringsSep "/" parts;
          destApp = builtins.head parts;
          destSub = lib.concatStringsSep "/" (builtins.tail parts);
          # The link's own text in node_modules, as yarn writes it: relative
          # to the directory the link sits in.
          treeTarget = (if scoped then "../../" else "../") + lib.removePrefix "./" rel;
        }
    ) linked;

  # What the install is shown of a link's destination: its package.json cut
  # down to what yarn reads through a link — the name, and the dependencies it
  # installs into the linking target — and of those only the ones the
  # target's lock has an entry for. yarn takes a linked package's dependencies
  # as the target's own, and offline a single one the lock lacks ("Couldn't
  # find any versions for …") fails the install. Upstream locks do lack them:
  # a lock is written when its target is installed, and the linked package
  # gains dependencies afterwards. Those stay with the linked package, which
  # resolves them from its own node_modules — it is a target too.
  #
  # Returns { manifest; dropped; }: the attrset to write, and the
  # "name@range" patterns left out.
  manifestFor =
    { lockFile, manifestFile }:
    let
      manifest = lib.importJSON manifestFile;
      # yarn.lock v1: an entry starts at column 0 with its patterns, quoted
      # when they need it, comma-separated, ending in a colon.
      patterns = lib.genAttrs (lib.concatMap (
        line:
        if line == "" || lib.hasPrefix " " line || lib.hasPrefix "#" line then
          [ ]
        else
          map (p: lib.removeSuffix "\"" (lib.removePrefix "\"" p)) (
            lib.splitString ", " (lib.removeSuffix ":" line)
          )
      ) (lib.splitString "\n" (builtins.readFile lockFile))) (_: true);
      sections = [
        "dependencies"
        "optionalDependencies"
      ];
      covered = name: range: patterns ? "${name}@${range}";
    in
    {
      manifest =
        lib.filterAttrs (n: _: n == "name" || n == "version") manifest
        // lib.genAttrs (lib.filter (s: manifest ? ${s}) sections) (
          s: lib.filterAttrs covered manifest.${s}
        );
      dropped = lib.concatMap (
        s:
        lib.mapAttrsToList (n: r: "${n}@${r}") (
          lib.filterAttrs (n: r: !(covered n r)) (manifest.${s} or { })
        )
      ) sections;
    };

  # The patch-package directory a target's postinstall names *through a link*
  # (`patch-package --patch-dir node_modules/<link>/<dir>`), as
  # { link; dir; }, or null. Lifecycle scripts do not run in the offline
  # install, and this one could not: its patches live in the linked package,
  # which is not in the store. So the install applies them itself.
  patchDirOf =
    t: links:
    let
      manifest = lib.importJSON (t.src + "/package.json");
      postinstall = manifest.scripts.postinstall or "";
      m = builtins.match ".*patch-package[[:space:]]+--patch-dir[[:space:]]+node_modules/([^[:space:];&|]+).*" postinstall;
      path = if m == null then null else builtins.head m;
      link = if path == null then null else lib.findFirst (l: lib.hasPrefix "${l.name}/" path) null links;
    in
    if link == null then
      null
    else
      {
        inherit link;
        dir = lib.removePrefix "${link.name}/" path;
      };
}
