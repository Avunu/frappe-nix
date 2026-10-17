# N5's checks (docs/app-standards/spec.md §7, "N5: product tools"), offline. The rule-by-rule
# cases of frappe-listing (L1 to L12, the multiset baseline, the registry dry run), the README
# blocks and frappe-icon's structural rules are py/frappe_nix_tools/tests/test_listing.py and
# test_icon.py, which run in the package build every check here depends on. What needs the
# network (L7's registry semgrep, L9's pilot bench, the registry clone) or a running bench
# (frappe-demo, frappe-shots) is .github/workflows/selftest-product.yml.
#
#   standards-product-tools  frappe-listing, frappe-icon, frappe-demo and frappe-shots build
#   standards-demo-shots     tests/standards/demo-shots.sh: frappe-demo's and frappe-shots'
#                            every flag, exit code and branch that needs no real bench or
#                            chromium, against a dev shell of stubs (the refusals and the
#                            prompt, the demo's date, seed and erpnext sources, the fake
#                            clock, the bench brought up and stopped again)
#   standards-shots-types    lib/shots/shots.d.ts and the packaged copy sync renders into
#                            marketplace/shots.d.ts are the same file
#   standards-icon           on the fixture with the example-org profile (tile #336699):
#                            `frappe-icon check` passes, raster rules included; `build`
#                            writes the same bytes twice; a stroke fails it (exit 1); an
#                            empty org.brand.tile-color is exit 2 naming it
#   standards-readme         on the same fixture: sync renders the README blocks, with
#                            example-org's support override; `frappe-listing readme --check`
#                            passes; a removed marker is exit 2 naming its block
#   standards-listing-mysqlclient
#                            the frappe-listing wrapper exports MYSQLCLIENT_CFLAGS and
#                            MYSQLCLIENT_LDFLAGS (L9's ImportCheck builds frappe's
#                            mysqlclient), as defaults a caller's value overrides; with only
#                            those and no pkg-config, mysqlclient's sdist builds offline and
#                            MySQLdb imports without LD_LIBRARY_PATH
{
  pkgs,
  frappeNixTools,
  ...
}:

let
  inherit (pkgs) lib;
  inherit (import ../../lib/standards/outputs.nix { inherit pkgs; }) tools;
  fixture = ../fixtures/standards-app;
  exampleOrg = ../fixtures/profiles/example-org;

  # A writable git checkout of the fixture on the example-org profile (in-repo), synced offline.
  exampleApp = ''
    export HOME="$TMPDIR" FRAPPE_NIX_OFFLINE=1 GIT_CONFIG_GLOBAL=/dev/null
    commit() { git add -A && git -c user.name=t -c user.email=t@t commit -qm "$1"; }
    cp -r ${fixture} app
    chmod -R u+w app
    cd app
    cp -r ${exampleOrg} .standards-profile
    chmod -R u+w .standards-profile
    sed -i 's#^profile = "recommended"$#profile = "./.standards-profile"#' pyproject.toml
    git init -q
    commit fixture
    frappe-nix sync --write > /dev/null 2>&1
    commit synced
  '';
  nativeBuildInputs = [
    pkgs.git
    pkgs.diffutils
    tools.frappe-nix
    tools.frappe-icon
    tools.frappe-listing
  ];
