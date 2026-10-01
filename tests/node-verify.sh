#!/usr/bin/env bash
# Checks for `frappe-nix-node-verify` — finding and repairing a yarn cache and
# node_modules that yarn itself calls up-to-date. See lib/node-verify.py.
#
# Usage: node-verify.sh <path-to-frappe-nix-node-verify>
set -euo pipefail

TOOL="$1"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

fails=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no() { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
check() { local d=$1; shift; if "$@" > /dev/null 2>&1; then ok "$d"; else no "$d"; fi; }
check_not() { local d=$1; shift; if "$@" > /dev/null 2>&1; then no "$d"; else ok "$d"; fi; }
check_eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi; }
says() { grep -qF -- "$1" <<< "$OUT"; }
not_says() { ! grep -qF -- "$1" <<< "$OUT"; }
silent() { [ -z "$OUT" ]; }
run() { OUT="$("$TOOL" "$@" 2>&1)" && RC=0 || RC=$?; }

BENCH="$ROOT/bench"
CACHE="$ROOT/cache"
export YARN_CACHE_FOLDER="$CACHE" # yarn keeps its v6 bucket inside this

# ── fixtures ─────────────────────────────────────────────────────────────────
# A real-looking ELF, complete or cut short, little- or big-endian. The header
# and one LOAD program header are all the tool reads.
mkelf() { # <path> <declared size> <actual size> [be]
  python3 - "$@" <<'PY'
import struct, sys
path, declared, actual = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
e = ">" if len(sys.argv) > 4 else "<"
h = b"\x7fELF" + bytes([2, 2 if e == ">" else 1, 1]) + b"\0" * 9
h += struct.pack(e + "HHIQQQIHHHHHH", 3, 62, 1, 0, 64, 0, 0, 64, 56, 1, 64, 0, 0)
ph = struct.pack(e + "IIQQQQQQ", 1, 5, 0, 0, 0, declared, declared, 4096)
data = (h + ph).ljust(actual, b"\0")[:actual]
open(path, "wb").write(data)
PY
}
pkg() { # <node_modules dir> <name> <version> [deps-json]
  mkdir -p "$1/$2"
  printf '{"name":"%s","version":"%s","dependencies":%s}\n' "$2" "$3" "${4:-{\}}" > "$1/$2/package.json"
}
cache_entry() { # <name> <version> <package.json deps> <record deps>
  local d="$CACHE/v6/npm-$1-$2-0000000000000000000000000000000000000000-integrity/node_modules/$1"
  mkdir -p "$d"
  printf '{"name":"%s","version":"%s","dependencies":%s}\n' "$1" "$2" "$3" > "$d/package.json"
  printf '{"manifest":{"name":"%s","version":"%s","dependencies":%s}}\n' "$1" "$2" "$4" > "$d/.yarn-metadata.json"
}
entry_dir() { echo "$CACHE/v6/npm-$1-$2-0000000000000000000000000000000000000000-integrity"; }

build() {
  rm -rf "$BENCH" "$CACHE"
  mkdir -p "$BENCH/apps/alpha/desk" "$CACHE/v6"
  AL="$BENCH/apps/alpha"
  for nm in "$AL/node_modules" "$AL/desk/node_modules"; do
    mkdir -p "$nm"
    echo 'hash' > "$nm/.yarn-integrity"
  done
  echo 'fp' > "$AL/node_modules/.frappe-nix-installed"
  pkg "$AL/node_modules" left-pad 1.0.0
  pkg "$AL/node_modules" @scope/util 2.0.0
  mkelf "$AL/node_modules/left-pad/native.node" 40000 40000
  mkelf "$AL/node_modules/@scope/util/be.node" 40000 40000 be
  pkg "$AL/desk/node_modules" vite 8.0.0
  mkelf "$AL/desk/node_modules/vite/rolldown.node" 40000 40000
  cache_entry left-pad 1.0.0 '{}' '{}'
  cache_entry rehype-raw 7.0.0 '{"hast-util-raw":"^9.0.0"}' '{"hast-util-raw":"^9.0.0"}'
}

