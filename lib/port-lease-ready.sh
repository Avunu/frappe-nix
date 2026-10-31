# frappe-nix-lease-ready RUNTIME: exit 0 when this bench's port lease is up,
# 1 when it is not. `port-lease.py ready` without Python (lib/port-lease.py):
# RUNTIME/frappe-nix/ports.json is there, and the holder that wrote it, the
# frappe-ports process, still holds RUNTIME/frappe-nix/ports.lock. A holder
# keeps an exclusive flock on it for as long as it runs, so a shared one is
# refused then; the kernel drops it however the holder ends, and the next
# holder removes the ports.json a killed one left before it writes its own.
#
# The frappe-ports readiness probe, the frappe:config task's wait and the
# check before `devenv up` run this, several times a second while a bench
# starts. A Python start-up per check (python3Minimal ships no bytecode)
# cost about 0.2 s of CPU, and on a CI host running many benches at once it
# took longer than the probe's timeout: the lease was up, the probe never
# passed, and nginx and the runtime never started.
dir="${1:?usage: frappe-nix-lease-ready RUNTIME}/frappe-nix"
[ -s "$dir/ports.json" ] || exit 1
# Read-only, so a check never creates the lock file.
{ exec 9< "$dir/ports.lock"; } 2> /dev/null || exit 1
# 75 when the shared lock is refused: the holder has it. 0 means it was free,
# anything else that the check itself failed; neither is a lease.
rc=0
flock --nonblock --shared --conflict-exit-code 75 9 || rc=$?
[ "$rc" = 75 ]
