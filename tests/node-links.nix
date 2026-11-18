# Checks for `link:` dependencies and the deeper-target rule in lib/bench.nix's
# yarn path (lib/node-links.nix, lib/node-targets.nix) on
# tests/fixtures/node-links, which is shaped like Frappe's island frontends:
# alpha/ui is a package other frontends link to and carries the patches their
# postinstall applies; alpha/panel links it twice (scoped and not);
# alpha/deep/app links it from two levels down, has its own lock (a target)
# and wants another version of the linked package's dependency;
# alpha/deep/nolock has no lock (not a target); beta/islands links it across
# apps, from a lock older than the linked package's newest dependency. Each node_modules is built for real, offline, from the one
# registry tarball the locks name, and linked into benchRoot — where the test
# then runs what Frappe's build runs: `yarn check --integrity` and
# `yarn install --frozen-lockfile`, with no network.
# builtBench is not built (the fixture has no Frappe to `bench build`): its
# build script and the real-directory entries in effect are asserted as data.
{
  pkgs,
  checks,
  base,
}:

let
  inherit (pkgs) lib;

  bench =
    apps: extra:
    import ../lib/bench.nix (
      {
        inherit pkgs lib base;
        prodPythonEnv = pkgs.emptyDirectory;
        inherit (pkgs) nodejs;
        workspaceRoot = ./fixtures/node-links;
        rootPyproject = {
          tool.uv.workspace.members = [ ];
        };
        appNames = apps;
      }
      // extra
    );

  both =
    bench
      [
        "alpha"
        "beta"
      ]
      {
        nodeRealEntries = {
          "alpha/deep/app" = [ "is-number" ];
        };
      };

  # The same apps with every frontend that links left out: no target has a
  # link: dependency.
  linkFree =
    bench
      [
        "alpha"
        "beta"
      ]
      {
        nodeNestedFrontendExcludes = [
          "alpha/panel"
          "alpha/deep/app"
          "beta/islands"
        ];
      };

  # builtBench's build script, read and not built: it runs `bench build`,
  # which the fixture has no Frappe for.
  buildScript = b: builtins.unsafeDiscardStringContext b.builtBench.buildPhase;
  has = needle: b: if lib.hasInfix needle (buildScript b) then "yes" else "no";
  ignoreScripts = "export YARN_IGNORE_SCRIPTS=true";

  links = import ../lib/node-links.nix { inherit lib; };
  linksOf =
    key: src:
    links.linksOf {
      t = {
        inherit key src;
      };
      appNames = [
        "alpha"
        "beta"
      ];
    };
  show = ls: lib.concatMapStringsSep " " (l: "${l.name}:${l.dest}:${l.treeTarget}") ls;

  errorOf =
    drv:
    let
      r = builtins.tryEval drv.drvPath;
    in
    if r.success then "no error" else "error";

  # The link's destination app is not in the bench.
  alone = errorOf (bench [ "beta" ] { }).nodeModules."beta/islands";
  escaping = errorOf (
    pkgs.writeText "x" (
      show (
        # A file in the tree, not one written at evaluation: `nix flake check
        # --no-build` cannot realise a derivation to read it.
        linksOf "alpha/panel" ./fixtures/node-links-escaping
      )
    )
  );

  restore = ../lib/restore-yarn-integrity.js;
  sh = lib.escapeShellArg;
