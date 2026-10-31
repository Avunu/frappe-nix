#!/usr/bin/env bash
# lib/port-lease.py, the dev shell's runtime port leases, over real sockets on
# loopback: twenty benches started at once on one preferred port all get their
# own; a holder killed with SIGKILL frees its port; strict refuses and names the
# holder; a port something outside frappe-nix has is skipped; ports.json, the
# rendered nginx.conf, `ready`, `show` and `who`; frappe-nix-lease-ready (the
# probes' check, lib/port-lease-ready.sh) agrees with `ready` at every step, and
# the runtime's lock admits one holder and makes a killed one's ports.json stale.
# Run once per backend: the abstract-socket claims (Linux) and the flock
# fallback (darwin), forced here.
#
#   port-lease.sh <python> <port-lease.py> <base port> <frappe-nix-lease-ready>
set -euo pipefail

PY="$1"
LEASE="$2"
BASE="$3"
READY="$4"
WORK="$(mktemp -d)"
pids=()
cleanup() {
  for p in "${pids[@]}"; do kill -9 "$p" 2> /dev/null || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT

fail() {
  echo "FAIL $*" >&2
  exit 1
}
ok() { echo "ok   $*"; }
lease() { "$PY" "$LEASE" "$@"; }
# is_ready <n>: bench n's lease is up, by `ready` and by the probes' check, which
# have to agree; is_down <n>: it is not, by either.
is_ready() {
  lease ready --runtime "$WORK/rt$1" || fail "ready: bench $1's lease is not up"
  "$READY" "$WORK/rt$1" || fail "frappe-nix-lease-ready: bench $1's lease is not up, though ready says it is"
}
is_down() {
  ! lease ready --runtime "$WORK/rt$1" || fail "ready: bench $1's lease is up"
  ! "$READY" "$WORK/rt$1" || fail "frappe-nix-lease-ready: bench $1's lease is up, though ready says it is not"
}

# hold <n> <args…>: start bench n's holder in the background; its pid in pids.
hold() {
  local n="$1"
  shift
  mkdir -p "$WORK/rt$n"
  "$PY" "$LEASE" hold --runtime "$WORK/rt$n" --bench-id "bench/$n" --name bench --root "$WORK/root$n" "$@" \
    > "$WORK/hold$n.log" 2>&1 &
  pids+=("$!")
}
# up <n>: wait until bench n's lease is ready.
up() {
  for _ in $(seq 100); do
    lease ready --runtime "$WORK/rt$1" && return 0
    sleep 0.1
  done
  cat "$WORK/hold$1.log" >&2
  fail "bench $1's lease never became ready"
}
port_of() { jq -r ".${2:-web}" "$WORK/rt$1/frappe-nix/ports.json"; }

run() {
  local mode="$1"
  echo "── $mode ──"
  pids=()

  # Twenty at once, one preferred port.
  printf 'listen 127.0.0.1:@WEB_PORT@; # @BENCH_ID@\n' > "$WORK/nginx.conf"
  for n in $(seq 1 20); do
    hold "$n" --render "$WORK/nginx.conf=$WORK/rt$n/frappe-nix/nginx.conf" \
      "web/nginx=127.0.0.1:$BASE" "mailpit_smtp/mailpit-smtp=127.0.0.1:$((BASE + 200))"
  done
  for n in $(seq 1 20); do up "$n"; done
  for n in $(seq 1 20); do is_ready "$n"; done
  local webs smtps
  webs="$(for n in $(seq 1 20); do port_of "$n"; done | sort -n)"
  smtps="$(for n in $(seq 1 20); do port_of "$n" mailpit_smtp; done | sort -n)"
  [ "$(uniq <<< "$webs" | wc -l)" = 20 ] || fail "$mode: twenty holders share web ports: $(tr '\n' ' ' <<< "$webs")"
  [ "$(uniq <<< "$smtps" | wc -l)" = 20 ] || fail "$mode: twenty holders share smtp ports"
  [ "$(head -n1 <<< "$webs")" = "$BASE" ] || fail "$mode: nobody got the preferred port $BASE"
  [ "$(tail -n1 <<< "$webs")" -lt "$((BASE + 100))" ] || fail "$mode: a port beyond the span"
  ok "$mode: twenty holders at once on one preferred port get twenty ports, $BASE up"

  grep -qx "listen 127.0.0.1:$(port_of 7); # bench/7" "$WORK/rt7/frappe-nix/nginx.conf" \
    || fail "$mode: nginx.conf not rendered: $(cat "$WORK/rt7/frappe-nix/nginx.conf")"
  [ "$(jq -r .bench_id "$WORK/rt7/frappe-nix/ports.json")" = bench/7 ] || fail "$mode: ports.json has no bench_id"
  [ "$(jq -r .preferred.web "$WORK/rt7/frappe-nix/ports.json")" = "$BASE" ] || fail "$mode: no preferred port in ports.json"
  [ "$(jq -r .identity "$WORK/rt7/frappe-nix/ports.json")" = true ] || fail "$mode: a rendered nginx.conf is not recorded as answering the identity"
  ok "$mode: nginx.conf rendered with the leased port and the bench id; ports.json records both"

  # Who holds the preferred port.
  local first=""
  for n in $(seq 1 20); do [ "$(port_of "$n")" = "$BASE" ] && first="$n"; done
  lease who "$BASE" | jq -e ".bench_id == \"bench/$first\"" > /dev/null || fail "$mode: who $BASE: $(lease who "$BASE")"
  [ "$(lease show --runtime "$WORK/rt$first" web)" = "$BASE" ] || fail "$mode: show web"
  ok "$mode: who names the bench holding $BASE, and show prints its port"

  # Strict on a held port: refused, naming the holder.
  local rc=0
  lease hold --runtime "$WORK/rtx" --bench-id bench/x --name strict --root /x --strict --offset 5 \
    "web/nginx=127.0.0.1:$BASE" > "$WORK/strict.log" 2>&1 || rc=$?
  [ "$rc" = 1 ] || fail "$mode: strict on a held port exits $rc"
  grep -q "cannot start strict: already in use: nginx=127.0.0.1:$BASE" "$WORK/strict.log" \
    && grep -q "held by bench bench/$first" "$WORK/strict.log" \
    && grep -q FRAPPE_NIX_PORT_OFFSET "$WORK/strict.log" || fail "$mode: strict message: $(cat "$WORK/strict.log")"
  [ ! -e "$WORK/rtx/frappe-nix/ports.json" ] || fail "$mode: a refused hold wrote ports.json"
  ok "$mode: strict refuses a held port, naming the holder and FRAPPE_NIX_PORT_OFFSET"
  # Without --offset (bench mode, which ignores the variable): ports.offset.
  rc=0
  lease hold --runtime "$WORK/rtx" --bench-id bench/x --name strict --root /x --strict \
    "web/nginx=127.0.0.1:$BASE" > "$WORK/strict-bench.log" 2>&1 || rc=$?
  [ "$rc" = 1 ] && grep -q "change ports.offset (or ports.base) in flake.nix" "$WORK/strict-bench.log" \
    && ! grep -q FRAPPE_NIX_PORT_OFFSET "$WORK/strict-bench.log" || fail "$mode: strict without --offset: $(cat "$WORK/strict-bench.log")"
  ok "$mode: without --offset, strict points at ports.offset, not FRAPPE_NIX_PORT_OFFSET"

  # One holder per runtime: a second on a held one is refused, naming the
  # first, and leaves its ports.json alone.
  rc=0
  lease hold --runtime "$WORK/rt$first" --bench-id bench/again --name again --root /again \
    "web/nginx=127.0.0.1:$((BASE + 300))" > "$WORK/again.log" 2>&1 || rc=$?
  [ "$rc" = 1 ] && grep -q "cannot start again: its ports are already leased by another frappe-ports (pid ${pids[$((first - 1))]})" "$WORK/again.log" \
    || fail "$mode: a second holder on one runtime: $rc $(cat "$WORK/again.log")"
  [ "$(jq -r .bench_id "$WORK/rt$first/frappe-nix/ports.json")" = "bench/$first" ] || fail "$mode: a refused holder touched ports.json"
  is_ready "$first"
  ok "$mode: a second holder on a runtime already held is refused, and the first's lease stands"

  # SIGKILL frees the port: no stale claim, and ready no longer passes.
  local victim="${pids[$((first - 1))]}"
  kill -9 "$victim"
  wait "$victim" 2> /dev/null || true
  [ -e "$WORK/rt$first/frappe-nix/ports.json" ] || fail "$mode: SIGKILL somehow removed ports.json"
  is_down "$first"
  # The next holder on that runtime drops the stale file and writes its own.
  hold "$first" --strict "web/nginx=127.0.0.1:$BASE"
  up "$first"
  is_ready "$first"
  [ "$(jq -r .pid "$WORK/rt$first/frappe-nix/ports.json")" = "${pids[-1]}" ] || fail "$mode: the new holder kept a killed one's ports.json"
  kill -9 "${pids[-1]}"
  wait "${pids[-1]}" 2> /dev/null || true
  is_down "$first"
  hold k --strict "web/nginx=127.0.0.1:$BASE"
  up k
  [ "$(port_of k)" = "$BASE" ] || fail "$mode: the port a killed holder had is not free"
  [ "$(jq -r .identity "$WORK/rtk/frappe-nix/ports.json")" = false ] || fail "$mode: no rendered config, yet ports.json claims the identity"
  ok "$mode: kill -9 frees the port at once, and a killed holder's ports.json is not ready"

  # SIGHUP is outlived; SIGTERM: ports.json goes with the holder, and show then
  # exits 3 with the preferred ports.
  local last="${pids[-1]}"
  kill -HUP "$last"
  sleep 0.3
  lease ready --runtime "$WORK/rtk" || fail "$mode: a holder does not outlive SIGHUP"
  kill -TERM "$last"
  wait "$last" 2> /dev/null || true
  [ ! -e "$WORK/rtk/frappe-nix/ports.json" ] || fail "$mode: SIGTERM left ports.json"
  is_down k
  rc=0
  lease show --runtime "$WORK/rtk" --preferred "web=$BASE" web > "$WORK/show.out" 2> /dev/null || rc=$?
  [ "$rc" = 3 ] && [ "$(cat "$WORK/show.out")" = "$BASE" ] || fail "$mode: show on a bench that is down: $rc $(cat "$WORK/show.out")"
  ok "$mode: SIGHUP is outlived; SIGTERM removes ports.json, and show then exits 3 with the preferred port"

  for p in "${pids[@]}"; do kill -TERM "$p" 2> /dev/null || true; done
  for p in "${pids[@]}"; do wait "$p" 2> /dev/null || true; done
  pids=()

  # Something outside frappe-nix on the preferred port: moved past; strict names it.
  "$PY" -c 'import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(); time.sleep(600)' "$BASE" &
  pids+=("$!")
  for _ in $(seq 50); do (exec 3<> "/dev/tcp/127.0.0.1/$BASE") 2> /dev/null && break; sleep 0.1; done
  hold o "web/nginx=127.0.0.1:$BASE"
  up o
  [ "$(port_of o)" != "$BASE" ] || fail "$mode: leased a port a plain listener has"
  [ "$(jq -r .moved.web "$WORK/rto/frappe-nix/ports.json")" = "$BASE" ] || fail "$mode: moved not recorded"
  grep -q "nginx moved to 127.0.0.1:$(port_of o) ($BASE is in use by a process outside frappe-nix)" "$WORK/holdo.log" \
    || fail "$mode: no move message: $(cat "$WORK/holdo.log")"
  rc=0
  lease hold --runtime "$WORK/rty" --bench-id bench/y --name strict --root /y --strict \
    "web/nginx=127.0.0.1:$BASE" > "$WORK/strict2.log" 2>&1 || rc=$?
  [ "$rc" = 1 ] && grep -q "is in use by a process outside frappe-nix" "$WORK/strict2.log" || fail "$mode: strict vs a plain listener: $(cat "$WORK/strict2.log")"
  grep -q "in use by a process outside frappe-nix" <(lease who "$BASE") || fail "$mode: who on a plain listener"
  ok "$mode: a port held outside frappe-nix is moved past, and named as such by strict and who"

  # --dry-run holds nothing, not even the runtime's lock; --pin makes one port strict.
  lease hold --runtime "$WORK/rtd" --bench-id bench/d --name dry --root /d --dry-run "web=127.0.0.1:$((BASE + 50))"
  [ ! -e "$WORK/rtd/frappe-nix/ports.json" ] && [ ! -e "$WORK/rtd/frappe-nix/ports.lock" ] \
    && [ "$(lease who "$((BASE + 50))")" = "$((BASE + 50)): free" ] || fail "$mode: --dry-run held something"
  # The check never makes the lock file it tests, and a ports.json with no
  # holder is not a lease.
  mkdir -p "$WORK/rtd/frappe-nix"
  echo '{"pid": 1, "web": 1}' > "$WORK/rtd/frappe-nix/ports.json"
  is_down d
  [ ! -e "$WORK/rtd/frappe-nix/ports.lock" ] || fail "$mode: frappe-nix-lease-ready created ports.lock"
  rc=0
  lease hold --runtime "$WORK/rtp" --bench-id bench/p --name pin --root /p --pin web --base "$BASE" \
    "web/nginx=127.0.0.1:$BASE" > "$WORK/pin.log" 2>&1 || rc=$?
  [ "$rc" = 1 ] && grep -q "frappe-nix.ports.base ($BASE)" "$WORK/pin.log" || fail "$mode: --pin web: $(cat "$WORK/pin.log")"
  ok "$mode: --dry-run holds nothing; --pin refuses rather than moving, naming ports.base"

  for p in "${pids[@]}"; do kill -9 "$p" 2> /dev/null || true; done
  for p in "${pids[@]}"; do wait "$p" 2> /dev/null || true; done
  pids=()
  rm -rf "$WORK"/rt* "$WORK"/*.log
}

if [ "$(uname -s)" = Linux ]; then
  FRAPPE_NIX_PORT_LEASE_BACKEND=abstract run abstract
fi
export FRAPPE_NIX_PORT_LEASE_DIR="$WORK/locks"
FRAPPE_NIX_PORT_LEASE_BACKEND=flock run flock
