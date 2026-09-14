#!/usr/bin/env bash
# Checks for `frappe-nix-root-sync` — the shell-entry hook that brings a bench's
# pyproject.toml up to what the current frappe-nix expects and re-locks when
# that changed something. See lib/root-sync.nix.
#
# Usage: root-sync.sh <path-to-frappe-nix-root-sync> <bench-template-pyproject.toml>
#
# uv-independent: a stub `uv` on PATH records where it was called and writes the
# lock a real one would, and the assertions are about when the tool decides to
# call it, and about what is on disk afterwards — above all after a failure,
# since a half-reconciled root is the one state the next evaluation refuses.
set -euo pipefail

TOOL="$1"
TEMPLATE="$2"

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

# ── a stub uv ──────────────────────────────────────────────────────────────
# UV_CALLS — one line per invocation (the cwd), so "did it re-lock?" is a count
# UV_FAIL  — make the lock fail, as an unsatisfiable requirement would
BIN="$ROOT/bin"
mkdir -p "$BIN"
# Not `#!/usr/bin/env bash`: this also runs as a Nix check, where the sandbox
# has no /usr/bin/env.
printf '#!%s\n' "$(command -v bash)" > "$BIN/uv"
cat >> "$BIN/uv" <<'STUB'
echo "$PWD $*" >> "$UV_CALLS"
if [ "${UV_FAIL:-}" = "1" ]; then
  echo "  × No solution found when resolving dependencies" >&2
  exit 1
fi
printf 'version = 1\n# locked by the stub\n' > uv.lock
exit 0
STUB
chmod +x "$BIN/uv"
export PATH="$BIN:$PATH"
export UV_CALLS="$ROOT/uv-calls"
: > "$UV_CALLS"
uv_calls() { wc -l < "$UV_CALLS" | tr -d ' '; }

# ── fixture: a bench from before frappe-runtime ────────────────────────────
# What frappe-init wrote a bench as, minus everything the runtime later added:
# the dependency, its [tool.uv.sources] entry and its build backend. Everything
# else is deliberately *not* the template's value, so "left alone" is testable.
BENCH="$ROOT/bench"
mkdir -p "$BENCH/apps/frappe"
cat > "$BENCH/pyproject.toml" <<'EOF'
[project]
name = "old-bench"
version = "0.1.0"
requires-python = ">=3.12"

# The comment above the dependencies, kept by tomlkit.
dependencies = [
    "frappe-bench>=5.29.0",
    "setuptools",
]

[dependency-groups]
dev = ["ruff>=0.15.0"]

[tool.uv]
package = false
override-dependencies = ["click>=8.2,<8.3"]

[tool.uv.extra-build-dependencies]
"backcall" = ["setuptools"]

[tool.uv.workspace]
members = ["apps/frappe"]

[tool.uv.sources]
frappe = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
EOF
printf 'version = 1\n# the lock from before\n' > "$BENCH/uv.lock"
cp "$BENCH/pyproject.toml" "$ROOT/pyproject.before"
cp "$BENCH/uv.lock" "$ROOT/uv.lock.before"

toml_get() { # <file> <python expression over d>
  python3 -c "import sys, tomllib; d = tomllib.load(open(sys.argv[1], 'rb')); print($2)" "$1"
}
# A fixed, old mtime, so "was it rewritten?" does not depend on the filesystem's
# timestamp resolution or on how fast the run was.
EPOCH=946684800
stamp() { touch -d "@$EPOCH" "$1"; }
mtime() { stat -c %Y "$1"; }
has_dep() { # <file> <name>
  toml_get "$1" "any(x.split('[')[0].split('>')[0].split('=')[0].strip() == '$2' for x in d['project']['dependencies'])" | grep -qx True
}

echo "── the template parses as it is ──"
check "the bench template is valid TOML unrendered (ensure-root reads it that way)" \
  python3 -c "import sys, tomllib; tomllib.load(open(sys.argv[1], 'rb'))" "$TEMPLATE"
check_eq "…and its override-dependencies token is a string, so the file stays parseable" \
  "@OVERRIDES@" "$(toml_get "$TEMPLATE" "d['tool']['uv']['override-dependencies']")"

