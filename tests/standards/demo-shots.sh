#!/usr/bin/env bash
# frappe-demo's and frappe-shots' command lines, against a dev shell of stubs
# (tests/standards/product.nix, standards-demo-shots). What needs a real bench
# and chromium is .github/workflows/selftest-product.yml; this is every flag,
# exit code and branch that does not.
#
#   DEMO=<frappe-demo> SHOTS=<frappe-shots> FIXTURE=<tests/fixtures/standards-app> \
#     REAL_PY=<python3> bash tests/standards/demo-shots.sh
#
# The stubs log every call to $STATE/log: devenv (up; STUB_DEVENV_RC), process-compose
# (`process list` answers while $STATE/pc-up exists; `process get` answers for the names
# in $STATE/procs), mariadb-admin, provision-site (STUB_PROVISION_RC), `bench` on PATH
# (`bench build`; STUB_BUILD_RC) and the bench's python, which runs `-c` with the real
# interpreter and logs anything else (the demo script; STUB_DEMO_RC). The web server is a
# real one on loopback. frappe-shots' runner is the real one: `--only` with a name the
# spec does not have, or a bad `--theme`, stops it with exit 2 before chromium starts.
# The stubs' bodies are single-quoted: they expand when the stubs run.
# shellcheck disable=SC2016
set -euo pipefail

: "${DEMO:?}" "${SHOTS:?}" "${FIXTURE:?}" "${REAL_PY:?}"
fail() {
  echo "FAIL $*" >&2
  exit 1
}
ok() { echo "ok   $*"; }

root="$(mktemp -d)"
cd "$root"
export HOME="$root" STATE="$root/state" FRAPPE_NIX_OFFLINE=1 GIT_CONFIG_GLOBAL=/dev/null
unset CI
mkdir -p "$STATE" stubs bench/env/bin bench/sites
port=18766
"$REAL_PY" -m http.server --bind 127.0.0.1 "$port" > /dev/null 2>&1 &
server=$!
trap 'kill "$server" 2> /dev/null || true' EXIT
for _ in $(seq 50); do
  "$REAL_PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/', timeout=1)" 2> /dev/null && break
  sleep 0.2
done
config_port() { echo "{\"webserver_port\": $port}" > bench/sites/common_site_config.json; }
config_port

stub() { # <path> <body>
  printf '#!%s\necho "%s $*" >> "$STATE/log"\n%s\n' "$BASH" "$(basename "$1")" "$2" > "$1"
  chmod +x "$1"
}
stub stubs/devenv 'rc="${STUB_DEVENV_RC:-0}"; [ "$rc" != 0 ] || touch "$STATE/pc-up"; exit "$rc"'
stub stubs/process-compose 'case "$*" in
  *" process list") [ -e "$STATE/pc-up" ] ;;
  *" process get "*) grep -qx "${@: -1}" "$STATE/procs" 2> /dev/null ;;
  *" down") rm -f "$STATE/pc-up" ;;
esac'
stub stubs/mariadb-admin 'exit 0'
stub stubs/provision-site 'echo "provision-site created $FRAPPE_SITE" >> "$STATE/log"
rc="${STUB_PROVISION_RC:-0}"; [ "$rc" != 0 ] || mkdir -p "$FRAPPE_BENCH_ROOT/sites/$FRAPPE_SITE"; exit "$rc"'
stub stubs/bench 'exit "${STUB_BUILD_RC:-0}"'
# The bench's interpreter: -c is real (frappe-demo reads [project].name with it), the demo
# script is logged with the clock it would run on.
cat > bench/env/bin/python << EOF
#!$BASH
if [ "\$1" = -c ]; then exec "$REAL_PY" "\$@"; fi
echo "python \$* FAKETIME=\${FAKETIME:-} TZ=\${TZ:-} PRELOAD=\${LD_PRELOAD:-}" >> "\$STATE/log"
exit "\${STUB_DEMO_RC:-0}"
EOF
chmod +x bench/env/bin/python

cp -r "$FIXTURE" repo
chmod -R u+w repo
git -C repo init -q
git -C repo add -A
git -C repo -c user.name=t -c user.email=t@t commit -qm fixture
cp repo/pyproject.toml pyproject.orig
# The fixture's pyproject.toml with <tables> appended (both modules are off in it).
pyproject() { # <tables>
  { cat pyproject.orig; printf '\n%s\n' "$1"; } > repo/pyproject.toml
}
ON=$'[tool.frappe-nix.demo]\nenable = true\n\n[tool.frappe-nix.screenshots]\nenable = true'

