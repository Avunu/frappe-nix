#!/usr/bin/env bash
# The N5 self-test (docs/app-standards/spec.md §7 "N5: product tools"), run by
# .github/workflows/selftest-product.yml and by hand the same way:
#
#   run.sh listing        no Nix: frappe-nix-tools from this checkout (uv tool install), then
#                         `frappe-nix listing check` on the fixture with the example-org profile,
#                         L9 (pinned pilot, dependency apps on the bench) included and timed
#                         (a cold run under 25 minutes: no uv cache, and an import the bench
#                         lacks, so ImportCheck installs frappe from source); a planted manual
#                         commit fails L7; and
#                         `registry --dry-run --onboard` writes the apps/<app>.json entry that
#                         the pinned tools/add_release.py writes, byte for byte; and under
#                         main+tags, release-please's generic updater bumps __version__ and
#                         README.md together and `listing readme --check` still passes
#   run.sh shots          frappe-demo twice on one site leaves every record count unchanged;
#                         outside CI neither drops that site unasked (exit 64);
#                         frappe-shots from two fresh sites is pixel-identical (maxDiffRatio 0),
#                         the second made on a host in another timezone (TZ=Asia/Tokyo);
#                         --check against the fixture's committed shots exits 0; a 1-pixel CSS
#                         change exits 1 and writes a diff PNG
#   run.sh demo-erpnext   frappe-demo with erpnext installed: the company is demo.company-name
#
# Each works on a copy of tests/fixtures/standards-app under $WORK (default
# $RUNNER_TEMP/standards-n5) on the example-org profile (in-repo, every module on). The Nix
# suites lock it against this checkout (`--override-input frappe-nix path:<checkout>`), as
# selftest-runtime does. Needs git, jq, curl and python3, uv and node (npm) for `listing`, nix
# for the others, and the network.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FN="$(git -C "$HERE" rev-parse --show-toplevel)"
WORK="${WORK:-${RUNNER_TEMP:-/tmp}/standards-n5}"
SITE=standards-fixture.localhost
NIXFLAGS=(--no-pure-eval --override-input frappe-nix "path:$FN")
# The release-please whose generic updater the listing suite runs, fixed so a run is repeatable.
RELEASE_PLEASE=17.11.2

fail() {
  echo "::error::$*" >&2
  exit 1
}
ok() { echo "ok   $*"; }
group() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::group::$*"; else echo "── $* ──"; fi; }
endgroup() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::endgroup::"; fi; }
commit() { git -C "$1" add -A && git -C "$1" -c user.name=selftest -c user.email=selftest@localhost commit -q --allow-empty -m "$2"; }

# <expected exit> <log> <command...>: run it, keep its output, and insist on the exit.
expect() {
  local want="$1" log="$2" rc
  shift 2
  set +e
  "$@" > "$log" 2>&1
  rc=$?
  set -e
  if [ "$rc" != "$want" ]; then
    tail -n 120 "$log"
    fail "$*: exit $rc, expected $want (log: $log)"
  fi
}

# A copy of the fixture app at <dir> on the example-org profile, a git repository of its own
# whose generated directories are ignored without a .gitignore (sync owns that file).
prepare() {
  local dir="$1"
  rm -rf "$dir"
  mkdir -p "$(dirname "$dir")"
  cp -r "$FN/tests/fixtures/standards-app" "$dir"
  cp -r "$FN/tests/fixtures/profiles/example-org" "$dir/.standards-profile"
  chmod -R u+w "$dir"
  sed -i 's#^profile = "recommended"$#profile = "./.standards-profile"#' "$dir/pyproject.toml"
  git -C "$dir" init -q -b develop
  printf '%s\n' /.devenv/ /.frappe-nix/ /.dev-dist/ /.direnv/ /node_modules/ __pycache__/ '*.pyc' >> "$dir/.git/info/exclude"
  commit "$dir" fixture
}

# ── listing (no Nix) ─────────────────────────────────────────────────────────────────

