"""Mail guard, Frappe layer.

``mail_stdlib`` already guarantees no mail leaves the machine. What this layer
buys is *fidelity*: mail still resolves an outgoing account on a site with no
Email Account rows, Email Account/Domain records still save without dialling
real servers, the scheduler's IMAP poll stops hammering production mailboxes,
and an app-level ``override_email_send`` hook cannot route around SMTP.
"""

from .._hook import on_import
from .._patch import (
    announce,
    disarm_subclasses,
    redirect_kwargs,
    require,
    with_in_install,
)
from .._settings import DevGuardBlocked, settings
from .mail_stdlib import announce_mail

NAME = "mail"

#: Name used for the synthesised account when a site has no outgoing Email
#: Account at all. It is never written to the Email Queue (``is_exists_in_db``
#: guards that), but ``Communication`` records it unguarded, so keep it
#: recognisable.
ACCOUNT_NAME = "Mailcatch"

#: Attributes an ``override_doctype_class`` subclass must not be allowed to
#: keep: each one is a documented escape route back to a real mail server.
_EMAIL_ACCOUNT_OVERRIDES = (
    "find_default_outgoing",
    "sendmail_config",
    "get_smtp_server",
    "validate",
    "validate_smtp_conn",
    "get_incoming_server",
    "append_email_to_sent_folder",
)
_EMAIL_DOMAIN_OVERRIDES = (
    "validate",
    "validate_incoming_server_conn",
    "validate_outgoing_server_conn",
)

_INSTALLED = False


def enabled():
    return settings().guard_enabled(NAME)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("frappe", _patch_frappe)
    on_import("frappe.email.smtp", _patch_smtp)
    on_import("frappe.email.doctype.email_account.email_account", _patch_email_account)
    on_import("frappe.email.doctype.email_queue.email_queue", _patch_email_queue)
    on_import("frappe.email.doctype.email_domain.email_domain", _patch_email_domain)


# --------------------------------------------------------------------------
# frappe
# --------------------------------------------------------------------------


def _patch_frappe(module):
    if not enabled():
        return

    original = require(module, "are_emails_muted")

    def are_emails_muted():
        if enabled() and settings().mail_unmute:
            # A site_config restored from production often carries
            # ``mute_emails``, which would drop mail before it ever reached the
            # catcher and read as "the mail guard is broken".
            return False
        return original()

    module.are_emails_muted = are_emails_muted


def _rebind_are_emails_muted(module):
    """Re-point a module that did ``from frappe import are_emails_muted``."""
    import frappe

    if hasattr(module, "are_emails_muted"):
        module.are_emails_muted = frappe.are_emails_muted


# --------------------------------------------------------------------------
# frappe.email.smtp
# --------------------------------------------------------------------------


def _patch_smtp(module):
    if not enabled():
        return

    smtp_server = require(module, "SMTPServer")
    original_init = require(smtp_server, "__init__")

    def __init__(self, *args, **kwargs):
        if not enabled():
            return original_init(self, *args, **kwargs)
        st = settings()
        announce_mail()
        # Rewrite before delegating: the original raises OutgoingEmailError on
        # an empty server, which a caller with no account would otherwise hit.
        new_args, new_kwargs = redirect_kwargs(
            original_init,
            self,
            args,
            kwargs,
            {
                "server": st.mail_host,
                "port": st.mail_port,
                "login": None,
                "password": None,
                "use_tls": 0,
                "use_ssl": 0,
                "use_oauth": 0,
                "access_token": None,
            },
        )
        original_init(*new_args, **new_kwargs)
        # ``server``/``port`` are read-only properties over these two.
        self._server = st.mail_host
        self._port = st.mail_port

    smtp_server.__init__ = __init__


# --------------------------------------------------------------------------
# frappe.email.doctype.email_account.email_account
# --------------------------------------------------------------------------


