# N2's checks (docs/app-standards/spec.md §7, "N2: assets"): Vite manifest
# registration (S30, §5.11) and the node-target exclusion behind the docs-site
# module. Network-free; the real benches (a stock `bench init` bench, and the
# spa-app fixture as a builtBench) are .github/workflows/selftest-assets.yml.
#
#   standards-preload-vite         tests/esbuild-preload.js against the
#                                  directory copy of lib/js: the preload's old
#                                  checks, and the registration after each
#                                  app's `yarn build` (always on, keep-going or
#                                  not; frappe's own keys untouched; idempotent;
#                                  the hard-link copy).
#   standards-vite-register-copy   lib/js/vite-register.cjs and the packaged
#                                  scripts/vite-register.mjs template hold the
#                                  same region between the markers, and so does
#                                  the spa-app fixture's rendered copy; that
#                                  copy is in oxfmt's output form (tabs and two
#                                  spaces, printWidth 80, 110 and 200) and
#                                  passes oxlint with the managed rules.
#   standards-node-targets-docs-site
#                                  a top-level docs-site/ with a package.json is
#                                  a node target by default (as on main) and
#                                  none with excludeNodeTargets = [ "docs-site" ],
#                                  in both the Nix discovery and the node-locks
#                                  tool; frappe-nix.app.excludeNodeTargets
#                                  defaults to [] in an app-mode flake.
#   standards-spa-app              the spa-app fixture: its committed managed
#                                  files are clean under `frappe-nix sync
#                                  --check` and compat (C8); its `build` script
#                                  on a stub bench maps spa.bundle.js and
#                                  spa.bundle.css to the hashed files, the
#                                  preload's pass after it changes no byte, a
#                                  real sites/assets/spa_app gets the bundles,
#                                  an assets.json that does not parse is a
#                                  warning (exit 0), a build that changes
#                                  directory is run in a subshell and still
#                                  registers, and outside a bench the script
#                                  skips.
{
  pkgs,
  lib,
  self,
  inputs,
  ...
}:

let
  inherit (pkgs.stdenv.hostPlatform) system;

  jsDir = ../../lib/js;
  frappeNix = (import ../../lib/standards/outputs.nix { inherit pkgs; }).tools.frappe-nix;
  spaApp = ../fixtures/spa-app;
  template = ../../py/frappe_nix_tools/frappe_nix_tools/data/templates/scripts/vite-register.mjs;

  # --- node targets ------------------------------------------------------------

  nodeTargets = import ../../lib/node-targets.nix { inherit lib; };
  docsApps = ./fixtures/node-targets-docs-site/apps;
  docsKeys =
    dirs:
    map (t: t.key) (
      nodeTargets.discover {
        names = [ "docs_app" ];
        appSrcOf = app: docsApps + "/${app}";
        excludes = nodeTargets.appExcludes {
          app = "docs_app";
          inherit dirs;
        };
      }
    );
  excludeFlags =
    dirs:
    lib.escapeShellArgs (
      map (k: "--exclude=${k}") (
        nodeTargets.appExcludes {
          app = "docs_app";
          inherit dirs;
        }
      )
    );
  nodeLocks = import ../../lib/node-locks.nix { inherit pkgs; };

  # The spa-app fixture's flake against this checkout, as
  # tests/standards/hookpoints.nix evaluates the standards fixture's: the
  # option's default as an app-mode flake sees it.
  spaInputs = {
    self = spaSelf;
    frappe-nix = self;
    inherit (inputs) nixpkgs;
    frappe = ../fixtures/app-workspace/frappe;
  };
  spaOutputs = (import (spaApp + "/flake.nix")).outputs (
    spaInputs
    // {
      frappe-nix = self // {
        lib = self.lib // {
          mkFlake =
            args: module:
            self.lib.mkFlake args {
              imports = [ module ];
              debug = true;
            };
        };
      };
    }
  );
  spaSelf = spaOutputs // {
    _type = "flake";
    outPath = spaApp;
    inputs = spaInputs;
    sourceInfo.outPath = spaApp;
  };
  spaExcludes = spaOutputs.allSystems.${system}.frappe-nix.app.excludeNodeTargets;

  nodeTargetFacts =
    assert lib.assertMsg
      (
        docsKeys [ ] == [
          "docs_app"
          "docs_app/docs-site"
          "docs_app/frontend"
        ]
      )
      "node targets: without excludeNodeTargets, docs-site must be a target as on main, got ${builtins.toJSON (docsKeys [ ])}";
    assert lib.assertMsg
      (
        docsKeys [ "docs-site" ] == [
          "docs_app"
          "docs_app/frontend"
        ]
      )
      "node targets: excludeNodeTargets = [ \"docs-site\" ] leaves ${
        builtins.toJSON (docsKeys [ "docs-site" ])
      }";
    assert lib.assertMsg (
      spaExcludes == [ ]
    ) "node targets: frappe-nix.app.excludeNodeTargets defaults to ${builtins.toJSON spaExcludes}";
    {
      default = docsKeys [ ];
      excluded = docsKeys [ "docs-site" ];
    };
