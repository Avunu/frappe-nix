# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class FixtureNote(Document):
	def validate(self):
		self.title = (self.title or "").strip()