export PATH="$root/stubs:$PATH" FRAPPE_BENCH_ROOT="$root/bench" DEVENV_ROOT="$root/repo" \
  FRAPPE_SITE=dev.localhost PC_SOCKET_PATH="$root/pc.sock" FRAPPE_DB_SOCKET=/nowhere

reset() { # a clean slate: bench down, no site, no processes, an empty log
  rm -rf bench/sites/dev.localhost bench/sites/other.localhost "$STATE/pc-up" "$STATE/procs"
  : > "$STATE/log"
}
run() { # <expected exit> <label> <command…>
  local want="$1" label="$2" rc=0
  shift 2
  : > "$STATE/log"
  timeout 120 "$@" > run.log 2>&1 < /dev/null || rc=$?
  [ "$rc" = "$want" ] || {
    cat run.log "$STATE/log"
    fail "$label: exit $rc, want $want"
  }
}
# run, with a terminal on stdin and stderr that answers <answer> to the prompt.
run_tty() { # <expected exit> <label> <answer> <command…>
  local want="$1" label="$2" answer="$3" rc=0
  shift 3
  : > "$STATE/log"
  printf '%s\n' "$answer" | timeout 120 script -qec "$(printf '%q ' "$@")" /dev/null > run.log 2>&1 || rc=$?
  [ "$rc" = "$want" ] || {
    cat run.log "$STATE/log"
    fail "$label: exit $rc, want $want"
  }
}
has() { grep -q -- "$1" run.log || fail "$2: no '$1' in: $(cat run.log)"; }
logged() { grep -q -- "$1" "$STATE/log" || fail "$2: no '$1' in the calls: $(cat "$STATE/log")"; }
not_logged() { ! grep -q -- "$1" "$STATE/log" || fail "$2: '$1' in the calls: $(cat "$STATE/log")"; }

# ── frappe-demo ──────────────────────────────────────────────────────────────
reset
run 0 "frappe-demo --help" "$DEMO" --help
has '--recreate-site' "--help"
run 10 "an unknown flag" "$DEMO" --bogus
has 'unknown argument --bogus' "an unknown flag"
for flag in --site --date --seed; do
  run 10 "$flag without a value" "$DEMO" "$flag"
  has "$flag needs a value" "$flag without a value"
done
run 10 "no dev shell" env -u FRAPPE_BENCH_ROOT "$DEMO"
has 'FRAPPE_BENCH_ROOT is unset' "no dev shell"
run 10 "no site" env FRAPPE_SITE= "$DEMO"
has 'no site' "no site"
ok "frappe-demo: --help is 0; an unknown flag, a missing value, no dev shell and no site are 10"

printf '[project]\nversion = "1"\n' > repo/pyproject.toml
run 10 "no [project].name" "$DEMO"
has 'cannot read \[project\].name' "no [project].name"
pyproject ""
run 0 "the demo module off" "$DEMO"
has 'the demo module is off' "the demo module off"
[ ! -s "$STATE/log" ] || fail "the demo module off still ran: $(cat "$STATE/log")"
(cd repo && env -u DEVENV_ROOT "$DEMO" > ../run.log 2>&1) || fail "frappe-demo without DEVENV_ROOT, in the repository: $(cat run.log)"
has 'the demo module is off' "without DEVENV_ROOT"
pyproject $'[tool.frappe-nix.demo]\nenable = true\nno-such-key = 1'
run 10 "an invalid demo configuration" "$DEMO"
has 'cannot read the' "an invalid configuration"
not_logged 'devenv' "an invalid configuration"
ok "frappe-demo: no [project].name is 10; the module off is 0 doing nothing (also found by git without DEVENV_ROOT); a bad configuration is 10"

pyproject "$ON"
run 10 "a bad --date" "$DEMO" --date 2026-1-5
has '--date must be YYYY-MM-DD, not 2026-1-5' "a bad --date"
run 10 "a bad --seed" "$DEMO" --seed x1
has '--seed must be a whole number, not x1' "a bad --seed"
ok "frappe-demo: a malformed --date or --seed is 10"

