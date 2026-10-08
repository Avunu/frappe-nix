# frappe-demo: a repeatable demo site for the app (docs/app-standards/spec.md §5.4).
#
#   frappe-demo [--site S] [--fresh] [--erpnext-demo | --no-erpnext-demo] [--date YYYY-MM-DD]
#               [--seed N] [--no-up]
#
# Built by lib/standards/tools/frappe-demo.nix (writeShellApplication, so shellcheck runs
# over it), and run inside an opted-in app's dev shell, which supplies devenv,
# process-compose, provision-site, bench and mariadb-admin. The defaults are the `demo` block
# of marketplace/screenshots.ts when the app has one, else the demo module's parameters.
#
#   1. bring the bench up as frappe-test's stage 1 does (unless --no-up), and with --fresh,
#      or when the site is missing, provision it;
#   2-5. lib/demo/frappe_demo.py with the bench's interpreter: the setup wizard with fixed
#      arguments, erpnext's demo data (when asked for and installed), the app's
#      <app>.demo.setup(ctx), one commit, sites/<site>/demo.json.
#
# The bench is left up (frappe-test --down, or process-compose down, stops it). Under
# frappe-shots (§5.5), FRAPPE_NIX_SHOTS_PRELOAD and FRAPPE_NIX_SHOTS_FAKETIME put the demo
# script on libfaketime's clock, so what it makes is dated on the demo day.
#
# Exit codes: 0 ok; 1 the demo hook raised; 10 an environment error.

DEMO_PY="@FRAPPE_DEMO_PY@"

fail_env() {
  echo "frappe-demo: $*" >&2
  exit 10
}

SITE="${FRAPPE_SITE:-}"
FRESH=0
ERPNEXT=""
DATE=""
SEED=""
UP=1
while [ $# -gt 0 ]; do
  case "$1" in
    --site)
      [ $# -ge 2 ] || fail_env "--site needs a value"
      SITE="$2"
      shift 2
      ;;
    --fresh)
      FRESH=1
      shift
      ;;
    --erpnext-demo)
      ERPNEXT=1
      shift
      ;;
    --no-erpnext-demo)
      ERPNEXT=0
      shift
      ;;
    --date)
      [ $# -ge 2 ] || fail_env "--date needs a value"
      DATE="$2"
      shift 2
      ;;
    --seed)
      [ $# -ge 2 ] || fail_env "--seed needs a value"
      SEED="$2"
      shift 2
      ;;
    --no-up)
      UP=0
      shift
      ;;
    -h | --help)
      echo "usage: frappe-demo [--site S] [--fresh] [--erpnext-demo | --no-erpnext-demo] [--date YYYY-MM-DD] [--seed N] [--no-up]"
      echo "A repeatable demo site (docs/app-standards/screenshots.md)."
      exit 0
      ;;
    *) fail_env "unknown argument $1 (see --help)" ;;
  esac
done

[ -n "${FRAPPE_BENCH_ROOT:-}" ] || fail_env "run it in the app's dev shell (nix develop): FRAPPE_BENCH_ROOT is unset"
REPO="${DEVENV_ROOT:-$(git rev-parse --show-toplevel)}"
BENCH="$FRAPPE_BENCH_ROOT"
[ -n "$SITE" ] || fail_env "no site: pass --site or set FRAPPE_SITE"
APP="$("$BENCH/env/bin/python" -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["name"])' \
  "$REPO/pyproject.toml")" || fail_env "cannot read [project].name from pyproject.toml"

if [ "$(cd "$REPO" && frappe-nix config modules.demo)" != true ]; then
  echo "frappe-demo: notice: the demo module is off for this app; nothing to do" >&2
  exit 0
fi
DEMO_CFG="$(cd "$REPO" && frappe-nix config --json demo)" || fail_env "cannot read the demo module's configuration"

# The spec's demo block, when the app has a screenshot spec (Node strips its types).
SPEC_DEMO='{}'
if [ -f "$REPO/marketplace/screenshots.ts" ]; then
  SPEC_DEMO="$(node --input-type=module -e '
    const m = await import(process.argv[1]);
    process.stdout.write(JSON.stringify((m.default && m.default.demo) || {}));
  ' "$REPO/marketplace/screenshots.ts")" || fail_env "marketplace/screenshots.ts does not load"
