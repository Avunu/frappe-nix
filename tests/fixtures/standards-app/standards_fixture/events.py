# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt

import frappe

FLAG = "standards_fixture_todo_updated"


def todo_on_update(doc, method=None):
	"""T3: doc_events ToDo on_update. Records that it ran, for the test to see."""
	frappe.flags[FLAG] = doc.name
