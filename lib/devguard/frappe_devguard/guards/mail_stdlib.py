"""Mail guard, transport layer.

Rewrites every outbound SMTP connection to the catcher and refuses IMAP/POP3
connections that have nowhere safe to go. Deliberately ignorant of Frappe: this
layer keeps holding when Frappe's internals move, when a third-party app talks
to ``smtplib`` directly, and when ``override_doctype_class`` replaces the
Email Account controller. ``mail.py`` is fidelity; this layer is the guarantee.

Every patch here targets the narrowest funnel rather than a constructor, so the
public signatures — which differ between Python versions — are left alone:

``SMTP.connect``
    reached by ``SMTP(host, port)`` and by an explicit ``.connect()``.
``SMTP_SSL._get_socket`` / ``POP3_SSL._create_socket``
    the single place each SSL variant wraps its socket in TLS. Delegating to
    the plaintext base keeps the real classes, so ``isinstance`` and ``except``
    sites are untouched.
"""

import smtplib

from .._patch import announce
from .._settings import DevGuardBlocked, settings

NAME = "mail"

_INSTALLED = False


def enabled():
    return settings().guard_enabled(NAME)


def announce_mail():
    st = settings()
    detail = f"ALL outgoing email is redirected to {st.mail_host}:{st.mail_port}"
    if st.pop3_enabled:
        detail += f", incoming is served from {st.mail_host}:{st.pop3_port} (POP3)"
    else:
        detail += ", incoming (IMAP/POP3) is blocked"
    announce(NAME, f"{detail}. Web UI: http://{st.mail_host}:{st.mail_http_port}")


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    _patch_smtplib()
    _patch_imaplib()
    _patch_poplib()


# --------------------------------------------------------------------------
# SMTP
# --------------------------------------------------------------------------


def _patch_smtplib():
    original_connect = smtplib.SMTP.connect
    original_starttls = smtplib.SMTP.starttls
    original_login = smtplib.SMTP.login
    original_get_socket = smtplib.SMTP_SSL._get_socket

    def connect(self, host="localhost", port=0, source_address=None):
        if not enabled():
            return original_connect(self, host, port, source_address)
        st = settings()
        announce_mail()
        # ``_host`` feeds STARTTLS' server_hostname; keep it consistent with
        # where we actually land, even though starttls is neutered below.
        self._host = st.mail_host
        return original_connect(self, st.mail_host, st.mail_port, source_address)

    def starttls(self, *args, **kwargs):
        if not enabled():
            return original_starttls(self, *args, **kwargs)
        # Mailpit only advertises STARTTLS when handed a certificate, and the
        # catcher is plaintext by design.
        return (220, b"2.0.0 Ready to start TLS")

    def login(self, *args, **kwargs):
        if not enabled():
            return original_login(self, *args, **kwargs)
        # Mailpit accepts unauthenticated mail. Report the success code Frappe
        # insists on: SMTPServer.session raises unless ``res[0] == 235``.
        return (235, b"2.7.0 Authentication successful")

    def _get_socket(self, host, port, timeout):
        if not enabled():
            return original_get_socket(self, host, port, timeout)
        # No implicit TLS: the catcher does not speak it on the SMTP port.
        return smtplib.SMTP._get_socket(self, host, port, timeout)

    smtplib.SMTP.connect = connect
    smtplib.SMTP.starttls = starttls
    smtplib.SMTP.login = login
    smtplib.SMTP_SSL._get_socket = _get_socket


# --------------------------------------------------------------------------
# IMAP / POP3
# --------------------------------------------------------------------------


def _patch_imaplib():
    import imaplib

    original_init = imaplib.IMAP4.__init__

    def __init__(self, *args, **kwargs):
        if not enabled():
            return original_init(self, *args, **kwargs)
        announce_mail()
        # Mailpit speaks POP3 but not IMAP, so there is nowhere to redirect to.
        raise DevGuardBlocked(
            "frappe_devguard[mail]: IMAP is blocked in this development bench. "
            "Set FRAPPE_DEVGUARD_DISABLE=mail to reach real mailboxes."
        )

    # IMAP4_SSL and IMAP4_stream both delegate to IMAP4.__init__.
    imaplib.IMAP4.__init__ = __init__


def _patch_poplib():
    import poplib

    original_init = poplib.POP3.__init__
    original_create_socket = poplib.POP3_SSL._create_socket
    original_user = poplib.POP3.user
    original_pass = poplib.POP3.pass_

    def __init__(self, host, port=poplib.POP3_PORT, *args, **kwargs):
        if not enabled():
            return original_init(self, host, port, *args, **kwargs)
        st = settings()
        announce_mail()
        if st.block_incoming:
            raise DevGuardBlocked(
                "frappe_devguard[mail]: POP3 is blocked in this development bench. "
                "Enable devguard.mail.pop3 to read caught mail back, or set "
                "FRAPPE_DEVGUARD_DISABLE=mail to reach real mailboxes."
            )
        return original_init(self, st.mail_host, st.pop3_port, *args, **kwargs)

    def _create_socket(self, timeout):
        if not enabled():
            return original_create_socket(self, timeout)
        return poplib.POP3._create_socket(self, timeout)

    def user(self, user):
        st = settings()
        if enabled() and not st.block_incoming:
            # The account's real mailbox credentials mean nothing to Mailpit,
            # which authenticates against its own --pop3-auth-file.
            return original_user(self, st.pop3_user)
        return original_user(self, user)

    def pass_(self, pswd):
        st = settings()
        if enabled() and not st.block_incoming:
            return original_pass(self, st.pop3_password)
        return original_pass(self, pswd)

    poplib.POP3.__init__ = __init__
    poplib.POP3.user = user
    poplib.POP3.pass_ = pass_
    poplib.POP3_SSL._create_socket = _create_socket
