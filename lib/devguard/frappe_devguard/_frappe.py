"""frappe-nix's own patching helpers, the ones that know Frappe.

The core's helpers (python-devops-nix's ``_patch``: warn, announce, block,
mark, no_op, blocking, require, require_any, user_error) know no framework.
These do, and so are kept apart from them. Nothing here imports Frappe at
module scope: guards run from a post-import hook, so Frappe is only ever
touched from inside a callback.
"""

import functools
import inspect

from ._patch import announce, mark, user_error, warn
from ._settings import settings


def throw(guard, message):
	"""Announce and surface the message in the desk UI (``frappe.throw``).

	For deliberate user actions, a button or an API call, where a silent no-op
	would leave someone believing an upload happened.
	"""
	user_error(guard, message)


def throwing(guard, what, message, banner=None):
	"""Replacement that surfaces the reason in the desk UI.

	For deliberate user actions, a button or an API call, where a silent no-op
	would leave someone believing the action succeeded.
	"""

	def replacement(*_args, **_kwargs):
		if banner:
			announce(guard, banner)
		throw(guard, f"{what}: {message}")

	return mark(replacement, what)


def redirect_kwargs(func, self, args, kwargs, replacements):
	"""Rebind a call's arguments, overriding ``replacements`` by name.

	Used where the original callable validates its arguments before we would
	otherwise get a chance to fix them up.
	"""
	bound = inspect.signature(func).bind(self, *args, **kwargs)
	bound.apply_defaults()
	bound.arguments.update(replacements)
	return bound.args, bound.kwargs


def with_in_install(doc, original):
	"""Run ``original`` with ``frappe.local.flags.in_install`` set.

	Several validators gate their connection tests (and their "credentials are
	required" throws) on that flag. It is request-scoped and leaking it would
	skip cache clearing and Deleted Document bookkeeping for the rest of the
	request, so restore it in ``finally``.
	"""
	import frappe

	previous = frappe.local.flags.in_install
	frappe.local.flags.in_install = True
	try:
		return original(doc)
	finally:
		frappe.local.flags.in_install = previous


def _swap_in_registry(registry, original, replacement, *, register_if_missing):
	"""Replace ``original`` with ``replacement`` in one Frappe method registry.

	``whitelisted``/``guest_methods``/``xss_safe_methods`` are lists on Frappe
	v15 and sets from v16 on, so handle both rather than pinning devguard to a
	framework version — on a set, ``.index()`` raises ``AttributeError`` at
	import time and takes the whole bench command down with it.
	"""
	present = original in registry
	if not (present or register_if_missing):
		return

	if isinstance(registry, list):
		if present:
			registry[registry.index(original)] = replacement
		else:
			registry.append(replacement)
		return

	registry.discard(original)
	registry.add(replacement)


def rewhitelist(original, replacement):
	"""Swap a ``@frappe.whitelist()`` function for ``replacement``, in place.

	``frappe.whitelisted`` is a collection of function *objects* and
	``allowed_http_methods_for_whitelisted_func`` is a dict keyed on them, so
	simply reassigning the module attribute would leave the endpoint resolving
	to a function the framework does not recognise: ``is_whitelisted`` throws a
	misleading "not whitelisted" 403, and ``handler.py`` subscripts the methods
	dict directly, giving a raw KeyError. Swapping in place — rather than
	adding — also stops the original object lingering as a live entry.
	"""
	import frappe

	functools.update_wrapper(replacement, original)
	mark(replacement, f"{original.__module__}.{original.__name__}")

	# register_if_missing: not decorated the way we expected; register rather
	# than lose the endpoint. The guest registries get no such fallback — adding
	# there would *widen* access instead of preserving it.
	_swap_in_registry(frappe.whitelisted, original, replacement, register_if_missing=True)

	methods = frappe.allowed_http_methods_for_whitelisted_func.pop(original, None)
	if methods is not None:
		frappe.allowed_http_methods_for_whitelisted_func[replacement] = methods

	for registry in (frappe.guest_methods, frappe.xss_safe_methods):
		_swap_in_registry(registry, original, replacement, register_if_missing=False)

	return replacement


def assert_not_overridden(guard, cmds):
	"""Warn if an app remaps a protected endpoint out from under us.

	``override_whitelisted_methods`` is consulted *before* ``get_attr`` in
	``frappe/handler.py``, so an app owning one of these keys would route
	around the patch entirely. cloud_storage already uses that hook.
	"""
	import frappe

	try:
		overrides = frappe.get_hooks("override_whitelisted_methods") or {}
	except Exception:  # no site context; nothing to check against
		return
	for cmd in cmds:
		if cmd in overrides:
			warn(
				guard,
				f"{cmd} is remapped by override_whitelisted_methods — the guard on it is bypassed",
			)


def all_subclasses(base):
	seen = []
	pending = list(base.__subclasses__())
	while pending:
		cls = pending.pop()
		if cls in seen:
			continue
		seen.append(cls)
		pending.extend(cls.__subclasses__())
	return seen


def disarm_subclasses(base, names):
	"""Strip overriding attributes from subclasses — now and in the future.

	``override_doctype_class`` subclasses shadow the methods we patch, and they
	are imported lazily on the first ``get_controller`` call, so a one-shot
	sweep would miss them. Hooking ``__init_subclass__`` disarms whichever ones
	show up later.
	"""

	def disarm(cls):
		for name in names:
			if name in cls.__dict__:
				delattr(cls, name)

	for subclass in all_subclasses(base):
		disarm(subclass)

	original = base.__dict__.get("__init_subclass__")

	def __init_subclass__(cls, **kwargs):
		if original is not None:
			original.__func__(cls, **kwargs)
		else:
			super(base, cls).__init_subclass__(**kwargs)
		if settings().enabled:
			disarm(cls)

	base.__init_subclass__ = classmethod(__init_subclass__)
