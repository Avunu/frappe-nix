# yarn.lock, read at evaluation time: the offline mirror `yarn install
# --offline` needs, assembled from one fetch per tarball.
#
# nixpkgs already installs from a yarn.lock without the network — yarnConfigHook
# points yarn at an offline mirror, rewrites each `resolved` to the mirror's
# filename (fixup-yarn-lock) and runs `yarn install --offline --frozen-lockfile`.
# What it expects the mirror to come from is fetchYarnDeps: one fixed-output
# derivation that downloads every tarball, whose hash nobody can know without
# running it. frappe-nix used to mine those hashes out of failing builds into
# node-offline-hashes.json, one per app, none for nested frontends.
#
# There is no need for that hash. A yarn.lock names every tarball by URL and
# carries its `integrity` (or, in older locks, a `#sha1` on the URL), which is
# exactly what a fetchurl takes. So this file parses the lock in Nix, makes one
# fetchurl per distinct tarball with the hash the lock states, and links them
# into a directory under the names fixup-yarn-lock will look for. The mirror is
# a plain derivation; only its members are fixed-output, and their hashes are
# upstream's. A git dependency, which has a commit but no hash, is fetched by
# that commit (builtins.fetchGit, at evaluation time — the one network access
# evaluation makes) and packed the way prefetch-yarn-deps packs it.
#
# The naming is the contract: `urlToName` is a port of nixpkgs'
# fetch-yarn-deps/common.js, and tests/yarn-lock.nix pins it to that file's
# answers. If nixpkgs ever changes the scheme, fixup-yarn-lock and this mirror
# disagree and every install fails at "Resolving packages" — loudly, at least.
{ lib }:

