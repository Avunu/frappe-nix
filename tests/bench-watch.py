"""Checks for lib/bench-watch.py's app selection: which apps the watch process
leaves out, read from fixture hooks.py files. Frappe-independent — only the
pure functions are exercised; main() is what imports frappe.

usage: python3 bench-watch.py <path-to-lib/bench-watch.py>
"""

import importlib.util
import os
import sys
import tempfile

spec = importlib.util.spec_from_file_location("bench_watch", sys.argv[1])
bench_watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench_watch)

fails = 0


def check(desc, expected, got):
	global fails
	if expected == got:
		print(f"  \033[32m✓\033[0m {desc}")
	else:
		print(f"  \033[31m✗\033[0m {desc}\n      expected: {expected!r}\n      got:      {got!r}")
		fails += 1


with tempfile.TemporaryDirectory() as apps_dir:
	hooks = {
		"frappe": 'app_name = "frappe"\napp_publisher = "Frappe Technologies"\n',
		"erpnext": 'app_publisher = "Frappe Technologies Pvt. Ltd."\n',
		"hrms": 'app_publisher = "frappe technologies pvt. ltd."\n',
		# Parsed, never imported: the import below would fail.
		"unimportable": 'import no_such_module\napp_publisher = "Frappe Technologies"\n',
		"mine": 'app_publisher = "Avunu LLC"\n',
		"vendored": 'app_publisher = "AgriTheory"\n',
		"unparseable": "app_publisher = (\n",
		"computed": 'app_publisher = "Frappe " + "Technologies"\n',
		"nohooks": None,
	}
	for app, text in hooks.items():
		os.makedirs(os.path.join(apps_dir, app, app))
		if text is not None:
			with open(os.path.join(apps_dir, app, app, "hooks.py"), "w") as f:
				f.write(text)

	def publisher(app):
		return bench_watch.publisher_of(os.path.join(apps_dir, app, app, "hooks.py"))

	all_apps = list(hooks)
	frappe_apps = ["frappe", "erpnext", "hrms", "unimportable"]
	others = ["mine", "vendored", "unparseable", "computed", "nohooks"]

	print("── publisher_of ─────────────────────────────────────────────────")
	check("reads app_publisher", "Frappe Technologies Pvt. Ltd.", publisher("erpnext"))
	check("without importing hooks.py", "Frappe Technologies", publisher("unimportable"))
	check("a hooks.py that does not parse has none", "", publisher("unparseable"))
	check("nor does a computed one", "", publisher("computed"))
	check("nor a missing hooks.py", "", publisher("nohooks"))

	print("── select_apps ──────────────────────────────────────────────────")
	check(
		"Frappe's apps are left out, in any case and suffix; the rest are watched, in order",
		(others, frappe_apps),
		bench_watch.select_apps(all_apps, None, ["Frappe Technologies"], publisher),
	)
	check(
		"another publisher can be left out too",
		(["mine", "unparseable", "computed", "nohooks"], frappe_apps + ["vendored"]),
		bench_watch.select_apps(all_apps, None, ["Frappe Technologies", "AgriTheory"], publisher),
	)
	check(
		"no publishers left out watches everything, as `bench watch` does",
		(all_apps, []),
		bench_watch.select_apps(all_apps, None, [], publisher),
	)
	check(
		"an explicit list wins over the publisher rule",
		(["frappe", "mine"], [a for a in all_apps if a not in ("frappe", "mine")]),
		bench_watch.select_apps(all_apps, ["frappe", "mine"], ["Frappe Technologies"], publisher),
	)
	check(
		"an explicit empty list watches nothing",
		([], all_apps),
		bench_watch.select_apps(all_apps, [], ["Frappe Technologies"], publisher),
	)

print()
if fails:
	print(f"{fails} check(s) failed.")
	sys.exit(1)
print("All bench-watch checks passed.")
