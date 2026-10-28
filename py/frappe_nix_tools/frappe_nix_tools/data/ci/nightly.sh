# frappe-nix's nightly integration script (docs/app-standards/spec.md §4.6), run by
# app-nightly.yml's `integration` job inside the app's dev shell:
#   nix develop --no-pure-eval -c bash -c 'bash "$(frappe-nix data-path ci/nightly.sh)"'
# Every failure is kept: the tests, the build and each nightly suite all run, and the
# script exits non-zero if any of them failed.
set -uo pipefail
rc=0
export FRAPPE_ADMIN_PASSWORD="${FRAPPE_NIX_TEST_ADMIN_PASSWORD:-admin}"
export FRAPPE_NIX_ARTIFACT_DIR="$PWD/.dev-dist/nightly"; mkdir -p "$FRAPPE_NIX_ARTIFACT_DIR"
frappe-test --ci --keep-up || rc=$?
bench build || rc=1
port="$(jq .webserver_port "$FRAPPE_BENCH_ROOT/sites/common_site_config.json")"
export FRAPPE_SITE_URL="http://127.0.0.1:${port}"
export CF_SITE_URL="$FRAPPE_SITE_URL" CF_SHOT_DIR="$FRAPPE_NIX_ARTIFACT_DIR"   # the names carbon's suites read
while IFS= read -r s; do
  bash -c "$s" || rc=1
done < <(frappe-nix config nightly-suites)
frappe-test --down
exit "$rc"
