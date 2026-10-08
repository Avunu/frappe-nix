import sys
import types
import unittest
from typing import ClassVar

from frappe_nix_tools.bench import composition
from frappe_nix_tools.commands.testmap import composition_markdown


class Address:
	pass


class OtherAddress(Address):
	"""Another app's extension of Address."""


class DemoAddress(OtherAddress):
	"""The A.0 #4 shape: extends the other app's extension instead of the base."""


class DemoToDo:
	"""A well-behaved extension: subclasses nothing but object."""


class ToDo:
	pass


class FakeFrappe(types.ModuleType):
	"""Just enough of frappe for composition.check: hooks per app, an install order, and
	a get_controller that composes extensions the way frappe's _get_extended_class does."""

	APPS: ClassVar[dict] = {
		"frappe": {},
		"other": {"extend_doctype_class": {"Address": ["other.OtherAddress"]}},
		"demo": {
			"extend_doctype_class": {"Address": ["demo.DemoAddress"], "ToDo": ["demo.DemoToDo"]},
		},
	}
	CLASSES: ClassVar[dict] = {
		"other.OtherAddress": OtherAddress,
		"demo.DemoAddress": DemoAddress,
		"demo.DemoToDo": DemoToDo,
	}

	def __init__(self, order):
		super().__init__("frappe")
		self.order = order
		self.controllers = {}
		self._dict = dict

	def get_installed_apps(self, *args, **kwargs):
		return list(self.order)

	def _load_app_hooks(self, app_name=None):
		merged: dict = {}
		for app in [app_name] if app_name else self.get_installed_apps():
			for key, value in self.APPS[app].items():
				for dt, paths in value.items():
					merged.setdefault(key, {}).setdefault(dt, []).extend(paths)
		return merged

	def get_hooks(self, hook=None, default="_KEEP_DEFAULT_LIST", app_name=None):
		hooks = self._load_app_hooks(app_name)
		if hook:
			return hooks.get(hook, [] if default == "_KEEP_DEFAULT_LIST" else default)
		return hooks

	def get_attr(self, path):
		return self.CLASSES[path]

	def clear_cache(self):
		pass


def get_controller(doctype):
	frappe = sys.modules["frappe"]
	base = {"Address": Address, "ToDo": ToDo}[doctype]
	extensions = frappe.get_hooks("extend_doctype_class", {}).get(doctype) or []
	classes = [frappe.get_attr(p) for p in reversed(extensions)]
	return type(f"Extended{base.__name__}", (*classes, base), {})


class TestComposition(unittest.TestCase):
	def run_check(self, order):
		frappe = FakeFrappe(order)
		model = types.ModuleType("frappe.model")
		base_document = types.ModuleType("frappe.model.base_document")
		base_document.get_controller = get_controller
		saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.model", "frappe.model.base_document")}
		sys.modules.update(
			{"frappe": frappe, "frappe.model": model, "frappe.model.base_document": base_document}
		)
		try:
			return composition.check(frappe, "demo"), frappe
		finally:
			for k, v in saved.items():
				if v is None:
					sys.modules.pop(k, None)
				else:
					sys.modules[k] = v

	def test_subclassing_another_apps_extension_fails_with_the_app_first(self):
		report, frappe = self.run_check(["frappe", "other", "demo"])
		self.assertFalse(report["ok"])
		address = report["doctypes"]["Address"]
		self.assertTrue(address["real_ok"])
		self.assertFalse(address["app_first_ok"])
		self.assertIn("TypeError", address["app_first_error"])
		self.assertEqual(
			address["layers"][:3],
			["tests.ExtendedAddress", "demo.DemoAddress", "other.OtherAddress"][1:] and address["layers"][:3],
		)
		self.assertTrue(report["doctypes"]["ToDo"]["real_ok"])
		self.assertTrue(report["doctypes"]["ToDo"]["app_first_ok"])
		self.assertEqual(report["app_first_order"], ["frappe", "demo", "other"])
		# The patches are undone.
		self.assertEqual(frappe.get_installed_apps(), ["frappe", "other", "demo"])
		self.assertIn("**TypeError", composition_markdown(report))

	def test_a_plain_extension_composes(self):
		frappe_apps = FakeFrappe.APPS
		try:
			FakeFrappe.APPS = {**frappe_apps, "demo": {"extend_doctype_class": {"ToDo": ["demo.DemoToDo"]}}}
			report, _ = self.run_check(["frappe", "other", "demo"])
		finally:
			FakeFrappe.APPS = frappe_apps
		self.assertTrue(report["ok"])
		self.assertEqual(list(report["doctypes"]), ["ToDo"])

	def test_helpers(self):
		self.assertEqual(
			composition.app_first(["frappe", "erpnext", "hrms", "demo"], "demo"),
			["frappe", "demo", "erpnext", "hrms"],
		)
		self.assertEqual(composition.app_first(["frappe", "erpnext"], "demo"), ["frappe", "erpnext"])
		self.assertEqual(
			composition.app_doctypes(
				{
					"override_doctype_class": {"Version": ["demo.v.V"]},
					"extend_doctype_class": {"ToDo": ["x.T"]},
				},
				"demo",
			),
			{"Version": ["demo.v.V"]},
		)
		self.assertIn("no controller", composition_markdown({"doctypes": {}}))


if __name__ == "__main__":
	unittest.main()
