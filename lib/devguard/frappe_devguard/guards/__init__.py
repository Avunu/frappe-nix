"""Guard registry.

Each guard module exposes ``NAME`` and ``install()``. ``install()`` registers
post-import hooks; it never imports Frappe itself, so importing a guard on a
machine with no bench is free.
"""

from . import (
    backups,
    google,
    integrations,
    mail,
    mail_stdlib,
    objectstore,
    plaid,
    scheduler,
    webhooks,
)

#: Order matters only for readability — every guard is independent.
GUARDS = (
    mail_stdlib,
    mail,
    backups,
    objectstore,
    integrations,
    google,
    webhooks,
    plaid,
    scheduler,
)

__all__ = ["GUARDS"]
