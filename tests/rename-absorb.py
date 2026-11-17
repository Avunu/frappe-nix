"""Checks for lib/rename/frappe_rename_app.py's site half when the new app has absorbed the old.

Frappe-independent: the site's database is an in-memory SQLite (its backquoted table names and
`%s` placeholders are translated), and `frappe` is a stand-in that answers what the tool asks of
it. What is exercised is SiteRename (check, rename), describe, and the mode each pair gets.

usage: python3 rename-absorb.py <path-to-lib/rename/frappe_rename_app.py>
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("frappe_rename_app", sys.argv[1])
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

failures = []


def eq(label, expected, got):
	if expected == got:
		print(f"  ok   {label}")
	else:
		print(f"  FAIL {label}\n       expected: {expected!r}\n       got:      {got!r}")
		failures.append(label)


def raises(label, fragment, fn):
	try:
		fn()
	except tool.Precondition as e:
		if fragment in str(e):
			print(f"  ok   {label}")
		else:
			print(f"  FAIL {label}\n       message: {e}")
			failures.append(label)
	else:
		print(f"  FAIL {label}: no Precondition raised")
		failures.append(label)


class FakeDB:
	"""The few db methods the tool uses, over SQLite."""

	def __init__(self):
		self.con = sqlite3.connect(":memory:")
		self.tables: dict[str, set[str]] = {}

	def create(self, doctype, *columns, rows=()):
		self.tables[doctype] = set(columns)
		cols = ", ".join(f"`{c}`" for c in columns)
		self.con.execute(f"create table `tab{doctype}` ({cols})")
		for row in rows:
			self.con.execute(f"insert into `tab{doctype}` values ({', '.join('?' * len(row))})", row)

	def table_exists(self, doctype):
		return doctype in self.tables

	def has_column(self, doctype, field):
		return field in self.tables.get(doctype, ())

	def sql(self, query, values=()):
		if isinstance(values, str):
			values = (values,)
		return self.con.execute(query.replace("%s", "?"), tuple(values)).fetchall()

	def rows(self, doctype):
		return self.con.execute(f"select * from `tab{doctype}` order by 1").fetchall()


def site(old, new, installed, owned_modules, new_modules, old_in_bench=False):
	"""A site with OLD (owning OWNED_MODULES) and NEW (declaring NEW_MODULES); NEW installed too
	when it is in INSTALLED."""
	db = FakeDB()
	db.create(
		"DefaultValue",
		"parent",
		"defkey",
		"defvalue",
		rows=[("__global", "installed_apps", json.dumps(installed))],
	)
	db.create(
		"Module Def",
		"name",
		"app_name",
		"custom",
		rows=[(m, old, 0) for m in owned_modules]
		+ [(m, new, 0) for m in new_modules if m not in owned_modules],
	)
	db.create("Desktop Icon", "name", "app", rows=[("a", old), ("b", new), ("c", "erpnext")])
	db.create(
		"Patch Log", "name", "patch", rows=[("1", f"{old}.patches.v1.thing"), ("2", "erpnext.patches.x")]
	)
	db.create(
		"Scheduled Job Type",
		"name",
		"method",
		rows=[("j1", f"{old}.triggers.dispatch_scheduled"), ("j2", "erpnext.tasks.daily")],
	)
	db.create(
		"Server Script", "name", "script", rows=[("s1", f"import {old}.api\n{old}.api.run()"), ("s2", "pass")]
	)
	db.create("Singles", "doctype", "field", "value")
	frappe = SimpleNamespace(
		local=SimpleNamespace(site="site.test"),
		db=db,
		get_module_list=lambda app: list(new_modules) if app == new else list(owned_modules),
	)
	return db, tool.SiteRename(frappe, dry_run=False)


# A package for NEW on the path: what the tool reads to know what `NEW.<x>` can name.
tmp = Path(tempfile.mkdtemp())
for name in ("framework", "sibling"):
	(tmp / name / "patches").mkdir(parents=True)
	(tmp / name / "__init__.py").write_text("")
	(tmp / name / "api.py").write_text("")
sys.path.insert(0, str(tmp))

ALL = ["framework", "erpnext", "flow"]
APPS = ["framework", "erpnext", "flow"]

print("a pair where NEW has absorbed OLD (both installed, NEW declares OLD's module)")
db, job = site("flow", "framework", ALL, ["Flow"], ["Core", "Flow"])
eq("check: absorb", "absorb", job.check("flow", "framework", ALL, APPS))
job.rename("flow", "framework", "absorb")
eq(
	"installed_apps loses OLD, order kept",
	["framework", "erpnext"],
	json.loads(db.rows("DefaultValue")[0][2]),
)
eq(
	"Module Def moves to NEW",
	[("Core", "framework", 0), ("Flow", "framework", 0)],
	sorted(db.rows("Module Def")),
)
eq(
	"an app-name column moves",
	[("a", "framework"), ("b", "framework"), ("c", "erpnext")],
	db.rows("Desktop Icon"),
)
eq(
	"the Patch Log is left alone",
	[("1", "flow.patches.v1.thing"), ("2", "erpnext.patches.x")],
	db.rows("Patch Log"),
)
eq(
	"a scheduled job and a script naming OLD are left alone",
	[("j1", "flow.triggers.dispatch_scheduled"), ("j2", "erpnext.tasks.daily")],
	db.rows("Scheduled Job Type"),
)
eq("…and the script text", "import flow.api\nflow.api.run()", db.rows("Server Script")[0][1])
log = "\n".join(job.log)
eq(
	"what is left is reported, by row",
	True,
	"Scheduled Job Type.method: 1 row(s) still name flow" in log and "j1" in log,
)
eq("…the script too", True, "Server Script.script: 1 row(s) still name flow" in log)

print("a plain rename (NEW not installed) is unchanged")
installed = ["flow", "erpnext"]
db, job = site("flow", "framework", installed, ["Flow"], ["Flow"])
eq("check: rename", "rename", job.check("flow", "framework", installed, ["framework", "erpnext"]))
job.rename("flow", "framework", "rename")
eq(
	"installed_apps: OLD becomes NEW in place",
	["framework", "erpnext"],
	json.loads(db.rows("DefaultValue")[0][2]),
)
eq("the Patch Log line is renamed", "framework.patches.v1.thing", db.rows("Patch Log")[0][1])
eq(
	"a dotted path in a script is renamed",
	True,
	db.rows("Server Script")[0][1].startswith("import framework.api"),
)

print("two installed apps that keep their own modules are still refused")
db, job = site("flow", "sibling", ["sibling", "flow"], ["Flow"], ["Other"])
raises(
	"NEW lacks OLD's module",
	"does not declare flow's module(s) Flow",
	lambda: job.check("flow", "sibling", ["sibling", "flow"], ["sibling", "flow"]),
)
db, job = site("flow", "sibling", ["sibling", "flow"], [], ["Other"])
raises(
	"OLD owns no module to hand over",
	"flow owns no module to hand over",
	lambda: job.check("flow", "sibling", ["sibling", "flow"], ["sibling", "flow"]),
)

print("nothing to do, and the bench lacks NEW")
db, job = site("flow", "framework", ["framework"], ["Flow"], ["Flow"])
eq("OLD not installed: nothing to do", None, job.check("flow", "framework", ["framework"], APPS))
db, job = site("flow", "framework", ["flow"], ["Flow"], ["Flow"])
raises(
	"NEW not on the bench",
	"framework is not in sites/apps.txt",
	lambda: job.check("flow", "framework", ["flow"], ["flow"]),
)

print("the sentence for a run")
todo = [("a", "b", "rename"), ("flow", "framework", "absorb"), ("c", "d", "rename")]
eq("before", "Rename a -> b, c -> d, Absorb flow into framework", tool.describe(todo))
eq("after", "renamed a -> b, c -> d, absorbed flow into framework", tool.describe(todo, done=True))

print()
if failures:
	print(f"{len(failures)} check(s) failed.")
	sys.exit(1)
print("All rename-absorb checks passed.")
