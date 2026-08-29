"""Self-contained tests for the Frappe-independent parts of frappe_devguard.

Runs without Frappe, without a site and without network access: stub SMTP/POP3
servers stand in for Mailpit, and the assertions are about *where the socket
landed*, which is the only property that actually matters.

Run directly (``python test_devguard.py``) or via ``nix flake check``.
"""

import os
import socket
import socketserver
import sys
import threading

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


# --------------------------------------------------------------------------
# stub servers
# --------------------------------------------------------------------------


class _LineServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    greeting = b""
    replies = {}
    default_reply = b""
    quit_command = b""


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        stream = self.request.makefile("rwb")
        stream.write(self.server.greeting)
        stream.flush()
        while True:
            line = stream.readline()
            if not line:
                return
            command = (line.split() or [b""])[0].upper()
            stream.write(self.server.replies.get(command, self.server.default_reply))
            stream.flush()
            if command == self.server.quit_command:
                return


def start_server(greeting, replies, default_reply, quit_command):
    server = _LineServer(("127.0.0.1", 0), _Handler)
    server.greeting = greeting
    server.replies = replies
    server.default_reply = default_reply
    server.quit_command = quit_command
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


smtp_server, SMTP_PORT = start_server(
    b"220 stub ESMTP\r\n",
    {b"EHLO": b"250 stub\r\n", b"HELO": b"250 stub\r\n", b"QUIT": b"221 bye\r\n"},
    b"250 ok\r\n",
    b"QUIT",
)
pop3_server, POP3_PORT = start_server(
    b"+OK stub POP3\r\n",
    {b"QUIT": b"+OK bye\r\n"},
    b"+OK\r\n",
    b"QUIT",
)

os.environ["FRAPPE_DEVGUARD_MAIL_HOST"] = "127.0.0.1"
os.environ["FRAPPE_DEVGUARD_MAIL_PORT"] = str(SMTP_PORT)
os.environ["FRAPPE_DEVGUARD_MAIL_POP3_PORT"] = str(POP3_PORT)
for stale in ("FRAPPE_DEVGUARD_ENABLED", "FRAPPE_DEVGUARD_DISABLE", "FRAPPE_DEVGUARD_MAIL_POP3_ENABLED"):
    os.environ.pop(stale, None)

import imaplib  # noqa: E402
import poplib  # noqa: E402
import smtplib  # noqa: E402

import frappe_devguard  # noqa: E402
from frappe_devguard import DevGuardBlocked, DevGuardPatchError  # noqa: E402

frappe_devguard.install()  # exactly what the .pth bootstrap does

from frappe_devguard._hook import on_import  # noqa: E402
from frappe_devguard._patch import disarm_subclasses, redirect_kwargs, require  # noqa: E402

# An unresolvable host is the point: if any of these reach DNS, the redirect
# did not happen and the test fails with gaierror rather than passing quietly.
ELSEWHERE = "smtp.devguard-must-not-resolve.invalid"


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

check("settings read from env", frappe_devguard.settings().mail_port == SMTP_PORT)
check("mail guard enabled by default", frappe_devguard.settings().guard_enabled("mail"))
check("incoming blocked by default", frappe_devguard.settings().block_incoming)

# The runtime file carries Mailpit's *allocated* ports, which only `devenv up`
# knows. It has to sit below env (so a single command can still be re-pointed)
# and above baked (so it wins when present) — but crucially it must NOT be the
# only source, or a bench outside devenv would fall back to the stock 1025 and
# mail into another project's catcher.
import json  # noqa: E402
import tempfile  # noqa: E402

from frappe_devguard import _settings as _dgs  # noqa: E402

_runtime_dir = tempfile.mkdtemp()
_runtime_file = os.path.join(_runtime_dir, "devguard-runtime.json")
with open(_runtime_file, "w") as _handle:
    json.dump({"guards": {"mail": {"port": 24680, "http_port": 24681}}}, _handle)

_saved_port = os.environ.pop("FRAPPE_DEVGUARD_MAIL_PORT", None)
os.environ["FRAPPE_DEVGUARD_RUNTIME"] = _runtime_file
_dgs._RUNTIME_CACHE.clear()
check("runtime file supplies the allocated port", frappe_devguard.settings().mail_port == 24680)
check("runtime file supplies the UI port", frappe_devguard.settings().mail_http_port == 24681)
check(
    "settings not in the runtime file still fall through",
    frappe_devguard.settings().mail_sender == "notifications@example.com",
)

os.environ["FRAPPE_DEVGUARD_MAIL_PORT"] = "24999"
check("env still outranks the runtime file", frappe_devguard.settings().mail_port == 24999)
os.environ.pop("FRAPPE_DEVGUARD_MAIL_PORT")

