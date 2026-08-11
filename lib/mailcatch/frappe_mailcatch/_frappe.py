"""Layer 1 — Frappe patches, plus the Layer 2 subclass sweep.

Layer 0 already guarantees no mail leaves the machine. What this layer buys is
*fidelity*: mail still resolves an outgoing account on a site with no Email
Account rows, Email Account/Domain records still save without dialling real
servers, the scheduler's IMAP poll stops hammering production mailboxes, and an
app-level ``override_email_send`` hook cannot route around SMTP entirely.

Each patch binds to a named Frappe internal, so each one is checked: a missing
target raises :class:`MailcatchPatchError` from inside the import, failing the
bench loudly rather than leaving a silently inert interceptor behind.

Patching is skipped altogether when the catcher is disabled at import time, so
``FRAPPE_MAILCATCH_ENABLED=0`` is stock Frappe even if these internals move.
"""

import inspect

from ._hook import on_import
from ._settings import MailcatchBlocked, MailcatchPatchError, announce, settings

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
# helpers
# --------------------------------------------------------------------------


def _require(owner, name):
    """Fetch a patch target, or fail the import loudly."""
    value = getattr(owner, name, None)
    if value is None:
        label = getattr(owner, "__qualname__", None) or getattr(owner, "__name__", owner)
        raise MailcatchPatchError(
            f"frappe_mailcatch: {label}.{name} is missing — Frappe's email internals "
            "have moved and the catch-all can no longer be guaranteed. Update "
            "frappe-nix, or set FRAPPE_MAILCATCH_ENABLED=0 to run without it."
        )
    return value


def _redirect_kwargs(func, self, args, kwargs, replacements):
    """Rebind a call's arguments, overriding ``replacements`` by name.

    Used where the original callable validates its arguments before we would
    otherwise get a chance to fix them up.
    """
    bound = inspect.signature(func).bind(self, *args, **kwargs)
    bound.apply_defaults()
    bound.arguments.update(replacements)
    return bound.args, bound.kwargs


def _in_install(doc, original):
    """Run ``original`` with ``frappe.local.flags.in_install`` set.

    Both ``EmailAccount.validate`` and ``EmailDomain.validate`` gate their
    connection tests (and the "Password is required" throw) on that flag. It is
    request-scoped and leaking it would skip cache clearing and Deleted
    Document bookkeeping for the rest of the request, so restore it in
    ``finally``.
    """
    import frappe

    previous = frappe.local.flags.in_install
    frappe.local.flags.in_install = True
    try:
        return original(doc)
    finally:
        frappe.local.flags.in_install = previous


