# frappe-shots: repeatable screenshots of the app's demo site (docs/app-standards/spec.md §5.5).
#
#   frappe-shots [--spec marketplace/screenshots.ts] [--only a,b] [--theme light|dark]
#                [--update | --check] [--reuse-site] [--out docs/screenshots] [--video <name>]
#
# Built by lib/standards/tools/frappe-shots.nix (writeShellApplication, so shellcheck runs
# over it), with chromium, libwebp, ffmpeg, Node 24, the fixed fonts and lib/shots/ (its
# node_modules built by Nix) on hand, and run inside an opted-in app's dev shell.
#
# Unless --reuse-site: the bench is restarted with libfaketime preloaded and FAKETIME set to
# the demo day at 09:00 (a start time: the clock advances, so timeouts still fire), and
# FAKETIME_DONT_RESET, so every process the bench starts shares that clock; frappe-demo
# --fresh then builds the demo site, and `bench build` runs outside the fake clock. The bench
# is stopped again at the end, so the dev shell is never left on a faked clock.
#
# Then lib/shots/runner.ts drives chromium over the site: --update (the default) writes the
# lossless WebPs and docs/screenshots/manifest.json; --check writes diff PNGs under
# .dev-dist/shots/diff/ only. Masters go to .dev-dist/shots/; --video <name> records one of
# the spec's flows to .dev-dist/shots/<name>.mp4 (never committed).
#
# Exit codes: 0 no differences, or updated; 1 differences under --check; 2 a spec error;
# 3 an environment error; 4 a capture error (navigation, timeout or a page error).

SHOTS_DIR="@SHOTS_DIR@"
# A literal $LIB, which the dynamic loader expands (lib/standards/tools/frappe-shots.nix).
# shellcheck disable=SC2016
FAKETIME_LIB='@FAKETIME_LIB@'

fail() {
  echo "frappe-shots: $2" >&2
  exit "$1"
}

SPEC=""
ONLY=""
THEME=""
MODE=update
REUSE=0
OUT=""
VIDEO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --spec | --only | --theme | --out | --video)
      [ $# -ge 2 ] || fail 2 "$1 needs a value"
      case "$1" in
        --spec) SPEC="$2" ;;
        --only) ONLY="$2" ;;
        --theme) THEME="$2" ;;
        --out) OUT="$2" ;;
        --video) VIDEO="$2" ;;
      esac
      shift 2
      ;;
    --update)
      MODE=update
      shift
      ;;
    --check)
      MODE=check
      shift
      ;;
    --reuse-site)
      REUSE=1
      shift
      ;;
    -h | --help)
      echo "usage: frappe-shots [--spec marketplace/screenshots.ts] [--only a,b] [--theme light|dark]"
      echo "                    [--update | --check] [--reuse-site] [--out docs/screenshots] [--video <name>]"
      echo "Repeatable screenshots of the demo site (docs/app-standards/screenshots.md)."
      exit 0
      ;;
    *) fail 2 "unknown argument $1 (see --help)" ;;
  esac
done

[ -n "${FRAPPE_BENCH_ROOT:-}" ] || fail 3 "run it in the app's dev shell (nix develop): FRAPPE_BENCH_ROOT is unset"
REPO="${DEVENV_ROOT:-$(git rev-parse --show-toplevel)}"
BENCH="$FRAPPE_BENCH_ROOT"
SITE="${FRAPPE_SITE:-}"
[ -n "$SITE" ] || fail 3 "FRAPPE_SITE is unset"
cd "$REPO" || fail 3 "no $REPO"

if [ "$(frappe-nix config modules.screenshots)" != true ]; then
  echo "frappe-shots: notice: the screenshots module is off for this app; nothing to do" >&2
  exit 0