in
{
  standards-preload-vite = pkgs.runCommand "standards-preload-vite-check" {
    nativeBuildInputs = [ pkgs.nodejs ];
  } "node ${../esbuild-preload.js} ${jsDir}/esbuild-preload.js 2>&1 | tee \"$out\"";

  standards-vite-register-copy =
    pkgs.runCommand "standards-vite-register-copy-check"
      {
        nativeBuildInputs = [
          pkgs.nodejs
          pkgs.oxfmt
          pkgs.oxlint
          pkgs.diffutils
          frappeNix
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }
        region() { sed -n '/^\/\/ vite-register:begin$/,/^\/\/ vite-register:end$/p' "$1"; }

        region ${jsDir}/vite-register.cjs > cjs
        [ "$(grep -c . cjs)" -gt 50 ] || fail "lib/js/vite-register.cjs has no marked region"
        packaged="$(frappe-nix data-path templates/scripts/vite-register.mjs)"
        cmp -s ${template} "$packaged" || fail "the package does not ship the template as committed"
        region "$packaged" > mjs
        diff -u cjs mjs || fail "the packaged scripts/vite-register.mjs and lib/js/vite-register.cjs differ"
        echo "ok   lib/js/vite-register.cjs and the packaged template hold the same region"

        region ${spaApp}/scripts/vite-register.mjs > rendered
        diff -u cjs rendered || fail "the spa-app fixture's rendered copy differs from lib/js/vite-register.cjs"
        echo "ok   so does the spa-app fixture's rendered scripts/vite-register.mjs"

        # The rendered copy under the app's formatter: tabs as rendered by
        # default, two spaces as js.format-tabs = false renders it.
        for tabs in true false; do
          for width in 80 110 200; do
            mkdir -p "fmt-$tabs-$width" && cd "fmt-$tabs-$width"
            printf '{"useTabs": %s, "printWidth": %s}\n' "$tabs" "$width" > .oxfmtrc.json
            if [ "$tabs" = true ]; then
              cp ${spaApp}/scripts/vite-register.mjs want.mjs
            else
              sed 's/\t/  /g' ${spaApp}/scripts/vite-register.mjs > want.mjs
            fi
            cp want.mjs got.mjs
            chmod u+w got.mjs
            oxfmt got.mjs > /dev/null
            diff -u want.mjs got.mjs || fail "oxfmt (useTabs $tabs, printWidth $width) rewrites scripts/vite-register.mjs"
            cd ..
          done
        done
        echo "ok   the rendered copy is oxfmt's output, tabs or spaces, at printWidth 80, 110 and 200"

        mkdir lint && cd lint
        cp ${../fixtures/standards-app/.oxlintrc.json} .oxlintrc.json
        mkdir scripts
        cp ${spaApp}/scripts/vite-register.mjs scripts/
        oxlint --deny-warnings scripts/vite-register.mjs || fail "oxlint flags scripts/vite-register.mjs under the managed rules"
        node --check scripts/vite-register.mjs
        node --check ${jsDir}/vite-register.cjs
        cd ..
        echo "ok   it passes oxlint with the managed .oxlintrc.json, and node parses both"
        touch "$out"
      '';

  standards-node-targets-docs-site =
    pkgs.runCommand "standards-node-targets-docs-site-check"
      {
        nativeBuildInputs = [ pkgs.findutils ];
        facts = builtins.toJSON nodeTargetFacts;
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }
        echo "ok   the Nix discovery: $facts"

        # The node-locks tool, with a stub yarn that writes an empty lock: which
        # targets get a node-locks/<key>/ is what it discovered.
        mkdir bin
        printf '#!%s\n' "$(command -v bash)" > bin/yarn
        cat >> bin/yarn <<'STUB'
        if [ "''${1:-}" = "--version" ]; then echo "1.22.22-stub"; exit 0; fi
        printf '# yarn lockfile v1\n' > yarn.lock
        STUB
        chmod +x bin/yarn
        export PATH="$PWD/bin:$PATH" HOME="$PWD"
        targets() { # <flag>...: the targets the tool locks
          rm -rf bench
          mkdir bench
          cp -r ${docsApps} bench/apps
          chmod -R u+w bench
          ${nodeLocks}/bin/frappe-nix-node-locks "$@" bench node-locks > /dev/null
          (cd bench/node-locks && find . -name yarn.lock -printf '%h\n' | sed 's|^\./||' | sort | tr '\n' ' ')
        }
        got="$(targets)"
        [ "$got" = "docs_app docs_app/docs-site docs_app/frontend " ] || fail "node-locks without excludes locked: $got"
        echo "ok   node-locks with the default [] locks docs-site, as on main"
        got="$(targets ${excludeFlags [ "docs-site" ]})"
        [ "$got" = "docs_app docs_app/frontend " ] || fail "node-locks with docs-site excluded locked: $got"
        echo "ok   node-locks with excludeNodeTargets = [ \"docs-site\" ] leaves it out"
        touch "$out"
      '';

  standards-spa-app =
    pkgs.runCommand "standards-spa-app-check"
      {
        nativeBuildInputs = [
          pkgs.nodejs
          pkgs.git
          pkgs.jq
          frappeNix
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }
        export HOME="$TMPDIR" FRAPPE_NIX_OFFLINE=1 GIT_CONFIG_GLOBAL=/dev/null
        unset FRAPPE_BENCH_ROOT
        commit() { git add -A && git -c user.name=t -c user.email=t@t commit -qm "$1"; }

        # The committed managed files: clean, and the build ends with the registration (C8).
        cp -r ${spaApp} app
        chmod -R u+w app
        (
          cd app
          git init -q -b develop
          commit fixture
          frappe-nix sync --check || fail "the committed spa-app fixture is not clean under --check"
          frappe-nix compat || fail "compat fails on the spa-app fixture"
          [ "$(jq -r .scripts.build package.json)" = "node build.mjs && node scripts/vite-register.mjs" ] \
            || fail "scripts.build does not end with the registration"
        )
        echo "ok   the fixture is clean under sync --check and compat (C8)"

        # A stub bench: what `bench build --app spa_app` leaves before the app's build.
        bench() { # <dir>
          mkdir -p "$1/sites/assets" "$1/apps/frappe/esbuild"
          printf 'frappe\nspa_app\n' > "$1/sites/apps.txt"
          printf '{\n    "desk.bundle.js": "/assets/frappe/dist/js/desk.bundle.FRAPPE1.js"\n}' > "$1/sites/assets/assets.json"
          cp -r ${spaApp} "$1/apps/spa_app"
          chmod -R u+w "$1/apps/spa_app"
        }
        build() { # <bench>: the app's own `yarn build`, run where frappe's esbuild.js runs it
          (cd "$1/apps/spa_app" && sh -c "$(jq -r .scripts.build package.json)")
        }
        key() { jq -r --arg k "$2" '.[$k] // "none"' "$1/sites/assets/assets.json"; }

        bench stock
        build stock > stock.log
        js="$(key stock spa.bundle.js)"
        css="$(key stock spa.bundle.css)"
        case "$js" in /assets/spa_app/dist/js/spa.bundle.*.js) ;; *) fail "spa.bundle.js maps to $js" ;; esac
        case "$css" in /assets/spa_app/dist/css/spa.bundle.*.css) ;; *) fail "spa.bundle.css maps to $css" ;; esac
        [ -f "stock/apps/spa_app/spa_app/public/''${js#/assets/spa_app/}" ] || fail "$js is not a file the build wrote"
        [ "$(key stock desk.bundle.js)" = /assets/frappe/dist/js/desk.bundle.FRAPPE1.js ] || fail "frappe's key moved"
        echo "ok   the app's own build maps spa.bundle.js -> $js and spa.bundle.css -> $css"

        cp stock/sites/assets/assets.json first.json
        build stock > again.log
        cmp -s first.json stock/sites/assets/assets.json || fail "a second build changed assets.json"
        ! grep -q '(vite)' again.log || fail "a second build registered keys again"

        # frappe-nix's preload after it: a stand-in esbuild.js runs `yarn build`
        # in apps/spa_app, as frappe's does, and the preload registers again.
        mkdir bin
        printf '#!%s\nexec sh -c "$(jq -r .scripts.build package.json)"\n' "$(command -v bash)" > bin/yarn
        chmod +x bin/yarn
        cat > stock/apps/frappe/esbuild/esbuild.js <<'JS'
        const path = require("path");
        const { execSync } = require("child_process");
        process.chdir(path.resolve(__dirname, "..", "..", "spa_app"));
        execSync("yarn build", { encoding: "utf8", stdio: "inherit" });
        JS
        PATH="$PWD/bin:$PATH" node --require ${jsDir}/esbuild-preload.js stock/apps/frappe/esbuild/esbuild.js > preload.log
        cmp -s first.json stock/sites/assets/assets.json || fail "the preload's pass after the app's own changed assets.json"
        echo "ok   a second build, and the preload's pass after it, leave assets.json byte-identical"

        # `bench build --hard-link`: sites/assets/spa_app is a real directory.
        bench hard
        mkdir -p hard/sites/assets/spa_app
        build hard > hard.log
        [ -f "hard/sites/assets/spa_app/dist/''${js#/assets/spa_app/dist/}" ] || fail "the hashed bundle was not copied into sites/assets/spa_app/dist"
        [ -f hard/sites/assets/spa_app/portal/index.html ] || fail "portal/ was not copied into sites/assets/spa_app"
        echo "ok   with a real sites/assets/spa_app, dist/ and portal/ are copied in"

        # An assets.json the app's build cannot parse (esbuild rewrites it in
        # place, so a concurrent `bench watch` can leave it torn): a warning, and
        # the build still succeeds with the file as it was.
        bench torn
        printf '{ not json' > torn/sites/assets/assets.json
        build torn > torn.log 2> torn.err || fail "the app's build failed on an assets.json that does not parse: $(cat torn.err)"
        [ "$(cat torn/sites/assets/assets.json)" = "{ not json" ] || fail "the unparsable assets.json was rewritten"
        grep -q '^vite-register: spa_app: .*its Vite bundles are not registered$' torn.err || fail "no warning naming the app: $(cat torn.err)"
        echo "ok   an assets.json that does not parse is a warning; the build exits 0 and leaves it as it was"

        # A build that changes directory (`cd frontend && yarn build`, erpnext's
        # shape): sync runs it in a subshell, so the step still runs from the
        # app's own directory and registers.
        (
          cd app
          jq -j --tab '.scripts.build = "node build.mjs && cd portal && true"' package.json > p.json
          printf '\n' >> p.json
          mv p.json package.json
          commit nested
          frappe-nix sync --write > /dev/null
          [ "$(jq -r .scripts.build package.json)" = "(node build.mjs && cd portal && true) && node scripts/vite-register.mjs" ] \
            || fail "sync wrote scripts.build = $(jq -r .scripts.build package.json)"
          commit synced
          frappe-nix compat || fail "compat fails on the subshell build"
        )
        bench nested
        cp app/package.json nested/apps/spa_app/package.json
        build nested > nested.log
        case "$(key nested spa.bundle.js)" in /assets/spa_app/dist/js/spa.bundle.*.js) ;; *) fail "after a cd, spa.bundle.js maps to $(key nested spa.bundle.js)" ;; esac
        echo "ok   a build that changes directory runs in a subshell and still registers"

        # Outside a bench.
        cp -r ${spaApp} lone
        chmod -R u+w lone
        (cd lone && node build.mjs > /dev/null && node scripts/vite-register.mjs) > lone.log
        grep -qx 'vite-register: no bench found; skipped' lone.log || fail "outside a bench: $(cat lone.log)"
        echo "ok   outside a bench it says so and exits 0"
        touch "$out"
      '';
}