os.environ["FRAPPE_DEVGUARD_RUNTIME"] = os.path.join(_runtime_dir, "does-not-exist.json")
_dgs._RUNTIME_CACHE.clear()
check(
    "a missing runtime file falls back rather than raising",
    frappe_devguard.settings().mail_port == 1025,
)

with open(_runtime_file, "w") as _handle:
    _handle.write("{not json")
os.environ["FRAPPE_DEVGUARD_RUNTIME"] = _runtime_file
_dgs._RUNTIME_CACHE.clear()
check(
    "an unreadable runtime file falls back rather than raising",
    frappe_devguard.settings().mail_port == 1025,
)

os.environ.pop("FRAPPE_DEVGUARD_RUNTIME")
_dgs._RUNTIME_CACHE.clear()
if _saved_port is not None:
    os.environ["FRAPPE_DEVGUARD_MAIL_PORT"] = _saved_port


# --------------------------------------------------------------------------
# SMTP is redirected
# --------------------------------------------------------------------------

session = smtplib.SMTP(ELSEWHERE, 25)
check(
    "SMTP() lands on the catcher",
    session.sock.getpeername()[1] == SMTP_PORT,
    session.sock.getpeername(),
)
check("SMTP.login is faked", session.login("someone", "hunter2")[0] == 235)
check("SMTP.starttls is a no-op", session.starttls()[0] == 220)
session.quit()

session = smtplib.SMTP()
session.connect(ELSEWHERE, 587)
check(
    "explicit .connect() lands on the catcher",
    session.sock.getpeername()[1] == SMTP_PORT,
    session.sock.getpeername(),
)
session.quit()

session = smtplib.SMTP_SSL(ELSEWHERE, 465)
check(
    "SMTP_SSL lands on the catcher in plaintext",
    session.sock.getpeername()[1] == SMTP_PORT and not hasattr(session.sock, "cipher"),
    session.sock.getpeername(),
)
session.quit()

check("SMTP_SSL is still the stdlib class", smtplib.SMTP_SSL.__module__ == "smtplib")


# --------------------------------------------------------------------------
# incoming is blocked
# --------------------------------------------------------------------------

expect_raises("IMAP4 is blocked", DevGuardBlocked, lambda: imaplib.IMAP4(ELSEWHERE, 143))
expect_raises("IMAP4_SSL is blocked", DevGuardBlocked, lambda: imaplib.IMAP4_SSL(ELSEWHERE, 993))
expect_raises("POP3 is blocked", DevGuardBlocked, lambda: poplib.POP3(ELSEWHERE, 110))
expect_raises("POP3_SSL is blocked", DevGuardBlocked, lambda: poplib.POP3_SSL(ELSEWHERE, 995))
check("DevGuardBlocked is an OSError", issubclass(DevGuardBlocked, OSError))


# --------------------------------------------------------------------------
# incoming redirected to the catcher's POP3 when enabled
# --------------------------------------------------------------------------

os.environ["FRAPPE_DEVGUARD_MAIL_POP3_ENABLED"] = "1"
mailbox = poplib.POP3(ELSEWHERE, 110)
check(
    "POP3 lands on the catcher when enabled",
    mailbox.sock.getpeername()[1] == POP3_PORT,
    mailbox.sock.getpeername(),
)
check("POP3 credentials are substituted", mailbox.user("real@example.com").startswith(b"+OK"))
mailbox.quit()

mailbox = poplib.POP3_SSL(ELSEWHERE, 995)
check(
    "POP3_SSL lands on the catcher in plaintext",
    mailbox.sock.getpeername()[1] == POP3_PORT,
    mailbox.sock.getpeername(),
)
mailbox.quit()

expect_raises(
    "IMAP stays blocked in POP3 mode",
    DevGuardBlocked,
    lambda: imaplib.IMAP4(ELSEWHERE, 143),
)
os.environ.pop("FRAPPE_DEVGUARD_MAIL_POP3_ENABLED")


# --------------------------------------------------------------------------
# disabling restores stock behaviour
# --------------------------------------------------------------------------

for label, var, value in (
    ("globally", "FRAPPE_DEVGUARD_ENABLED", "0"),
    ("per guard", "FRAPPE_DEVGUARD_DISABLE", "backups,mail"),
):
    os.environ[var] = value
    check(f"mail guard reports disabled ({label})", not frappe_devguard.settings().guard_enabled("mail"))
    expect_raises(
        f"disabled SMTP resolves the real host ({label})",
        socket.gaierror,
        lambda: smtplib.SMTP(ELSEWHERE, 25, timeout=5),
    )
    expect_raises(
        f"disabled IMAP resolves the real host ({label})",
        socket.gaierror,
        lambda: imaplib.IMAP4(ELSEWHERE, 143),
    )
    session = smtplib.SMTP("127.0.0.1", SMTP_PORT)
    check(f"disabled SMTP still connects normally ({label})", session.sock.getpeername()[1] == SMTP_PORT)
    session.quit()
    os.environ.pop(var)

