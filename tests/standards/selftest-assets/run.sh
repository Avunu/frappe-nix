#!/usr/bin/env bash
# The N2 self-test (docs/app-standards/spec.md §7 "N2: assets"), run by
# .github/workflows/selftest-assets.yml and by hand the same way:
#
#   tests/standards/selftest-assets/run.sh stock        a plain `bench init` bench (no Nix, no
#                                                      NODE_OPTIONS): `bench get-app` of the
#                                                      spa-app fixture and `bench build --app
#                                                      spa_app` map spa.bundle.js and
#                                                      spa.bundle.css to the hashed files; a
#                                                      second build under frappe-nix's preload
#                                                      leaves assets.json byte-identical, and
#                                                      the preload alone registers them; with
#                                                      --hard-link, sites/assets/spa_app/dist/
#                                                      holds the hashed bundle
#   tests/standards/selftest-assets/run.sh builtbench   the spa-app fixture locked against this
#                                                      checkout and built as a builtBench
#                                                      (`nix build .#builtBench`): its
#                                                      assets.json maps spa.bundle.js to the
#                                                      hashed file; built again with no
#                                                      registration step of the app's own,
#                                                      the preload alone maps them
#
# Both work on copies of tests/fixtures/spa-app under $WORK (default
# $RUNNER_TEMP/standards-n2), with frappe at the revision frappe-nix's own dev
# env pins (dev/pyproject.toml). `stock` needs git, jq, node (24), yarn, a
# Python 3.14 (`PYTHON`, default python3.14), the `bench` CLI and what
# mysqlclient builds against; `builtbench` needs nix, git and jq. Both need
# the network.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FN="$(git -C "$HERE" rev-parse --show-toplevel)"
WORK="${WORK:-${RUNNER_TEMP:-/tmp}/standards-n2}"
FIXTURE="$FN/tests/fixtures/spa-app"
FRAPPE_REV="$(sed -n 's|.*frappe/archive/\([0-9a-f]\{40\}\)\.tar\.gz.*|\1|p' "$FN/dev/pyproject.toml")"
[ -n "$FRAPPE_REV" ] || {
  echo "::error::no frappe revision in dev/pyproject.toml" >&2
  exit 1
}

fail() {
  echo "::error::$*" >&2
  exit 1
}
ok() { echo "ok   $*"; }
group() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::group::$*"; else echo "── $* ──"; fi; }
endgroup() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::endgroup::"; fi; }
commit() { git -C "$1" add -A && git -C "$1" -c user.name=selftest -c user.email=selftest@localhost commit -q --allow-empty -m "$2"; }

# A copy of the fixture at <dir>, a git repository of its own.
prepare() {
  local dir="$1"
  rm -rf "$dir"
  mkdir -p "$(dirname "$dir")"
  cp -r "$FIXTURE" "$dir"
  chmod -R u+w "$dir"
  git -C "$dir" init -q -b develop
  printf '%s\n' /.devenv/ /.frappe-nix/ /.dev-dist/ /.direnv/ /result node_modules/ '*/public/portal/' >> "$dir/.git/info/exclude"
  commit "$dir" fixture
}

# <assets.json> <key>: the value, or "none".
key() { jq -r --arg k "$2" '.[$k] // "none"' "$1"; }

# <assets.json> <public dir>: spa.bundle.js and spa.bundle.css name hashed files under <public dir>/dist.
expect_bundles() {
  local assets="$1" public="$2" js css
  js="$(key "$assets" spa.bundle.js)"
  css="$(key "$assets" spa.bundle.css)"
  [[ "$js" =~ ^/assets/spa_app/dist/js/spa\.bundle\.[A-Za-z0-9_-]{6,}\.js$ ]] || fail "spa.bundle.js maps to $js ($assets)"
  [[ "$css" =~ ^/assets/spa_app/dist/css/spa\.bundle\.[A-Za-z0-9_-]{6,}\.css$ ]] || fail "spa.bundle.css maps to $css ($assets)"
  [ -f "$public/${js#/assets/spa_app/}" ] || fail "$js is not in $public"
  [ -f "$public/${css#/assets/spa_app/}" ] || fail "$css is not in $public"
  ok "spa.bundle.js -> $js, spa.bundle.css -> $css"
}

# ── stock ─────────────────────────────────────────────────────────────────────

