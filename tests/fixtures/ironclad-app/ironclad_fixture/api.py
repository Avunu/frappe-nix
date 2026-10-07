import frappe


@frappe.whitelist()
def ping(name: str = "world") -> str:
	"""T1, tested: tests/test_api.py calls it."""
	return f"pong {name}"


@frappe.whitelist()
def legacy() -> str:
	"""T1, deliberately untested: exempt through [[tool.ironclad.untested]]."""
	return "legacy"
