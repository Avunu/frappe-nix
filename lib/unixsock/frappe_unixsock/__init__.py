"""Make Frappe honour a unix socket where it only half-does.

Loaded at interpreter start from ``zzz-frappe-unixsock.pth`` inside the
virtualenv, i.e. *below* the Frappe app layer, so it applies to ``bench serve``,
``worker``, ``schedule``, ``console`` and any bare ``env/bin/python`` without an
app install or a ``site_config.json`` edit.

Frappe already speaks unix sockets for most of its backing services — MariaDB
via ``db_socket``, both Redis instances via ``unix://`` URLs, the realtime
server via ``socketio_uds``. What is left is a short list of places where the
support was never wired through, and each one silently degrades to TCP rather
than failing, which is the dangerous shape: on a machine running several benches
at once, "fell back to TCP" means "connected to another project's service".

* :mod:`.web` — ``frappe/app.py`` hardcodes ``run_simple("0.0.0.0", int(port))``,
  so the development server cannot bind a socket at all.
* :mod:`.database` — ``frappe.connect_replica`` hardcodes ``socket=None``.

Unlike ``frappe_devguard``, this ships to **production as well** — it is
installed into ``prodPythonEnv`` and therefore into ``builtBench``, the OCI
images and ``services.frappe``. That is deliberate, and it is not a weakening of
devguard's dev-only guarantee: the two packages are separate, and nothing here
imports anything from there. These are transport corrections that a socket-mode
deployment needs precisely *because* it is production; devguard's job is to stop
a development bench reaching production, which is meaningless there.

Every patch is gated on its socket actually being configured in the environment.
A bench with no sockets installs nothing, so it cannot be broken by a Frappe
upgrade moving one of these targets. A bench that *is* on sockets fails loudly
instead — see :func:`._patch.require`.

``FRAPPE_UNIXSOCK_ENABLED=0`` gives byte-for-byte stock behaviour for a single
command.
"""

from ._settings import UnixSocketPatchError, UnixSocketPathError, enabled

__all__ = [
    "UnixSocketPathError",
    "UnixSocketPatchError",
    "enabled",
    "install",
    "status",
]

_INSTALLED = False

#: Order is irrelevant — each module hooks a different import.
_MODULES = ("web", "database")


def install():
    """Install every patch whose socket is configured. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    if not enabled():
        return

    from importlib import import_module

    for name in _MODULES:
        import_module(f"{__name__}.{name}").install()


def status():
    """Per-target report of what was actually redirected."""
    from ._patch import STATUS

    return dict(STATUS)


# NB: install() is called by the .pth bootstrap, not here — importing the patch
# modules from this module's body would make `import frappe_unixsock.web`
# re-enter a partially initialised package.
