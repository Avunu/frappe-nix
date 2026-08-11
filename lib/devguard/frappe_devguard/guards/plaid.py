"""Plaid guard — no bank sync from a development bench.

Plaid talks through its own SDK rather than ``frappe.integrations.utils``, so
the integrations guard does not cover it. ``automatic_synchronization`` runs
hourly off ERPNext's scheduler and, with the production access tokens carried
in a restored database, pulls real bank transactions into the dev database and
bills against the production Plaid item.

Only applies when ERPNext is installed; otherwise the post-import hooks simply
never fire.
"""

from .._hook import on_import
from .._patch import blocking, no_op, require

NAME = "plaid"

_PREFIX = "erpnext.erpnext_integrations.doctype.plaid_settings"

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import(f"{_PREFIX}.plaid_settings", _patch_settings)
    on_import(f"{_PREFIX}.plaid_connector", _patch_connector)


def _patch_settings(module):
    require(module, "automatic_synchronization")
    module.automatic_synchronization = no_op(
        NAME,
        "plaid_settings.automatic_synchronization",
        "Plaid bank synchronisation is blocked",
    )


def _patch_connector(module):
    connector = require(module, "PlaidConnector")
    require(connector, "__init__")
    connector.__init__ = blocking(
        NAME,
        "PlaidConnector.__init__",
        "Plaid access is blocked in this development bench — the access tokens "
        "in this database are production's.",
        "Plaid bank synchronisation is blocked",
    )
