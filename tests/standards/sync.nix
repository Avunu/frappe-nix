# N3's checks (docs/app-standards/spec.md §7, "N3: scaffold"): the managed-file
# engine, offline. The cases on throwaway apps (local regions, floors, retire,
# SPAs, web-include, unchecked-js, generated globs, the version seed, node-lock
# seeding, skew, module toggles and retraction, profiles, the review-round
# parameters) are py/frappe_nix_tools/tests/test_sync.py, test_standards.py,
# test_scaffold.py and test_bootstrap.py, which run in the package build every
# check here depends on.
#
#   standards-sync      on a copy of the committed fixture app: it is clean as
#                       committed, a second --sync changes no byte, and an
#                       edited managed file is drift with its diff; synced with
#                       the example-org profile (every module on), --check is
#                       clean and a second --sync changes no byte.
#   standards-manifest  no path in two manifest fragments; every `uses` key is a
#                       schema key, and every module and org value an entry's
#                       code reads is in its `uses`; every template renders for
#                       the fixture contexts (plain, erpnext+hrms, scss, nested
#                       frontend, SPA at the root and in portal/, docs-site,
#                       pilot-assets, Vite) × {minimal, recommended,
#                       example-org}, round-trips through --check, and
#                       flake.nix is nixfmt-stable.
#   standards-app-mode  the default app mode is unchanged without opt-in (S35):
#                       templates/app is main's (and the package's copies of it
#                       match); `frappe-init --app` on a bare app writes exactly
#                       flake.nix, .envrc and .gitignore from it and leaves
#                       pyproject.toml alone; --sync and --check there exit 2
#                       with the opt-in hint and write nothing. `frappe-init
#                       --app --standards recommended` writes templates/app's
#                       files and then what `frappe-nix sync --write` renders,
#                       nothing else; --site reaches the table, and so does
#                       --profile-path; a usage error under `frappe-init --check`
#                       is exit 2 or 3, never 1; without --sync/--check the new
#                       flags are refused where nothing reads them, as main
#                       refuses an unknown flag (exit 1, nothing written).
{
  pkgs,
  frappeNixTools,
  ...
}:

let
  frappeNix = (import ../../lib/standards/outputs.nix { inherit pkgs; }).tools.frappe-nix;
  python = pkgs.python314.withPackages (_: [ frappeNixTools ]);
  fixture = ../fixtures/standards-app;
  exampleOrg = ../fixtures/profiles/example-org;
  frappeInit = import ../../lib/init.nix { inherit pkgs; };
  tools = [
    pkgs.git
    pkgs.diffutils
    frappeNix
  ];

  # A writable git checkout of the fixture: the committed tree, its locks included.
  copyFixture = ''
    export HOME="$TMPDIR" FRAPPE_NIX_OFFLINE=1 GIT_CONFIG_GLOBAL=/dev/null
    cp -r ${fixture} app
    chmod -R u+w app
    cd app
    git init -q
    git add -A
    git -c user.name=t -c user.email=t@t commit -qm fixture
  '';

  # templates/app as frappe-nix `main` has it (9e63c96): the opt-in leaves it alone (S35).
  mainTemplate = {
    "flake.nix" = "d09a35cb2741a87a13dc5365fb1f79246b6a3d2d";
    ".envrc" = "a41f435fb8be098715df478ec5e78da83661d45d";
    ".gitignore" = "c2de0b11e743206b8c81c77603a36c58fa72c5e2";
  };