os.environ["FRAPPE_DEVGUARD_DISABLE"] = "backups"
check(
    "disabling one guard leaves the others alone",
    frappe_devguard.settings().guard_enabled("mail")
    and not frappe_devguard.settings().guard_enabled("backups"),
)
os.environ.pop("FRAPPE_DEVGUARD_DISABLE")


# --------------------------------------------------------------------------
# post-import hook
# --------------------------------------------------------------------------

fired = []
on_import("json.decoder", lambda module: fired.append(module.__name__))
sys.modules.pop("json.decoder", None)
sys.modules.pop("json", None)
import json  # noqa: E402, F401

check("post-import hook fires", fired == ["json.decoder"], fired)
check("hooked module still works", json.loads('{"a": 1}') == {"a": 1})

already = []
on_import("base64", lambda module: already.append(module.__name__))
check("hook on an imported module fires immediately", already == ["base64"], already)


# --------------------------------------------------------------------------
# subclass disarming (the mechanism that defeats override_doctype_class)
# --------------------------------------------------------------------------


class Base:
    def send(self):
        return "base"


class EarlySubclass(Base):
    def send(self):
        return "escaped"


disarm_subclasses(Base, ("send",))
check("existing subclass is disarmed", EarlySubclass().send() == "base")


class LateSubclass(Base):
    """Stands in for a controller imported later by get_controller."""

    def send(self):
        return "escaped"


check("late subclass is disarmed", LateSubclass().send() == "base")


class Untouched(Base):
    def other(self):
        return "kept"


check("unrelated attributes survive", Untouched().other() == "kept")


def _sample(self, server, port=None, password=None):
    return (server, port, password)


args, kwargs = redirect_kwargs(
    _sample, None, ("real.example.com",), {"password": "hunter2"}, {"server": "127.0.0.1", "port": 1025}
)
check(
    "call arguments are rewritten before delegation",
    _sample(*args, **kwargs) == ("127.0.0.1", 1025, "hunter2"),
    _sample(*args, **kwargs),
)

expect_raises(
    "a missing patch target fails loudly",
    DevGuardPatchError,
    lambda: require(Base, "no_such_method"),
)


# --------------------------------------------------------------------------
# the synthesised account used on sites with no Email Account at all
# --------------------------------------------------------------------------

from frappe_devguard.guards.mail import _synthetic_account  # noqa: E402


class FakeEmailAccount:
    @classmethod
    def from_record(cls, record):
        account = cls()
        account.record = record
        return account


record = _synthetic_account(FakeEmailAccount).record
check("synthetic account targets the catcher", record["smtp_server"] == "127.0.0.1")
check("synthetic account uses the catcher port", record["smtp_port"] == SMTP_PORT)
check("synthetic account carries no password", "password" not in record)
check(
    "synthetic account cannot negotiate TLS",
    record["use_tls"] == 0 and record["use_ssl_for_outgoing"] == 0,
)
check(
    "synthetic account never touches a mailbox",
    record["enable_incoming"] == 0 and record["append_emails_to_sent_folder"] == 0,
)
check(
    "synthetic account preserves the real sender",
    record["always_use_account_email_id_as_sender"] == 0,
)


# --------------------------------------------------------------------------
# whitelist swap — a replacement the framework still recognises
# --------------------------------------------------------------------------

import types  # noqa: E402


def stub_frappe():
    """Minimal stand-in for the four registries frappe.whitelist() populates."""
    stub = types.ModuleType("frappe")
    stub.whitelisted = []
    stub.guest_methods = []
    stub.xss_safe_methods = []
    stub.allowed_http_methods_for_whitelisted_func = {}
    stub.logger = lambda *_a, **_k: types.SimpleNamespace(warning=lambda *_a, **_k: None)
    sys.modules["frappe"] = stub
    return stub


frappe_stub = stub_frappe()
from frappe_devguard._patch import assert_not_overridden, rewhitelist  # noqa: E402


def take_backup():
    return "uploaded"


frappe_stub.whitelisted.append(take_backup)
frappe_stub.guest_methods.append(take_backup)
frappe_stub.allowed_http_methods_for_whitelisted_func[take_backup] = ["GET", "POST"]


def refuse():
    return "blocked"


replacement = rewhitelist(take_backup, refuse)

check("replacement is whitelisted", replacement in frappe_stub.whitelisted)
check("original is no longer whitelisted", take_backup not in frappe_stub.whitelisted)
check("whitelist did not grow", len(frappe_stub.whitelisted) == 1, frappe_stub.whitelisted)
check(
    "http methods follow the replacement",
    frappe_stub.allowed_http_methods_for_whitelisted_func.get(replacement) == ["GET", "POST"],
)
check(
    "stale http-methods key is gone",
    take_backup not in frappe_stub.allowed_http_methods_for_whitelisted_func,
)
check("guest_methods follows too", replacement in frappe_stub.guest_methods)
check(
    "replacement keeps the original identity",
    replacement.__name__ == "take_backup" and replacement.__module__ == take_backup.__module__,
)

