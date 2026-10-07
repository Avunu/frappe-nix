# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt

import frappe


def daily():
	"""T2: scheduler_events daily. Counts open Fixture Notes."""
	return frappe.db.count("Fixture Note", {"status": "Open"})
