#!/usr/bin/env bash
# Checks for `bench provision` (and `provision-site`, its alias): create
# $FRAPPE_SITE and install every app sites/apps.txt names, against a stub bench
# and a stub frappe-nix-ports. No database, no Frappe, no network.
#
# Usage: provision.sh <rendered-provision-site> <rendered-bench-dispatch>
set -euo pipefail

SCRIPT="$1"
DISPATCH="$2"

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
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}
check_not() { # <description> <command...> — passes when the command fails
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}

# ── stubs ──────────────────────────────────────────────────────────────────
# bench: one line per call in $CALLS, "<cwd> <args>"; `new-site` also reads
# the line bench new-site's getpass would (STDIN_LOG gets it, or EOF).
# STUB_FAIL=<word> fails the call whose last argument is that word, with 3.
BIN="$ROOT/bin"
mkdir -p "$BIN"
# Not `#!/usr/bin/env bash`: this also runs as a Nix check, where the sandbox
# has no /usr/bin/env.
printf '#!%s\n' "$(command -v bash)" > "$BIN/bench"
cat >> "$BIN/bench" << 'STUB'
printf '%s %s\n' "$PWD" "$*" >> "$CALLS"
printf 'raw=%s\n' "${_FRAPPE_BENCH_RAW:-}" >> "$ROOT/env"
case " $* " in
  *" new-site "*) if IFS= read -r line; then printf 'line=[%s]\n' "$line"; else echo EOF; fi >> "$ROOT/stdin" ;;
esac
[ -n "${STUB_FAIL:-}" ] && [ "${*: -1}" = "$STUB_FAIL" ] && exit 3
exit 0
STUB
# frappe-nix-ports web: the leased port, or a failure when STUB_PORTS_FAIL.
printf '#!%s\n' "$(command -v bash)" > "$BIN/frappe-nix-ports"
cat >> "$BIN/frappe-nix-ports" << 'STUB'
[ -n "${STUB_PORTS_FAIL:-}" ] && { echo "no bench running" >&2; exit 1; }
[ "$1" = web ] && echo 8123
STUB
chmod +x "$BIN/bench" "$BIN/frappe-nix-ports"
export PATH="$BIN:$PATH" CALLS="$ROOT/calls" ROOT

export FRAPPE_BENCH_ROOT="$ROOT/bench" FRAPPE_DB_SOCKET="$ROOT/mysql.sock"
B="$FRAPPE_BENCH_ROOT"
mkdir -p "$B/sites" "$ROOT/elsewhere"
printf 'frappe\nerpnext\n\nhrms\n' > "$B/sites/apps.txt"
echo '{"webserver_port": 8042}' > "$B/sites/common_site_config.json"
calls() { cut -d' ' -f2- "$CALLS" | tr '\n' '|'; }
# provision <log> [args…]: `bench provision`, run from somewhere other than
# the bench; its status in $rc. alias <log> [args…]: the same, as provision-site.
run_it() { # <log> <script> [args…]
  local log=$1 script=$2
  shift 2
  : > "$CALLS"
  : > "$ROOT/stdin"
  : > "$ROOT/env"
  rc=0
  (cd "$ROOT/elsewhere" && printf '\n' | bash "$script" "$@") > "$ROOT/$log.out" 2> "$ROOT/$log.err" || rc=$?
}
provision() {
  local log=$1
  shift
  run_it "$log" "$DISPATCH" provision "$@"
}
alias_() {
  local log=$1
  shift
  run_it "$log" "$SCRIPT" "$@"
}
NEW_SITE="new-site mysite.localhost --db-type mariadb --db-socket $FRAPPE_DB_SOCKET --db-root-username root"

echo "── no FRAPPE_SITE ──────────────────────────────────────────────"
unset FRAPPE_SITE
provision nosite
check_eq "exits 1" 1 "$rc"
check "says so on stderr" grep -qxF "ERROR: FRAPPE_SITE is not set. Set it in your .env or devenv config." "$ROOT/nosite.err"
check_eq "…and runs nothing" "" "$(calls)"
FRAPPE_SITE= provision emptysite
check_eq "an empty FRAPPE_SITE is the same: exit 1" 1 "$rc"
FRAPPE_SITE=mysite.localhost FRAPPE_DB_SOCKET= provision nosocket
check_eq "no FRAPPE_DB_SOCKET: exit 1" 1 "$rc"
check "…naming it" grep -q "FRAPPE_DB_SOCKET is not set" "$ROOT/nosocket.err"
check_eq "…before anything runs" "" "$(calls)"

export FRAPPE_SITE=mysite.localhost

echo "── a site, the default password ────────────────────────────────"
provision default
check_eq "exits 0" 0 "$rc"
check_eq "new-site with the bench's socket and --force, then each app but frappe, in order" \
  "$NEW_SITE --admin-password admin --set-default --force|--site mysite.localhost install-app erpnext|--site mysite.localhost install-app hrms|" \
  "$(calls)"
check "every call runs from the bench root" bash -c "! grep -v '^$B ' '$CALLS'"
check "with _FRAPPE_BENCH_RAW set (its bench calls reach the real bench)" bash -c "! grep -vx 'raw=1' '$ROOT/env'"
check_eq "new-site reads the root password from its stdin" "line=[]" "$(cat "$ROOT/stdin")"
check_eq "stdout, in order" \
  "⚠  When prompted for the MySQL root password, leave it blank and press Enter.|Creating site mysite.localhost...|Installing app: erpnext|Installing app: hrms||✅ Site mysite.localhost provisioned!|   Admin password: admin|   URL: http://127.0.0.1:8123|" \
  "$(tr '\n' '|' < "$ROOT/default.out")"