cmd_stock() {
  # bench clones a local repository into apps/<its directory's name>.
  local bench="$WORK/bench" app="$WORK/src/spa_app" frappe="$WORK/src/frappe" python="${PYTHON:-python3.14}"
  local assets public sitepkg
  unset NODE_OPTIONS FRAPPE_NIX_ESBUILD_PRELOAD FRAPPE_BENCH_ROOT

  group "bench init (frappe $FRAPPE_REV)"
  rm -rf "$bench" "$frappe"
  mkdir -p "$WORK/src"
  git init -q "$frappe"
  git -C "$frappe" fetch -q --depth 1 https://github.com/frappe/frappe "$FRAPPE_REV"
  git -C "$frappe" checkout -q -b pinned FETCH_HEAD
  (cd "$WORK" && bench init --frappe-path "$frappe" --frappe-branch pinned --python "$(command -v "$python")" \
    --skip-redis-config-generation --skip-assets --no-procfile --no-backups bench)
  endgroup

  group "bench get-app spa-app; bench build --app spa_app"
  prepare "$app"
  (cd "$bench" && bench get-app --skip-assets "$app")
  [ -d "$bench/apps/spa_app" ] || fail "bench get-app did not create apps/spa_app"
  (cd "$bench" && bench build --app spa_app) 2>&1 | tee "$WORK/build-stock.log"
  endgroup
  assets="$bench/sites/assets/assets.json"
  public="$bench/apps/spa_app/spa_app/public"
  expect_bundles "$assets" "$public"
  grep -q 'vite-register: spa_app: spa.bundle.js -> ' "$WORK/build-stock.log" || fail "the app's own build did not register (no vite-register line in the log)"
  ok "stock bench: the app's own scripts/vite-register.mjs registered its bundles"
  cp "$assets" "$WORK/assets.stock.json"

  group "a second build, under frappe-nix's preload"
  # frappe-nix's frappe_nodebuild, grafted as lib/python.nix grafts it, carries
  # FRAPPE_NIX_ESBUILD_PRELOAD into frappe's build (it replaces NODE_OPTIONS).
  sitepkg="$("$bench/env/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  cp -r "$FN/lib/nodebuild/frappe_nodebuild" "$sitepkg/"
  printf 'import frappe_nodebuild; frappe_nodebuild.install()\n' > "$sitepkg/zzz-frappe-nodebuild.pth"
  (cd "$bench" && FRAPPE_NIX_ESBUILD_PRELOAD="$FN/lib/js/esbuild-preload.js" bench build --app spa_app) 2>&1 | tee "$WORK/build-preload.log"
  endgroup
  cmp "$WORK/assets.stock.json" "$assets" || {
    diff -u "$WORK/assets.stock.json" "$assets" || true
    fail "the build under the preload changed assets.json"
  }
  ok "a second bench build under frappe-nix's preload leaves assets.json byte-identical"

  group "the preload alone"
  # Without the app's own step and without its keys, the preload's registration
  # after `yarn build` is the only one: it must bring them back.
  cp "$bench/apps/spa_app/package.json" "$WORK/package.orig.json"
  jq '.scripts.build = "node build.mjs"' "$WORK/package.orig.json" > "$bench/apps/spa_app/package.json"
  jq -j --indent 4 'del(.["spa.bundle.js"], .["spa.bundle.css"])' "$assets" > "$WORK/assets.trimmed.json"
  cp "$WORK/assets.trimmed.json" "$assets"
  (cd "$bench" && FRAPPE_NIX_ESBUILD_PRELOAD="$FN/lib/js/esbuild-preload.js" bench build --app spa_app) 2>&1 | tee "$WORK/build-preload-only.log"
  cp "$WORK/package.orig.json" "$bench/apps/spa_app/package.json"
  endgroup
  expect_bundles "$assets" "$public"
  grep -q 'vite-register: spa_app: spa.bundle.js -> ' "$WORK/build-preload-only.log" || fail "the preload did not log its registration"
  ok "the preload registers them on its own"

  group "bench build --app spa_app --hard-link"
  rm -f "$sitepkg/zzz-frappe-nodebuild.pth"
  # No outputs before the build, so only the registration can copy them in.
  rm -rf "$public/dist" "$public/portal"
  (cd "$bench" && bench build --app spa_app --hard-link) 2>&1 | tee "$WORK/build-hard-link.log"
  endgroup
  [ -d "$bench/sites/assets/spa_app" ] && [ ! -L "$bench/sites/assets/spa_app" ] || fail "sites/assets/spa_app is not a real directory after --hard-link"
  expect_bundles "$assets" "$bench/sites/assets/spa_app"
  [ -f "$bench/sites/assets/spa_app/portal/index.html" ] || fail "portal/ was not copied into sites/assets/spa_app"
  ok "with --hard-link, sites/assets/spa_app/dist/ (and portal/) hold the hashed bundles"
}

