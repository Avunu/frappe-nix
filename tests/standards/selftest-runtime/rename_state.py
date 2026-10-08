"""What the fixture's rename must keep (docs/app-standards/spec.md §7 N1, frappe-rename-app).

Run with the bench's interpreter from its sites/ directory:

    python rename_state.py record SITE APP STATE.json   before the rename
    python rename_state.py verify SITE APP STATE.json   after the rename and the migrate

`record` marks the daily job stopped and adds a Fixture Note; `verify` checks, under the
new app name, that the note is there, that the job is the same row (name) and still
stopped, that the Patch Log line was renamed rather than re-run (the patch counts its own
runs), and that the Module Def belongs to the new app.
"""

import json
import sys

import frappe


def state(app: str) -> dict:
	method = f"{app}.tasks.daily"
	return {
		"app": app,
		"installed_apps": frappe.get_installed_apps(),
		"job": frappe.db.get_value(
			"Scheduled Job Type", {"method": method}, ["name", "stopped"], as_dict=True
		),
		"note": frappe.db.exists("Fixture Note", {"title": "keep me"}),
		"patch_log": frappe.get_all(
			"Patch Log", filters={"patch": ("like", f"{app}.patches.%")}, pluck="patch"
		),
		"patch_runs": frappe.db.count("Fixture Note", {"title": "patch ran"}),
		"module_app": frappe.db.get_value("Module Def", "Standards Fixture", "app_name"),
	}


def main() -> int:
	mode, site, app, path = sys.argv[1:5]
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	try:
		if mode == "record":
			name = frappe.db.get_value("Scheduled Job Type", {"method": f"{app}.tasks.daily"})
			frappe.db.set_value("Scheduled Job Type", name, "stopped", 1)
			frappe.get_doc({"doctype": "Fixture Note", "title": "keep me"}).insert()
			frappe.db.commit()
			now = state(app)
			print(json.dumps(now, indent=2, default=str))
			assert now["patch_runs"] == 1 and now["patch_log"], f"the fixture patch did not run once: {now}"
			with open(path, "w") as f:
				json.dump(now, f, default=str)
			return 0
		with open(path) as f:
			before = json.load(f)
		after = state(app)
		print(json.dumps(after, indent=2, default=str))
		old = before["app"]
		failures = []
		if app not in after["installed_apps"] or old in after["installed_apps"]:
			failures.append(f"installed_apps is {after['installed_apps']}")
		if not after["job"] or after["job"]["name"] != before["job"]["name"] or not after["job"]["stopped"]:
			failures.append(f"the daily job was {before['job']}, is {after['job']}")
		if after["note"] != before["note"]:
			failures.append("the Fixture Note is gone")
		if [p.replace(app, old, 1) for p in after["patch_log"]] != before["patch_log"]:
			failures.append(f"the Patch Log was {before['patch_log']}, is {after['patch_log']}")
		if after["patch_runs"] != 1:
			failures.append(f"the patch ran {after['patch_runs']} times")
		if after["module_app"] != app:
			failures.append(f"Module Def Standards Fixture belongs to {after['module_app']}")
		for failure in failures:
			print(f"FAIL {failure}", file=sys.stderr)
		return 1 if failures else 0
	finally:
		frappe.destroy()


if __name__ == "__main__":
	sys.exit(main())
