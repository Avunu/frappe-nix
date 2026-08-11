"""Catch-all outgoing mail interception for Frappe development benches.

Loaded at interpreter start from ``zzz-frappe-mailcatch.pth`` inside the dev
virtualenv, i.e. *below* the Frappe app layer. Every site in the bench is
covered without installing an app and without editing any ``site_config.json``
— the Frappe analogue of odoo-nix's ``dev_mailcatch`` server-wide module.

Two layers, in order of trust:

``_stdlib``
    Rewrites ``smtplib`` connection targets and blocks ``imaplib``/``poplib``.
    Knows nothing about Frappe, so it survives Frappe upgrades, third-party
    apps, and ``override_doctype_class`` subclasses. This layer is the
    containment *guarantee*.

``_frappe``
    Patches the Frappe email stack through a post-import hook so that mail
    still resolves an outgoing account, Email Account/Domain records still
    save without dialling real servers, and an app-level ``override_email_send``
    hook cannot route around SMTP entirely. This layer is *fidelity*.

Both are installed unconditionally; each wrapper consults :func:`settings` at
call time and delegates to the captured original when the catcher is disabled,
so ``FRAPPE_MAILCATCH_ENABLED=0`` gives byte-for-byte stock behaviour.
"""

from ._settings import MailcatchBlocked, MailcatchPatchError, settings

__all__ = ["MailcatchBlocked", "MailcatchPatchError", "install", "settings"]

_INSTALLED = False


def install():
    """Install both layers. Idempotent; safe to call from anywhere."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import _frappe, _stdlib

    _stdlib.install()
    _frappe.install()


install()