# ── builtbench ────────────────────────────────────────────────────────────────

cmd_builtbench() {
  local app="$WORK/spa-app-nix" alone="$WORK/spa-app-nix-alone" out="$WORK/built" assets
  local nixflags=(--no-pure-eval --override-input frappe-nix "path:$FN")

  # PyPI as of the last change to what pins the resolution, as selftest-runtime does.
  if [ -z "${UV_EXCLUDE_NEWER:-}" ]; then
    UV_EXCLUDE_NEWER="$(git -C "$FN" log -1 --format=%cI -- dev/pyproject.toml templates/bench/pyproject.toml tests/fixtures/spa-app "$HERE")"
  fi
  export UV_EXCLUDE_NEWER
  echo "selftest-assets: resolving PyPI as of ${UV_EXCLUDE_NEWER:-now}"

  group "lock the spa-app fixture against this checkout"
  prepare "$app"
  (cd "$app" && nix flake lock --override-input frappe-nix "path:$FN" --override-input frappe "github:frappe/frappe/$FRAPPE_REV")
  (cd "$app" && nix run "${nixflags[@]}" .#relock)
  # frappe's ui/ frontend ships a yarn.lock that does not cover its own
  # package.json at this revision (an offline install misses dompurify); a
  # forced fallback lock fills the gap with upstream's pins kept, the remedy
  # frappe-nix's own error names. Nothing here is about frappe's frontend.
  (cd "$app" && nix run "${nixflags[@]}" .#relock -- --node-locks frappe/ui)
  commit "$app" locks
  endgroup

  group "nix build .#builtBench"
  (cd "$app" && nix build "${nixflags[@]}" -L .#builtBench -o "$out") 2>&1 | tee "$WORK/builtbench.log"
  endgroup
  assets="$out/bench/sites/assets/assets.json"
  [ -f "$assets" ] || fail "the builtBench has no sites/assets/assets.json"
  expect_bundles "$assets" "$out/bench/apps/spa_app/spa_app/public"
  ok "the spa-app builtBench maps spa.bundle.js and spa.bundle.css to the hashed files"

  # The Q7 path: an app-mode app that has not opted in has no registration
  # step of its own, so the preload (lib/js/esbuild-preload.js, from the store,
  # inside the Nix build) is the only registrar. Same locks; only the build
  # script and the managed file differ.
  group "nix build .#builtBench, the preload alone"
  rm -rf "$alone"
  cp -r "$app" "$alone"
  chmod -R u+w "$alone"
  jq -j --tab '.scripts.build = "node build.mjs"' "$alone/package.json" > "$WORK/package.alone.json"
  printf '\n' >> "$WORK/package.alone.json"
  mv "$WORK/package.alone.json" "$alone/package.json"
  rm "$alone/scripts/vite-register.mjs"
  commit "$alone" "the preload alone"
  (cd "$alone" && nix build "${nixflags[@]}" -L .#builtBench -o "$out-alone") 2>&1 | tee "$WORK/builtbench-alone.log"
  endgroup
  grep -q 'node scripts/vite-register.mjs' "$alone/package.json" && fail "the variant still runs the app's own step"
  assets="$out-alone/bench/sites/assets/assets.json"
  [ -f "$assets" ] || fail "the preload-alone builtBench has no sites/assets/assets.json"
  expect_bundles "$assets" "$out-alone/bench/apps/spa_app/spa_app/public"
  grep -q 'vite-register: spa_app: spa.bundle.js -> ' "$WORK/builtbench-alone.log" || fail "the preload did not log its registration in the Nix build"
  ok "with no step of the app's own, the preload registers them inside the Nix build"
}

case "${1:-}" in
  stock) cmd_stock ;;
  builtbench) cmd_builtbench ;;
  *)
    echo "usage: $0 stock|builtbench" >&2
    exit 2
    ;;
esac
