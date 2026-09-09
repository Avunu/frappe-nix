#!/usr/bin/env bash
# Regenerate src/frappe_runtime/ from a pinned Frappe commit.
#
#   scripts/sync-upstream.sh <frappe-checkout> [ref]
#
# The extracted source is committed rather than generated at build time, because
# `uv` builds this repo from a git source in a sandbox with no network: a build
# hook that fetched Frappe could not run there. Committing it also means
# `git diff` after a re-pin shows exactly what upstream changed.
#
# After running this, review the diff, run the tests, and update FRAPPE_REF in
# UPSTREAM if you moved the pin.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SRC_REPO=${1:?usage: sync-upstream.sh <frappe-checkout> [ref]}
REF=${2:-$(sed -n 's/^FRAPPE_REF=//p' "$HERE/UPSTREAM")}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

git -C "$SRC_REPO" archive "$REF" frappe/realtime frappe/asgi.py frappe/runner.py | tar -x -C "$WORK"

rm -rf "$HERE/src"
OVERLAY="$HERE/overlay/frappe_runtime" PATCHES="$HERE/patches" \
	"$HERE/scripts/extract.sh" "$WORK" "$HERE/src"

# The test suite comes along too, with the same rewrite plus one excision: the
# TestPublisherHelpers class exercises the publish helpers in upstream's
# frappe/realtime/__init__.py, which is the half we deliberately do not ship.
git -C "$SRC_REPO" show "$REF:frappe/tests/test_realtime_py.py" > "$WORK/test_in.py"
python3 "$HERE/scripts/rewrite_tests.py" "$WORK/test_in.py" "$HERE/tests/test_frappe_runtime.py"

echo "sync-upstream.sh: src/ and tests/ regenerated from $REF"
