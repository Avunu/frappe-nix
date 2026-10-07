# N3's checks (docs/ironclad/spec.md §7, "N3: scaffold"): the managed-file
# engine, offline. The cases on throwaway apps (local regions, floors, retire,
# SPAs, web-include, unchecked-js, generated globs, the version seed, node-lock
# seeding, skew) are py/ironclad/tests/test_sync.py and test_scaffold.py, which
# run in the package build every check here depends on.
#
#   ironclad-sync          on a copy of the committed fixture app: it is clean as
#                          committed, a second --sync changes no byte, and an
#                          edited managed file is drift with its diff.
#   ironclad-manifest      no path in two manifest fragments; every template
#                          renders for the fixture contexts (plain, erpnext+hrms,
#                          scss, nested frontend, SPA at the root and in portal/,
#                          docs-site, pilot-assets, Vite), round-trips through
#                          --check, and flake.nix is nixfmt-stable.
#   ironclad-app-template  templates/app holds exactly .envrc and .gitignore,
#                          both identical to what sync renders, and
#                          `frappe-init --app` on a bare app writes those two plus
#                          what `ironclad sync --write` renders, nothing else; and
#                          a usage error under `frappe-init --check` is exit 2 or 3,
#                          never 1 (drift).
{
  pkgs,
  ironclad,
  ...
}:

let
  python = pkgs.python314.withPackages (_: [ ironclad ]);
  fixture = ../fixtures/ironclad-app;
  frappeInit = import ../../lib/init.nix { inherit pkgs; };
  tools = [
    pkgs.git
    pkgs.diffutils
    ironclad
  ];

  # A writable git checkout of the fixture: the committed tree, its locks included.
  copyFixture = ''
    export HOME="$TMPDIR" IRONCLAD_OFFLINE=1 GIT_CONFIG_GLOBAL=/dev/null
    cp -r ${fixture} app
    chmod -R u+w app
    cd app
    git init -q
    git add -A
    git -c user.name=t -c user.email=t@t commit -qm fixture
  '';
in
{
  ironclad-sync = pkgs.runCommand "ironclad-sync-check" { nativeBuildInputs = tools; } ''
    set -euo pipefail
    fail() { echo "FAIL $*" >&2; exit 1; }
    ${copyFixture}

    ironclad sync --check || fail "the committed fixture is not clean under --check"
    echo "ok   the committed fixture is clean"

    ironclad sync --write
    [ -z "$(git status --porcelain)" ] || { git status --porcelain; fail "--sync changed the committed fixture"; }
    ironclad sync --write
    [ -z "$(git status --porcelain)" ] || fail "a second --sync changed bytes"
    ironclad sync --check || fail "--check after --sync is not clean"
    echo "ok   --sync is a no-op twice, and --check stays clean"

    sed -i 's/subject_length = 100/subject_length = 72/' committed.toml
    set +e
    ironclad sync --check > "$TMPDIR/report"
    code=$?
    set -e
    [ "$code" = 1 ] || fail "an edited committed.toml gave --check exit $code, not 1"
    grep -q '^committed.toml (whole): differs' "$TMPDIR/report" || fail "the report does not name committed.toml: $(cat "$TMPDIR/report")"
    grep -q '^+subject_length = 100' "$TMPDIR/report" || fail "the report has no diff: $(cat "$TMPDIR/report")"
    echo "ok   an edited managed file is drift, with its diff"

    ironclad sync --write
    [ -z "$(git status --porcelain)" ] || fail "--sync did not restore committed.toml"
    echo "ok   --sync restores it"
    touch "$out"
  '';

  ironclad-manifest =
    pkgs.runCommand "ironclad-manifest-check"
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
        python -c 'from ironclad.scaffold import manifest; m = manifest.load(); print("ok   manifest:", len(m.entries), "entries,", len(m.retire), "retire rules")'
        python ${./sync-contexts.py}
        touch "$out"
      '';

  ironclad-app-template =
    pkgs.runCommand "ironclad-app-template-check"
      {
        nativeBuildInputs = tools ++ [
          frappeInit
          pkgs.findutils
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }
        export HOME="$TMPDIR" IRONCLAD_OFFLINE=1 GIT_CONFIG_GLOBAL=/dev/null

        got="$(cd ${../../templates/app} && find . -type f | sort | tr '\n' ' ')"
        [ "$got" = "./.envrc ./.gitignore " ] || fail "templates/app holds $got"
        cmp ${../../templates/app/.gitignore} "$(ironclad data-path templates/gitignore.block)" ||
          fail "templates/app/.gitignore differs from ironclad/data/templates/gitignore.block"
        echo "ok   templates/app is exactly .envrc and .gitignore"

        # A bare app: pyproject.toml, the package and its hooks.py, one commit.
        mkdir app && cd app
        git init -q
        printf '[project]\nname = "bare_app"\ndynamic = ["version"]\n' > pyproject.toml
        mkdir bare_app
        printf '__version__ = "0.0.1"\n' > bare_app/__init__.py
        printf 'app_title = "Bare App"\n' > bare_app/hooks.py
        git add -A
        git -c user.name=t -c user.email=t@t commit -qm app
        before="$(git ls-files | sort)"

        frappe-init --app --frappe-version version-16
        cmp .envrc ${../../templates/app/.envrc} || fail ".envrc is not templates/app/.envrc"

        # What sync alone renders for the same app: the set frappe-init must equal.
        mkdir ../again && git archive HEAD | tar -x -C ../again
        (cd ../again && git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -qm app &&
          ironclad sync --write --frappe-version version-16 > /dev/null)
        want="$(cd ../again && git ls-files -co --exclude-standard | sort)"
        have="$(git ls-files -co --exclude-standard | sort)"
        [ "$have" = "$want" ] || fail "frappe-init --app wrote $(diff <(echo "$want") <(echo "$have"))"
        for f in $(comm -13 <(echo "$before") <(echo "$have")); do
          cmp -s "$f" "../again/$f" || fail "$f differs from what ironclad sync renders"
          case "$f" in
            *.j2 | *.block | manifest.d/* | known-apps.json) fail "$f was copied from ironclad/data" ;;
          esac
        done
        echo "ok   frappe-init --app writes .envrc, .gitignore and what ironclad sync renders:"
        comm -13 <(echo "$before") <(echo "$have") | sed 's/^/       /'

        code_of() {
          set +e
          "$@" > /dev/null 2>&1
          echo "$?"
          set -e
        }
        for argv in "--check --format" "--check --only" "--check --expect-rev" "--check --bogus" "--bogus --check"; do
          # shellcheck disable=SC2086 # word-split on purpose
          code="$(code_of frappe-init $argv)"
          [ "$code" = 2 ] || fail "frappe-init $argv exited $code, not 2"
        done
        code="$(code_of frappe-init --check /nonexistent)"
        [ "$code" = 3 ] || fail "frappe-init --check /nonexistent exited $code, not 3"
        echo "ok   frappe-init --check usage errors are 2, a bad target 3"
        touch "$out"
      '';
}