echo "── a bench that predates the runtime ──"
out="$("$TOOL" "$BENCH" 2>&1)" && rc=0 || rc=$?
check_eq "exits 0" 0 "$rc"
check "frappe-runtime is now a dependency" has_dep "$BENCH/pyproject.toml" frappe-runtime
check_eq "…with the template's git source" runtime \
  "$(toml_get "$BENCH/pyproject.toml" "d['tool']['uv']['sources']['frappe-runtime']['subdirectory']")"
check_eq "…and its build backend named for uv" "['hatchling']" \
  "$(toml_get "$BENCH/pyproject.toml" "d['tool']['uv']['extra-build-dependencies']['frappe-runtime']")"
check_eq "the lock was regenerated, once" 1 "$(uv_calls)"
check_eq "…from the bench root" "$BENCH lock" "$(cat "$UV_CALLS")"
check "…and is the new lock" grep -q 'locked by the stub' "$BENCH/uv.lock"
check "it says the shell was built from the old lock" grep -q 're-enter the shell' <<< "$out"
check "it lists what it added" grep -q '+ \[project\].dependencies += frappe-runtime' <<< "$out"

echo "── nothing already correct is touched ──"
check_eq "[project].name kept" old-bench "$(toml_get "$BENCH/pyproject.toml" "d['project']['name']")"
check_eq "requires-python kept" ">=3.12" "$(toml_get "$BENCH/pyproject.toml" "d['project']['requires-python']")"
check_eq "the bench's own override-dependencies kept" "['click>=8.2,<8.3']" \
  "$(toml_get "$BENCH/pyproject.toml" "d['tool']['uv']['override-dependencies']")"
check_eq "the bench's own dev group kept" "['ruff>=0.15.0']" \
  "$(toml_get "$BENCH/pyproject.toml" "d['dependency-groups']['dev']")"
check_eq "the app source kept" True \
  "$(toml_get "$BENCH/pyproject.toml" "d['tool']['uv']['sources']['frappe']['workspace']")"
check "the comment in the file survived (tomlkit, not a rewrite)" \
  grep -q 'The comment above the dependencies' "$BENCH/pyproject.toml"
check_eq "existing extra-build-dependencies kept" "['setuptools']" \
  "$(toml_get "$BENCH/pyproject.toml" "d['tool']['uv']['extra-build-dependencies']['backcall']")"

echo "── a second run is a no-op ──"
cp "$BENCH/pyproject.toml" "$ROOT/pyproject.synced"
stamp "$BENCH/pyproject.toml"
out="$("$TOOL" "$BENCH" 2>&1)" && rc=0 || rc=$?
check_eq "exits 0" 0 "$rc"
check_eq "says nothing" "" "$out"
check "pyproject.toml byte-identical" cmp -s "$BENCH/pyproject.toml" "$ROOT/pyproject.synced"
check_eq "…and not even rewritten (mtime kept)" "$EPOCH" "$(mtime "$BENCH/pyproject.toml")"
check_eq "uv not called again" 1 "$(uv_calls)"

echo "── a bench that points frappe-runtime somewhere else keeps its entry ──"
FORK="$ROOT/fork"
mkdir -p "$FORK"
# The fork's entry goes into [tool.uv.sources], next to the app's.
sed 's|^frappe = { workspace = true }$|&\nfrappe-runtime = { git = "https://example.com/me/frappe-nix", subdirectory = "runtime" }|' \
  "$ROOT/pyproject.before" > "$FORK/pyproject.toml"
check_eq "fixture: the fork's source is where it should be" "https://example.com/me/frappe-nix" \
  "$(toml_get "$FORK/pyproject.toml" "d['tool']['uv']['sources']['frappe-runtime']['git']")"
: > "$UV_CALLS"
check "runs" "$TOOL" "$FORK"
check "the dependency is added" has_dep "$FORK/pyproject.toml" frappe-runtime
check_eq "the bench's own source wins over the template's" "https://example.com/me/frappe-nix" \
  "$(toml_get "$FORK/pyproject.toml" "d['tool']['uv']['sources']['frappe-runtime']['git']")"

