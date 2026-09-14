# Checks for lib/yarn-lock.nix — the yarn.lock parser and the offline mirror
# it plans. Pure evaluation over tests/fixtures/yarn-lock, plus one built
# mirror whose only member is a git dependency served by a stub fetchGit, so
# nothing here touches the network: a registry tarball is checked by the
# fetchurl it *would* run (URL, hash, mirror name), not by running it.
{ pkgs }:

let
  inherit (pkgs) lib;

  yl = import ../lib/yarn-lock.nix { inherit lib; };
  fixtures = ./fixtures/yarn-lock;

  simple = yl.plan (fixtures + "/simple.lock");
  byName = lib.listToAttrs (map (i: lib.nameValuePair i.name i) simple);
  names = lib.concatStringsSep " " (map (i: i.name) simple);

  # The mirror for simple.lock, planned but never built: its entries carry the
  # derivations that would fetch each tarball.
  mirror = yl.mkOfflineMirror {
    inherit pkgs;
    lockFile = fixtures + "/simple.lock";
    name = "simple";
    fetchGit = _: fixtures + "/checkout";
  };
  entry =
    name: (lib.findFirst (e: e.name == name) (throw "no mirror entry ${name}") mirror.entries).path;

  # A mirror that can be built offline: one git dependency, "cloned" from a
  # fixture directory.
  gitMirror = yl.mkOfflineMirror {
    inherit pkgs;
    lockFile = fixtures + "/git-only.lock";
    name = "git-only";
    fetchGit =
      args:
      assert args.rev == "ed37b94d95c68d8544357e330be0c89d044a3eea";
      fixtures + "/checkout";
  };

  # nixpkgs' own answers (fetch-yarn-deps/common.js, urlToName) for the URLs
  # below, so the port is pinned to the file fixup-yarn-lock uses.
  nameTable = [
    [
      "https://registry.yarnpkg.com/left-pad/-/left-pad-1.3.0.tgz"
      "left_pad___left_pad_1.3.0.tgz"
    ]
    [
      "https://registry.yarnpkg.com/@babel/code-frame/-/code-frame-7.22.13.tgz"
      "_babel_code_frame___code_frame_7.22.13.tgz"
    ]
    [
      "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz"
      "https___registry.npmjs.org_left_pad___left_pad_1.3.0.tgz"
    ]
    [
      "https://registry.npmjs.org/@types/node/-/node-20.10.5.tgz"
      "https___registry.npmjs.org__types_node___node_20.10.5.tgz"
    ]
    [
      "https://npm.example.com/@scope/pkg/-/pkg-1.0.0-beta.1.tgz"
      "_scope_pkg___pkg_1.0.0_beta.1.tgz"
    ]
    [
      "git+https://github.com/frappe/air-datepicker"
      "air-datepicker"
    ]
    [
      "git+https://github.com/example/repo.git"
      "repo.git"
    ]
    [
      "https://codeload.github.com/caolan/async/tar.gz/fc9ba651341af5ab974aade6b1640e345912be83"
      "fc9ba651341af5ab974aade6b1640e345912be83"
    ]
    [
      "https://github.com/caolan/async.git"
      "caolan_async.git"
    ]
    [
      "https://github.com/owner/repo/archive/refs/tags/v0.220.1.tar.gz"
      "owner_repo_archive_refs_tags_v0.220.1.tar.gz"
    ]
    [
      "file:../local"
      "file:../local"
    ]
  ];
  nameMismatches = lib.concatMapStringsSep "; " (
    p:
    "${builtins.elemAt p 0} → ${yl.urlToName (builtins.elemAt p 0)}, nixpkgs says ${builtins.elemAt p 1}"
  ) (lib.filter (p: yl.urlToName (builtins.elemAt p 0) != builtins.elemAt p 1) nameTable);

  # A lock that cannot be planned is an evaluation error naming the entry.
  failure =
    file:
    let
      r = builtins.tryEval (builtins.deepSeq (yl.plan (fixtures + "/${file}")) "no error");
    in
    if r.success then r.value else "error";

  sh = lib.escapeShellArg;
