"""Bind the development web server to a unix socket.

``frappe/app.py`` hardcodes the bind address::

    run_simple("0.0.0.0", int(port), application, ...)

so ``bench serve`` is TCP-only, and on all interfaces at that. Werkzeug itself
has supported unix sockets for years — ``select_address_family`` returns
``AF_UNIX`` for a ``unix://`` hostname, ``get_sockaddr`` strips the scheme, and
the bind path unlinks a stale socket file first — there is simply no way to
reach it through ``serve()``'s ``int(port)``.

``serve()`` resolves ``run_simple`` by importing it *inside the function body*,
so redirecting that one attribute for the duration of the call is enough, and
leaves ``serve()``'s own logic (profiler, statics, ProxyFix, reloader flags)
untouched. Reimplementing ``serve()`` here would mean re-deriving all of that
from Frappe on every upgrade.

The reloader keeps working. Werkzeug's parent process binds once with
``fd=None`` — unlinking any stale socket in the process — then re-execs itself
with ``WERKZEUG_SERVER_FD`` set; the child takes the ``socket.fromfd`` branch
and never rebinds, so there is no unlink race and no window where the socket is
missing. Requests queue in the listen backlog across a reload instead of being
refused, which is strictly better than the TCP behaviour it replaces.

In production this is inert: ``services.frappe`` and the OCI images run gunicorn,
which takes ``--bind unix:...`` natively. It ships there anyway so that a
``bench serve`` run by hand on a deployed host lands on the same socket as
everything else rather than opening a surprise port on 0.0.0.0.
"""

from ._hook import on_import
from ._patch import announce, mark, require, swapped
from ._settings import socket_path

NAME = "web"

#: Path the development server should bind. Set by the devenv shell, and by
#: `services.frappe` when the site is in socket mode.
ENV = "FRAPPE_WEB_SOCKET"

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("frappe.app", _patch_app)


def _patch_app(module):
    # Resolve the setting before touching Frappe: with no socket configured
    # this package must leave the module exactly as it found it, so that a
    # TCP deployment cannot be broken by a patch target moving upstream.
    if socket_path(ENV) is None:
        return

    original = require(module, "serve")

    def serve(*args, **kwargs):
        path = socket_path(ENV)
        if path is None:  # unset between import and call, e.g. FRAPPE_UNIXSOCK_ENABLED=0
            return original(*args, **kwargs)

        import werkzeug.serving

        announce(NAME, f"development server binding unix://{path}")
        with swapped(werkzeug.serving, "run_simple", lambda real: _bind_unix(real, path)):
            return original(*args, **kwargs)

    module.serve = mark(serve, "frappe.app.serve")


def _bind_unix(real, path):
    """Replace the (host, port) pair with a unix address.

    The port is dropped rather than passed through: werkzeug ignores it for
    ``AF_UNIX``, and `bench serve --port` still has a meaning in this setup —
    it is the port nginx is listening on out front, which callers keep passing.
    """

    def run_simple(_hostname, _port, application, **kwargs):
        return real(f"unix://{path}", 0, application, **kwargs)

    return mark(run_simple, "werkzeug.serving.run_simple")