cmd_listing() {
  local app="$WORK/listing" start elapsed
  group "frappe-nix-tools from this checkout, and the fixture on example-org"
  uv tool install --force --quiet "$FN/py/frappe_nix_tools"
  PATH="$(uv tool dir --bin):$PATH"
  export PATH
  prepare "$app"
  (cd "$app" && FRAPPE_NIX_OFFLINE=1 frappe-nix sync --write > /dev/null)
  commit "$app" synced
  # A dependency of the app's own, which the validation bench lacks: pilot's ImportCheck then
  # installs frappe from source and the app into a throwaway venv (mysqlclient is built
  # there), the expensive path the 25-minute budget and the job's apt packages are for.
  sed -i 's/^dependencies = \[\]$/dependencies = ["pyfiglet"]/' "$app/pyproject.toml"
  grep -q '^dependencies = \["pyfiglet"\]$' "$app/pyproject.toml" || fail "the fixture's [project] dependencies did not take pyfiglet"
  printf '%s\n' '"""A third-party import the bench lacks (selftest-product)."""' '' 'import pyfiglet' '' 'BANNER = pyfiglet.figlet_format' > "$app/standards_fixture/banner.py"
  commit "$app" "a dependency the bench lacks"
  endgroup

  group "frappe-nix listing check (L9 with the pinned pilot)"
  start=$SECONDS
  (cd "$app" && EIO_BACKEND=posix frappe-nix listing check --format github) | tee "$WORK/check.log" || fail "frappe-nix listing check fails on the fixture"
  elapsed=$((SECONDS - start))
  endgroup
  grep -q 'pilot ImportCheck passes' "$app/.dev-dist/marketplace/report.json" || fail "L9 did not run pilot's ImportCheck"
  jq -e '.status == "pass"' "$app/.dev-dist/marketplace/report.json" > /dev/null || fail "report.json does not say pass"
  [ "$elapsed" -lt 1500 ] || fail "the cold check took ${elapsed}s, over 25 minutes"
  ok "listing check passes on the fixture with example-org, L9 included, cold in ${elapsed}s (< 1500)"

  group "a planted manual commit fails L7"
  printf '\n\n@frappe.whitelist()\ndef planted() -> None:\n\tfrappe.db.commit()\n' >> "$app/standards_fixture/api.py"
  expect 1 "$WORK/l7.log" bash -c "cd '$app' && frappe-nix listing check --no-getapp"
  grep -q 'L7 error: standards_fixture/api.py: new frappe-manual-commit finding' "$WORK/l7.log" || fail "L7 does not name the planted finding: $(cat "$WORK/l7.log")"
  git -C "$app" checkout -q -- standards_fixture/api.py
  endgroup
  ok "a planted frappe.db.commit() is a new L7 finding (exit 1)"

  group "registry --dry-run --onboard against the pinned add_release.py"
  # publish needs to be on for the registry command; the fork is example-org's (fictitious, so
  # its branch is never found and only the new release is pending).
  sed -i 's#^registry-fork = "example-org/marketplace"$#registry-fork = "example-org/marketplace"\npublish = true#' "$app/.standards-profile/profile.toml"
  commit "$app" publish
  git init -q --bare "$WORK/origin.git"
  git -C "$app" remote add origin "$WORK/origin.git"
  git -C "$app" push -q origin HEAD:refs/heads/version-16
  git -C "$app" fetch -q origin
  git -C "$app" tag v16.0.0
  rm -rf "$WORK/ours"
  (cd "$app" && frappe-nix listing registry --dry-run --onboard --no-check --keep "$WORK/ours") | tee "$WORK/registry.log"
  grep -q 'frappe/marketplace main ← example-org/marketplace:example-org/standards_fixture' "$WORK/registry.log" || fail "the dry run does not name the fork and its branch"
  grep -q 'nothing was pushed' "$WORK/registry.log" || fail "the dry run did not stop before the push"
  marketplace="$(cd "$app" && frappe-nix pin-path marketplace)"
  rm -rf "$WORK/direct"
  git clone -q --depth 1 --branch main https://github.com/frappe/marketplace "$WORK/direct"
  printf '{\n  "name": "standards_fixture",\n  "releases": []\n}\n' > "$WORK/direct/apps/standards_fixture.json"
  APP=standards_fixture BRANCH=version-16 COMMIT="$(git -C "$app" rev-parse v16.0.0)" CHANNEL=stable \
    uv run --no-project --with packaging python "$marketplace/tools/add_release.py" --app-dir "$app" --registry "$WORK/direct"
  cmp "$WORK/ours/apps/standards_fixture.json" "$WORK/direct/apps/standards_fixture.json" ||
    fail "registry --dry-run --onboard's apps/standards_fixture.json differs from add_release.py's: $(diff "$WORK/ours/apps/standards_fixture.json" "$WORK/direct/apps/standards_fixture.json")"
  endgroup
  ok "registry --dry-run --onboard writes the apps/<app>.json entry the pinned add_release.py writes, byte for byte"

  group "release-please's generic updater on README.md under main+tags, then readme --check"
  # What a release PR does to the extra files (release-please's own updater, not a stand-in):
  # __version__ and the README's install line move together, and the blocks still check.
  local rp="$WORK/rp-app"
  prepare "$rp"
  sed -i 's/^\[releases\]$/[releases]\nbranching = "main+tags"/' "$rp/.standards-profile/profile.toml"
  grep -q '^branching = "main+tags"$' "$rp/.standards-profile/profile.toml" || fail "the profile copy did not take main+tags"
  (cd "$rp" && FRAPPE_NIX_OFFLINE=1 frappe-nix sync --write > /dev/null)
  commit "$rp" synced
  grep -q -- '--branch v16.0.0' "$rp/README.md" || fail "the synced README names no --branch v16.0.0"
  rm -rf "$WORK/release-please"
  npm install --silent --no-audit --no-fund --prefix "$WORK/release-please" "release-please@$RELEASE_PLEASE"
  node "$HERE/release-please-bump.cjs" "$WORK/release-please/node_modules/release-please" "$rp" 16.1.0 | tee "$WORK/release-please.log"
  grep -q '^__version__ = "16.1.0"$' "$rp/standards_fixture/__init__.py" || fail "release-please did not bump __version__"
  grep -q -- '--branch v16.1.0' "$rp/README.md" || fail "release-please did not bump the README's install line"
  expect 0 "$WORK/readme-after-release.log" bash -c "cd '$rp' && frappe-nix listing readme --check"
  endgroup
  ok "after release-please's generic updater bumps __version__ and README.md together, readme --check passes"
}