# Up, provision, run: the bench is down and the site does not exist.
reset
run 0 "frappe-demo on a bench that is down" "$DEMO"
has 'starting the bench (devenv up -D)' "bench down"
has 'dev.localhost, date 2026-01-15, seed 1, erpnext demo off' "the summary line"
logged '^devenv up -D' "bench down"
logged '^provision-site created dev.localhost' "no site"
logged "^python [^ ]*frappe_demo.py --site dev.localhost --sites-path $root/bench/sites --app standards_fixture --date 2026-01-15 --seed 1 --demo {.*} --password admin FAKETIME= " "the demo script"
not_logged ' down' "the bench is left up"
not_logged 'erpnext-demo FAKETIME' "erpnext off"
ok "frappe-demo: brings the bench up, provisions a missing site, runs the demo script with the spec's date and seed, leaves the bench up"

# Up already, the site exists: neither started nor provisioned again.
run 0 "frappe-demo again" "$DEMO"
has 'the bench is already up' "bench up"
not_logged '^devenv' "bench up"
not_logged '^provision-site' "the site exists"
logged '^python ' "the site exists"
ok "frappe-demo: a bench that is up is not started; an existing site is not provisioned without --fresh"

# --fresh on an existing site: refused outside CI without a terminal, nothing started.
rm -f "$STATE/pc-up"
run 64 "--fresh on an existing site, no terminal" "$DEMO" --fresh
has 'pass --recreate-site' "--fresh refusal"
[ ! -s "$STATE/log" ] || fail "a refused --fresh started something: $(cat "$STATE/log")"
run_tty 64 "--fresh, answered no" n "$DEMO" --fresh
has 'kept dev.localhost' "--fresh answered no"
has 'Recreate it? \[y/N\]' "the prompt"
not_logged '^provision-site' "answered no"
run_tty 0 "--fresh, answered yes" y "$DEMO" --fresh
logged '^provision-site created dev.localhost' "answered yes"
run 0 "--fresh in CI" env CI=true "$DEMO" --fresh
logged '^provision-site created dev.localhost' "--fresh in CI"
run 0 "--recreate-site" "$DEMO" --recreate-site
logged '^provision-site created dev.localhost' "--recreate-site"
ok "frappe-demo: --fresh on an existing site is 64 without a terminal and on a no, recreates it on a yes, in CI and with --recreate-site"

# --no-up, --site, the date, seed and erpnext sources.
reset
run 0 "--no-up --site other.localhost" "$DEMO" --no-up --site other.localhost --date 2025-02-03 --seed 9
not_logged '^devenv' "--no-up"
not_logged 'process list' "--no-up"
logged '^provision-site created other.localhost' "--site"
logged '^python .* --site other.localhost .* --date 2025-02-03 --seed 9 ' "flags win"
mv repo/marketplace/screenshots.ts screenshots.ts.away
pyproject $'[tool.frappe-nix.demo]\nenable = true\ndate = "2024-04-04"\nseed = 7\nerpnext-demo = true'
run 0 "the module's demo block" "$DEMO" --no-up
logged '^python .* --date 2024-04-04 --seed 7 .* --erpnext-demo FAKETIME' "the module's demo block"
has 'erpnext demo on' "erpnext on"
run 0 "--no-erpnext-demo" "$DEMO" --no-up --no-erpnext-demo
not_logged 'erpnext-demo FAKETIME' "--no-erpnext-demo"
mv screenshots.ts.away repo/marketplace/screenshots.ts
sed -i 's/demo: { date: "2026-01-15", seed: 1 }/demo: { date: "2026-01-15", seed: 1, erpnextDemo: false }/' repo/marketplace/screenshots.ts
run 0 "the spec's erpnextDemo over the module's" "$DEMO" --no-up
logged '^python .* --date 2026-01-15 --seed 1 ' "the spec's demo block wins"
not_logged 'erpnext-demo FAKETIME' "the spec's erpnextDemo: false"
run 0 "--erpnext-demo" "$DEMO" --no-up --erpnext-demo
logged 'erpnext-demo FAKETIME' "--erpnext-demo"
git -C repo checkout -q marketplace/screenshots.ts
echo 'export default {' > repo/marketplace/broken.ts
mv repo/marketplace/screenshots.ts screenshots.ts.away
mv repo/marketplace/broken.ts repo/marketplace/screenshots.ts
run 10 "a spec that does not load" "$DEMO" --no-up
has 'marketplace/screenshots.ts does not load' "a broken spec"
mv screenshots.ts.away repo/marketplace/screenshots.ts
pyproject "$ON"
ok "frappe-demo: --no-up starts nothing; --site, --date, --seed and --[no-]erpnext-demo win over the spec's demo block, which wins over the module's; a spec that does not load is 10"

