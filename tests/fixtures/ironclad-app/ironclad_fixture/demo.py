# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt

"""Demo data for frappe-demo (docs/ironclad/spec.md §5.4): idempotent, dated from ctx["today"]."""

import frappe


def setup(ctx):
	for i, title in enumerate(("Water the plants", "Renew the domain", "Ship the release")):
		name = f"DEMO-{i + 1}"
		if frappe.db.exists("Fixture Note", name):
			continue
		frappe.get_doc(
			{
				"doctype": "Fixture Note",
				"name": name,
				"title": title,
				"status": "Open" if i else "Closed",
				"due_date": frappe.utils.add_days(ctx["today"], i * 7),
			}
		).insert(ignore_permissions=True, set_name=name)