check_eq "nothing on stderr" "" "$(cat "$ROOT/default.err")"

echo "── a password ──────────────────────────────────────────────────"
provision pw 's3cret pass'
check_eq "exits 0" 0 "$rc"
check "the first argument is Administrator's password" grep -qF -- "--admin-password s3cret pass --set-default --force" "$CALLS"
check "…and is printed" grep -qxF "   Admin password: s3cret pass" "$ROOT/pw.out"
provision emptypw ''
check "an empty one is admin" grep -qF -- "--admin-password admin --set-default" "$CALLS"
provision dashpw -- -x
check_eq "after --, one that starts with a dash: exit 0" 0 "$rc"
check "…is the password" grep -qF -- "--admin-password -x --set-default" "$CALLS"

echo "── arguments ───────────────────────────────────────────────────"
provision help --help
check_eq "--help: exit 0" 0 "$rc"
check "…prints the usage" grep -q "^Usage: bench provision \[ADMIN_PASSWORD\]" "$ROOT/help.out"
check_eq "…and runs nothing (it once dropped the site, with --help as the password)" "" "$(calls)"
provision h -h
check_eq "-h: the same" "0 " "$rc $(calls)"
provision extra pw extra
check_eq "a second argument: exit 2" 2 "$rc"
check "…naming it" grep -qF "unexpected argument 'extra'" "$ROOT/extra.err"
check_eq "…and runs nothing" "" "$(calls)"

echo "── provision-site, the alias ───────────────────────────────────"
alias_ alias 's3cret'
check_eq "exits 0" 0 "$rc"
check_eq "the same calls" \
  "$NEW_SITE --admin-password s3cret --set-default --force|--site mysite.localhost install-app erpnext|--site mysite.localhost install-app hrms|" \
  "$(calls)"
check "the same stdout" grep -qxF "✅ Site mysite.localhost provisioned!" "$ROOT/alias.out"
check_eq "one line on stderr: it is deprecated" \
  "provision-site is deprecated: run \`bench provision\` (the alias goes in frappe-nix 2.0.0)" "$(cat "$ROOT/alias.err")"
STUB_FAIL=erpnext alias_ aliasfail
check_eq "a failure: the same status" 3 "$rc"

echo "── the URL's port ──────────────────────────────────────────────"
STUB_PORTS_FAIL=1 provision noports
check_eq "frappe-nix-ports failing: still 0" 0 "$rc"
check "…the port is common_site_config.json's webserver_port" grep -qxF "   URL: http://127.0.0.1:8042" "$ROOT/noports.out"
check_not "…and frappe-nix-ports' stderr is not shown" grep -q "no bench running" "$ROOT/noports.err"
echo '{}' > "$B/sites/common_site_config.json"
STUB_PORTS_FAIL=1 provision noport
check "no webserver_port: 8000" grep -qxF "   URL: http://127.0.0.1:8000" "$ROOT/noport.out"
rm "$B/sites/common_site_config.json"
STUB_PORTS_FAIL=1 provision noconfig
check_eq "no common_site_config.json: still 0 (the site is made)" 0 "$rc"
check "…and 8000" grep -qxF "   URL: http://127.0.0.1:8000" "$ROOT/noconfig.out"
echo '{"webserver_port": 8042}' > "$B/sites/common_site_config.json"

echo "── failures ────────────────────────────────────────────────────"
STUB_FAIL=--force provision newsitefail
check_eq "a failed new-site: its status" 3 "$rc"
check_eq "…and nothing is installed" "$NEW_SITE --admin-password admin --set-default --force|" "$(calls)"
check_not "…and it does not say provisioned" grep -q provisioned "$ROOT/newsitefail.out"

STUB_FAIL=erpnext provision installfail
check_eq "a failed install-app: its status" 3 "$rc"
check_not "…and the apps after it are not installed" grep -q "install-app hrms" "$CALLS"
check_not "…and it does not say provisioned" grep -q provisioned "$ROOT/installfail.out"

printf '  erpnext \nhrms' > "$B/sites/apps.txt"
provision lastline
check_eq "apps.txt's blanks are trimmed and a last line without a newline is installed" \
  "--site mysite.localhost install-app erpnext|--site mysite.localhost install-app hrms|" \
  "$(calls | cut -d'|' -f2-)"
printf 'frappe\nerpnext\n\nhrms\n' > "$B/sites/apps.txt"

mv "$B/sites/apps.txt" "$B/sites/apps.txt.off"
provision noapps
check_eq "no sites/apps.txt: exit 1, after the site is made" 1 "$rc"
check_eq "…which new-site did" "$NEW_SITE --admin-password admin --set-default --force|" "$(calls)"
check "…naming the file" grep -q "sites/apps.txt" "$ROOT/noapps.err"
mv "$B/sites/apps.txt.off" "$B/sites/apps.txt"

echo
if [ "$fails" -gt 0 ]; then
  echo "provision: $fails check(s) failed"
  exit 1
fi
echo "provision: all checks passed"
