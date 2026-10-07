# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt

from frappe.desk.doctype.todo.todo import ToDo


class IroncladToDo(ToDo):
	"""T4: extends ToDo (extend_doctype_class), so it must compose with other apps' extensions."""

	def fixture_label(self) -> str:
		return f"[fixture] {self.description or ''}".strip()
