"""Webhook guard — do not POST dev-side state to production endpoints.

``enqueue_webhook`` is the single place a Webhook's HTTP request is made. Every
scheduled job that saves a document fires the matching webhooks, and Frappe
skips them only during import/patch/install/migrate — a running scheduler is
none of those. On a bench restored from production the Webhook rows point at
production integrations.

A no-op rather than a raise: webhook delivery is fire-and-forget from the
document's point of view, and failing it would surface as document save errors
all over the desk for no benefit.
"""

from .._hook import on_import
from .._patch import no_op, require

NAME = "webhooks"

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("frappe.integrations.doctype.webhook.webhook", _patch_webhook)


def _patch_webhook(module):
    require(module, "enqueue_webhook")
    module.enqueue_webhook = no_op(
        NAME,
        "webhook.enqueue_webhook",
        "outbound webhooks are blocked — Webhook rows and the desk UI are untouched",
    )
