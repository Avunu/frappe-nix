"""Google guard — refuse Calendar, Contacts and Drive access.

``GoogleOAuth`` is the single funnel: ``get_google_service_object`` builds the
API client for every Google integration, and ``refresh_access_token`` is what
gets it a usable token. Blocking both covers Calendar, Contacts and Drive at
once, whichever entry point is used.

Calendar and Contacts are the reason this matters more than it looks. They are
not read-only: Frappe binds ``Event.after_insert/on_update/on_trash`` to
``insert/update/delete_event_in_google_calendar`` and the same for Contacts, so
a dev bench acting on restored production rows can create, mutate and **delete
real events in a real person's calendar**, and write into their real contacts.
"""

from .._hook import on_import
from .._patch import blocking, require

NAME = "google"

_BLOCKED = (
    "Google API access is blocked in this development bench — the OAuth tokens "
    "in this database are production's, and Calendar/Contacts sync writes back."
)
_BANNER = "Google Calendar, Contacts and Drive access is blocked"

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("frappe.integrations.google_oauth", _patch_google_oauth)


def _patch_google_oauth(module):
    google_oauth = require(module, "GoogleOAuth")
    require(google_oauth, "get_google_service_object")
    require(google_oauth, "refresh_access_token")

    google_oauth.get_google_service_object = blocking(
        NAME, "GoogleOAuth.get_google_service_object", _BLOCKED, _BANNER
    )
    google_oauth.refresh_access_token = blocking(
        NAME, "GoogleOAuth.refresh_access_token", _BLOCKED, _BANNER
    )