in
{
  standards-sync = pkgs.runCommand "standards-sync-check" { nativeBuildInputs = tools; } ''
    set -euo pipefail
    fail() { echo "FAIL $*" >&2; exit 1; }
    ${copyFixture}

    frappe-nix sync --check || fail "the committed fixture is not clean under --check"
    echo "ok   the committed fixture is clean"

    frappe-nix sync --write
    [ -z "$(git status --porcelain)" ] || { git status --porcelain; fail "--sync changed the committed fixture"; }
    frappe-nix sync --write
    [ -z "$(git status --porcelain)" ] || fail "a second --sync changed bytes"
    frappe-nix sync --check || fail "--check after --sync is not clean"
    echo "ok   --sync is a no-op twice, and --check stays clean"

    sed -i 's/subject_length = 100/subject_length = 72/' committed.toml
    set +e
    frappe-nix sync --check > "$TMPDIR/report"
    code=$?
    set -e
    [ "$code" = 1 ] || fail "an edited committed.toml gave --check exit $code, not 1"
    grep -q '^committed.toml (whole): differs' "$TMPDIR/report" || fail "the report does not name committed.toml: $(cat "$TMPDIR/report")"
    grep -q '^+subject_length = 100' "$TMPDIR/report" || fail "the report has no diff: $(cat "$TMPDIR/report")"
    echo "ok   an edited managed file is drift, with its diff"

    frappe-nix sync --write
    [ -z "$(git status --porcelain)" ] || fail "--sync did not restore committed.toml"
    echo "ok   --sync restores it"

    cp -r ${exampleOrg} .standards-profile
    chmod -R u+w .standards-profile
    sed -i 's#^profile = "recommended"$#profile = "./.standards-profile"#' pyproject.toml
    git add -A
    git -c user.name=t -c user.email=t@t commit -qm example-org
    frappe-nix sync --write
    frappe-nix sync --check || fail "--check after --sync with example-org is not clean"
    git add -A
    git -c user.name=t -c user.email=t@t commit -qm synced
    frappe-nix sync --write
    [ -z "$(git status --porcelain)" ] || fail "a second --sync with example-org changed bytes"
    grep -q '"author": "Example Org"' package.json || fail "example-org's publisher is not package.json's author"
    [ -f SECURITY.md ] || fail "example-org's [[extra-files]] SECURITY.md is missing"
    echo "ok   synced with example-org (every module on): clean, and a second --sync is a no-op"
    touch "$out"
  '';

  standards-manifest =
    pkgs.runCommand "standards-manifest-check"
      {
        nativeBuildInputs = [
          python
          pkgs.git
          pkgs.nixfmt
        ];
      }
      ''
        set -euo pipefail
        export HOME="$TMPDIR" GIT_CONFIG_GLOBAL=/dev/null
        python ${./sync-contexts.py} ${exampleOrg}
        touch "$out"
      '';

  standards-app-mode =
    pkgs.runCommand "standards-app-mode-check"
      {
        nativeBuildInputs = tools ++ [
          frappeInit
          pkgs.findutils
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }
        export HOME="$TMPDIR" FRAPPE_NIX_OFFLINE=1 GIT_CONFIG_GLOBAL=/dev/null

        got="$(cd ${../../templates/app} && find . -type f | sort | tr '\n' ' ')"
        [ "$got" = "./.envrc ./.gitignore ./flake.nix " ] || fail "templates/app holds $got"
        ${pkgs.lib.concatStrings (
          pkgs.lib.mapAttrsToList (name: sha: ''
            [ "$(git hash-object ${../../templates/app + "/${name}"})" = ${sha} ] ||
              fail "templates/app/${name} is not frappe-nix main's"
          '') mainTemplate
        )}
        cmp ${../../templates/app/flake.nix} "$(frappe-nix data-path app-template/flake.nix.in)" ||
          fail "the package's app-template/flake.nix.in is not templates/app/flake.nix"
        cmp ${../../templates/app/.envrc} "$(frappe-nix data-path app-template/envrc)" ||
          fail "the package's app-template/envrc is not templates/app/.envrc"
        block="$(frappe-nix data-path templates/gitignore.block)"
        cmp -n "$(stat -c %s ${../../templates/app/.gitignore})" ${../../templates/app/.gitignore} "$block" ||
          fail "the opted-in .gitignore block does not start with templates/app/.gitignore"
        echo "ok   templates/app is main's, and the package's copies of it match"

        # A bare app: pyproject.toml, the package and its hooks.py, one commit.
        mkdir bare && cd bare
        git init -q
        printf '[project]\nname = "bare_app"\ndynamic = ["version"]\n\n[tool.ruff]\nline-length = 110\n' > pyproject.toml
        mkdir bare_app
        printf '__version__ = "0.0.1"\n' > bare_app/__init__.py
        printf 'app_title = "Bare App"\n' > bare_app/hooks.py
        git add -A
        git -c user.name=t -c user.email=t@t commit -qm app
        cp -r . ../again
        cp -r . ../sited
        cp -r . ../flags
        cp -r . ../orgflags

        frappe-init --app --frappe-version version-16 --skip-lock
        have="$(git status --porcelain | sort)"
        [ "$have" = "$(printf 'A  .envrc\nA  .gitignore\nA  flake.nix')" ] || fail "frappe-init --app changed $have"
        sed -e 's|@APP_NAME@|bare_app|g' -e 's|@FRAPPE_BRANCH@|version-16|g' \
          -e 's|@SITE_NAME@|bare-app.localhost|g' -e 's|@FRAPPE_VERSION@|version-16|g' \
          ${../../templates/app/flake.nix} | cmp - flake.nix || fail "flake.nix is not templates/app's"
        cmp ${../../templates/app/.envrc} .envrc || fail ".envrc is not templates/app's"
        grep -qF "$(head -1 ${../../templates/app/.gitignore})" .gitignore || fail ".gitignore lacks the template's block"
        echo "ok   frappe-init --app without --standards writes exactly main's three files"

        for argv in "--sync" "--check"; do
          set +e
          # shellcheck disable=SC2086 # word-split on purpose
          frappe-init $argv > "$TMPDIR/out" 2>&1
          code=$?
          set -e
          [ "$code" = 2 ] || fail "frappe-init $argv on an app that has not opted in exited $code, not 2"
          grep -q "has not opted in" "$TMPDIR/out" || fail "frappe-init $argv gave no opt-in hint: $(cat "$TMPDIR/out")"
        done
        [ "$(git status --porcelain | sort)" = "$(printf 'A  .envrc\nA  .gitignore\nA  flake.nix')" ] ||
          fail "a refused --sync or --check wrote something"
        echo "ok   frappe-init --sync and --check without opt-in exit 2 with the hint and write nothing"

        # --standards: templates/app's three files, then exactly what sync renders.
        cd ../again
        frappe-init --app --standards recommended --frappe-version version-16 --skip-lock > /dev/null
        mkdir ../plain && cp -r ../sited/. ../plain
        (cd ../plain && frappe-nix sync --write --standards recommended --frappe-version version-16 > /dev/null 2>&1)
        want="$(cd ../plain && git ls-files -co --exclude-standard | sort)"
        have="$(git ls-files -co --exclude-standard | sort)"
        [ "$have" = "$want" ] || fail "frappe-init --app --standards wrote $(diff <(echo "$want") <(echo "$have"))"
        for f in $have; do
          cmp -s "$f" "../plain/$f" || fail "$f differs from what frappe-nix sync renders"
        done
        grep -qx 'profile = "recommended"' pyproject.toml || fail "--standards did not create the table"
        echo "ok   frappe-init --app --standards recommended writes what frappe-nix sync renders, nothing else"

        # --site reaches sync: [tool.frappe-nix] site and the flake's siteName.
        cd ../sited
        frappe-init --app --standards minimal --frappe-version version-16 --site custom.localhost --skip-lock > /dev/null
        grep -qx 'site = "custom.localhost"' pyproject.toml || fail "--site is not [tool.frappe-nix] site"
        grep -q 'siteName = "custom.localhost";' flake.nix || fail "--site is not the flake's siteName"
        echo "ok   frappe-init --app --standards --site X renders site X"

        code_of() {
          set +e
          "$@" > /dev/null 2>&1
          echo "$?"
          set -e
        }
        for argv in "--check --format" "--check --only" "--check --expect-rev" "--check --bogus" "--bogus --check" "--check --standards minimal"; do
          # shellcheck disable=SC2086 # word-split on purpose
          code="$(code_of frappe-init $argv)"
          [ "$code" = 2 ] || fail "frappe-init $argv exited $code, not 2"
        done
        code="$(code_of frappe-init --check /nonexistent)"
        [ "$code" = 3 ] || fail "frappe-init --check /nonexistent exited $code, not 3"
        echo "ok   frappe-init --check usage errors are 2, a bad target 3"

        # Without --sync/--check, flags behave as on main for an app that has not opted in:
        # the --check flags and a stray flag are unknown (exit 1, before --help), and the app
        # standards flags are refused where nothing would read them; nothing is written.
        cd ../flags
        for argv in "--app --frappe-version version-16 --skip-lock --only flake.nix" \
          "--app --format json --dry-run" "--app --expect-rev x --skip-lock" "--app --bogus --help" \
          "--app --frappe-version version-16 --skip-lock --profile-path ." \
          "--app --frappe-version version-16 --skip-lock --init-listing"; do
          # shellcheck disable=SC2086 # word-split on purpose
          code="$(code_of frappe-init $argv)"
          [ "$code" = 1 ] || fail "frappe-init $argv exited $code, not 1"
          [ -z "$(git status --porcelain)" ] || fail "frappe-init $argv wrote $(git status --porcelain)"
        done
        mkdir ../empty && cd ../empty
        code="$(code_of frappe-init --init --standards recommended)"
        [ "$code" = 1 ] || fail "frappe-init --init --standards exited $code, not 1"
        [ -z "$(ls -A)" ] || fail "frappe-init --init --standards wrote $(ls -A)"
        echo "ok   frappe-init without --sync/--check refuses the new flags where nothing reads them"

        # --app --standards passes the sync flags on: --profile-path is the profile read.
        cd ../orgflags
        cp -r ${exampleOrg} ../org-profile
        chmod -R u+w ../org-profile
        frappe-init --app --standards github:example/profile --profile-path ../org-profile \
          --frappe-version version-16 --skip-lock > /dev/null 2>&1 || fail "frappe-init --app --profile-path failed"
        grep -q '"author": "Example Org"' package.json || fail "--profile-path did not reach frappe-nix sync"
        echo "ok   frappe-init --app --standards passes --profile-path to frappe-nix sync"
        touch "$out"
      '';
}
