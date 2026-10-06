"""Self-contained tests for lib/offline-migrate.py.

Runs without Frappe, MariaDB or percona-toolkit. A stub ``frappe`` module stands
in for the one object the tool reaches the database through (``frappe.local.db``),
and a shell script stands in for pt-online-schema-change. The assertions are
about what matters if the tool is wrong: that planning can never write a row, that
a credential is never on a command line and never outlives the run, that a failed
table stops the migrate behind it, and that the switches mean what they say.

Run directly (``python test_offline_migrate.py <path-to-offline-migrate.py>``) or
via ``nix flake check``.
"""

import contextlib
import importlib.util
import io
import os
import stat
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "lib", "offline-migrate.py")

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"ok   {label}")
    else:
        print(f"FAIL {label} {detail}")
        FAILURES.append(label)


def load():
    spec = importlib.util.spec_from_file_location("offline_migrate", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


om = load()


def stub_frappe(db):
    """A ``frappe`` with just what the write guard touches."""
    frappe = types.ModuleType("frappe")
    frappe.local = types.SimpleNamespace(db=db)
    sys.modules["frappe"] = frappe
    return frappe


class FakeDb:
    """What the planning code is allowed to see of ``frappe.db``."""

    def __init__(self):
        self.ran = []

    def sql(self, query, *args, **kwargs):
        self.ran.append(str(query))
        return [[1]]

    def sql_ddl(self, query, *args, **kwargs):
        raise AssertionError("a real DDL ran")


def says(fn, *args, **kwargs):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args, **kwargs)
    return result, out.getvalue()


# ── the write guard ──────────────────────────────────────────────────────────

for query in (
    "SELECT 1",
    "  select * from `tabUser`",
    "(SELECT 1) UNION (SELECT 2)",
    "SHOW FULL COLUMNS FROM `tabUser`",
    "SHOW INDEX FROM `tabUser`",
    "DESCRIBE `tabUser`",
    "EXPLAIN SELECT 1",
    "WITH x AS (SELECT 1) SELECT * FROM x",
):
    check(f"reads are let through: {query[:32]}", not om.looks_like_write(query))

for query in (
    "UPDATE `tabUser` SET x = 1 WHERE x IS NULL",
    "  update `tabUser` set x = 1",
    "INSERT INTO `tabUser` VALUES (1)",
    "DELETE FROM `tabUser`",
    "ALTER TABLE `tabUser` ADD COLUMN x int",
    "DROP TABLE `tabUser`",
    "CREATE TABLE t (a int)",
    "TRUNCATE `tabUser`",
    "SET SESSION lock_wait_timeout = 1",
):
    check(f"writes are stopped: {query[:32]}", om.looks_like_write(query))

db = FakeDb()
frappe = stub_frappe(db)
seen = []
with om.recording(seen):
    frappe.local.db.sql_ddl("ALTER TABLE `tabX` ADD COLUMN a int")
    rows = frappe.local.db.sql("SELECT 1")
    try:
        frappe.local.db.sql("UPDATE `tabX` SET a = 1 WHERE a IS NULL")
        refused = False
    except om.WriteAttempt:
        refused = True
check("DDL is recorded, not run", seen == ["ALTER TABLE `tabX` ADD COLUMN a int"], seen)
check("a read still reaches the database", rows == [[1]] and db.ran == ["SELECT 1"], db.ran)
check("an UPDATE is refused before it reaches the database", refused and len(db.ran) == 1, db.ran)
check(
    "the database object is left as it was found",
    "sql" not in db.__dict__ and "sql_ddl" not in db.__dict__,
    list(db.__dict__),
)

try:
    with om.recording([]):
        raise RuntimeError("boom")
except RuntimeError:
    pass
check("…even when planning blows up", "sql" not in db.__dict__ and "sql_ddl" not in db.__dict__)

# ── small pure helpers ───────────────────────────────────────────────────────