in
{
  node-links =
    checks.script "node-links"
      {
        tools = [
          pkgs.findutils
          pkgs.gnugrep
          pkgs.nodejs
          pkgs.yarn
          pkgs.jq
        ];
      }
      ''
        fails=0
        ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
        no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
        eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }

        echo "── evaluation ─────────────────────────────────────────────────"
        eq "a directory two levels down is a target when it ships a lock, and only then" \
          "alpha alpha/panel alpha/ui alpha/deep/app beta beta/islands" \
          ${sh (lib.concatStringsSep " " both.nodeTargets)}
        eq "a link in the same app, scoped and not" \
          "@fixture/ui:alpha/ui:../../../ui plain-ui:alpha/ui:../../ui" \
          ${sh (show (linksOf "alpha/panel" ./fixtures/node-links/apps/alpha/panel))}
        eq "a link from two levels down" \
          "@fixture/ui:alpha/ui:../../../../ui" \
          ${sh (show (linksOf "alpha/deep/app" ./fixtures/node-links/apps/alpha/deep/app))}
        eq "a link into another app" \
          "@fixture/ui:alpha/ui:../../../../alpha/ui" \
          ${sh (show (linksOf "beta/islands" ./fixtures/node-links/apps/beta/islands))}
        eq "a link into an app the bench does not have is refused" "error" ${sh alone}
        eq "a link out of apps/ is refused" "error" ${sh escaping}
        eq "a target without links is built from flat manifests, as before" "no" \
          ${sh (if both.nodeModules."alpha/ui" ? linkDeps then "yes" else "no")}

        echo "── builtBench's build script ─────────────────────────────────"
        eq "the link-free bench has no target with a link" "alpha alpha/ui beta" \
          ${sh (lib.concatStringsSep " " linkFree.nodeTargets)}
        eq "a bench with a link: target builds with YARN_IGNORE_SCRIPTS, a link-free one without" "yes no" \
          ${sh "${has ignoreScripts both} ${has ignoreScripts linkFree}"}
        eq "the build's common_site_config.json stub carries socketio_port, with or without links" "yes yes" \
          ${sh "${has ''echo '{"socketio_port": 9000}' > $TMPDIR/bench/sites/common_site_config.json'' both} ${has ''echo '{"socketio_port": 9000}' > $TMPDIR/bench/sites/common_site_config.json'' linkFree}"}
        eq "the built-in real entries are in effect, with the caller's merged over them" \
          '{"alpha/deep/app":["is-number"],"frappe/studio/frontend":["frappe-ui"]}' \
          ${sh (builtins.toJSON both.nodeRealEntries)}
        eq "…and a caller's entry for the same target replaces the built-in one" '["other"]' \
          ${sh (
            builtins.toJSON
              (bench [ "alpha" ] { nodeRealEntries."frappe/studio/frontend" = [ "other" ]; })
              .nodeRealEntries."frappe/studio/frontend"
          )}

        echo "── node_modules, offline ─────────────────────────────────────"
        panel=${both.nodeModules."alpha/panel"}/node_modules
        islands=${both.nodeModules."beta/islands"}/node_modules
        eq "the store's copy holds no link, dangling or otherwise" "" \
          "$(find "$panel" "$islands" -xtype l; ls -d "$panel"/@fixture "$panel"/plain-ui 2>/dev/null)"
        eq "the linked package's dependency is installed in the target" "yes" \
          "$([ -e "$panel/is-number/package.json" ] && echo yes)"
        eq "…but not one the target's lock has no entry for: that stays with the linked package" "yes no" \
          "$([ -e "$panel/is-odd" ] && echo yes) $([ -e "$islands/is-odd" ] && echo yes || echo no)"
        eq "where target and linked package want different versions, the target's is the one installed" "6.0.0" \
          "$(jq -r .version ${both.nodeModules."alpha/deep/app"}/node_modules/is-number/package.json)"
        eq "the patches the postinstall names are applied" "2" \
          "$(cat "$panel/is-number/index.js" "$islands/is-number/index.js" | grep -c patchedByFixture)"
        eq "a target with no such postinstall is not patched" "0" \
          "$(grep -c patchedByFixture ${both.nodeModules."alpha/deep/app"}/node_modules/is-number/index.js)"
        eq ".yarn-integrity names the lock as the app ships it" \
          "https://registry.yarnpkg.com/is-number/-/is-number-7.0.0.tgz#7535345b896734d5f80c4d06c50955527a14f12b" \
          "$(jq -r '.lockfileEntries["is-number@^7.0.0"]' "$panel/.yarn-integrity")"

        echo "── benchRoot ─────────────────────────────────────────────────"
        apps=${both.benchRoot}/bench/apps
        eq "the scope is a real directory" "yes" \
          "$([ -d "$apps/alpha/panel/node_modules/@fixture" ] && [ ! -L "$apps/alpha/panel/node_modules/@fixture" ] && echo yes)"
        eq "the links are yarn's own, relative" "../../../ui ../../ui ../../../../ui ../../../../alpha/ui" \
          "$(echo $(readlink "$apps/alpha/panel/node_modules/@fixture/ui" "$apps/alpha/panel/node_modules/plain-ui" \
            "$apps/alpha/deep/app/node_modules/@fixture/ui" "$apps/beta/islands/node_modules/@fixture/ui"))"
        resolve() { (cd "$1" && node -e 'console.log(require("fs").realpathSync(require.resolve(process.argv[1])))' "$2"); }
        eq "node resolves the link to the package in the tree" "$apps/alpha/ui/index.js" \
          "$(resolve "$apps/alpha/panel" @fixture/ui)"
        eq "…the unscoped one too" "$apps/alpha/ui/index.js" "$(resolve "$apps/alpha/panel" plain-ui)"
        eq "…and across apps" "$apps/alpha/ui/index.js" "$(resolve "$apps/beta/islands" @fixture/ui)"
        eq "the linked package works: its own dependency, patched in the target" "true" \
          "$(cd "$apps/alpha/panel" && node -p 'require("is-number").patchedByFixture')"
        eq "the linked package finds a dependency the target's lock left out in its own node_modules" \
          "$apps/alpha/ui/node_modules/is-odd/index.js" \
          "$(cd "$apps/beta/islands" && node -p 'require("fs").realpathSync(require("@fixture/ui").isOddPath).replace(/^\/nix\/store\/[^/]+-alpha-ui-node-modules/, process.argv[1])' "$apps/alpha/ui")"
        eq "a nodeRealEntries entry is a real directory of links" "yes" \
          "$(e=$apps/alpha/deep/app/node_modules/is-number; [ -d "$e" ] && [ ! -L "$e" ] && [ -L "$e/package.json" ] && echo yes)"
        eq "the other entries are links" "yes" \
          "$([ -L "$apps/alpha/panel/node_modules/is-number" ] && echo yes)"

        echo "── what Frappe's build asks yarn ─────────────────────────────"
        export HOME=$TMPDIR/home
        mkdir -p "$HOME"
        cp -a ${both.benchRoot}/bench "$TMPDIR/bench"
        chmod -R u+w "$TMPDIR/bench"
        unset NODE_ENV
        for t in alpha/panel alpha/deep/app beta/islands; do
          dir=$TMPDIR/bench/apps/$t
          eq "$t: yarn check --integrity passes" "0" \
            "$(YARN_IGNORE_SCRIPTS=true yarn check --integrity --silent --production=false --cwd "$dir" > /dev/null 2>&1 && echo 0 || echo 1)"
          # Not beta/islands: its lock lacks a dependency of the linked
          # package, and yarn installs for that wherever the lock came from.
          [ "$t" = beta/islands ] && continue
          eq "$t: yarn install --frozen-lockfile finds it installed" "yes" \
            "$(cd "$dir" && YARN_IGNORE_SCRIPTS=true yarn install --frozen-lockfile 2>&1 | grep -q 'Already up-to-date' && echo yes)"
          eq "$t: and leaves the link alone" "yes" "$([ -e "$dir/node_modules/@fixture/ui/package.json" ] && echo yes)"
        done
        eq "without the flag the install recorded, the check fails" "1" \
          "$(yarn check --integrity --silent --production=false --cwd "$TMPDIR/bench/apps/alpha/panel" > /dev/null 2>&1 && echo 0 || echo 1)"

        echo "── restore-yarn-integrity.js ─────────────────────────────────"
        work=$TMPDIR/restore
        mkdir -p "$work"
        printf '%s\n' '# yarn lockfile v1' "" '"@s/a@^1.0.0", "@s/a@^1.1.0":' '  version "1.1.0"' \
          '  resolved "https://example.test/a-1.1.0.tgz#abc"' "" '"l@link:../l":' '  version "0.0.0"' '  uid ""' "" \
          'b@^2.0.0:' '  version "2.0.0"' '  resolved "https://example.test/b-2.0.0.tgz#def"' > "$work/yarn.lock"
        echo '{"flags":["ignoreScripts"],"lockfileEntries":{"@s/a@^1.0.0":"a.tgz","@s/a@^1.1.0":"a.tgz","b@^2.0.0":"b.tgz","l@link:../l":""}}' > "$work/integrity"
        node ${restore} "$work/integrity" "$work/yarn.lock"
        eq "each entry gets its lock's resolved, a link none, and the flags stay" \
          '{"flags":["ignoreScripts"],"lockfileEntries":{"@s/a@^1.0.0":"https://example.test/a-1.1.0.tgz#abc","@s/a@^1.1.0":"https://example.test/a-1.1.0.tgz#abc","b@^2.0.0":"https://example.test/b-2.0.0.tgz#def","l@link:../l":""}}' \
          "$(jq -c . "$work/integrity")"
        echo '{"lockfileEntries":{"gone@^1.0.0":"x"}}' > "$work/integrity"
        eq "an entry the lock does not have is an error" "1" \
          "$(node ${restore} "$work/integrity" "$work/yarn.lock" > /dev/null 2>&1 && echo 0 || echo 1)"

        if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi
        echo "all node-links checks passed" | tee "$out"
      '';
}