echo "── a sound bench and cache ──────────────────────────────────────"
build
run "$BENCH" alpha
check_eq "exits 0" 0 "$RC"
check "and says nothing" silent
check "a big-endian binary is not mistaken for a truncated one" test -f "$AL/node_modules/@scope/util/be.node"
check "it leaves the install markers alone" test -f "$AL/node_modules/.frappe-nix-installed" -a -f "$AL/desk/node_modules/.yarn-integrity"

echo "── an empty package directory (the sass case) ───────────────────"
build
mkdir -p "$AL/node_modules/sass"
run --full "$BENCH" alpha
check_eq "exits 0 after repairing" 0 "$RC"
check "names the package" says "apps/alpha: node_modules/sass: no package.json"
check "removes it" test ! -e "$AL/node_modules/sass"
check "…and the marker that says it is installed, so it is reinstalled" test ! -e "$AL/node_modules/.yarn-integrity"
check "…and the app's sentinel" test ! -e "$AL/node_modules/.frappe-nix-installed"
check "…and says what is reinstalled" says "reinstalling: alpha"
check "sound packages are untouched" test -f "$AL/node_modules/left-pad/package.json" -a -f "$AL/node_modules/left-pad/native.node"
check "a nested frontend that was sound keeps its marker" test -f "$AL/desk/node_modules/.yarn-integrity"

echo "── <app>/public/node_modules linked at the app's own ────────────"
build
mkdir -p "$AL/alpha/public" "$AL/node_modules/sass"
ln -s ../../node_modules "$AL/alpha/public/node_modules"
run --check "$BENCH" alpha
check "the damage is named once, at the app's own node_modules" test "$(grep -c 'no package.json' <<< "$OUT")" = 1
check "…not through the link" not_says "alpha/public/node_modules"
run --full "$BENCH" alpha
check "the repair goes through" test ! -e "$AL/node_modules/sass"
check "and leaves the link in place" test -L "$AL/alpha/public/node_modules"

echo "── a package extracted without its package.json ─────────────────"
build
mkdir -p "$AL/node_modules/html5-qrcode/cjs"
echo 'x' > "$AL/node_modules/html5-qrcode/cjs/index.js"
run --full "$BENCH" alpha
check "is found and removed" test ! -e "$AL/node_modules/html5-qrcode"

echo "── a truncated native binary in a nested frontend ───────────────"
build
mkelf "$AL/desk/node_modules/vite/rolldown.node" 40000 30000
run --full "$BENCH" alpha
check "names the binary and its sizes" says "rolldown.node is 30000 of 40000 bytes"
check "removes the package" test ! -e "$AL/desk/node_modules/vite"
check "…and that node_modules' marker" test ! -e "$AL/desk/node_modules/.yarn-integrity"
check "…and the app's sentinel, which is what triggers the reinstall" test ! -e "$AL/node_modules/.frappe-nix-installed"
check "the app's own node_modules is untouched" test -f "$AL/node_modules/left-pad/package.json"

echo "── a truncated binary in the cache ──────────────────────────────"
build
cache_entry esbuild 0.25.0 '{}' '{}'
mkelf "$(entry_dir esbuild 0.25.0)/node_modules/esbuild/x.node" 40000 12000
run --full "$BENCH" alpha
check "the entry is removed" test ! -e "$(entry_dir esbuild 0.25.0)"
check "the others are kept" test -d "$(entry_dir left-pad 1.0.0)"
check "nothing installed was affected, so nothing is reinstalled" not_says "reinstalling"

