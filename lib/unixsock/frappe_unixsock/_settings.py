"""Settings and shared exception types.

Unlike ``frappe_devguard``, nothing here is baked in by Nix. Every setting is a
*runtime fact* — the path of a socket that some other process in this bench is
listening on — rather than a policy decision, so the environment is the only
sensible source and there is nothing to keep in sync at build time.

Read at call time, so a single command can opt out with
``FRAPPE_UNIXSOCK_ENABLED=0`` without a rebuild.
"""

import os
import socket
import sys

#: Linux caps ``sockaddr_un.sun_path`` at 108 bytes including the NUL; the BSDs
#: are stricter still (104). Binding past it fails with a bare "AF_UNIX path too
#: long" from deep inside the server, so check up front and say which path.
SUN_PATH_MAX = 100

_TRUTHY = {"1", "true", "yes", "on", "t", "y"}
_FALSY = {"0", "false", "no", "off", "f", "n", ""}


class UnixSocketPatchError(RuntimeError):
    """A patch target went missing — Frappe's internals moved.

    Raised from inside the post-import hook so the offending ``import`` fails
    loudly instead of leaving a hop silently back on TCP. Only ever raised for
    a socket that was actually configured: with no socket in the environment
    this package installs nothing and cannot fail.
    """


class UnixSocketPathError(ValueError):
    """A configured socket path cannot work as an AF_UNIX address."""


def enabled():
    value = os.environ.get("FRAPPE_UNIXSOCK_ENABLED")
    if value is None:
        return True
    text = value.strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return True


def socket_path(name):
    """Return the socket path configured in ``name``, or None.

    Validates eagerly: a too-long path is a configuration error worth naming,
    not something to discover as an opaque OSError once the server starts.
    """
    if not enabled():
        return None
    path = (os.environ.get(name) or "").strip()
    if not path:
        return None
    if not path.startswith("/"):
        raise UnixSocketPathError(
            f"frappe_unixsock: {name}={path!r} is relative; an AF_UNIX address must be absolute"
        )
    if len(path.encode()) > SUN_PATH_MAX:
        raise UnixSocketPathError(
            f"frappe_unixsock: {name}={path!r} is {len(path.encode())} bytes, over the "
            f"{SUN_PATH_MAX}-byte AF_UNIX limit. Put the socket somewhere shorter — "
            "$DEVENV_RUNTIME in development, or the site's own runtime dir in production."
        )
    if not hasattr(socket, "AF_UNIX"):
        raise UnixSocketPathError(
            f"frappe_unixsock: {name} is set but this platform has no AF_UNIX support"
        )
    return path


def warn(message):
    sys.stderr.write(f"WARNING frappe_unixsock {message}\n")