check("a row count may carry separators", om.parse_threshold("1,000_000") == 1_000_000)
check("a missing threshold is None, not 0", om.parse_threshold(None) is None and om.parse_threshold(" ") is None)
check("0 is a threshold", om.parse_threshold("0") == 0)
for bad in ("lots", "-5"):
    try:
        om.parse_threshold(bad)
        check(f"threshold {bad!r} is rejected", False)
    except SystemExit as e:
        check(f"threshold {bad!r} is rejected", bad in str(e), str(e))

check(
    "the ALTER prefix is stripped",
    om.clause_of("ALTER TABLE `tabX` ADD COLUMN `a` int", "tabX") == "ADD COLUMN `a` int",
)
check("another table's ALTER is not taken for this one's", om.clause_of("ALTER TABLE `tabY` ADD a int", "tabX") is None)
check("a statement that is not an ALTER is not taken", om.clause_of("OPTIMIZE TABLE `tabX`", "tabX") is None)
check(
    "clauses combine in order, once each",
    om.combine(["ADD COLUMN a int", "", "ADD INDEX i(a)", "ADD COLUMN a int"]) == "ADD COLUMN a int, ADD INDEX i(a)",
)

# ── the option file ──────────────────────────────────────────────────────────

sock = om.option_file("site_user", 'p"a\\ss#word', socket="/run/db/mysql.sock", host="ignored", port=1)
check("a socket is named and host/port are not", 'socket="/run/db/mysql.sock"' in sock and "host=" not in sock and "port=" not in sock, sock)
check("the password is quoted and escaped", 'password="p\\"a\\\\ss#word"' in sock, sock)
tcp = om.option_file("u", "", host="db.internal", port="3307")
check("TCP names host and port", 'host="db.internal"' in tcp and "port=3307" in tcp and "socket" not in tcp, tcp)
check("no password, no password line", "password" not in tcp, tcp)

# ── the command ──────────────────────────────────────────────────────────────

cmd = om.build_command("/x/pt", "site_db", "tabGL Entry", "ADD COLUMN a int, ADD INDEX i(a)", "/tmp/o.cnf")
check("the ALTER is one argument", "--alter=ADD COLUMN a int, ADD INDEX i(a)" in cmd)
check("the DSN names database and table", "D=site_db,t=tabGL Entry" in cmd)
check("credentials come from the file", "--defaults-file=/tmp/o.cnf" in cmd)
check("it executes by default", cmd[-1] == "--execute" and "--dry-run" not in cmd)
check("a rehearsal is --dry-run, never --execute", om.build_command("p", "d", "t", "a", "f", dry_run=True)[-1] == "--dry-run")
check("single-node defaults are set", "--recursion-method=none" in cmd and "--force" in cmd)
extra = om.build_command("p", "d", "t", "a", "f", extra=["--chunk-size=50", "--recursion-method=processlist"])
check(
    "what the caller passes comes after the defaults, so it wins",
    extra.index("--chunk-size=50") > extra.index("--chunk-size=1000")
    and extra.index("--recursion-method=processlist") > extra.index("--recursion-method=none"),
)

# ── running one table ────────────────────────────────────────────────────────

work = tempfile.mkdtemp()
record = os.path.join(work, "record")
fake = os.path.join(work, "pt")
# Records what it was run with, as the tool's subprocess sees it: its argv, the
# mode of the option file at that moment, and the file's contents.
with open(fake, "w") as f:
    f.write(
        f"""#!/bin/sh
for a in "$@"; do
  case "$a" in --defaults-file=*) CNF="${{a#--defaults-file=}}" ;; esac
done
{{
  echo "ARGV $*"
  echo "MODE $(stat -c %a "$CNF")"
  cat "$CNF"
}} > "{record}"
exit "${{PT_STUB_RC:-0}}"
"""
    )
os.chmod(fake, 0o755)

