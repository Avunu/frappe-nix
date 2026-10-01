#!/usr/bin/env bash
# Checks for `frappe-nix-db-nocow` — keeping the MariaDB datadir off btrfs
# copy-on-write. See lib/db-nocow.nix.
#
# Usage: db-nocow.sh <path-to-frappe-nix-db-nocow>
#
# What the tool does depends on the filesystem it runs on, and a build sandbox
# lives on whatever the builder's temp directory is. So the assertions branch:
# on btrfs they check the attribute is really set and the copy really made; on
# anything else, that every subcommand is a silent no-op. Both branches check
# the refusals.
set -euo pipefail

TOOL="$1"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

fails=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no() {
  printf '  \033[31m✗\033[0m %s\n' "$1"
  fails=$((fails + 1))
}
check() { # <description> <command...>
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then ok "$desc"; else no "$desc"; fi
}
check_not() { # <description> <command...> — passes when the command fails
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}
nocow() { lsattr -d -- "$1" 2> /dev/null | cut -d' ' -f1 | grep -q C; }

if [ "$(stat -f -c %T "$ROOT")" = btrfs ]; then
  btrfs=1
  echo "(running on btrfs: checking the attribute and the copy)"
else
  btrfs=0
  echo "(not on btrfs: checking that nothing is done)"
fi

echo "── prepare ──────────────────────────────────────────────────────"
out=$("$TOOL" prepare "$ROOT/fresh" "$ROOT/tmpdir")
check "a missing datadir is created" test -d "$ROOT/fresh"
check "so is a second directory named alongside it" test -d "$ROOT/tmpdir"
check "a fresh datadir is prepared silently" test -z "$out"
if [ "$btrfs" = 1 ]; then
  check "a fresh datadir gets copy-on-write off" nocow "$ROOT/fresh"
  touch "$ROOT/fresh/ibdata1"
  check "files created in it inherit that" nocow "$ROOT/fresh/ibdata1"
else
  check_not "no attribute is set off btrfs" nocow "$ROOT/fresh"
fi

mkdir -p "$ROOT/used"
echo data > "$ROOT/used/ibdata1"
out=$("$TOOL" prepare "$ROOT/used")
check_not "a datadir already holding data is left as it is" nocow "$ROOT/used"
if [ "$btrfs" = 1 ]; then
  check "…and reported, with the command that fixes it" \
    grep -q "frappe-nix-db-nocow migrate" <<< "$out"
else
  check "…and not reported off btrfs" test -z "$out"
fi

echo "── migrate ──────────────────────────────────────────────────────"
mkdir -p "$ROOT/db/site"
echo one > "$ROOT/db/ibdata1"
echo two > "$ROOT/db/site/tabItem.ibd"

# Anything carrying --datadir=<dir> counts as the server, as in the reaper. The
# trailing `:` keeps bash from exec'ing straight into sleep, which would drop
# the --datadir argument from the command line pgrep reads.
bash -c 'sleep 30; :' fake-mariadbd "--datadir=$ROOT/db" &
server=$!
for _ in $(seq 1 50); do
  pgrep -f -- "--datadir=$ROOT/db" > /dev/null && break
  sleep 0.1
done
check_not "migrate refuses while a server is running on the datadir" "$TOOL" migrate "$ROOT/db"
kill "$server" 2> /dev/null || true
wait "$server" 2> /dev/null || true

check "migrate succeeds once it is stopped" "$TOOL" migrate "$ROOT/db"
check "the data is all still there" \
  test "$(cat "$ROOT/db/ibdata1")-$(cat "$ROOT/db/site/tabItem.ibd")" = "one-two"
if [ "$btrfs" = 1 ]; then
  check "the datadir has copy-on-write off" nocow "$ROOT/db"
  check "and so do the files copied into it" nocow "$ROOT/db/site/tabItem.ibd"
  check "the original is kept beside it" \
    test "$(cat "$ROOT"/db.cow-backup-*/site/tabItem.ibd)" = "two"
  check "a second migrate has nothing to do" "$TOOL" migrate "$ROOT/db"
  check "and leaves no second backup" \
    test "$(find "$ROOT" -maxdepth 1 -name 'db.cow-backup-*' | wc -l)" -eq 1
else
  check "nothing is copied off btrfs" \
    test "$(find "$ROOT" -maxdepth 1 -name 'db.*' | wc -l)" -eq 0
fi
check_not "a missing datadir is an error" "$TOOL" migrate "$ROOT/nope"

echo "── usage ────────────────────────────────────────────────────────"
check_not "no arguments is an error" "$TOOL"
check_not "an unknown subcommand is an error" "$TOOL" frobnicate

echo ""
if [ "$fails" -eq 0 ]; then
  echo "All db-nocow checks passed."
else
  echo "$fails check(s) failed."
  exit 1
fi