echo "── a cache record that lists no dependencies (the rehype-raw case) ─"
# The package installs, looks fine, and is missing what it needs: yarn resolved
# it from the record. Nothing in node_modules is wrong to look at.
build
cache_entry hast-util-raw 9.1.0 '{}' '{}'
pkg "$AL/node_modules" rehype-raw 7.0.0 '{"hast-util-raw":"^9.0.0"}'
pkg "$AL/desk/node_modules" rehype-raw 7.0.0 '{"hast-util-raw":"^9.0.0"}'
cache_entry rehype-raw 7.0.0 '{"hast-util-raw":"^9.0.0"}' '{}'
run --full "$BENCH" alpha
check "names the record" says "its record lists different dependencies than its package.json"
check "removes the cache entry" test ! -e "$(entry_dir rehype-raw 7.0.0)"
check "and every installed copy of that name and version" test ! -e "$AL/node_modules/rehype-raw" -a ! -e "$AL/desk/node_modules/rehype-raw"
check "says they were installed from it" says "installed from a damaged cache record"
check "a sound cache entry is kept" test -d "$(entry_dir hast-util-raw 9.1.0)"
check "and the app is reinstalled" says "reinstalling: alpha"

echo "── repairing is idempotent ──────────────────────────────────────"
run --full "$BENCH" alpha
check_eq "a second run exits 0" 0 "$RC"
check "and finds nothing" silent

echo "── --check reports and changes nothing ──────────────────────────"
build
mkdir -p "$AL/node_modules/sass"
cache_entry rehype-raw 7.0.0 '{"hast-util-raw":"^9.0.0"}' '{}'
before="$(find "$ROOT" | sort)"
run --check "$BENCH" alpha
check_eq "exits 1" 1 "$RC"
check "reports the package" says "node_modules/sass: no package.json"
check "…and the cache record" says "its record lists different dependencies"
check "says it did not repair" says "not repaired"
check_eq "the tree is exactly as it was" "$before" "$(find "$ROOT" | sort)"

echo "── a clean scan is remembered, and --full overrides it ──────────"
build
run "$BENCH" alpha
check "the first scan leaves a fingerprint" test -f "$CACHE/.frappe-nix-verified"
# Damage that changes neither the cache nor a marker: invisible to the cheap
# fingerprint, which is the point of it — and what --full is for.
mkdir -p "$AL/node_modules/sass"
run "$BENCH" alpha
check "an unchanged fingerprint skips the scan" test -d "$AL/node_modules/sass"
run --full "$BENCH" alpha
check "--full does not" test ! -e "$AL/node_modules/sass"
build
run "$BENCH" alpha
mkdir -p "$AL/node_modules/sass"
echo 'new' > "$AL/node_modules/.yarn-integrity"
run "$BENCH" alpha
check "an install (a moved .yarn-integrity) brings the scan back" test ! -e "$AL/node_modules/sass"
build
run "$BENCH" alpha
cache_entry esbuild 0.25.0 '{}' '{}'
mkelf "$(entry_dir esbuild 0.25.0)/node_modules/esbuild/x.node" 40000 12000
mkdir "$CACHE/v6/npm-new-entry"
run "$BENCH" alpha
check "…and so does a new cache entry" test ! -e "$(entry_dir esbuild 0.25.0)"

echo "── a repair leaves no fingerprint, so the next entry looks again ──"
build
mkdir -p "$AL/node_modules/sass"
rm -f "$CACHE/.frappe-nix-verified"
run "$BENCH" alpha
check "no fingerprint after a repair" test ! -e "$CACHE/.frappe-nix-verified"

echo "── lockfiles that differ from their commit ──────────────────────"
build
git -C "$AL" init -q
git -C "$AL" -c user.name=t -c user.email=t@t add -A
printf '# yarn lockfile v1\n' > "$AL/desk/yarn.lock"
git -C "$AL" add -A
git -C "$AL" -c user.name=t -c user.email=t@t commit -q -m base
printf '# rewritten\n' > "$AL/desk/yarn.lock"
run --lockfiles "$BENCH" alpha
check "is listed" says "apps/alpha/desk/yarn.lock differs from its commit"
check "and left as it is" grep -q rewritten "$AL/desk/yarn.lock"
check_eq "without failing the run" 0 "$RC"
run "$BENCH" alpha
check "not listed unless asked for" not_says "yarn.lock"

echo "── no cache yet, an app that is not there ───────────────────────"
rm -rf "$CACHE"
run "$BENCH" alpha ghost
check_eq "exits 0" 0 "$RC"
check "and says nothing" silent

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
