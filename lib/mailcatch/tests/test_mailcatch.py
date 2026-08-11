"""Self-contained tests for frappe_mailcatch's Frappe-independent layers.

Runs without Frappe, without a site and without network access: stub SMTP/POP3
servers stand in for Mailpit, and the assertions are about *where the socket
landed*, which is the only property that actually matters.

Run directly (``python test_mailcatch.py``) or via ``nix flake check``.
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

os.environ["FRAPPE_MAILCATCH_HOST"] = "127.0.0.1"
os.environ["FRAPPE_MAILCATCH_PORT"] = str(SMTP_PORT)
os.environ["FRAPPE_MAILCATCH_POP3_PORT"] = str(POP3_PORT)
os.environ.pop("FRAPPE_MAILCATCH_ENABLED", None)
os.environ.pop("FRAPPE_MAILCATCH_POP3_ENABLED", None)

import imaplib  # noqa: E402
import poplib  # noqa: E402
import smtplib  # noqa: E402

import frappe_mailcatch  # noqa: E402  - installs both layers on import
from frappe_mailcatch import MailcatchBlocked  # noqa: E402
from frappe_mailcatch._hook import on_import  # noqa: E402

# An unresolvable host is the point: if any of these reach DNS, the redirect
# did not happen and the test fails with gaierror rather than passing quietly.
ELSEWHERE = "smtp.mailcatch-must-not-resolve.invalid"


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

check("settings read from env", frappe_mailcatch.settings().port == SMTP_PORT)
check("incoming blocked by default", frappe_mailcatch.settings().block_incoming)


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

expect_raises("IMAP4 is blocked", MailcatchBlocked, lambda: imaplib.IMAP4(ELSEWHERE, 143))
expect_raises("IMAP4_SSL is blocked", MailcatchBlocked, lambda: imaplib.IMAP4_SSL(ELSEWHERE, 993))
expect_raises("POP3 is blocked", MailcatchBlocked, lambda: poplib.POP3(ELSEWHERE, 110))
expect_raises("POP3_SSL is blocked", MailcatchBlocked, lambda: poplib.POP3_SSL(ELSEWHERE, 995))
check("MailcatchBlocked is an OSError", issubclass(MailcatchBlocked, OSError))


# --------------------------------------------------------------------------
# incoming redirected to the catcher's POP3 when enabled
# --------------------------------------------------------------------------

os.environ["FRAPPE_MAILCATCH_POP3_ENABLED"] = "1"
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
    MailcatchBlocked,
    lambda: imaplib.IMAP4(ELSEWHERE, 143),
)
os.environ.pop("FRAPPE_MAILCATCH_POP3_ENABLED")


# --------------------------------------------------------------------------
# disabling restores stock behaviour
# --------------------------------------------------------------------------

os.environ["FRAPPE_MAILCATCH_ENABLED"] = "0"
check("disabled settings report disabled", not frappe_mailcatch.settings().enabled)
expect_raises(
    "disabled SMTP resolves the real host",
    socket.gaierror,
    lambda: smtplib.SMTP(ELSEWHERE, 25, timeout=5),
)
expect_raises(
    "disabled IMAP resolves the real host",
    socket.gaierror,
    lambda: imaplib.IMAP4(ELSEWHERE, 143),
)
session = smtplib.SMTP("127.0.0.1", SMTP_PORT)
check("disabled SMTP still connects normally", session.sock.getpeername()[1] == SMTP_PORT)
session.quit()
os.environ.pop("FRAPPE_MAILCATCH_ENABLED")


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

from frappe_mailcatch._frappe import (  # noqa: E402
    _disarm_subclasses,
    _redirect_kwargs,
    _require,
)
from frappe_mailcatch._settings import MailcatchPatchError  # noqa: E402


class Base:
    def send(self):
        return "base"


class EarlySubclass(Base):
    def send(self):
        return "escaped"


_disarm_subclasses(Base, ("send",))
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


args, kwargs = _redirect_kwargs(
    _sample, None, ("real.example.com",), {"password": "hunter2"}, {"server": "127.0.0.1", "port": 1025}
)
check(
    "call arguments are rewritten before delegation",
    _sample(*args, **kwargs) == ("127.0.0.1", 1025, "hunter2"),
    _sample(*args, **kwargs),
)

expect_raises(
    "a missing patch target fails loudly",
    MailcatchPatchError,
    lambda: _require(Base, "no_such_method"),
)


# --------------------------------------------------------------------------
# the synthesised account used on sites with no Email Account at all
# --------------------------------------------------------------------------

from frappe_mailcatch._frappe import _synthetic_account  # noqa: E402


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

smtp_server.shutdown()
pop3_server.shutdown()

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
    sys.exit(1)
print("all mailcatch checks passed")