rec {
  # yarn.lock v1 → [ { key; version?; resolved?; integrity?; } ], one per entry.
  # Only the fields a fetch needs are kept; dependency blocks are yarn's business.
  # The format is line-oriented: an unindented `"a@^1", "a@^1.2":` line opens an
  # entry, two-space-indented `field value` lines fill it, and everything deeper
  # is a nested block. Values are quoted when yarn thinks they need it, which
  # `resolved` always is and `integrity` never.
  parse =
    path:
    let
      text = builtins.readFile path;
      lines = lib.splitString "\n" text;
      keyOf = line: builtins.match "([^ \t#].*):[ \t]*" line;
      fieldOf = line: builtins.match "  (resolved|integrity|version) \"?([^\"]*)\"?[ \t]*" line;
      step =
        acc: line:
        let
          k = keyOf line;
          f = fieldOf line;
        in
        if k != null then
          {
            cur = {
              key = builtins.head k;
            };
            done = acc.done ++ lib.optional (acc.cur != null) acc.cur;
          }
        else if f != null && acc.cur != null then
          acc
          // {
            cur = acc.cur // {
              ${builtins.head f} = builtins.elemAt f 1;
            };
          }
        else
          acc;
      r = builtins.foldl' step {
        cur = null;
        done = [ ];
      } lines;
    in
    # The header is in the first lines; not lib.hasInfix, which is a regex over
    # the whole file and overflows the stack on a real-sized lock.
    if !(lib.elem "# yarn lockfile v1" (lib.take 8 lines)) then
      throw "frappe-nix: ${toString path} is not a yarn v1 lockfile (a yarn berry lock is not something yarn 1 can install from)"
    else
      r.done ++ lib.optional (r.cur != null) r.cur;

  # fetch-yarn-deps/common.js, urlToName: the filename a tarball has in the
  # offline mirror. Registry URLs lose everything through the last `com/` and
  # have [@/%:-] turned into `_`; git and codeload URLs are their basename.
  urlToName =
    url:
    if lib.hasPrefix "file:" url then
      url
    else if lib.hasPrefix "git+" url || isCodeload url then
      baseNameOf url
    else
      let
        # The JS regex is greedy too: `https://(.)*(.com)/` matches through the
        # *last* `?com/`, and a URL with none is kept whole.
        m = builtins.match "https://(.*)com/(.*)" url;
      in
      lib.replaceStrings [ "@" "/" "%" ":" "-" ] [ "_" "_" "_" "_" "_" ] (
        if m == null then url else builtins.elemAt m 1
      );

  isCodeload = url: lib.hasPrefix "https://codeload.github.com/" url && lib.hasInfix "/tar.gz/" url;

  # fetch-yarn-deps/index.js, isGitUrl — over the URL with its `#fragment`
  # already removed, as the prefetcher tests it.
  isGitUrl =
    url:
    let
      hostPath = builtins.match "[a-z+]+://([^/]+)/(.*)" url;
      segments = lib.filter (s: s != "") (lib.splitString "/" (builtins.elemAt hostPath 1));
    in
    builtins.match "git:.*" url != null
    || builtins.match "git\\+.+:.*" url != null
    || builtins.match "ssh:.*" url != null
    || builtins.match "https?:.+\\.git" url != null
    || (
      hostPath != null
      && lib.elem (builtins.head hostPath) [
        "github.com"
        "gitlab.com"
        "bitbucket.com"
        "bitbucket.org"
      ]
      && builtins.length segments == 2
    );

  # What to fetch for a lock: one item per distinct tarball, each
  #   { name; url; key; kind = "tarball"; hash | sha1; }   a registry tarball
  #   { name; url; key; kind = "git"; git = { url; rev | ref; }; }   a git checkout to pack
  # Entries yarn resolves from the source tree (`file:`/`link:` — no `resolved`
  # at all) are not in it, as prefetch-yarn-deps ignores them too. An entry the
  # build could not fetch is an evaluation error naming it, not a build failure
  # a thousand derivations later.
  plan =
    lockFile:
    let
      entries = lib.filter (e: e ? resolved) (parse lockFile);
      where = e: "${toString lockFile}, entry ${e.key}";

      itemOf =
        e:
        let
          parts = lib.splitString "#" e.resolved;
          url = builtins.head parts;
          frag = if builtins.length parts > 1 then builtins.elemAt parts 1 else null;
          name = urlToName url;
          # codeload.github.com/o/r/tar.gz/<commit> and github.com/o/r/archive/<ref>.tar.gz
          # are git checkouts in tarball clothing; prefetch-yarn-deps clones them.
          codeload = builtins.match "https://codeload\\.github\\.com/([^/]+)/([^/]+)/tar\\.gz/(.+)" url;
          archive = builtins.match "https://github\\.com/([^/]+)/([^/]+)/archive/(.+)\\.tar\\.gz" url;
          isSha = s: builtins.match "[0-9a-f]{7,40}" s != null;
          gitRef =
            rev:
            if rev == null then
              throw "frappe-nix: ${where e}: a git dependency without a commit cannot be fetched reproducibly (${e.resolved})"
            else if isSha rev then
              { inherit rev; }
            else
              {
                ref = "refs/tags/${rev}";
                allRefs = true;
              };
          git =
            if codeload != null then
              {
                url = "https://github.com/${builtins.elemAt codeload 0}/${builtins.elemAt codeload 1}.git";
              }
              // gitRef (builtins.elemAt codeload 2)
            else if archive != null then
              {
                url = "https://github.com/${builtins.elemAt archive 0}/${builtins.elemAt archive 1}.git";
              }
              // gitRef (baseNameOf (builtins.elemAt archive 2))
            else if isGitUrl url then
              {
                # git+ssh://git@github.com/… would need a key in the sandbox; the
                # same repository over https needs nothing.
                url = lib.removePrefix "git+" (
                  lib.replaceStrings [ "git+ssh://git@github.com/" ] [ "git+https://github.com/" ] url
                );
              }
              // gitRef frag
            else
              null;
        in
        {
          inherit name url;
          inherit (e) key;
        }
        // (
          if git != null then
            {
              kind = "git";
              inherit git;
            }
          else if lib.hasPrefix "https://" url && e ? integrity then
            {
              kind = "tarball";
              hash = e.integrity;
            }
          else if lib.hasPrefix "https://" url && frag != null then
            {
              kind = "tarball";
              sha1 = frag;
            }
          else if lib.hasPrefix "https://" url then
            throw "frappe-nix: ${where e}: no integrity and no #hash on ${url}, so the build cannot fetch it"
          else
            throw "frappe-nix: ${where e}: don't know how to fetch ${e.resolved}"
        );

      # Two specs that resolve to one tarball (`string-width-cjs@npm:string-width`
      # next to `string-width`) are one mirror file. Keyed by the name, since
      # that is what has to be unique in the mirror; the same name from two
      # different URLs would be a collision yarn cannot tell apart.
      byName = builtins.foldl' (
        acc: item:
        if acc ? ${item.name} && acc.${item.name}.url != item.url then
          throw "frappe-nix: ${toString lockFile}: ${acc.${item.name}.url} and ${item.url} would both be ${item.name} in the offline mirror"
        else
          acc // { ${item.name} = item; }
      ) { } (map itemOf entries);
    in
    builtins.attrValues byName;

  # The offline mirror for one lock: a directory of the fetched tarballs under
  # their fixup-yarn-lock names, plus the lock itself (yarnConfigHook diffs it
  # against the source's, as it does for a fetchYarnDeps mirror).
  #
  # `fetchGit` is a parameter so tests can stand in for the network; the build
  # uses builtins.fetchGit, which pure evaluation accepts because every item
  # names a commit.
  mkOfflineMirror =
    {
      pkgs,
      lockFile,
      name,
      fetchGit ? builtins.fetchGit,
    }:
    let
      fetch =
        item:
        if item.kind == "git" then
          # prefetch-yarn-deps' tar invocation, so the archive is the same bytes
          # the fetchYarnDeps mirror would have held.
          pkgs.runCommand item.name { } ''
            tar --owner=0 --group=0 --numeric-owner --format=gnu --sort=name --mtime=@1 --mode u+w \
              -C ${fetchGit item.git} -cf $out .
          ''
        else
          pkgs.fetchurl (
            {
              inherit (item) url name;
            }
            // (if item ? hash then { inherit (item) hash; } else { inherit (item) sha1; })
          );
      entries =
        map (item: {
          inherit (item) name;
          path = fetch item;
        }) (plan lockFile)
        ++ [
          {
            name = "yarn.lock";
            path = lockFile;
          }
        ];
    in
    (pkgs.linkFarm "${name}-yarn-offline-mirror" entries).overrideAttrs (_: {
      # For tests and for anyone asking what a mirror holds without building it.
      passthru = { inherit entries; };
    });
}
