"""Self-contained tests for frappe_unixsock.

Runs without Frappe, without werkzeug, without a site and without network
access. Stub modules stand in for ``frappe``, ``frappe.app``, ``frappe.database``
and ``werkzeug.serving``; the assertions are about *which address the server was
told to bind* and *which socket the replica was told to connect to*, which is
the only property that matters.

Run directly (``python test_unixsock.py``) or via ``nix flake check``.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"ok   {label}")
    else:
        print(f"FAIL {label} {detail}")
        FAILURES.append(label)


def expect_raises(label, exc_type, fn):
    try:
        fn()
    except exc_type:
        print(f"ok   {label}")
    except Exception as exc:  # noqa: BLE001 - the point is to report the mismatch
        print(f"FAIL {label} raised {type(exc).__name__}: {exc}")
        FAILURES.append(label)
    else:
        print(f"FAIL {label} did not raise")
        FAILURES.append(label)


from frappe_unixsock import _settings, database, web  # noqa: E402
from frappe_unixsock._settings import UnixSocketPathError  # noqa: E402
from frappe_unixsock._patch import require, swapped  # noqa: E402
from frappe_unixsock._settings import UnixSocketPatchError  # noqa: E402

SOCK = "/run/user/1000/devenv-abcdef0/web.sock"


def clear_env():
    for name in (
        "FRAPPE_WEB_SOCKET",
        "FRAPPE_REPLICA_DB_SOCKET",
        "FRAPPE_UNIXSOCK_ENABLED",
    ):
        os.environ.pop(name, None)


# --------------------------------------------------------------------------
# settings resolution
# --------------------------------------------------------------------------
print("== settings ==")
clear_env()
check("unset socket resolves to None", _settings.socket_path("FRAPPE_WEB_SOCKET") is None)

os.environ["FRAPPE_WEB_SOCKET"] = "   "
check("blank socket resolves to None", _settings.socket_path("FRAPPE_WEB_SOCKET") is None)

os.environ["FRAPPE_WEB_SOCKET"] = SOCK
check("configured socket resolves", _settings.socket_path("FRAPPE_WEB_SOCKET") == SOCK)

os.environ["FRAPPE_UNIXSOCK_ENABLED"] = "0"
check(
    "FRAPPE_UNIXSOCK_ENABLED=0 disables resolution",
    _settings.socket_path("FRAPPE_WEB_SOCKET") is None,
)
os.environ.pop("FRAPPE_UNIXSOCK_ENABLED")

# The relative-path trap: node's `connStr.replace("unix://", "")` and Python's
# urlparse disagree about what a two-slash URL means, so a relative path must
# never reach either of them.
os.environ["FRAPPE_WEB_SOCKET"] = "run/user/1000/web.sock"
expect_raises(
    "relative socket path is rejected",
    UnixSocketPathError,
    lambda: _settings.socket_path("FRAPPE_WEB_SOCKET"),
)

# The failure this actually catches in the wild: a socket under a long project
# path, which fails as a bare "AF_UNIX path too long" from inside the server.
os.environ["FRAPPE_WEB_SOCKET"] = "/" + ("a" * 200) + "/web.sock"
expect_raises(
    "over-long socket path is rejected up front",
    UnixSocketPathError,
    lambda: _settings.socket_path("FRAPPE_WEB_SOCKET"),
)
clear_env()


# --------------------------------------------------------------------------
# _patch.swapped
# --------------------------------------------------------------------------
print()
print("== swapped ==")
holder = types.ModuleType("holder")
holder.target = lambda: "original"

with swapped(holder, "target", lambda real: (lambda: "replaced")):
    check("swapped installs the replacement", holder.target() == "replaced")
check("swapped restores on exit", holder.target() == "original")

try:
    with swapped(holder, "target", lambda real: (lambda: "replaced")):
        raise RuntimeError("boom")
except RuntimeError:
    pass
check("swapped restores on exception", holder.target() == "original")

expect_raises(
    "require() raises when the target has moved",
    UnixSocketPatchError,
    lambda: require(holder, "no_such_attribute"),
)


# --------------------------------------------------------------------------
# web: the bind address
# --------------------------------------------------------------------------
print()
print("== web ==")


def make_werkzeug():
    """Stub werkzeug.serving, recording what run_simple was handed."""
    serving = types.ModuleType("werkzeug.serving")
    serving.calls = []

    def run_simple(hostname, port, application, **kwargs):
        serving.calls.append((hostname, port, kwargs))
        return "served"

    serving.run_simple = run_simple
    pkg = types.ModuleType("werkzeug")
    pkg.serving = serving
    sys.modules["werkzeug"] = pkg
    sys.modules["werkzeug.serving"] = serving
    return serving


def make_frappe_app():
    """Stub frappe.app, whose serve() imports run_simple the way Frappe does."""
    module = types.ModuleType("frappe.app")

    def serve(port=8000, **kwargs):
        from werkzeug.serving import run_simple

        return run_simple("0.0.0.0", int(port), "application", threaded=True)

    module.serve = serve
    return module


serving = make_werkzeug()

# 1. No socket configured: Frappe must be left exactly as it was found.
clear_env()
app = make_frappe_app()
original_serve = app.serve
web._patch_app(app)
check("no socket configured leaves serve() untouched", app.serve is original_serve)
app.serve(port=8000)
check("...and the server still binds TCP", serving.calls[-1][0] == "0.0.0.0")

# 2. Socket configured: the bind address is rewritten, everything else survives.
os.environ["FRAPPE_WEB_SOCKET"] = SOCK
app = make_frappe_app()
web._patch_app(app)
check("socket configured replaces serve()", app.serve is not original_serve)
result = app.serve(port=8889)
hostname, port, kwargs = serving.calls[-1]
check("bind address is the unix socket", hostname == f"unix://{SOCK}", hostname)
check("port is dropped for AF_UNIX", port == 0, port)
check("other run_simple kwargs pass through", kwargs.get("threaded") is True, kwargs)
check("serve()'s return value passes through", result == "served")
check(
    "run_simple is restored after serve() returns",
    serving.run_simple.__name__ == "run_simple"
    and getattr(serving.run_simple, "__unixsock__", None) is None,
)

# 3. Disabled mid-flight: falls back to stock behaviour without re-patching.
os.environ["FRAPPE_UNIXSOCK_ENABLED"] = "0"
app.serve(port=8889)
check("FRAPPE_UNIXSOCK_ENABLED=0 falls back to TCP", serving.calls[-1][0] == "0.0.0.0")
os.environ.pop("FRAPPE_UNIXSOCK_ENABLED")

# 4. A moved target is loud, but only when a socket is configured.
app = make_frappe_app()
del app.serve
expect_raises(
    "a missing serve() fails the import loudly",
    UnixSocketPatchError,
    lambda: web._patch_app(app),
)
clear_env()
app = make_frappe_app()
del app.serve
web._patch_app(app)
check("...but is silent when no socket is configured", not hasattr(app, "serve"))

# 5. The hook wiring: a module already in sys.modules is patched immediately.
os.environ["FRAPPE_WEB_SOCKET"] = SOCK
web._INSTALLED = False
sys.modules["frappe.app"] = make_frappe_app()
web.install()
sys.modules["frappe.app"].serve(port=1)
check("install() patches an already-imported frappe.app", serving.calls[-1][0] == f"unix://{SOCK}")
del sys.modules["frappe.app"]
clear_env()


# --------------------------------------------------------------------------
# database: the replica connection
# --------------------------------------------------------------------------
print()
print("== replica ==")

REPLICA_SOCK = "/run/user/1000/devenv-abcdef0/replica.sock"


def make_frappe():
    """Stub frappe + frappe.database, mirroring connect_replica's real shape."""
    db = types.ModuleType("frappe.database")
    db.calls = []

    def get_db(**kwargs):
        db.calls.append(kwargs)
        return "connection"

    db.get_db = get_db

    module = types.ModuleType("frappe")

    def connect_replica():
        from frappe.database import get_db

        return get_db(
            socket=None,
            host="replica.internal",
            port=3306,
            user="u",
            password="p",
            cur_db_name="db",
        )

    module.connect_replica = connect_replica
    module.database = db
    sys.modules["frappe"] = module
    sys.modules["frappe.database"] = db
    return module, db


