# Checks for lib/editable-src.nix — the cut that keeps an edit to an app's code
# from rebuilding the development virtualenv.
#
# Two things have to hold: the trimmed source holds only what the editable build
# reads (so nothing else feeds the derivation's hash), and that is enough — flit
# really does build an editable wheel from it. The second is checked by running
# flit_core's own PEP 660 hook over each trimmed tree.
{ pkgs }:

let
  inherit (pkgs) lib;

  trim = import ../lib/editable-src.nix { inherit lib; };
  fx = ./fixtures/editable-src;

  flitApp = trim (fx + "/flit-app");
  srcApp = trim (fx + "/src-app");

  # Members the cut must leave whole: another build backend, and PEP 639
  # license-files globs, which can reach anywhere.
  untouched = name: if trim (fx + "/${name}") == fx + "/${name}" then "untouched" else "trimmed";

  python = pkgs.python3.withPackages (ps: [ ps.flit-core ]);
in
{
  editable-src = pkgs.runCommand "frappe-nix-editable-src-check" { } ''
    fails=0
    ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }
    files() { (cd "$1" && find . -type f | sed 's#^\./##' | LC_ALL=C sort); }

    eq "a flit app keeps its metadata and __init__.py, not its code, assets or locks" \
      "$(printf '%s\n' LICENSE README.md flit_app/__init__.py license.txt pyproject.toml)" \
      "$(files ${flitApp})"

    eq "a src-layout app keeps a renamed module and a readme in a subdirectory" \
      "$(printf '%s\n' COPYING.txt docs/README.md pyproject.toml src/renamed/__init__.py)" \
      "$(files ${srcApp})"

    eq "another build backend keeps its whole source" untouched ${untouched "hatch-app"}
    eq "PEP 639 license-files keep the whole source" untouched ${untouched "pep639-app"}

    for app in ${flitApp} ${srcApp}; do
      work=$(mktemp -d)
      cp -r "$app/." "$work/"
      chmod -R u+w "$work"
      mkdir "$work/dist"
      if (cd "$work" && ${python}/bin/python -c \
            "from flit_core import buildapi; buildapi.build_editable('$work/dist')" >/dev/null 2>&1) \
         && ls "$work"/dist/*.whl >/dev/null 2>&1; then
        ok "flit builds an editable wheel from $(basename "$app")"
      else
        no "flit builds an editable wheel from $(basename "$app")"
        (cd "$work" && ${python}/bin/python -c \
          "from flit_core import buildapi; buildapi.build_editable('$work/dist')") || true
      fi
    done

    echo ""
    if [ "$fails" -eq 0 ]; then
      echo "All editable-src checks passed." | tee "$out"
    else
      echo "$fails check(s) failed."
      exit 1
    fi
  '';
}
