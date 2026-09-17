# The one invariant-check-and-heal script for sites/assets/assets.json — used
# by the devenv-restart task, the fswatch-driven bench-watch process, and the
# manual `assets-reassert` script (see modules/devenv.nix, `assetsReassertCheck`).
# A single rendering so a fix proven in one path cannot drift from the others.
#
# Some Frappe apps shadow Frappe's own bundle keys in assets.json with their
# own build tooling. Frappe's esbuild pipeline writes that file two different
# ways for the same logical bundle — write_assets_json, keyed by the *source*
# entry file's basename, runs on every `bench build` and every `bench watch`
# rebuild; update_assets_json_from_built_assets, keyed by the *built* file's
# basename with its content hash stripped, runs only on
# `bench build --using-cached`. An app whose hooks.py names the second key can
# have a `bench watch` rebuild touch only the first, while esbuild's own dist
# cleanup deletes the file the second key still points at — and Frappe's own
# resolution (bundled_asset() in frappe/utils/jinja_globals.py) is a bare dict
# lookup with no existence check, so a stale key 404s with no server-side
# signal. See https://github.com/Avunu/frappe-nix/issues/32.
#
# This names no app: it is the generic half (does any assets.json value point
# at a file that doesn't exist?) plus a caller-supplied list of `bench execute`
# targets to run when that's true. Whatever app is doing the shadowing points
# its own healing entry point at this from its own flake, via
# `assets.reassert.hooks`.
{
  lib,
  pkgs,
  # Absolute path to the real bench CLI, e.g. devPythonEnv/bin/bench — not the
  # interactive `bench` wrapper: this also runs from a task/process context
  # with no wrapper on PATH.
  benchBin,
  # redis-cli invocation, pre-built with -s/-p as appropriate (mirrors
  # modules/devenv.nix's own `redisCli`). Clearing the assets_json cache key
  # is defensive — frappe-nix's own dev benches set developer_mode = 1, which
  # bypasses that cache entirely — so a failure here must never block healing.
  redisCli,
  # The one site `bench execute` runs against.
  site,
  # Dotted `bench execute` targets, run in order, each time the check fails.
  hooks,
}:

# NB every early exit below is a bare `exit 0` — correct when this string is
# the whole body of a task or script, but fatal to a long-running watcher if
# spliced into a `while read` loop. Callers that invoke this repeatedly (the
# fswatch process) must wrap it: `( <this> ) || true`.
''
  _assets="$FRAPPE_BENCH_ROOT/sites/assets/assets.json"
  [ -f "$_assets" ] || exit 0

  # Not atomic on esbuild's side (fs.promises.writeFile truncates in place, no
  # temp-file-and-rename), so a read can land mid-write. Retried rather than
  # trusted on the first parse failure: a torn read is transient, not an
  # invariant failure, and treating it as one would fire the hooks on every
  # rebuild instead of only when something is actually missing.
  _tries=0
  _json=""
  while [ "$_tries" -lt 5 ]; do
    _json="$(${pkgs.jq}/bin/jq -c '.' "$_assets" 2>/dev/null)" && break
    _json=""
    _tries=$((_tries + 1))
    ${pkgs.coreutils}/bin/sleep 0.2
  done
  if [ -z "$_json" ]; then
    echo "frappe-nix: sites/assets/assets.json did not parse after $_tries retries — will recheck later" >&2
    exit 0
  fi

  # Values are absolute web paths already prefixed /assets/..., and
  # sites/assets/ is the webroot backing /assets/... .
  _missing=""
  while IFS= read -r _val; do
    [ -z "$_val" ] && continue
    [ -f "$FRAPPE_BENCH_ROOT/sites$_val" ] || _missing="$_missing $_val"
  done < <(printf '%s' "$_json" | ${pkgs.jq}/bin/jq -r '.[]' | sort -u)

  [ -n "$_missing" ] || exit 0

  echo "frappe-nix: sites/assets/assets.json names missing file(s):$_missing"
  ${redisCli} del assets_json > /dev/null 2>&1 || true

  ${lib.concatMapStringsSep "\n" (hook: ''
    echo "frappe-nix: bench --site ${lib.escapeShellArg site} execute ${hook}"
    ${benchBin} --site ${lib.escapeShellArg site} execute ${lib.escapeShellArg hook} \
      || echo "frappe-nix: ${hook} failed — see above" >&2
  '') hooks}
''