def _patch_email_account(module):
    if not enabled():
        return

    _rebind_are_emails_muted(module)

    email_account = require(module, "EmailAccount")
    original_find_default = require(email_account, "find_default_outgoing")
    original_sendmail_config = require(email_account, "sendmail_config")
    original_validate = require(email_account, "validate")
    original_validate_smtp = require(email_account, "validate_smtp_conn")
    original_incoming = require(email_account, "get_incoming_server")
    original_append = require(email_account, "append_email_to_sent_folder")
    original_pull = require(module, "pull")

    def find_default_outgoing(cls):
        doc = original_find_default.__func__(cls)
        if doc is not None or not enabled():
            return doc
        # A site with no Email Account rows would otherwise raise
        # OutgoingEmailError and never queue the mail at all.
        return _synthetic_account(cls)

    def sendmail_config(self):
        if not enabled():
            return original_sendmail_config(self)
        st = settings()
        announce_mail()
        # Built from scratch rather than by overriding the original's result:
        # reading ``self._password`` decrypts, which throws outright on a bench
        # restored from production without its encryption_key.
        config = {
            "email_account": self.name,
            "server": st.mail_host,
            "port": st.mail_port,
            "login": None,
            "password": None,
            "use_ssl": 0,
            "use_tls": 0,
            "use_oauth": 0,
            "access_token": None,
        }
        if self.flags.validate_smtp_connection:
            config["timeout"] = 15
        return config

    def validate(self):
        if not enabled():
            return original_validate(self)
        return with_in_install(self, original_validate)

    def validate_smtp_conn(self):
        import smtplib

        import frappe
        from frappe import _

        if not enabled():
            return original_validate_smtp(self)
        st = settings()
        try:
            session = smtplib.SMTP(st.mail_host, st.mail_port, timeout=10)
            session.ehlo()
            session.quit()
        except Exception as exc:
            frappe.throw(
                _("Could not reach the development mail catcher at {0}:{1}: {2}").format(
                    st.mail_host, st.mail_port, exc
                ),
                title=_("Mail catcher unreachable"),
            )
        return True

    def get_incoming_server(self, *args, **kwargs):
        if not enabled():
            return original_incoming(self, *args, **kwargs)
        st = settings()
        announce_mail()
        if st.block_incoming:
            raise DevGuardBlocked(
                "frappe_devguard[mail]: refusing to open the mailbox for "
                f"{self.get('email_id')!r} from a development bench. Enable "
                "devguard.mail.pop3 to read caught mail back instead."
            )
        # Point the account at Mailpit's POP3 listener. Credentials are
        # substituted at the poplib layer, and clearing ``password`` here keeps
        # get_password() — which decrypts — off the path entirely.
        overrides = {
            "email_server": st.mail_host,
            "incoming_port": st.pop3_port,
            "use_imap": 0,
            "use_ssl": 0,
            "use_starttls": 0,
            "password": None,
            "auth_method": "Basic",
        }
        saved = {key: self.get(key) for key in overrides}
        for key, value in overrides.items():
            self.set(key, value)
        try:
            return original_incoming(self, *args, **kwargs)
        finally:
            for key, value in saved.items():
                self.set(key, value)

    def append_email_to_sent_folder(self, message):
        if not enabled():
            return original_append(self, message)
        # Would IMAP-APPEND every dev-sent mail into the production Sent folder.
        return None

    def pull(*args, **kwargs):
        if enabled() and settings().block_incoming:
            # Scheduled every 10 minutes; left alone it marks production mail
            # SEEN and can fire auto-replies.
            return None
        return original_pull(*args, **kwargs)

    email_account.find_default_outgoing = classmethod(find_default_outgoing)
    email_account.sendmail_config = sendmail_config
    email_account.validate = validate
    email_account.validate_smtp_conn = validate_smtp_conn
    email_account.get_incoming_server = get_incoming_server
    email_account.append_email_to_sent_folder = append_email_to_sent_folder
    module.pull = pull

    disarm_subclasses(email_account, _EMAIL_ACCOUNT_OVERRIDES)


def _synthetic_account(cls):
    st = settings()
    account = cls.from_record(
        {
            "name": ACCOUNT_NAME,
            "email_account_name": ACCOUNT_NAME,
            "email_id": st.mail_sender,
            "enable_outgoing": 1,
            "default_outgoing": 1,
            "enable_incoming": 0,
            "use_imap": 0,
            "append_emails_to_sent_folder": 0,
            "smtp_server": st.mail_host,
            "smtp_port": st.mail_port,
            "use_tls": 0,
            "use_ssl_for_outgoing": 0,
            "auth_method": "Basic",
            "no_smtp_authentication": 1,
            "awaiting_password": 0,
            # Leave the sender the code actually meant to send as: rewriting it
            # would hide who sent what in the catcher's UI.
            "always_use_account_email_id_as_sender": 0,
            "always_use_account_name_as_sender_name": 0,
        }
    )
    account._from_site_config = True
    return account


# --------------------------------------------------------------------------
# frappe.email.doctype.email_queue.email_queue
# --------------------------------------------------------------------------


def _patch_email_queue(module):
    if not enabled():
        return

    _rebind_are_emails_muted(module)

    original_get_hook_method = require(module, "get_hook_method")
    smtp_server = require(module, "SMTPServer")
    send_mail_context = require(module, "SendMailContext")
    original_fetch = require(send_mail_context, "fetch_smtp_server")

    def get_hook_method(hook_name, fallback=None):
        if enabled() and hook_name == "override_email_send":
            # An app owning this hook (cloudflare_email_delivery, …) sends over
            # its own HTTP API and never touches SMTP, escaping the catcher.
            return fallback
        return original_get_hook_method(hook_name, fallback=fallback)

    def fetch_smtp_server(self):
        if not enabled():
            return original_fetch(self)
        self.email_account_doc = self.queue_doc.get_email_account(raise_error=True)
        if not self.smtp_server:
            st = settings()
            self.smtp_server = smtp_server(server=st.mail_host, port=st.mail_port)

    # Bound by name at module scope (``from frappe.utils import get_hook_method``)
    # and resolved from module globals at call time, so patching frappe.utils
    # would have no effect here.
    module.get_hook_method = get_hook_method
    send_mail_context.fetch_smtp_server = fetch_smtp_server


# --------------------------------------------------------------------------
# frappe.email.doctype.email_domain.email_domain
# --------------------------------------------------------------------------


def _patch_email_domain(module):
    if not enabled():
        return

    email_domain = require(module, "EmailDomain")
    original_validate = require(email_domain, "validate")
    require(email_domain, "validate_incoming_server_conn")
    require(email_domain, "validate_outgoing_server_conn")

    def validate(self):
        if not enabled():
            return original_validate(self)
        # validate() already early-returns on in_install, which covers both the
        # incoming and outgoing connection tests in one go.
        return with_in_install(self, original_validate)

    email_domain.validate = validate

    disarm_subclasses(email_domain, _EMAIL_DOMAIN_OVERRIDES)


# ``announce`` is re-exported for guards that want the shared one-shot banner.
__all__ = ["NAME", "announce", "install"]
