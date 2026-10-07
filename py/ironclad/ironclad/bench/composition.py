"""The composition check (spec §5.1.2, plan A.3): does the app's controller extension compose?

Run with the bench's interpreter::

    <bench>/env/bin/python composition.py --site S --app A --sites-path <bench>/sites --out OUT.json

For every DocType the app names in ``extend_doctype_class`` or ``override_doctype_class``
(read from the merged hooks of every installed app), it builds the controller twice with
frappe's own ``get_controller``: in the site's real app order, and with the app moved
directly after frappe. Both must build without a ``TypeError`` and have each of the app's
classes in their ``__mro__``. The second order is the A.0 #4 failure: an extension that
subclasses another app's extension instead of the base composes only while that app is
installed first, and an MRO conflict otherwise.

Standard library and frappe only. Exits 0 after writing the report (``ok`` holds the
verdict), 3 when the site cannot be reached.
"""

import argparse
import json
import sys
from pathlib import Path

HOOKS = ("extend_doctype_class", "override_doctype_class")


def strings(value) -> list[str]:
	if isinstance(value, str):
		return [value]
	if isinstance(value, list | tuple):
		return [s for v in value for s in strings(v)]
	return []


def app_doctypes(hooks: dict, app: str) -> dict[str, list[str]]:
	"""DocType → the class paths ``app`` hooks onto it, in hook order."""
	out: dict[str, list[str]] = {}
	for key in HOOKS:
		for doctype, value in (hooks.get(key) or {}).items():
			for path in strings(value):
				if path.startswith(f"{app}."):
					out.setdefault(doctype, []).append(path)
	return out


def app_first(order: list[str], app: str) -> list[str]:
	"""``order`` with ``app`` moved directly after frappe."""
	rest = [a for a in order if a not in ("frappe", app)]
	head = ["frappe"] if "frappe" in order else []
	return [*head, app, *rest] if app in order else list(order)


def layers(cls: type) -> list[str]:
	"""The controller's MRO as dotted names, up to and excluding ``object``."""
	return [f"{c.__module__}.{c.__qualname__}" for c in cls.__mro__ if c is not object]


class Orders:
	"""Builds controllers with frappe's own code under a chosen installed-apps order.

	``frappe.get_installed_apps`` is replaced for the duration, and so is
	``frappe.get_hooks``, by one that re-reads every app's hooks in that order on each call:
	frappe caches the merged hooks per site and per request, and the controller cache per
	site, and none of those caches knows the order changed.
	"""

	def __init__(self, frappe):
		self.frappe = frappe
		self.real_installed = frappe.get_installed_apps
		self.real_hooks = frappe.get_hooks

	def controller(self, doctype: str, order: list[str] | None):
		from frappe.model.base_document import get_controller

		frappe = self.frappe
		if order is not None:

			def installed(*args, **kwargs):
				return list(order)

			def hooks(hook=None, default="_KEEP_DEFAULT_LIST", app_name=None):
				if app_name:
					return self.real_hooks(hook, default, app_name)
				merged = frappe._dict(frappe._load_app_hooks())
				if hook:
					return merged.get(hook, [] if default == "_KEEP_DEFAULT_LIST" else default)
				return merged

			frappe.get_installed_apps = installed
			frappe.get_hooks = hooks
		try:
			frappe.controllers = {}
			frappe.clear_cache()
			return get_controller(doctype)
		finally:
			frappe.get_installed_apps = self.real_installed
			frappe.get_hooks = self.real_hooks
			frappe.controllers = {}


def check(frappe, app: str) -> dict:
	"""The composition report: per DocType, the real order's MRO and both verdicts."""
	merged = frappe.get_hooks()
	targets = app_doctypes(merged, app)
	real = frappe.get_installed_apps()
	orders = Orders(frappe)
	report: dict = {
		"app": app,
		"ok": True,
		"order": real,
		"app_first_order": app_first(real, app),
		"doctypes": {},
	}
	for doctype, paths in sorted(targets.items()):
		entry: dict = {"classes": paths}
		for label, order in (("real", None), ("app_first", app_first(real, app))):
			try:
				controller = orders.controller(doctype, order)
				classes = [frappe.get_attr(p) for p in paths]
				missing = [p for p, c in zip(paths, classes, strict=True) if c not in controller.__mro__]
				if missing:
					entry[f"{label}_ok"] = False
					entry[f"{label}_error"] = f"{', '.join(missing)} not in the controller's MRO"
				else:
					entry[f"{label}_ok"] = True
				if label == "real":
					entry["layers"] = layers(controller)
			except TypeError as e:
				entry[f"{label}_ok"] = False
				entry[f"{label}_error"] = f"TypeError: {e}"
			except Exception as e:
				entry[f"{label}_ok"] = False
				entry[f"{label}_error"] = f"{type(e).__name__}: {e}"
		if not (entry["real_ok"] and entry["app_first_ok"]):
			report["ok"] = False
		report["doctypes"][doctype] = entry
	return report


def main(argv=None) -> int:
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("--site", required=True)
	parser.add_argument("--app", required=True)
	parser.add_argument("--sites-path", default=".")
	parser.add_argument("--out", required=True, type=Path)
	args = parser.parse_args(argv)

	import frappe

	try:
		frappe.init(site=args.site, sites_path=str(args.sites_path))
		frappe.connect()
	except Exception as e:
		print(f"composition: cannot connect to {args.site}: {type(e).__name__}: {e}", file=sys.stderr)
		return 3
	try:
		report = check(frappe, args.app)
	finally:
		frappe.destroy()
	args.out.write_text(json.dumps(report, indent=2) + "\n")
	return 0


if __name__ == "__main__":
	sys.exit(main())
