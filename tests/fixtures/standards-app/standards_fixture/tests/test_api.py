# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt

from frappe.tests import IntegrationTestCase

from standards_fixture.api import ping


class TestApi(IntegrationTestCase):
	def test_ping(self):
		self.assertEqual(ping("fixture"), "pong fixture")
