"""Guard rails for Frappe development benches.

Loaded at interpreter start from ``zzz-frappe-devguard.pth`` inside the dev
virtualenv, i.e. *below* the Frappe app layer. Every site in the bench is
covered without installing an app and without editing any ``site_config.json``
— the Frappe analogue of odoo-nix's ``dev_mailcatch`` server-wide module.

A bench restored from a production backup carries working production
credentials in its database and ``site_config.json``. Left alone it will mail
real customers, push its dev-mutated database over the production backup
rotation, delete production files out of an object store, and capture real
payments. Each guard closes one of those routes; see ``guards/``.

Guards are installed unconditionally and consult :func:`settings` at call time,
so ``FRAPPE_DEVGUARD_ENABLED=0`` (everything) or
``FRAPPE_DEVGUARD_DISABLE=backups,google`` (named guards) gives byte-for-byte
stock behaviour for a single command.

Only the mail guard offers transport-level containment: it patches
``smtplib``/``imaplib``/``poplib``, which know nothing about Frappe and so hold
across upgrades and third-party apps. The rest patch Frappe and app APIs, and
are therefore one refactor or one unknown app away from being bypassed. The
fail-closed :func:`_patch.require` checks make such a drift loud rather than
silent, but they are not the same guarantee.
"""

from ._settings import DevGuardBlocked, DevGuardPatchError, settings

__all__ = [
    "DevGuardBlocked",
    "DevGuardPatchError",
    "install",
    "settings",
    "status",
]

_INSTALLED = False


def install():
    """Install every enabled guard. Idempotent; safe to call from anywhere."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from .guards import GUARDS

    for guard in GUARDS:
        if settings().guard_enabled(guard.NAME):
            guard.install()


def status():
    """Per-target report: patched, or never reached because its SDK is absent."""
    from ._patch import STATUS

    return dict(STATUS)


install()
