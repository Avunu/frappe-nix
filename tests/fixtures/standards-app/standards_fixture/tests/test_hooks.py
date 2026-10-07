import datetime
from types import SimpleNamespace

import frappe
from frappe.tests import IntegrationTestCase

from standards_fixture import demo, tasks
from standards_fixture.events import FLAG
from standards_fixture.request import add_fixture_header


class TestHooks(IntegrationTestCase):
	def test_todo_on_update_and_extension(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "check the hooks"}).insert()
		self.assertEqual(frappe.flags.get(FLAG), todo.name)
		self.assertEqual(todo.fixture_label(), "[fixture] check the hooks")

	def test_daily(self):
		frappe.get_doc({"doctype": "Fixture Note", "title": "open one"}).insert()
		self.assertGreaterEqual(tasks.daily(), 1)

	def test_after_request(self):
		response = SimpleNamespace(headers={})
		add_fixture_header(response=response)
		self.assertEqual(response.headers["X-Standards-Fixture"], "1")

	def test_demo_is_idempotent(self):
		ctx = {"today": datetime.date(2026, 1, 15), "seed": 1, "company": None, "erpnext_demo": False}
		demo.setup(ctx)
		count = frappe.db.count("Fixture Note", {"name": ("like", "DEMO-%")})
		demo.setup(ctx)
		self.assertEqual(frappe.db.count("Fixture Note", {"name": ("like", "DEMO-%")}), count)
		self.assertEqual(str(frappe.db.get_value("Fixture Note", "DEMO-2", "due_date")), "2026-01-22")
