"""Shared patching helpers used by every guard.

Nothing here imports Frappe at module scope: guards run from a post-import
hook, so Frappe is only ever touched from inside a callback.
"""

import functools
import inspect
import sys

from ._settings import DevGuardBlocked, DevGuardPatchError, settings

#: target -> "patched" | "not installed". Surfaced by `devguard-status` so an
#: inert guard is visible; a module whose SDK is missing never reaches its
#: post-import callback, which is fail-safe but otherwise indistinguishable
#: from a guard that ran.
STATUS = {}

_ANNOUNCED = set()


def announce(guard, detail):
    """Emit a guard's activation banner once per process, on first use.

    Deferred rather than printed at import so the many Python processes a bench
    starts which never reach a guarded code path (`bench build`, tooling,
    editor helpers) stay quiet.
    """
    if guard in _ANNOUNCED:
        return
    _ANNOUNCED.add(guard)
    warn(guard, f"ACTIVE — {detail}")


def warn(guard, message):
    """Announce loudly, by two routes.

    Raising is not enough on the scheduler path: Frappe's backup integrations
    catch Exception and turn it into a "backup failed" email,
    ScheduledJobType.execute catches it and records status "Failed", and
    Dropbox's upload_from_folder swallows per-file exceptions into a list. A
    guard that only raised would be invisible.
    """
    sys.stderr.write(f"WARNING frappe_devguard[{guard}] {message}\n")
    try:
        import frappe

        frappe.logger("frappe_devguard").warning(f"[{guard}] {message}")
    except Exception:  # noqa: BLE001 - logging must never be the thing that fails
        pass


def block(guard, message):
    """Announce and raise. For code paths where failing is the right answer."""
    warn(guard, message)
    raise DevGuardBlocked(f"frappe_devguard[{guard}]: {message}")


def throw(guard, message):
    """Announce and surface the message in the desk UI.

    For deliberate user actions — a button, an API call — where a silent no-op
    would leave someone believing an upload happened.
    """
    import frappe

    warn(guard, message)
    frappe.throw(message, title="Blocked by frappe-devguard")


def no_op(guard, what, banner=None):
    """Replacement that logs and returns None.

    For scheduler entry points, where manufacturing a failure would only fill
    Scheduled Job Log with noise nobody reads.
    """

    def replacement(*_args, **_kwargs):
        if banner:
            announce(guard, banner)
        warn(guard, f"skipping {what}")
        return None

    return mark(replacement, what)


def blocking(guard, what, message, banner=None):
    """Replacement that logs and raises. For the funnels that must not proceed."""

    def replacement(*_args, **_kwargs):
        if banner:
            announce(guard, banner)
        block(guard, f"{what}: {message}")

    return mark(replacement, what)


def throwing(guard, what, message, banner=None):
    """Replacement that surfaces the reason in the desk UI.

    For deliberate user actions — a button, an API call — where a silent no-op
    would leave someone believing the action succeeded.
    """

    def replacement(*_args, **_kwargs):
        if banner:
            announce(guard, banner)
        throw(guard, f"{what}: {message}")

    return mark(replacement, what)


def mark(fn, target):
    """Tag a replacement so it is identifiable after ``update_wrapper``.

    ``rewhitelist`` copies the original's ``__name__``/``__module__`` on
    purpose, so introspection cannot otherwise tell a guarded endpoint from an
    untouched one.
    """
    fn.__devguard__ = target
    return fn


def is_guarded(fn):
    return getattr(fn, "__devguard__", None) is not None


def require(owner, name):
    """Fetch a patch target, or fail the import loudly."""
    value = getattr(owner, name, None)
    if value is None:
        label = getattr(owner, "__qualname__", None) or getattr(owner, "__name__", owner)
        raise DevGuardPatchError(
            f"frappe_devguard: {label}.{name} is missing — Frappe's internals have moved "
            "and this guard can no longer be relied on. Update frappe-nix, or set "
            "FRAPPE_DEVGUARD_ENABLED=0 to run without it."
        )
    STATUS[f"{getattr(owner, '__name__', owner)}.{name}"] = "patched"
    return value


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


def rewhitelist(original, replacement):
    """Swap a ``@frappe.whitelist()`` function for ``replacement``, in place.

    ``frappe.whitelisted`` is a list of function *objects* and
    ``allowed_http_methods_for_whitelisted_func`` is a dict keyed on them, so
    simply reassigning the module attribute would leave the endpoint resolving
    to a function the framework does not recognise: ``is_whitelisted`` throws a
    misleading "not whitelisted" 403, and ``handler.py`` subscripts the methods
    dict directly, giving a raw KeyError. Swapping in place — rather than
    appending — also stops the original object lingering as a live entry.
    """
    import frappe

    functools.update_wrapper(replacement, original)
    mark(replacement, f"{original.__module__}.{original.__name__}")

    if original in frappe.whitelisted:
        frappe.whitelisted[frappe.whitelisted.index(original)] = replacement
    else:  # not decorated the way we expected; register rather than lose the endpoint
        frappe.whitelisted.append(replacement)

    methods = frappe.allowed_http_methods_for_whitelisted_func.pop(original, None)
    if methods is not None:
        frappe.allowed_http_methods_for_whitelisted_func[replacement] = methods

    for registry in (frappe.guest_methods, frappe.xss_safe_methods):
        if original in registry:
            registry[registry.index(original)] = replacement

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
    except Exception:  # noqa: BLE001 - no site context; nothing to check against
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