fi
[ -n "$DATE" ] || DATE="$(jq -rn --argjson s "$SPEC_DEMO" --argjson d "$DEMO_CFG" '$s.date // $d.date')"
[ -n "$SEED" ] || SEED="$(jq -rn --argjson s "$SPEC_DEMO" --argjson d "$DEMO_CFG" '$s.seed // $d.seed')"
if [ -z "$ERPNEXT" ]; then
  ERPNEXT="$(jq -rn --argjson s "$SPEC_DEMO" --argjson d "$DEMO_CFG" \
    'if ($s | has("erpnextDemo")) then $s.erpnextDemo else $d["erpnext-demo"] end | if . then 1 else 0 end')"
fi
case "$DATE" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) fail_env "--date must be YYYY-MM-DD, not $DATE" ;;
esac
case "$SEED" in
  '' | *[!0-9]*) fail_env "--seed must be a whole number, not $SEED" ;;
esac
echo "frappe-demo: $SITE, date $DATE, seed $SEED, erpnext demo $([ "$ERPNEXT" = 1 ] && echo on || echo off)"

pc() { process-compose -U -u "${PC_SOCKET_PATH:?}" "$@"; }

# <command…> on frappe-shots' fake clock when it set one, else as it is. Without libfaketime's
# shared-memory clock, whose process-shared semaphore can deadlock a process.
faked() {
  if [ -n "${FRAPPE_NIX_SHOTS_FAKETIME:-}" ]; then
    LD_PRELOAD="$FRAPPE_NIX_SHOTS_PRELOAD${LD_PRELOAD:+:$LD_PRELOAD}" FAKETIME="$FRAPPE_NIX_SHOTS_FAKETIME" \
      FAKETIME_DONT_FAKE_MONOTONIC=1 FAKETIME_DISABLE_SHM=1 "$@"
  else
    "$@"
  fi
}

# ── 1. up, and the site ────────────────────────────────────────────────────────
if [ "$UP" = 1 ]; then
  if [ -n "${PC_SOCKET_PATH:-}" ] && pc process list > /dev/null 2>&1; then
    echo "frappe-demo: the bench is already up"
  else
    echo "frappe-demo: starting the bench (devenv up -D)"
    (cd "$REPO" && DEVENV_IN_DIRENV_SHELL=true PC_TUI_ENABLED=0 devenv up -D) || fail_env "devenv up -D failed"
  fi
  # Counted, not timed: under frappe-shots this shell runs on libfaketime's clock.
  tries=150
  until mariadb-admin --socket="${FRAPPE_DB_SOCKET:-}" --connect-timeout=3 ping > /dev/null 2>&1; do
    tries=$((tries - 1))
    [ "$tries" -gt 0 ] || fail_env "the database did not come up within 300 s"
    sleep 2
  done
  # And the web server, as frappe-test waits for it: a process stopped while it is still
  # pending (provisioning stops them) is not started again.
  port=""
  until port="$(jq -r '.webserver_port // empty' "$BENCH/sites/common_site_config.json" 2> /dev/null)" \
    && [ -n "$port" ] && curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$port/"; do
    tries=$((tries - 1))
    [ "$tries" -gt 0 ] || fail_env "the web server did not answer within 300 s"
    sleep 2
  done
fi

if [ "$FRESH" = 1 ] || [ ! -d "$BENCH/sites/$SITE" ]; then
  echo "frappe-demo: provisioning $SITE"
  # As frappe-test does: with the app processes stopped, so the apps-reconcile task the
  # runtime runs on start does not race provision-site's installs.
  stopped=()
  if [ -n "${PC_SOCKET_PATH:-}" ]; then
    for proc in runtime web worker scheduler socketio; do
      if pc process get "$proc" > /dev/null 2>&1; then
        pc process stop "$proc" > /dev/null 2>&1 || true
        stopped+=("$proc")
      fi
    done
  fi
  site_ok=1
  printf '\n' | FRAPPE_SITE="$SITE" provision-site "${FRAPPE_TEST_ADMIN_PASSWORD:-admin}" || site_ok=0
  for proc in "${stopped[@]}"; do
    pc process start "$proc" > /dev/null 2>&1 || true
  done
  [ "$site_ok" = 1 ] || fail_env "provision-site $SITE failed"
fi

# ── 2-5. the wizard, the demo data, the app's hook, demo.json ─────────────────────
flags=()
[ "$ERPNEXT" = 1 ] && flags+=(--erpnext-demo)
cd "$BENCH/sites" || fail_env "no $BENCH/sites"
faked "$BENCH/env/bin/python" "$DEMO_PY" --site "$SITE" --sites-path "$BENCH/sites" --app "$APP" \
  --date "$DATE" --seed "$SEED" --demo "$DEMO_CFG" --password "${FRAPPE_TEST_ADMIN_PASSWORD:-admin}" "${flags[@]}"