in
lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
  standards-product-tools = pkgs.linkFarm "standards-product-tools" (
    map
      (name: {
        inherit name;
        path = tools.${name};
      })
      [
        "frappe-listing"
        "frappe-icon"
        "frappe-demo"
        "frappe-shots"
      ]
  );

  standards-demo-shots =
    pkgs.runCommand "standards-demo-shots-check"
      {
        nativeBuildInputs = [
          pkgs.git
          pkgs.python3
          pkgs.coreutils
          pkgs.gnugrep
          pkgs.gnused
          pkgs.util-linux
        ];
      }
      ''
        DEMO=${lib.getExe tools.frappe-demo} SHOTS=${lib.getExe tools.frappe-shots} \
          FIXTURE=${fixture} REAL_PY=${lib.getExe pkgs.python3} \
          bash ${./demo-shots.sh}
        touch "$out"
      '';

  standards-shots-types = pkgs.runCommand "standards-shots-types-check" { } ''
    cmp ${../../lib/shots/shots.d.ts} ${frappeNixTools}/${pkgs.python314.sitePackages}/frappe_nix_tools/data/templates/marketplace/shots.d.ts.j2 || {
      echo "FAIL lib/shots/shots.d.ts and the packaged data/templates/marketplace/shots.d.ts.j2 differ" >&2
      exit 1
    }
    touch "$out"
  '';

  standards-icon = pkgs.runCommand "standards-icon-check" { inherit nativeBuildInputs; } ''
    set -euo pipefail
    fail() { echo "FAIL $*" >&2; exit 1; }
    ${exampleApp}

    frappe-icon check || fail "frappe-icon check fails on the fixture's icon pair"
    echo "ok   frappe-icon check (structural and raster) passes on the good pair, tile #336699"

    frappe-icon build > /dev/null
    first="$(sha256sum .dev-dist/icons/*.png)"
    frappe-icon build > /dev/null
    [ "$first" = "$(sha256sum .dev-dist/icons/*.png)" ] || fail "frappe-icon build is not byte-stable"
    echo "ok   frappe-icon build writes the same PNGs twice"

    symbolic=standards_fixture/public/images/standards_fixture-symbolic.svg
    cp "$symbolic" "$TMPDIR/symbolic.svg"
    sed -i 's#<path fill="currentColor"#<path stroke="black" fill="currentColor"#' "$symbolic"
    set +e
    frappe-icon check > "$TMPDIR/out" 2>&1
    code=$?
    set -e
    [ "$code" = 1 ] || fail "a stroke gave exit $code, not 1: $(cat "$TMPDIR/out")"
    grep -q "convert strokes to outlines" "$TMPDIR/out" || fail "the stroke is not named: $(cat "$TMPDIR/out")"
    cp "$TMPDIR/symbolic.svg" "$symbolic"
    echo "ok   a stroke fails it (exit 1)"

    sed -i 's/^tile-color = "#336699"$/tile-color = ""/' .standards-profile/profile.toml
    set +e
    frappe-icon check --structural > "$TMPDIR/out" 2>&1
    code=$?
    set -e
    [ "$code" = 2 ] || fail "an empty tile colour gave exit $code, not 2"
    grep -q "org.brand.tile-color is empty" "$TMPDIR/out" || fail "the empty tile colour is not named: $(cat "$TMPDIR/out")"
    echo "ok   an empty org.brand.tile-color is exit 2 naming it"
    touch "$out"
  '';

  standards-listing-mysqlclient =
    let
      python = pkgs.python3.withPackages (ps: [
        ps.pip
        ps.setuptools
      ]);
    in
    pkgs.runCommandCC "standards-listing-mysqlclient-check" { nativeBuildInputs = [ python ]; } ''
      set -euo pipefail
      fail() { echo "FAIL $*" >&2; exit 1; }
      export HOME="$TMPDIR"
      wrapper=${tools.frappe-listing}/bin/frappe-listing
      grep '^export MYSQLCLIENT_' "$wrapper" > flags.sh || fail "the wrapper exports no MYSQLCLIENT_* flags"

      MYSQLCLIENT_CFLAGS=-Icaller bash -c '. ./flags.sh; [ "$MYSQLCLIENT_CFLAGS" = -Icaller ]' ||
        fail "a caller's MYSQLCLIENT_CFLAGS does not win over the wrapper's default"
      echo "ok   a caller's MYSQLCLIENT_CFLAGS wins over the wrapper's default"

      unset MYSQLCLIENT_CFLAGS MYSQLCLIENT_LDFLAGS LD_LIBRARY_PATH PKG_CONFIG_PATH
      . ./flags.sh
      [ -n "$MYSQLCLIENT_CFLAGS" ] && [ -n "$MYSQLCLIENT_LDFLAGS" ] || fail "the wrapper's flags are empty"
      ! command -v pkg-config > /dev/null || fail "pkg-config is on PATH, so the flags are not what is tested"
      python -m pip install -q --no-index --no-build-isolation --no-deps --target site \
        ${pkgs.python3.pkgs.mysqlclient.src} > pip.log 2>&1 || { cat pip.log; fail "mysqlclient does not build with the wrapper's flags"; }
      echo "ok   mysqlclient ${pkgs.python3.pkgs.mysqlclient.version} builds from its sdist with only the wrapper's flags"
      PYTHONPATH=site python -c 'import MySQLdb; print(MySQLdb._mysql.get_client_info())' > info ||
        fail "MySQLdb does not import without LD_LIBRARY_PATH"
      echo "ok   MySQLdb imports without LD_LIBRARY_PATH (client $(cat info))"
      touch "$out"
    '';

  standards-readme = pkgs.runCommand "standards-readme-check" { inherit nativeBuildInputs; } ''
    set -euo pipefail
    fail() { echo "FAIL $*" >&2; exit 1; }
    ${exampleApp}

    grep -q '^# Standards Fixture$' README.md || fail "the header block is not rendered: $(cat README.md)"
    grep -q '^Example Org supports Standards Fixture at https://example.org/support/' README.md ||
      fail "example-org's support override is not used: $(cat README.md)"
    grep -q 'bench get-app https://github.com/example-org/standards_fixture --branch version-16' README.md ||
      fail "the installation block is not rendered"
    frappe-listing readme --check || fail "frappe-listing readme --check fails right after sync"
    frappe-nix sync --check > /dev/null || fail "--check after sync is not clean"
    echo "ok   sync renders the README blocks (with example-org's override), and readme --check passes"

    sed -i '/<!-- frappe-nix:end license -->/d' README.md
    set +e
    frappe-listing readme --check > "$TMPDIR/out" 2>&1
    code=$?
    set -e
    [ "$code" = 2 ] || fail "a removed marker gave exit $code, not 2"
    grep -q "block license" "$TMPDIR/out" || fail "the block is not named: $(cat "$TMPDIR/out")"
    echo "ok   a removed marker is exit 2 naming its block"
    touch "$out"
  '';
}