# The bench does not start; the site cannot be provisioned; the demo hook raises.
reset
STUB_DEVENV_RC=1 run 10 "devenv up fails" env STUB_DEVENV_RC=1 "$DEMO"
has 'devenv up -D failed' "devenv up fails"
reset
printf 'web\nworker\n' > "$STATE/procs"
run 10 "provision-site fails" env STUB_PROVISION_RC=1 "$DEMO" --no-up
has 'provision-site dev.localhost failed' "provision-site fails"
logged 'process stop web' "processes stopped"
logged 'process stop worker' "processes stopped"
logged 'process start web' "processes started again"
not_logged 'process stop scheduler' "only present processes"
not_logged '^python ' "no demo after a failed provision"
reset
mkdir -p bench/sites/dev.localhost
run 1 "the demo hook raises" env STUB_DEMO_RC=1 "$DEMO" --no-up
ok "frappe-demo: devenv up or provision-site failing is 10 (the app processes restarted); the demo script's exit is frappe-demo's"

# frappe-shots' clock.
run 0 "on frappe-shots' clock" env FRAPPE_NIX_SHOTS_FAKETIME="@2026-01-15 14:00:00" FRAPPE_NIX_SHOTS_PRELOAD=/nowhere/libfaketime.so.1 "$DEMO" --no-up
logged 'FAKETIME=@2026-01-15 14:00:00 TZ=UTC PRELOAD=/nowhere/libfaketime.so.1' "the fake clock"
ok "frappe-demo: FRAPPE_NIX_SHOTS_FAKETIME puts the demo script on libfaketime's clock, in UTC"

# ── frappe-shots ─────────────────────────────────────────────────────────────
reset
pyproject ""
run 0 "frappe-shots --help" "$SHOTS" --help
has '--reuse-site' "--help"
run 2 "an unknown flag" "$SHOTS" --bogus
has 'unknown argument --bogus' "an unknown flag"
for flag in --spec --only --theme --out --video; do
  run 2 "$flag without a value" "$SHOTS" "$flag"
  has "$flag needs a value" "$flag without a value"
done
run 3 "no dev shell" env -u FRAPPE_BENCH_ROOT "$SHOTS"
has 'FRAPPE_BENCH_ROOT is unset' "no dev shell"
run 3 "no site" env FRAPPE_SITE= "$SHOTS"
has 'FRAPPE_SITE is unset' "no site"
run 2 "--reuse-site with --recreate-site" "$SHOTS" --reuse-site --recreate-site
has 'exclude each other' "--reuse-site with --recreate-site"
run 0 "the screenshots module off" "$SHOTS"
has 'the screenshots module is off' "the module off"
[ ! -s "$STATE/log" ] || fail "the module off still ran: $(cat "$STATE/log")"
pyproject $'[tool.frappe-nix.screenshots]\nenable = true\nno-such-key = 1'
run 2 "an invalid configuration" "$SHOTS"
has 'cannot read the' "an invalid configuration"
ok "frappe-shots: --help is 0; a usage error is 2; no dev shell or site is 3; the module off is 0 doing nothing; a bad configuration is 2"

pyproject "$ON"
run 2 "no spec" "$SHOTS" --spec marketplace/nowhere.ts
has "no screenshot spec at $root/repo/marketplace/nowhere.ts" "no spec"
echo 'export default {' > repo/marketplace/broken.ts
run 2 "a spec that does not load" "$SHOTS" --spec marketplace/broken.ts
has 'broken.ts does not load' "a broken spec"
rm repo/marketplace/broken.ts
ok "frappe-shots: a missing spec or one that does not load is 2"

