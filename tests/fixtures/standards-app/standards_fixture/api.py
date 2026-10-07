# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt

import frappe


@frappe.whitelist()
def ping(name: str = "world") -> str:
	"""T1, tested: tests/test_api.py calls it."""
	return f"pong {name}"


@frappe.whitelist()
def legacy() -> str:
	"""T1, deliberately untested: exempt through [[tool.frappe-nix.untested]]."""
	return "legacy"