echo "── a failed lock restores both files ──"
FAIL="$ROOT/fail"
mkdir -p "$FAIL"
cp "$ROOT/pyproject.before" "$FAIL/pyproject.toml"
cp "$ROOT/uv.lock.before" "$FAIL/uv.lock"
stamp "$FAIL/pyproject.toml"
stamp "$FAIL/uv.lock"
: > "$UV_CALLS"
out="$(UV_FAIL=1 "$TOOL" "$FAIL" 2>&1)" && rc=0 || rc=$?
check_eq "exits non-zero" 1 "$rc"
check_eq "uv was tried" 1 "$(uv_calls)"
check "pyproject.toml is byte-identical to before" cmp -s "$FAIL/pyproject.toml" "$ROOT/pyproject.before"
check_eq "…with its mtime, so git and mtime stamps see no change" "$EPOCH" "$(mtime "$FAIL/pyproject.toml")"
check "uv.lock is byte-identical to before" cmp -s "$FAIL/uv.lock" "$ROOT/uv.lock.before"
check_eq "…with its mtime" "$EPOCH" "$(mtime "$FAIL/uv.lock")"
check "it says the files were restored" grep -q 'restored' <<< "$out"
check "…and how to do it by hand" grep -q "'uv lock'" <<< "$out"
check "uv's own diagnosis is passed through" grep -q 'No solution found' <<< "$out"

echo "── a failed lock on a bench with no lock yet leaves none behind ──"
NOLOCK="$ROOT/nolock"
mkdir -p "$NOLOCK"
cp "$ROOT/pyproject.before" "$NOLOCK/pyproject.toml"
check_not "exits non-zero" env UV_FAIL=1 "$TOOL" "$NOLOCK"
check "pyproject.toml restored" cmp -s "$NOLOCK/pyproject.toml" "$ROOT/pyproject.before"
check "no uv.lock was left behind" test ! -e "$NOLOCK/uv.lock"

echo "── no uv on PATH ──"
NOUV="$ROOT/nouv"
mkdir -p "$NOUV"
cp "$ROOT/pyproject.before" "$NOUV/pyproject.toml"
out="$(PATH="${PATH#"$BIN":}" "$TOOL" "$NOUV" 2>&1)" && rc=0 || rc=$?
check_eq "exits non-zero" 1 "$rc"
check "pyproject.toml restored" cmp -s "$NOUV/pyproject.toml" "$ROOT/pyproject.before"
check "it names uv" grep -q 'uv is not on PATH' <<< "$out"

echo "── a pyproject.toml that does not parse ──"
BAD="$ROOT/bad"
mkdir -p "$BAD"
printf '[project\nname = "broken"\n' > "$BAD/pyproject.toml"
cp "$BAD/pyproject.toml" "$ROOT/pyproject.bad"
: > "$UV_CALLS"
check_not "exits non-zero" "$TOOL" "$BAD"
check "the file is untouched" cmp -s "$BAD/pyproject.toml" "$ROOT/pyproject.bad"
check_eq "uv not called" 0 "$(uv_calls)"

echo "── a root with no [project].name is not this hook's to invent ──"
NONAME="$ROOT/noname"
mkdir -p "$NONAME"
printf '[project]\nrequires-python = ">=3.12"\ndependencies = []\n' > "$NONAME/pyproject.toml"
cp "$NONAME/pyproject.toml" "$ROOT/pyproject.noname"
out="$("$TOOL" "$NONAME" 2>&1)" && rc=0 || rc=$?
check_eq "exits non-zero" 1 "$rc"
check "it says which key and which flag" grep -q 'no \[project\].name; pass --name' <<< "$out"
check "the file is untouched" cmp -s "$NONAME/pyproject.toml" "$ROOT/pyproject.noname"

echo "── usage ──"
check_not "no argument" "$TOOL"
check_not "a directory with no pyproject.toml" "$TOOL" "$ROOT/bin"

echo ""
if [ "$fails" -eq 0 ]; then
  echo "All root-sync checks passed."
else
  echo "$fails check(s) failed."
  exit 1
fi