fi
SPEC="$(realpath -m "${SPEC:-marketplace/screenshots.ts}")"
OUT="$(realpath -m "${OUT:-docs/screenshots}")"
[ -f "$SPEC" ] || fail 2 "no screenshot spec at $SPEC"
SHOTS_CFG="$(frappe-nix config --json screenshots)" || fail 2 "cannot read the screenshots module's configuration"
DEMO_CFG="$(frappe-nix config --json demo)" || fail 2 "cannot read the demo module's configuration"
SPEC_DEMO="$(node --input-type=module -e '
  const m = await import(process.argv[1]);
  process.stdout.write(JSON.stringify((m.default && m.default.demo) || {}));
' "$SPEC")" || fail 2 "$SPEC does not load"
DATE="$(jq -rn --argjson s "$SPEC_DEMO" --argjson d "$DEMO_CFG" '$s.date // $d.date')"
SEED="$(jq -rn --argjson s "$SPEC_DEMO" --argjson d "$DEMO_CFG" '$s.seed // $d.seed')"
ERPNEXT="$(jq -rn --argjson s "$SPEC_DEMO" --argjson d "$DEMO_CFG" \
  'if ($s | has("erpnextDemo")) then $s.erpnextDemo else $d["erpnext-demo"] end')"

pc() { process-compose -U -u "${PC_SOCKET_PATH:?}" "$@"; }
up() { [ -n "${PC_SOCKET_PATH:-}" ] && pc process list > /dev/null 2>&1; }

STARTED=0
if [ "$REUSE" = 0 ]; then
  if up; then
    echo "frappe-shots: stopping the bench, to start it again on the demo day's clock"
    pc down > /dev/null 2>&1 || true
    for _ in $(seq 60); do
      up || break
      sleep 1
    done
  fi
  export LD_PRELOAD="$FAKETIME_LIB${LD_PRELOAD:+:$LD_PRELOAD}"
  export FAKETIME="@$DATE 09:00:00" FAKETIME_DONT_RESET=1 FAKETIME_DONT_FAKE_MONOTONIC=1
  echo "frappe-shots: the bench runs from $FAKETIME (libfaketime)"
  demo_flags=(--fresh --site "$SITE" --date "$DATE" --seed "$SEED")
  if [ "$ERPNEXT" = true ]; then demo_flags+=(--erpnext-demo); else demo_flags+=(--no-erpnext-demo); fi
  STARTED=1
  trap 'up && pc down > /dev/null 2>&1 || true' EXIT
  frappe-demo "${demo_flags[@]}" || fail 3 "frappe-demo failed"
  (cd "$BENCH" && env -u LD_PRELOAD -u FAKETIME bench build) || fail 3 "bench build failed"
  unset LD_PRELOAD FAKETIME FAKETIME_DONT_RESET FAKETIME_DONT_FAKE_MONOTONIC
elif ! up; then
  fail 3 "--reuse-site, but the bench is not up (frappe-demo brings it up)"
fi

port="$(jq -r '.webserver_port // empty' "$BENCH/sites/common_site_config.json")"
[ -n "$port" ] || fail 3 "no webserver_port in $BENCH/sites/common_site_config.json"
deadline=$((SECONDS + 300))
until curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$port/"; do
  [ "$SECONDS" -lt "$deadline" ] || fail 3 "the web server did not answer on port $port within 300 s"
  sleep 2
done

args=(
  --spec "$SPEC" --base "http://$SITE:$port" --mode "$MODE" --out "$OUT" --masters "$REPO/.dev-dist/shots"
  --timezone "$(jq -r '.timezone' <<< "$SHOTS_CFG")" --locale "$(jq -r '.locale' <<< "$SHOTS_CFG")"
  --clock "$DATE" --password "${FRAPPE_TEST_ADMIN_PASSWORD:-admin}"
)
[ -n "$ONLY" ] && args+=(--only "$ONLY")
[ -n "$THEME" ] && args+=(--theme "$THEME")
[ -n "$VIDEO" ] && args+=(--video "$VIDEO")
set +e
node "$SHOTS_DIR/runner.ts" "${args[@]}"
rc=$?
set -e
if [ "$STARTED" = 1 ]; then
  pc down > /dev/null 2>&1 || true
  trap - EXIT
fi
exit "$rc"
