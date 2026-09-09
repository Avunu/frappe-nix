#!/usr/bin/env bash
# Extract the Python runtime (realtime server + ASGI adapter + process runner)
# out of a Frappe source tree and rewrite it into a standalone `frappe_runtime`
# package.
#
#   extract.sh <frappe-source-dir> <output-dir>
#
# <frappe-source-dir> is the root of a Frappe checkout (the directory that
# contains `frappe/`). <output-dir> receives `frappe_runtime/`.
#
# The rewrite is deliberately per-symbol, not a blanket `frappe.realtime` sed.
# Three classes of occurrence must survive untouched:
#
#   * `from frappe.realtime import SOCKETIO_SECRET_KEY` — defined by Frappe's own
#     `frappe/realtime.py`, which we do not replace.
#   * `"/api/method/frappe.realtime.{get_user_info,has_permission}"` — whitelisted
#     endpoint URLs on the web side.
#   * `logging.getLogger("frappe.realtime")` and `"frappe.realtime.packets"` —
#     keeping these means upstream's logging documentation still applies.
#
# Run it again after re-pinning upstream; it is idempotent with respect to a
# clean output directory.
set -euo pipefail

SRC=${1:?usage: extract.sh <frappe-source-dir> <output-dir>}
OUT=${2:?usage: extract.sh <frappe-source-dir> <output-dir>}

# The realtime package minus __init__.py: that file is the *publish* half
# (publish_realtime, emit_via_redis, the room helpers, SOCKETIO_SECRET_KEY and
# the two whitelisted endpoints). Frappe already ships it as frappe/realtime.py
# and we leave it alone, so taking it would fork the framework for nothing.
REALTIME_MODULES=(auth bridge config context dispatch handlers registry server socket util)

PKG="$OUT/frappe_runtime"
mkdir -p "$PKG"

for m in "${REALTIME_MODULES[@]}"; do
	cp "$SRC/frappe/realtime/$m.py" "$PKG/$m.py"
done
cp "$SRC/frappe/asgi.py"   "$PKG/asgi.py"
cp "$SRC/frappe/runner.py" "$PKG/runner.py"

# Submodule imports: `from frappe.realtime.<mod> import ...` and the one
# `import frappe.realtime.handlers` in server.py.
mods=$(IFS='|'; echo "${REALTIME_MODULES[*]}")
sed -i -E \
	-e "s/\bfrom frappe\.realtime\.($mods)\b/from frappe_runtime.\1/g" \
	-e "s/\bimport frappe\.realtime\.($mods)\b/import frappe_runtime.\1/g" \
	"$PKG"/*.py

# handlers.py pulls the authoring surface from the package root. Anchored to the
# start of a line so the identical text inside registry.py's docstring — which is
# the app-author example, and stays true via the compat shim — is left alone.
sed -i -E \
	-e 's/^from frappe\.realtime import Socket, realtime$/from frappe_runtime import Socket, realtime/' \
	"$PKG"/*.py

# runner.py imports the ASGI application, which now lives beside it.
sed -i -E \
	-e 's/\bfrom frappe\.asgi import\b/from frappe_runtime.asgi import/' \
	"$PKG"/*.py

# Docstrings that name the module path as a runnable target.
sed -i -E \
	-e 's/\bpython -m frappe\.realtime\.server\b/python -m frappe_runtime.server/g' \
	-e 's/\buvicorn frappe\.asgi:application\b/uvicorn frappe_runtime.asgi:application/g' \
	"$PKG"/*.py

# Local patches, applied after the rewrite so they are written against the shape
# the package actually ships (see patches/ for what each one is for).
if [ -d "${PATCHES:-}" ]; then
	for p in "$PATCHES"/*.patch; do
		[ -e "$p" ] || continue
		echo "extract.sh: applying $(basename "$p")"
		patch -p1 -d "$OUT" --no-backup-if-mismatch < "$p"
	done
fi

# Hand-written files win over anything extracted.
if [ -d "${OVERLAY:-}" ]; then
	cp -r "$OVERLAY"/. "$PKG/"
fi

# Fail loudly if a rewrite was missed, rather than shipping a package that
# imports Frappe's own (absent) frappe.realtime submodules at runtime.
if grep -rnE '\b(from|import) frappe\.(realtime\.|asgi\b)' "$PKG"; then
	echo "extract.sh: unrewritten frappe.realtime/frappe.asgi import above" >&2
	exit 1
fi

# Conversely, the three classes listed at the top must still be present.
grep -q 'from frappe.realtime import SOCKETIO_SECRET_KEY' "$PKG/auth.py" \
	|| { echo "extract.sh: SOCKETIO_SECRET_KEY import was clobbered" >&2; exit 1; }
grep -q '/api/method/frappe.realtime.get_user_info' "$PKG/auth.py" \
	|| { echo "extract.sh: get_user_info endpoint URL was clobbered" >&2; exit 1; }

echo "extract.sh: wrote $(ls "$PKG"/*.py | wc -l) modules to $PKG"
