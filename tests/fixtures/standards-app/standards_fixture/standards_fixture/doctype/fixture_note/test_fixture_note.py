# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestFixtureNote(IntegrationTestCase):
	def test_title_is_trimmed(self):
		note = frappe.get_doc({"doctype": "Fixture Note", "title": "  spaced  "}).insert()
		self.assertEqual(note.title, "spaced")
		self.assertEqual(note.status, "Open")
