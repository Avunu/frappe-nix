# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class FixtureNote(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		due_date: DF.Date | None
		status: DF.Literal["Open", "Closed"]
		title: DF.Data
	# end: auto-generated types

	def validate(self):
		self.title = (self.title or "").strip()