def _all_subclasses(base):
    seen = []
    pending = list(base.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.append(cls)
        pending.extend(cls.__subclasses__())
    return seen


def _disarm_subclasses(base, names):
    """Strip overriding attributes from subclasses — now and in the future.

    ``override_doctype_class`` subclasses (``DevEmailAccount``, cloudflare's
    ``EmailAccount``, …) shadow the methods patched below, and they are imported
    lazily on the first ``get_controller`` call, so a one-shot sweep would miss
    them. Hooking ``__init_subclass__`` disarms whichever ones show up later.
    """

    def disarm(cls):
        for name in names:
            if name in cls.__dict__:
                delattr(cls, name)

    for subclass in _all_subclasses(base):
        disarm(subclass)

    original = base.__dict__.get("__init_subclass__")

    def __init_subclass__(cls, **kwargs):
        if original is not None:
            original.__func__(cls, **kwargs)
        else:
            super(base, cls).__init_subclass__(**kwargs)
        if settings().enabled:
            disarm(cls)

    base.__init_subclass__ = classmethod(__init_subclass__)


# --------------------------------------------------------------------------
# frappe
# --------------------------------------------------------------------------


def _patch_frappe(module):
    if not settings().enabled:
        return

    original = _require(module, "are_emails_muted")

    def are_emails_muted():
        st = settings()
        if st.enabled and st.unmute:
            # A site_config restored from production often carries
            # ``mute_emails``, which would drop mail before it ever reaches the
            # catcher and read as "mailcatch is broken".
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
    if not settings().enabled:
        return

    smtp_server = _require(module, "SMTPServer")
    original_init = _require(smtp_server, "__init__")

    def __init__(self, *args, **kwargs):
        st = settings()
        if not st.enabled:
            return original_init(self, *args, **kwargs)
        announce()
        # Rewrite before delegating: the original raises OutgoingEmailError on
        # an empty server, which a caller with no account would otherwise hit.
        new_args, new_kwargs = _redirect_kwargs(
            original_init,
            self,
            args,
            kwargs,
            {
                "server": st.host,
                "port": st.port,
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
        self._server = st.host
        self._port = st.port

    smtp_server.__init__ = __init__


# --------------------------------------------------------------------------
# frappe.email.doctype.email_account.email_account
# --------------------------------------------------------------------------


def _patch_email_account(module):
    if not settings().enabled:
        return

    _rebind_are_emails_muted(module)

    email_account = _require(module, "EmailAccount")
    original_find_default = _require(email_account, "find_default_outgoing")
    original_sendmail_config = _require(email_account, "sendmail_config")
    original_validate = _require(email_account, "validate")
    original_validate_smtp = _require(email_account, "validate_smtp_conn")
    original_incoming = _require(email_account, "get_incoming_server")
    original_append = _require(email_account, "append_email_to_sent_folder")
    original_pull = _require(module, "pull")

    def find_default_outgoing(cls):
        doc = original_find_default.__func__(cls)
        if doc is not None or not settings().enabled:
            return doc
        # A site with no Email Account rows would otherwise raise
        # OutgoingEmailError and never queue the mail at all.
        return _synthetic_account(cls)

    def sendmail_config(self):
        st = settings()
        if not st.enabled:
            return original_sendmail_config(self)
        announce()
        # Built from scratch rather than by overriding the original's result:
        # reading ``self._password`` decrypts, which throws outright on a bench
        # restored from production without its encryption_key.
        config = {
            "email_account": self.name,
            "server": st.host,
            "port": st.port,
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
        if not settings().enabled:
            return original_validate(self)
        return _in_install(self, original_validate)

    def validate_smtp_conn(self):
        import frappe
        import smtplib
        from frappe import _

        st = settings()
        if not st.enabled:
            return original_validate_smtp(self)
        try:
            session = smtplib.SMTP(st.host, st.port, timeout=10)
            session.ehlo()
            session.quit()
        except Exception as exc:
            frappe.throw(
                _("Could not reach the development mail catcher at {0}:{1}: {2}").format(
                    st.host, st.port, exc
                ),
                title=_("Mailcatch unreachable"),
            )
        return True

    def get_incoming_server(self, *args, **kwargs):
        st = settings()
        if not st.enabled:
            return original_incoming(self, *args, **kwargs)
        announce()
        if st.block_incoming:
            raise MailcatchBlocked(
                "frappe_mailcatch: refusing to open the mailbox for "
                f"{self.get('email_id')!r} from a development bench. Enable "
                "mailcatch.pop3 to read caught mail back instead."
            )
        # Point the account at Mailpit's POP3 listener. Credentials are
        # substituted at the poplib layer, and clearing ``password`` here keeps
        # get_password() — which decrypts — off the path entirely.
        overrides = {
            "email_server": st.host,
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
        if not settings().enabled:
            return original_append(self, message)
        # Would IMAP-APPEND every dev-sent mail into the production Sent folder.
        return None

    def pull(*args, **kwargs):
        st = settings()
        if st.enabled and st.block_incoming:
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

    _disarm_subclasses(email_account, _EMAIL_ACCOUNT_OVERRIDES)


def _synthetic_account(cls):
    st = settings()
    account = cls.from_record(
        {
            "name": ACCOUNT_NAME,
            "email_account_name": ACCOUNT_NAME,
            "email_id": st.sender,
            "enable_outgoing": 1,
            "default_outgoing": 1,
            "enable_incoming": 0,
            "use_imap": 0,
            "append_emails_to_sent_folder": 0,
            "smtp_server": st.host,
            "smtp_port": st.port,
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
    if not settings().enabled:
        return

    _rebind_are_emails_muted(module)

    original_get_hook_method = _require(module, "get_hook_method")
    smtp_server = _require(module, "SMTPServer")
    send_mail_context = _require(module, "SendMailContext")
    original_fetch = _require(send_mail_context, "fetch_smtp_server")

    def get_hook_method(hook_name, fallback=None):
        if settings().enabled and hook_name == "override_email_send":
            # An app owning this hook (cloudflare_email_delivery, …) sends over
            # its own HTTP API and never touches SMTP, escaping the catcher.
            return fallback
        return original_get_hook_method(hook_name, fallback=fallback)

    def fetch_smtp_server(self):
        if not settings().enabled:
            return original_fetch(self)
        self.email_account_doc = self.queue_doc.get_email_account(raise_error=True)
        if not self.smtp_server:
            st = settings()
            self.smtp_server = smtp_server(server=st.host, port=st.port)

    # Bound by name at module scope (``from frappe.utils import get_hook_method``)
    # and resolved from module globals at call time, so patching frappe.utils
    # would have no effect here.
    module.get_hook_method = get_hook_method
    send_mail_context.fetch_smtp_server = fetch_smtp_server


# --------------------------------------------------------------------------
# frappe.email.doctype.email_domain.email_domain
# --------------------------------------------------------------------------


def _patch_email_domain(module):
    if not settings().enabled:
        return

    email_domain = _require(module, "EmailDomain")
    original_validate = _require(email_domain, "validate")
    _require(email_domain, "validate_incoming_server_conn")
    _require(email_domain, "validate_outgoing_server_conn")

    def validate(self):
        if not settings().enabled:
            return original_validate(self)
        # validate() already early-returns on in_install, which covers both the
        # incoming and outgoing connection tests in one go.
        return _in_install(self, original_validate)

    email_domain.validate = validate

    _disarm_subclasses(email_domain, _EMAIL_DOMAIN_OVERRIDES)