secret = "s3cr3t-pw"
plan = om.TablePlan("GL Entry", 12_345_678, ["ADD COLUMN `a` int", "ADD INDEX `a_index`(`a`)"])
dbinfo = types.SimpleNamespace(user="site_user", password=secret, host="127.0.0.1", port=3306, socket="/run/db.sock", cur_db_name="site_db")

ok, out = says(om.run_online, plan, fake, dbinfo, False, ["--chunk-size=50"])
recorded = open(record).read()
argv_line = recorded.splitlines()[0]
check("a table that altered cleanly is reported so", ok is True)
check("the table and the ALTER are said before the run", "GL Entry (12,345,678 rows)" in out and "ADD COLUMN `a` int, ADD INDEX" in out, out)
check("pt-online-schema-change got the ALTER and the extras", "--alter=ADD COLUMN `a` int, ADD INDEX `a_index`(`a`)" in argv_line and "--chunk-size=50" in argv_line, argv_line)
check("the password is not on the command line", secret not in argv_line, argv_line)
check("…but it was in the option file it was given", f'password="{secret}"' in recorded and 'socket="/run/db.sock"' in recorded, recorded)
check("the option file was owner-only while it existed", recorded.splitlines()[1] == "MODE 600", recorded.splitlines()[1])
cnf = [a for a in argv_line.split() if a.startswith("--defaults-file=")][0].split("=", 1)[1]
check("the option file is gone afterwards", not os.path.exists(cnf), cnf)

os.environ["PT_STUB_RC"] = "3"
ok, out = says(om.run_online, plan, fake, dbinfo, False, [])
cnf = [a for a in open(record).read().splitlines()[0].split() if a.startswith("--defaults-file=")][0].split("=", 1)[1]
del os.environ["PT_STUB_RC"]
check("a failed table is reported as failed", ok is False)
check("…and its option file is gone too", not os.path.exists(cnf), cnf)

# ── the command line ─────────────────────────────────────────────────────────

args = om.parse_args(["--site", "a.example", "--plan"])
check("--plan parses", args.plan and not args.dry_run and args.site == "a.example")
args = om.parse_args(["--site", "a.example", "--threshold", "5", "--", "--chunk-size=9", "--max-lag=2"])
check("what follows -- is for pt-online-schema-change", args.extra == ["--chunk-size=9", "--max-lag=2"] and args.threshold == "5", args.extra)
for argv in (["--site", "a", "--plan", "--dry-run"], []):
    err = io.StringIO()
    os.environ.pop("FRAPPE_SITE", None)
    try:
        with contextlib.redirect_stderr(err):
            om.parse_args(argv)
        check(f"{argv or 'no site'} is refused", False)
    except SystemExit as e:
        check(f"{argv or 'no site'} is refused", e.code == 2, err.getvalue())

os.environ["FRAPPE_SITE"] = "env.example"
check("the site comes from FRAPPE_SITE when no flag names one", om.parse_args([]).site == "env.example")
del os.environ["FRAPPE_SITE"]

os.environ[om.ENV_ENABLED] = "0"
rc, out = says(om.main, ["--site", "a.example"])
del os.environ[om.ENV_ENABLED]
check("FRAPPE_OFFLINE_MIGRATE=0 skips, says so, and succeeds", rc == 0 and "skipped" in out, out)

# ── which threshold wins ─────────────────────────────────────────────────────

os.environ.pop(om.ENV_THRESHOLD, None)
check("with nothing set, the default", om.resolve_threshold(None) == om.DEFAULT_THRESHOLD)
os.environ[om.ENV_THRESHOLD] = "7000"
check("the environment beats the default", om.resolve_threshold(None) == 7000)
check("a flag beats the environment", om.resolve_threshold("42") == 42)
check("a flag of 0 is a flag", om.resolve_threshold("0") == 0)
del os.environ[om.ENV_THRESHOLD]

if FAILURES:
    print(f"\n{len(FAILURES)} failed")
    sys.exit(1)
print("\nall passed")
