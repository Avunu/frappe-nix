"""Integration HTTP guard — refuse outbound calls made through Frappe's helper.

``frappe.integrations.utils.make_request`` is the single funnel behind
``make_get_request`` / ``make_post_request`` / ``make_put_request`` /
``make_patch_request`` / ``make_delete_request``, and it is what Frappe,
ERPNext and the payments app use to talk to third-party services. Patching it
covers a lot of ground for one function.

The one that matters most: ``razorpay_settings.capture_payment`` runs every
minute off the ``all`` scheduler bucket, iterates Integration Requests in state
"Authorized" and POSTs a live capture — with no ``enabled`` check and no
sandbox guard. On a bench restored from production, that charges real
customers.

Everything is refused by default. ``devguard.integrations.allowHosts`` opens
specific hosts back up for deliberate integration work; loopback is always
allowed so a locally-stubbed service still works.
"""

from urllib.parse import urlsplit

from .._hook import on_import
from .._patch import announce, block, mark, require
from .._settings import settings

NAME = "integrations"

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("frappe.integrations.utils", _patch_utils)


def allowed(url):
    host = (urlsplit(url).hostname or "").lower()
    if not host or host in _LOOPBACK:
        return True
    return host in {entry.lower() for entry in settings().items(NAME, "allow_hosts")}


def _patch_utils(module):
    original = require(module, "make_request")

    def make_request(method, url, *args, **kwargs):
        if not settings().guard_enabled(NAME) or allowed(url):
            return original(method, url, *args, **kwargs)
        announce(
            NAME,
            "outbound integration HTTP is blocked — add hosts to "
            "devguard.integrations.allowHosts to permit specific ones",
        )
        block(NAME, f"{method} {url}: outbound integration request blocked")

    # make_get_request and friends call this by module-global name.
    module.make_request = mark(make_request, "frappe.integrations.utils.make_request")