# The existing site: refused outside CI.
mkdir -p bench/sites/dev.localhost
run 64 "an existing site, no terminal" "$SHOTS" --check
has 'pass --reuse-site' "the refusal"
[ ! -s "$STATE/log" ] || fail "a refused run started something: $(cat "$STATE/log")"
run_tty 64 "an existing site, answered no" n "$SHOTS" --check
has 'kept dev.localhost' "answered no"
has 'Recreate it? \[y/N\]' "the prompt"
[ ! -s "$STATE/log" ] || fail "answered no started something: $(cat "$STATE/log")"
ok "frappe-shots: outside CI an existing site is 64 without a terminal and on a no, and nothing starts"

# The whole run on a bench that is down: the demo on the fake clock, bench build, the
# runner (stopped with 2 by an --only the spec does not have), the bench stopped again.
reset
run 2 "a fresh run" "$SHOTS" --only nosuch
has 'the demo data is made from 2026-01-15 09:00 America/New_York (2026-01-15 14:00:00 UTC, libfaketime)' "the demo clock"
logged '^devenv up -D' "a fresh run"
logged '^provision-site created dev.localhost' "a fresh run"
logged "^python .* --site dev.localhost .* --date 2026-01-15 --seed 1 --demo .* --password admin FAKETIME=@2026-01-15 14:00:00 TZ=UTC PRELOAD=.*/libfaketime.so.1" "the demo on the fake clock"
logged '^bench build' "bench build"
has '--only names no shot: nosuch' "the runner's --only"
logged ' down$' "the bench this run started is stopped"
[ ! -e "$STATE/pc-up" ] || fail "the bench is still up"
ok "frappe-shots: on a bench that is down, frappe-demo --fresh on the fake clock, bench build, the runner's exit, the bench stopped"

# Answered yes; the bench already up stays up; the spec's and the module's erpnext choice.
reset
mkdir -p bench/sites/dev.localhost
touch "$STATE/pc-up"
run_tty 2 "answered yes" y "$SHOTS" --theme purple
logged '^provision-site created dev.localhost' "answered yes"
has '--theme is light or dark, not purple' "--theme reaches the runner"
not_logged ' down$' "a bench that was up is left up"
not_logged 'erpnext-demo FAKETIME' "erpnext off"
pyproject $'[tool.frappe-nix.demo]\nenable = true\nerpnext-demo = true\n\n[tool.frappe-nix.screenshots]\nenable = true\ntimezone = "Asia/Tokyo"'
run 2 "in CI, erpnext and Tokyo" env CI=true "$SHOTS" --only nosuch
logged '^provision-site created dev.localhost' "in CI"
logged 'erpnext-demo FAKETIME=@2026-01-15 00:00:00' "the module's erpnext-demo and timezone"
has '2026-01-15 09:00 Asia/Tokyo (2026-01-15 00:00:00 UTC' "the module's timezone"
run 2 "--recreate-site" "$SHOTS" --recreate-site --only nosuch
logged '^provision-site created dev.localhost' "--recreate-site"
pyproject "$ON"
ok "frappe-shots: a yes recreates the site; CI and --recreate-site need no answer; a bench that was up stays up; the module's erpnext-demo and timezone reach the demo"

# --reuse-site: no demo, no build; the bench must be up.
reset
mkdir -p bench/sites/dev.localhost
run 3 "--reuse-site, bench down" "$SHOTS" --reuse-site
has 'the bench is not up' "--reuse-site, bench down"
touch "$STATE/pc-up"
run 2 "--reuse-site" "$SHOTS" --reuse-site --only nosuch
not_logged '^provision-site\|^python \|^bench build\|^devenv' "--reuse-site"
echo '{}' > bench/sites/common_site_config.json
run 3 "no webserver_port" "$SHOTS" --reuse-site
has 'no webserver_port in' "no webserver_port"
config_port
ok "frappe-shots: --reuse-site makes no demo and needs the bench up (3); no webserver_port is 3"

# frappe-demo or bench build failing: 3, and the bench this run started is stopped.
reset
run 3 "frappe-demo fails" env STUB_DEMO_RC=1 "$SHOTS"
has 'frappe-demo failed' "frappe-demo fails"
logged ' down$' "stopped after frappe-demo failed"
reset
run 3 "bench build fails" env STUB_BUILD_RC=1 "$SHOTS"
has 'bench build failed' "bench build fails"
logged ' down$' "stopped after bench build failed"
ok "frappe-shots: frappe-demo or bench build failing is 3, and the bench it started is stopped"
