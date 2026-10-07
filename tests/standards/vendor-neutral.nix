# No organisation values in frappe-nix (docs/app-standards/spec.md S37, §7 N3a).
#
#   standards-vendor-neutral  vendor_neutral.py `code` finds no line of
#                             vendor-denylist.txt in frappe-nix's built-ins: the
#                             frappe-nix-tools code and data (profiles, templates,
#                             manifests, schemas, known-apps.json), lib/**,
#                             templates/app/**, the repo-policy defaults and the
#                             reusable app-*.yml and fleet-audit.yml workflows.
#                             Then each planted variant must fail, naming its file
#                             and line, so the scan can't go blind unnoticed.
#   standards-docs            the docs index is titled "App standards and quality
#                             gates" and links every page, and vendor_neutral.py
#                             `docs` finds org values in docs/app-standards only
#                             inside blocks introduced by "Avunu profile example".
{
  pkgs,
  lib,
  ...
}:

let
  root = ../..;
  scoped = lib.fileset.toSource {
    inherit root;
    fileset = lib.fileset.unions [
      (root + "/py/frappe_nix_tools/frappe_nix_tools")
      (root + "/lib")
      (root + "/templates/app")
      (lib.fileset.maybeMissing (root + "/repo-policy"))
      (lib.fileset.fileFilter (f: lib.hasPrefix "app-" f.name || f.name == "fleet-audit.yml") (
        root + "/.github/workflows"
      ))
    ];
  };
  docs = lib.fileset.toSource {
    inherit root;
    fileset = root + "/docs/app-standards";
  };
  scanner = ./vendor_neutral.py;
  denylist = ./vendor-denylist.txt;
  python = "${pkgs.python3}/bin/python3";

  # Each plant: a file (created when missing) and a line appended to it. The scan
  # must then fail and name that file and line.
  plants = [
    {
      file = "py/frappe_nix_tools/frappe_nix_tools/data/templates/package.json.j2";
      line = ''"author": "Avunu LLC",'';
    }
    {
      file = "py/frappe_nix_tools/frappe_nix_tools/data/profiles/recommended@1.0.toml";
      line = ''tile-color = "#834aff"'';
    }
    {
      file = "lib/rename/frappe_rename_app.py";
      line = ''REPLACE_PAIRS = {"jailbreak": "data_steward"}  # avunu fleet'';
    }
    {
      file = ".github/workflows/app-release.yml";
      line = ''registry-fork: { default: "Avunu/marketplace" }'';
    }
  ];
  plantScript = lib.concatMapStringsSep "\n" (p: ''
    rm -rf tree
    cp -r ${scoped} tree
    chmod -R u+w tree
    mkdir -p "$(dirname tree/${lib.escapeShellArg p.file})"
    touch tree/${lib.escapeShellArg p.file}
    line="$(( $(wc -l < tree/${lib.escapeShellArg p.file}) + 1 ))"
    printf '%s\n' ${lib.escapeShellArg p.line} >> tree/${lib.escapeShellArg p.file}
    if ${python} ${scanner} code tree ${denylist} > found; then
      echo "FAIL the scan missed ${p.file}: ${p.line}" >&2
      exit 1
    fi
    grep -q "^${p.file}:$line: " found || {
      echo "FAIL the scan did not name ${p.file}:$line" >&2
      cat found >&2
      exit 1
    }
    echo "ok   planted in ${p.file} → $(head -n1 found)"
  '') plants;
in
{
  standards-vendor-neutral = pkgs.runCommand "standards-vendor-neutral-check" { } ''
    set -euo pipefail
    if ! ${python} ${scanner} code ${scoped} ${denylist}; then
      echo "FAIL an organisation value is in frappe-nix's built-ins (spec S37); move it to a profile" >&2
      exit 1
    fi
    echo "ok   frappe-nix's built-ins name no organisation"
    ${plantScript}
    touch "$out"
  '';

  standards-docs = pkgs.runCommand "standards-docs-check" { } ''
    set -euo pipefail
    fail() { echo "FAIL $*" >&2; exit 1; }
    d=${docs}/docs/app-standards
    grep -qx 'title: App standards and quality gates' "$d/README.md" \
      || fail "docs/app-standards/README.md is not titled \"App standards and quality gates\""
    for page in "$d"/*.md; do
      name="$(basename "$page")"
      [ "$name" = README.md ] && continue
      grep -q "($name)" "$d/README.md" || fail "the docs index does not link $name"
      head -n1 "$page" | grep -qx -- '---' || fail "$name has no front matter"
      grep -q '^title: ' "$page" || fail "$name has no title"
    done
    echo "ok   the docs index is titled and links every page"
    if ! ${python} ${scanner} docs ${docs} ${denylist}; then
      fail "docs/app-standards shows an organisation value outside an \"Avunu profile example\" block"
    fi
    echo "ok   docs/app-standards shows org values only in Avunu profile example blocks"

    # Planted: a value inside an example block passes; the same value in prose, or
    # in a fence that no "Avunu profile example" line introduces, fails.
    mkdir -p planted/docs/app-standards
    page=planted/docs/app-standards/page.md
    printf '%s\n' 'An Avunu profile example:' "" '```toml' 'publisher = "Avunu LLC"' '```' > "$page"
    ${python} ${scanner} docs planted ${denylist} || fail "an example block was flagged"
    printf '%s\n' "" 'Set the publisher to Avunu LLC.' >> "$page"
    if ${python} ${scanner} docs planted ${denylist} > found; then fail "prose naming an org value passed"; fi
    grep -q '^docs/app-standards/page.md:7: ' found || fail "the docs lint did not name page.md:7"
    printf '%s\n' 'An example:' '```toml' 'tile-color = "#834AFF"' '```' > "$page"
    if ${python} ${scanner} docs planted ${denylist} > found; then fail "an unmarked fence naming an org value passed"; fi
    echo "ok   planted docs values are caught outside Avunu profile example blocks"
    touch "$out"
  '';
}