in
{
  yarn-lock = pkgs.runCommand "frappe-nix-yarn-lock-check" { } ''
    fails=0
    ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }

    echo "── parsing ───────────────────────────────────────────────────"
    eq "every entry is read, with or without a resolved URL" "9" ${
      sh (toString (builtins.length (yl.parse (fixtures + "/simple.lock"))))
    }
    eq "an entry with no resolved URL (file:) is not fetched, and two specs on one tarball are one fetch" \
      "_babel_code_frame___code_frame_7.22.13.tgz air-datepicker fc9ba651341af5ab974aade6b1640e345912be83 https___registry.npmjs.org_old_style___old_style_1.0.0.tgz left_pad___left_pad_1.3.0.tgz over-ssh.git string_width___string_width_4.2.3.tgz" \
      ${sh names}

    echo "── mirror names match nixpkgs' urlToName ──────────────────────"
    eq "for every URL in the table" "" ${sh nameMismatches}

    echo "── what each entry fetches ────────────────────────────────────"
    eq "a registry tarball: its URL" "https://registry.yarnpkg.com/left-pad/-/left-pad-1.3.0.tgz" \
      ${sh (builtins.head (entry "left_pad___left_pad_1.3.0.tgz").urls)}
    eq "…by the lock's integrity" "sha512-XI5MPzVNApjAyhQzphX8BZTKfMYWU6BbWw4ijh6snfe7bF9RBv0X2qvNYEwSqLgJbW0OwcGL2vP8P3nXgg2rag==" \
      ${sh (entry "left_pad___left_pad_1.3.0.tgz").outputHash}
    eq "…under the mirror name" "left_pad___left_pad_1.3.0.tgz" ${sh (entry "left_pad___left_pad_1.3.0.tgz").name}
    eq "an old entry without integrity: by the #sha1 on its URL" "0123456789abcdef0123456789abcdef01234567" \
      ${sh (entry "https___registry.npmjs.org_old_style___old_style_1.0.0.tgz").outputHash}
    eq "…as sha1" "sha1" ${sh (entry "https___registry.npmjs.org_old_style___old_style_1.0.0.tgz").outputHashAlgo}
    eq "a git+https dependency: the repository and the commit" "https://github.com/example/air-datepicker ed37b94d95c68d8544357e330be0c89d044a3eea" \
      ${sh "${byName.air-datepicker.git.url} ${byName.air-datepicker.git.rev}"}
    eq "a github: shorthand (codeload tarball): cloned by its commit" "https://github.com/caolan/async.git fc9ba651341af5ab974aade6b1640e345912be83" \
      ${sh "${byName."fc9ba651341af5ab974aade6b1640e345912be83".git.url} ${
        byName."fc9ba651341af5ab974aade6b1640e345912be83".git.rev
      }"}
    eq "a git+ssh GitHub dependency is fetched over https" "https://github.com/example/over-ssh.git" \
      ${sh byName."over-ssh.git".git.url}
    eq "a git entry is packed, not downloaded" "1" \
      ${sh (
        toString (if lib.hasInfix "tar --owner=0" (entry "air-datepicker").buildCommand then 1 else 0)
      )}
    eq "the lock itself is in the mirror" "yarn.lock" \
      ${sh (
        lib.concatStringsSep " " (map (e: e.name) (lib.filter (e: e.name == "yarn.lock") mirror.entries))
      )}

    echo "── a built mirror ────────────────────────────────────────────"
    m=${gitMirror}
    if [ -f "$m/air-datepicker" ] || [ -L "$m/air-datepicker" ]; then ok "the git dependency is there under its name"; else no "the git dependency is there under its name"; fi
    eq "…as a tar of the checkout" "./index.js ./package.json" \
      "$(tar -tf "$m/air-datepicker" | grep -v '^\./$' | sort | xargs)"
    if [ -e "$m/yarn.lock" ]; then ok "with the lock beside it"; else no "with the lock beside it"; fi

    echo "── what cannot be fetched is an evaluation error ─────────────"
    eq "a tarball with neither integrity nor #hash" "error" ${sh (failure "no-hash.lock")}
    eq "a git dependency without a commit" "error" ${sh (failure "unpinned-git.lock")}
    eq "two URLs that would share a mirror name" "error" ${sh (failure "collision.lock")}
    eq "a yarn berry lock" "error" ${sh (failure "berry.lock")}
    eq "and a good lock is not" "no error" ${sh (failure "simple.lock")}

    if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi
    echo "all yarn-lock checks passed" | tee "$out"
  '';
}