# ── the Nix suites ───────────────────────────────────────────────────────────────────

# frappe at the revision frappe-nix's own dev env pins, and erpnext at the matching release,
# as selftest-runtime does (a branch tip can break the resolution).
FRAPPE_REV="$(sed -n 's|.*frappe/archive/\([0-9a-f]\{40\}\)\.tar\.gz.*|\1|p' "$FN/dev/pyproject.toml")"
ERPNEXT_REV=7474d9e786277383de1242ab882f16856d17a9c9 # v16.50.0, beside frappe's

lock() {
  local dir="$1" pins=(--override-input frappe-nix "path:$FN" --override-input frappe "github:frappe/frappe/$FRAPPE_REV")
  if grep -q 'erpnext = {' "$dir/flake.nix"; then
    pins+=(--override-input erpnext "github:frappe/erpnext/$ERPNEXT_REV")
  fi
  if [ -z "${UV_EXCLUDE_NEWER:-}" ]; then
    UV_EXCLUDE_NEWER="$(git -C "$FN" log -1 --format=%cI -- dev/pyproject.toml templates/bench/pyproject.toml tests/fixtures/standards-app "$HERE")"
    export UV_EXCLUDE_NEWER
  fi
  (cd "$dir" && nix flake lock "${pins[@]}")
  (cd "$dir" && nix run "${NIXFLAGS[@]}" .#relock)
  commit "$dir" locks
}

# Run a command in <dir>'s dev shell (not CI mode: frappe-shots builds assets).
dev() {
  local dir="$1"
  shift
  (cd "$dir" && nix develop "${NIXFLAGS[@]}" -c "$@")
}

# dev, as a desk runs it: no CI, and no terminal to answer a prompt.
off_ci() {
  (
    unset CI
    dev "$@" < /dev/null
  )
}

# dev, on a host in another timezone than the runner's UTC.
in_tokyo() {
  (
    export TZ=Asia/Tokyo TZDIR=/usr/share/zoneinfo
    dev "$@"
  )
}

# Record counts per doctype on the site, as JSON: what a second frappe-demo must not change.
# Logs and the scheduler's own records move on their own, so they are left out.
counts() {
  cat > "$WORK/counts.py" << 'PY'
import json
import sys

import frappe

frappe.init(site=sys.argv[1], sites_path=sys.argv[2])
frappe.connect()
out = {}
skip = ("Version", "Route History", "Prepared Report", "Deleted Document", "Communication")
for d in frappe.get_all("DocType", filters={"istable": 0, "issingle": 0, "is_virtual": 0}, pluck="name"):
    if d.endswith("Log") or d in skip:
        continue
    try:
        out[d] = frappe.db.count(d)
    except Exception:
        pass
print(json.dumps(out, sort_keys=True))
PY
  # shellcheck disable=SC2016 # expanded by the dev shell's bash, not this one
  dev "$1" bash -c 'cd "$FRAPPE_BENCH_ROOT/sites" && "$FRAPPE_BENCH_ROOT/env/bin/python" "$0" "$1" "$FRAPPE_BENCH_ROOT/sites"' "$WORK/counts.py" "$SITE"
}

cmd_shots() {
  local app="$WORK/shots"
  group "prepare the fixture on example-org"
  prepare "$app"
  lock "$app"
  endgroup

  group "frappe-demo twice"
  dev "$app" frappe-demo --fresh --site "$SITE" 2>&1 | tee "$WORK/demo-1.log"
  counts "$app" | tail -n 1 > "$WORK/counts-1.json"
  dev "$app" frappe-demo --site "$SITE" 2>&1 | tee "$WORK/demo-2.log"
  counts "$app" | tail -n 1 > "$WORK/counts-2.json"
  endgroup
  jq -e '.["Fixture Note"] == 3' "$WORK/counts-1.json" > /dev/null || fail "the demo hook did not make the three Fixture Notes: $(cat "$WORK/counts-1.json")"
  cmp "$WORK/counts-1.json" "$WORK/counts-2.json" ||
    fail "a second frappe-demo changed record counts: $(diff <(jq . "$WORK/counts-1.json") <(jq . "$WORK/counts-2.json"))"
  [ -f "$app/.frappe-nix/bench/sites/$SITE/demo.json" ] || fail "frappe-demo wrote no sites/$SITE/demo.json"
  ok "frappe-demo twice on one site leaves every record count unchanged"

  group "outside CI, neither drops the existing site unasked"
  expect 64 "$WORK/guard-demo.log" off_ci "$app" frappe-demo --fresh --site "$SITE"
  grep -q 'pass --recreate-site' "$WORK/guard-demo.log" || fail "frappe-demo --fresh's refusal does not name --recreate-site: $(cat "$WORK/guard-demo.log")"
  expect 64 "$WORK/guard-shots.log" off_ci "$app" frappe-shots --check
  grep -q 'pass --reuse-site' "$WORK/guard-shots.log" || fail "frappe-shots' refusal does not name --reuse-site: $(cat "$WORK/guard-shots.log")"
  counts "$app" | tail -n 1 > "$WORK/counts-3.json"
  cmp "$WORK/counts-1.json" "$WORK/counts-3.json" || fail "a refused run changed the site"
  endgroup
  ok "outside CI, frappe-demo --fresh and frappe-shots refuse to drop an existing site (exit 64)"

  # A spec that tolerates no differing pixel, and one with a 1-pixel layout change.
  sed 's/readme: "hero",/readme: "hero",\n\t\t\tmaxDiffRatio: 0,/' "$app/marketplace/screenshots.ts" > "$app/marketplace/screenshots-strict.ts"
  sed 's/^\tdemo: /\tcss: ".page-head { margin-top: 1px !important; }",\n\tdemo: /' "$app/marketplace/screenshots.ts" > "$app/marketplace/screenshots-moved.ts"
  grep -q 'maxDiffRatio: 0' "$app/marketplace/screenshots-strict.ts" || fail "the strict spec variant is wrong"
  grep -q 'margin-top: 1px' "$app/marketplace/screenshots-moved.ts" || fail "the moved spec variant is wrong"

  group "frappe-shots from a fresh site"
  rm -rf "$WORK/shots-1"
  dev "$app" frappe-shots --update --out "$WORK/shots-1" 2>&1 | tee "$WORK/shots-1.log"
  endgroup
  for f in fixture-note-list-light.webp fixture-note-list-dark.webp manifest.json; do
    [ -f "$WORK/shots-1/$f" ] || fail "frappe-shots --update wrote no $f"
  done
  ok "frappe-shots --update writes both themes and the manifest"

  # The second on a host nine hours east of the first: the demo's clock is fixed in the
  # screenshots' timezone, not the host's (TZDIR, so Nix's glibc finds the zone at all).
  group "frappe-shots from a second fresh site in another host timezone, against the first, with maxDiffRatio 0"
  expect 0 "$WORK/shots-2.log" in_tokyo "$app" frappe-shots --check --spec marketplace/screenshots-strict.ts --out "$WORK/shots-1"
  endgroup
  ok "two runs from two fresh sites, on hosts in different timezones, are pixel-identical (maxDiffRatio 0)"

  group "--check against the fixture's committed shots"
  if [ -f "$app/docs/screenshots/manifest.json" ]; then
    expect 0 "$WORK/shots-committed.log" dev "$app" frappe-shots --check
    ok "--check against the committed fixture shots exits 0"
  else
    echo "::warning::the fixture has no committed docs/screenshots yet; the selftest-product artifact holds a run's"
  fi
  endgroup

  group "a 1-pixel CSS change"
  rm -rf "$app/.dev-dist/shots/diff"
  expect 1 "$WORK/shots-moved.log" dev "$app" frappe-shots --check --spec marketplace/screenshots-moved.ts --out "$WORK/shots-1"
  ls "$app"/.dev-dist/shots/diff/*.png > /dev/null 2>&1 || fail "the 1-pixel change wrote no diff PNG"
  endgroup
  ok "a 1-pixel CSS change exits 1 and writes a diff PNG"
}

cmd_demo_erpnext() {
  local app="$WORK/erpnext"
  group "prepare the fixture with erpnext, on example-org"
  prepare "$app"
  python3 - "$app" << 'PY'
import sys
from pathlib import Path
app = Path(sys.argv[1])
flake = app / "flake.nix"
s = flake.read_text()
s = s.replace(
	'    frappe = {\n      url = "github:frappe/frappe/version-16";\n      flake = false;\n    };\n',
	'    frappe = {\n      url = "github:frappe/frappe/version-16";\n      flake = false;\n    };\n'
	'    erpnext = {\n      url = "github:frappe/erpnext/version-16";\n      flake = false;\n    };\n',
)
s = s.replace("siblings = [ ];", 'siblings = [\n                  {\n                    name = "erpnext";\n                    src = inputs.erpnext;\n                  }\n                ];')
assert "inputs.erpnext" in s, s
flake.write_text(s)
py = app / "pyproject.toml"
s = py.read_text().replace("siblings = []", 'siblings = ["erpnext"]')
s = s.replace('frappe = ">=16.0.0,<17.0.0"', 'frappe = ">=16.0.0,<17.0.0"\nerpnext = ">=16.0.0,<17.0.0"')
py.write_text(s)
hooks = app / "standards_fixture" / "hooks.py"
hooks.write_text(hooks.read_text().replace("required_apps = []", 'required_apps = ["erpnext"]'))
PY
  commit "$app" "erpnext sibling"
  lock "$app"
  endgroup

  group "frappe-demo with erpnext"
  dev "$app" frappe-demo --fresh --site "$SITE" 2>&1 | tee "$WORK/demo-erpnext.log"
  endgroup
  company="$(dev "$app" bash -c "cd \"\$FRAPPE_BENCH_ROOT\" && bench --site $SITE execute frappe.db.get_value --args '[\"Company\", {\"company_name\": \"Example Demo\"}, \"abbr\"]'" | tail -n 1)"
  [ "$company" = ED ] || [ "$company" = '"ED"' ] || fail "no company Example Demo (ED) after frappe-demo with erpnext: $company"
  ok "with erpnext installed, the company is demo.company-name (Example Demo, ED)"
}

mkdir -p "$WORK"
case "${1:-}" in
  listing) cmd_listing ;;
  shots) cmd_shots ;;
  demo-erpnext) cmd_demo_erpnext ;;
  *)
    echo "usage: $0 listing|shots|demo-erpnext" >&2
    exit 2
    ;;
esac