clear_env()
frappe, db = make_frappe()
untouched = frappe.connect_replica
database._patch_frappe(frappe)
check("no replica socket leaves connect_replica untouched", frappe.connect_replica is untouched)

os.environ["FRAPPE_REPLICA_DB_SOCKET"] = REPLICA_SOCK
frappe, db = make_frappe()
database._patch_frappe(frappe)
frappe.connect_replica()
call = db.calls[-1]
check("replica connects over the socket", call["socket"] == REPLICA_SOCK, call)
check("replica_host is cleared so postgres cannot prefer it", call["host"] is None, call)
check("credentials pass through", call["user"] == "u" and call["cur_db_name"] == "db", call)
check(
    "get_db is restored after connect_replica returns",
    getattr(db.get_db, "__unixsock__", None) is None,
)

# A positional call would make the overrides land on the wrong parameter, so it
# is passed through rather than guessed at.
frappe, db = make_frappe()


def positional_connect_replica():
    from frappe.database import get_db

    return get_db("sock", "host")


db.get_db = lambda *a, **k: db.calls.append((a, k))
frappe.connect_replica = positional_connect_replica
database._patch_frappe(frappe)
frappe.connect_replica()
check("a positional get_db() call is passed through untouched", db.calls[-1] == (("sock", "host"), {}))

del sys.modules["frappe"]
del sys.modules["frappe.database"]
clear_env()

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
    sys.exit(1)
print("all unixsock checks passed")
