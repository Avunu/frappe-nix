# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt

from typing import cast

import frappe
from frappe.tests import IntegrationTestCase

from standards_fixture.standards_fixture.doctype.fixture_note.fixture_note import FixtureNote


class TestFixtureNote(IntegrationTestCase):
	def test_title_is_trimmed(self):
		note = cast(FixtureNote, frappe.get_doc({"doctype": "Fixture Note", "title": "  spaced  "}).insert())
		self.assertEqual(note.title, "spaced")
		self.assertEqual(note.status, "Open")
