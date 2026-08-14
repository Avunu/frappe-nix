#!/usr/bin/env bash
# Checks for `frappe-nix-bench-patches` — the reconcile that keeps `bench update`
# from importing bench.patches.v3. See lib/bench-patches.nix.
#
# Usage: bench-patches.sh <path-to-frappe-nix-bench-patches>
#
# Frappe-independent: the "shipped" list is a fixture with the same shape as the
# one frappe-bench installs, and the assertions are about what lands in the bench
# root's patches.txt.
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
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}

# The real shape: leading/trailing whitespace, a comment line, a blank line, and
# the `#2` suffix on install_yarn that bench treats as part of the entry.
SHIPPED="$ROOT/shipped.txt"
cat > "$SHIPPED" <<'EOF'
# a comment bench skips
bench.patches.v3.deprecate_old_config
  bench.patches.v4.update_node

bench.patches.v4.install_yarn #2
bench.patches.v5.fix_backup_cronjob
EOF

EXPECTED='bench.patches.v3.deprecate_old_config
bench.patches.v4.update_node
bench.patches.v4.install_yarn #2
bench.patches.v5.fix_backup_cronjob'

echo "── a bench root with no patches.txt (fresh scaffold, fresh clone) ──"
mkdir -p "$ROOT/fresh"
"$TOOL" "$SHIPPED" "$ROOT/fresh" > "$ROOT/fresh.log"
check_eq "every shipped patch is recorded as done" "$EXPECTED" "$(cat "$ROOT/fresh/patches.txt")"
check_not "the comment line is not recorded" \
  grep -q '^#' "$ROOT/fresh/patches.txt"
check_not "entries are stripped of surrounding whitespace" \
  grep -q '^[[:space:]]' "$ROOT/fresh/patches.txt"
check "it says what it did" grep -q 'recorded 4 bench patch' "$ROOT/fresh.log"
check "the file ends with a newline, as bench writes it" \
  test -z "$(tail -c1 "$ROOT/fresh/patches.txt")"

echo "── the one-byte file a failed \`bench update\` leaves behind ─────"
mkdir -p "$ROOT/broken"
printf '\n' > "$ROOT/broken/patches.txt"
"$TOOL" "$SHIPPED" "$ROOT/broken" > /dev/null
check_eq "the blank line is dropped and the list recorded" \
  "$EXPECTED" "$(cat "$ROOT/broken/patches.txt")"

echo "── a migrated bench that really ran some of them ────────────────"
mkdir -p "$ROOT/migrated"
printf 'bench.patches.v4.update_node\nbench.patches.v5.fix_backup_cronjob\n' \
  > "$ROOT/migrated/patches.txt"
"$TOOL" "$SHIPPED" "$ROOT/migrated" > /dev/null
check_eq "existing entries keep their order, missing ones are appended" \
  'bench.patches.v4.update_node
bench.patches.v5.fix_backup_cronjob
bench.patches.v3.deprecate_old_config
bench.patches.v4.install_yarn #2' \
  "$(cat "$ROOT/migrated/patches.txt")"

echo "── idempotency ──────────────────────────────────────────────────"
before="$(cat "$ROOT/fresh/patches.txt")"
"$TOOL" "$SHIPPED" "$ROOT/fresh" > "$ROOT/fresh.2.log"
check_eq "a second run changes nothing" "$before" "$(cat "$ROOT/fresh/patches.txt")"
check_eq "and says nothing" "" "$(cat "$ROOT/fresh.2.log")"
check "no temp files left behind" \
  test 1 -eq "$(find "$ROOT/fresh" -type f | wc -l)"

echo "── a file with no trailing newline is not spliced ───────────────"
mkdir -p "$ROOT/notrail"
printf 'bench.patches.v4.update_node' > "$ROOT/notrail/patches.txt"
"$TOOL" "$SHIPPED" "$ROOT/notrail" > /dev/null
check "the recorded entry survives as its own line" \
  grep -qxF 'bench.patches.v4.update_node' "$ROOT/notrail/patches.txt"
check_eq "and exactly the shipped set is present" "4" \
  "$(wc -l < "$ROOT/notrail/patches.txt")"

echo "── no frappe-bench in the environment ───────────────────────────"
mkdir -p "$ROOT/nobench"
"$TOOL" "$ROOT/does-not-exist.txt" "$ROOT/nobench" > /dev/null
check "nothing is written when the shipped list is absent" \
  test ! -e "$ROOT/nobench/patches.txt"

echo "── usage ────────────────────────────────────────────────────────"
check_not "no arguments is an error" "$TOOL"

echo ""
if [ "$fails" -eq 0 ]; then
  echo "All bench-patches checks passed."
else
  echo "$fails check(s) failed."
  exit 1
fi
