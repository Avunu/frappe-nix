"""Shared patching helpers.

Nothing here imports Frappe at module scope: the patches run from a post-import
hook, so Frappe is only ever touched from inside a callback.

Deliberately smaller than ``frappe_devguard._patch``. A guard has to survive
being routed around — hence that module's ``rewhitelist``, ``disarm_subclasses``
and friends. This package only ever swaps a *transport*: one callable that
decides where a server binds or a client connects. If the swap does not take,
the result is a connection on the wrong transport, which fails immediately and
visibly rather than silently doing something dangerous.
"""

import sys

from ._settings import UnixSocketPatchError, warn

#: target -> "patched". Surfaced by `frappe-unixsock-status` so a hop that was
#: expected to move and did not is visible.
STATUS = {}

_ANNOUNCED = set()


def announce(what, detail):
    """Emit an activation banner once per process, on first use.

    Deferred rather than printed at import, so the many short-lived Python
    processes a bench starts which never bind or connect anything stay quiet.
    """
    if what in _ANNOUNCED:
        return
    _ANNOUNCED.add(what)
    sys.stderr.write(f"frappe_unixsock[{what}] {detail}\n")


def require(owner, name):
    """Fetch a patch target, or fail the import loudly.

    Only ever reached once a socket has been configured — see the module
    docstring of ``_settings``. That ordering is the whole safety story for
    shipping this to production: a bench with no socket configured installs no
    patches, so a Frappe upgrade that moves one of these targets cannot break
    it. A bench that *is* on sockets would otherwise silently fall back to TCP,
    which in a multi-project or multi-tenant setting means connecting to
    somebody else's service — much worse than a loud import error.
    """
    value = getattr(owner, name, None)
    if value is None:
        label = getattr(owner, "__qualname__", None) or getattr(owner, "__name__", owner)
        raise UnixSocketPatchError(
            f"frappe_unixsock: {label}.{name} is missing — Frappe's internals have moved "
            "and this transport can no longer be redirected to a unix socket. Update "
            "frappe-nix, or set FRAPPE_UNIXSOCK_ENABLED=0 to run on TCP."
        )
    STATUS[f"{getattr(owner, '__name__', owner)}.{name}"] = "patched"
    return value


def swapped(owner, name, replacement):
    """Context manager swapping ``owner.name`` for the duration of a call.

    Both patches here work the same way: the function we want to influence
    resolves its collaborator by attribute lookup at call time (``frappe/app.py``
    imports ``run_simple`` inside ``serve()``; ``connect_replica`` imports
    ``get_db`` inside itself), so redirecting the collaborator for exactly the
    length of one call is both sufficient and far less invasive than
    reimplementing the caller.
    """

    class _Swap:
        def __enter__(self):
            self.original = require(owner, name)
            setattr(owner, name, replacement(self.original))
            return self.original

        def __exit__(self, *exc):
            setattr(owner, name, self.original)
            return False

    return _Swap()


def mark(fn, target):
    """Tag a replacement so it stays identifiable after ``update_wrapper``."""
    fn.__unixsock__ = target
    return fn


__all__ = ["STATUS", "announce", "mark", "require", "swapped", "warn"]