frappe_stub.get_hooks = lambda _name: {"some.other.cmd": ["app.override"]}
assert_not_overridden("backups", ["protected.cmd"])  # must not raise
print("ok   assert_not_overridden tolerates an unrelated override map")


# --------------------------------------------------------------------------
# scheduler denylist — exact match only
# --------------------------------------------------------------------------

from frappe_devguard.guards.scheduler import blocked_jobs  # noqa: E402

jobs = blocked_jobs()
check(
    "core backup jobs are blocked",
    "frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_daily" in jobs,
)
check(
    "the local retention reaper is NOT blocked",
    "frappe.desk.page.backups.backups.delete_downloadable_backups" not in jobs,
)
check("all seven core entries are listed", len(jobs) == 7, len(jobs))

os.environ["FRAPPE_DEVGUARD_SCHEDULER_EXTRA_BLOCKED_JOBS"] = "myapp.tasks.push_backup"
check(
    "extra jobs are unioned in, not replacing",
    "myapp.tasks.push_backup" in blocked_jobs() and len(blocked_jobs()) == 8,
    len(blocked_jobs()),
)
os.environ.pop("FRAPPE_DEVGUARD_SCHEDULER_EXTRA_BLOCKED_JOBS")

# --------------------------------------------------------------------------
# the Email Queue outgoing-transport patch
#
# `bench new-site` imports this module while installing the Email Queue DocType
# (on_doctype_update -> load_doctype_module), which is the first thing in a
# bench's life that reliably reaches this guard - post-install, nothing imports
# it until mail is actually sent. Frappe 16 renamed fetch_smtp_server to
# fetch_outgoing_server and added "Frappe Mail" beside SMTP, an HTTP transport
# to a remote site that smtplib never sees.
# --------------------------------------------------------------------------

from frappe_devguard.guards.mail import _patch_email_queue  # noqa: E402


class FakeSMTPServer:
    def __init__(self, server=None, port=None, **_kwargs):
        self.server = server
        self.port = port


class FakeAccount:
    """Enough of a Document: ``.get()`` reads the attribute."""

    def __init__(self, service=""):
        self.service = service

    def get(self, key, default=None):
        return getattr(self, key, default)


class FakeQueueDoc:
    def __init__(self, account):
        self.account = account

    def get_email_account(self, raise_error=False):
        return self.account


def email_queue_module(fetch_name):
    module = types.ModuleType("frappe.email.doctype.email_queue.email_queue")

    class SendMailContext:
        def __init__(self, account):
            self.queue_doc = FakeQueueDoc(account)
            self.smtp_server = None
            self.frappe_mail_client = "a real Frappe Mail client"
            self.email_account_doc = None

    def original(self):
        raise AssertionError("the original transport lookup must not run")

    if fetch_name:
        setattr(SendMailContext, fetch_name, original)
    module.SendMailContext = SendMailContext
    module.SMTPServer = FakeSMTPServer
    module.get_hook_method = lambda _name, fallback=None: fallback
    return module


check("mail guard still enabled here", frappe_devguard.settings().guard_enabled("mail"))

_st = frappe_devguard.settings()

_mod16 = email_queue_module("fetch_outgoing_server")
_patch_email_queue(_mod16)
_ctx = _mod16.SendMailContext(FakeAccount(service="Frappe Mail"))
_ctx.fetch_outgoing_server()

check("frappe 16's outgoing-server lookup is patched", _ctx.smtp_server is not None)
check("the queue is pointed at the catcher", _ctx.smtp_server.server == _st.mail_host)
check("on the catcher's port", _ctx.smtp_server.port == _st.mail_port)
check(
    "the Frappe Mail HTTP transport is disarmed",
    _ctx.email_account_doc.service == "",
    _ctx.email_account_doc.service,
)
check("and its client is dropped", _ctx.frappe_mail_client is None)

_mod15 = email_queue_module("fetch_smtp_server")
_patch_email_queue(_mod15)
_ctx15 = _mod15.SendMailContext(FakeAccount())
_ctx15.fetch_smtp_server()
check("the pre-16 spelling is still patched", _ctx15.smtp_server.server == _st.mail_host)

expect_raises(
    "neither spelling surviving fails loudly",
    DevGuardPatchError,
    lambda: _patch_email_queue(email_queue_module(None)),
)


del sys.modules["frappe"]

smtp_server.shutdown()
pop3_server.shutdown()

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
    sys.exit(1)
print("all devguard checks passed")
