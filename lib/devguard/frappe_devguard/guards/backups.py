"""Backup guard — no site backup may be uploaded to a configured integration.

Local backups keep working end to end: ``bench backup``, ``scheduled_backup``,
``new_backup``, ``bench restore``, ``trim-database``, ``drop-site`` and the
desk Backups download page are all untouched. Only egress is blocked.

A bench restored from production carries enabled `S3 Backup Settings` /
`Dropbox Settings` / `Google Drive` rows with working credentials. Its nightly
`daily_maintenance` job would dump the *dev-mutated* database and push it over
the production backup rotation — `site_config.json`, and therefore the DB
password, encryption key and object-store keys, included.

Unlike the mail guard this is app-layer only: there is no transport-level
containment behind it, so a Frappe refactor or an unknown third-party uploader
is not covered. `require()` makes such a drift loud rather than silent, and the
`scheduler` guard catches scheduled jobs by name, but neither is the same
guarantee. Do not read the ``offsite_backup_utils`` patch below as one either:
two paths reach an upload without ever calling it.
"""

from .._hook import on_import
from .._patch import assert_not_overridden, blocking, no_op, require, rewhitelist, throwing

NAME = "backups"

_UPLOAD_BLOCKED = (
    "offsite backup upload is blocked in this development bench — the configured "
    "destination is production's. Local backups still work: use `bench backup`."
)

#: Endpoints we replace. Checked against override_whitelisted_methods, which is
#: consulted before get_attr and would otherwise route around the patch.
_PROTECTED_CMDS = (
    "frappe.integrations.doctype.dropbox_settings.dropbox_settings.take_backup",
    "frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backup",
    "frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_s3",
    "frappe.integrations.doctype.google_drive.google_drive.take_backup",
)

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("frappe.integrations.offsite_backup_utils", _patch_offsite_utils)
    on_import(
        "frappe.integrations.doctype.dropbox_settings.dropbox_settings", _patch_dropbox
    )
    on_import("frappe.integrations.doctype.s3_backup_settings.s3_backup_settings", _patch_s3)
    on_import("frappe.integrations.doctype.google_drive.google_drive", _patch_google_drive)
    on_import("frappe.integrations.frappe_providers.frappecloud", _patch_frappecloud)
    on_import("frappe.integrations.frappe_providers", _rebind_frappe_providers)


_BANNER = "offsite backup upload is blocked; local backups still work"


def _no_op(what):
    """Scheduler entry points: skip quietly rather than manufacturing a failure.

    Patching these as well as the upload funnels means a dev bench does not
    spend twenty minutes every night dumping a production-sized database that
    is then thrown away.
    """
    return no_op(NAME, what, _BANNER)


def _blocked(what):
    """Upload funnels: raise, having already logged.

    Every caller of these swallows exceptions — Dropbox and S3 turn them into a
    "backup failed" email, ScheduledJobType.execute records status "Failed" —
    so the log line is the only thing an operator will actually see.
    """
    return blocking(NAME, what, _UPLOAD_BLOCKED, _BANNER)


def _throwing(what):
    """Deliberate user actions: surface the reason in the desk UI."""
    return throwing(NAME, what, _UPLOAD_BLOCKED, _BANNER)


# --------------------------------------------------------------------------
# frappe.integrations.offsite_backup_utils
# --------------------------------------------------------------------------


def _patch_offsite_utils(module):
    """Catch a *future* integration following the house convention.

    Explicitly not a chokepoint for the three that exist:
    ``backup_to_dropbox(upload_db_backup=False)`` — exactly what the RQ retry
    handler enqueues — skips straight to uploading every File attachment, and
    Google Drive creates a folder in the production Drive before it gets here.
    """
    require(module, "get_latest_backup_file")
    require(module, "validate_file_size")

    module.get_latest_backup_file = _blocked("offsite_backup_utils.get_latest_backup_file")
    module.validate_file_size = _blocked("offsite_backup_utils.validate_file_size")


# --------------------------------------------------------------------------
# the three offsite integrations
# --------------------------------------------------------------------------


def _patch_dropbox(module):
    for name in ("take_backups_daily", "take_backups_weekly"):
        require(module, name)
        setattr(module, name, _no_op(f"dropbox_settings.{name}"))

    # Single funnel for both the database path and the file_backup path that
    # uploads every public and private File attachment on the site.
    require(module, "backup_to_dropbox")
    module.backup_to_dropbox = _blocked("dropbox_settings.backup_to_dropbox")

    original = require(module, "take_backup")
    module.take_backup = rewhitelist(original, _throwing("Dropbox backup"))


def _patch_s3(module):
    for name in ("take_backups_daily", "take_backups_weekly", "take_backups_monthly"):
        require(module, name)
        setattr(module, name, _no_op(f"s3_backup_settings.{name}"))

    require(module, "backup_to_s3")
    module.backup_to_s3 = _blocked("s3_backup_settings.backup_to_s3")

    # take_backups_s3 is whitelisted *and* runs inline in the web process, so
    # it needs the same treatment as the enqueuing wrapper.
    for name in ("take_backup", "take_backups_s3"):
        original = require(module, name)
        setattr(module, name, rewhitelist(original, _throwing("Amazon S3 backup")))


def _patch_google_drive(module):
    for name in ("daily_backup", "weekly_backup"):
        require(module, name)
        setattr(module, name, _no_op(f"google_drive.{name}"))

    # Must replace the whole function: it calls get_google_drive_object() and
    # check_for_folder_in_google_drive() first, and the latter *creates a
    # folder in the production Drive* and writes backup_folder_id back.
    require(module, "upload_system_backup_to_google_drive")
    module.upload_system_backup_to_google_drive = _blocked(
        "google_drive.upload_system_backup_to_google_drive"
    )

    original = require(module, "take_backup")
    module.take_backup = rewhitelist(original, _throwing("Google Drive backup"))

    assert_not_overridden(NAME, _PROTECTED_CMDS)


# --------------------------------------------------------------------------
# Frappe Cloud site migrator
# --------------------------------------------------------------------------


def _patch_frappecloud(module):
    """``bench migrate-to`` pushes the whole site offsite.

    It downloads a script from frappecloud.com and ``os.execv``s it, which is
    also why no guard downstream of this point can help: the process is gone.
    """
    require(module, "frappecloud_migrator")
    require(module, "get_remote_script")

    module.frappecloud_migrator = _blocked("frappecloud.frappecloud_migrator")
    module.get_remote_script = _blocked("frappecloud.get_remote_script")


def _rebind_frappe_providers(module):
    """The package re-exports the migrator, and ``migrate_to`` reads it there.

    Patching the leaf module alone would be inert — same shape as Frappe's
    ``from frappe import are_emails_muted`` re-exports.
    """
    require(module, "frappecloud_migrator")
    require(module, "migrate_to")

    module.frappecloud_migrator = _blocked("frappe_providers.frappecloud_migrator")
    module.migrate_to = _blocked("frappe_providers.migrate_to")
