"""Let the read replica connect over a unix socket.

``frappe.connect()`` passes ``socket=conf.db_socket`` through to ``get_db``, so
the primary honours a socket. ``frappe.connect_replica()`` — twenty lines below
it — hardcodes ``socket=None``::

    local.replica_db = get_db(
        socket=None,
        host=local.conf.replica_host,
        ...
    )

so a site with ``read_from_replica`` enabled silently keeps one TCP connection
open no matter how the rest of it is configured. That is a production-only gap:
nothing in the development shell uses a replica.

Like the web patch, this leans on the fact that ``connect_replica`` resolves
``get_db`` by importing it inside its own body, so the collaborator can be
redirected for the length of one call rather than the caller reimplemented —
which matters here because ``connect_replica`` also does credential selection
and the primary/replica connection swap that we have no business duplicating.

Opt-in via ``FRAPPE_REPLICA_DB_SOCKET``: without it this module patches
nothing, so a TCP replica keeps working and cannot be broken by a target
moving upstream.

**Not patched:** ``PostgresDatabase.get_connection`` has the same class of bug —
``"host": self.host or self.socket`` can never reach ``self.socket``, because
``frappe/config.py`` always defaults ``db_host`` to ``127.0.0.1`` — but every
path in frappe-nix hardcodes ``db_type = "mariadb"`` (``modules/nixos.nix``,
``modules/containers.nix``, the dev shell), so a patch for it would be dead code
carrying real upgrade risk. Worth fixing upstream instead.
"""

from ._hook import on_import
from ._patch import announce, mark, require, swapped
from ._settings import socket_path, warn

NAME = "replica"

#: Socket the read replica listens on. Distinct from FRAPPE_DB_SOCKET: a replica
#: is a different server, usually on a different host, and sharing the primary's
#: socket path would silently read from the primary.
ENV = "FRAPPE_REPLICA_DB_SOCKET"

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("frappe", _patch_frappe)


def _patch_frappe(module):
    if socket_path(ENV) is None:
        return

    original = require(module, "connect_replica")

    def connect_replica(*args, **kwargs):
        path = socket_path(ENV)
        if path is None:
            return original(*args, **kwargs)

        import frappe.database

        announce(NAME, f"read replica connecting over unix://{path}")
        with swapped(frappe.database, "get_db", lambda real: _via_socket(real, path)):
            return original(*args, **kwargs)

    module.connect_replica = mark(connect_replica, "frappe.connect_replica")


def _via_socket(real, path):
    """Force the replica's connection onto the socket.

    ``host`` is cleared as well as ``socket`` being set. MariaDB ignores the
    host once ``unix_socket`` is present, but Postgres reads
    ``self.host or self.socket``, so leaving ``replica_host`` in place would
    keep it on TCP. An explicitly configured replica socket outranks the host.
    """

    def get_db(*args, **kwargs):
        if args:
            # get_db() is keyword-only at every call site in Frappe; a
            # positional call would collide with the overrides below, and
            # guessing which position is which is how this starts connecting
            # to the wrong database.
            warn(f"{ENV} is set but get_db() was called positionally; leaving the replica on TCP")
            return real(*args, **kwargs)
        kwargs["socket"] = path
        kwargs["host"] = None
        return real(**kwargs)

    return mark(get_db, "frappe.database.get_db")
