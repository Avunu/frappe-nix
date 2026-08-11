"""Scheduler guard — refuse named scheduled jobs, and Server Script events.

The named guards each know one integration. This one is the backstop for the
rest: a third-party app's backup job listed by dotted path, and `Server Script`
rows with ``script_type = "Scheduler Event"``, which are arbitrary production
Python carried in the database dump.

``execute`` is the chokepoint, not ``enqueue``: ``bench trigger-scheduler-event``
calls ``execute()`` directly and would bypass a patch on ``enqueue``.

Why a code patch rather than setting ``stopped = 1`` on the Scheduled Job Type
rows: DB state survives ``bench migrate`` but does *not* survive the thing that
actually matters here — a restore from a production dump, which carries
``stopped = 0``.
"""

from .._hook import on_import
from .._patch import announce, require, warn
from .._settings import settings

NAME = "scheduler"

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import(
        "frappe.core.doctype.scheduled_job_type.scheduled_job_type", _patch_scheduled_job_type
    )


def blocked_jobs():
    """Exact dotted paths to refuse.

    Deliberately exact-match, never substring: ``hourly_maintenance`` also runs
    ``frappe.desk.page.backups.backups.delete_downloadable_backups``, the
    purely local retention reaper, and loose matching on "backup" would disable
    it and fill the disk.
    """
    st = settings()
    return set(st.items(NAME, "blocked_jobs")) | set(st.items(NAME, "extra_blocked_jobs"))


def _patch_scheduled_job_type(module):
    scheduled_job_type = require(module, "ScheduledJobType")
    original_execute = require(scheduled_job_type, "execute")

    def execute(self):
        reason = _refusal(self)
        if reason is None:
            return original_execute(self)

        announce(NAME, "scheduled jobs that reach production services are skipped")
        warn(NAME, f"skipping scheduled job {self.method!r}: {reason}")
        _mark_ran(self)
        return None

    scheduled_job_type.execute = execute


def _refusal(job):
    st = settings()
    if job.get("server_script") and st.flag(NAME, "block_server_scripts"):
        # Arbitrary Python from the production dump. Its Scheduled Job Type
        # method is a scrubbed script name, never a dotted path, so the
        # denylist below cannot match it — this branch is the only cover.
        return "Server Script scheduler events are blocked in this development bench"
    if job.method in blocked_jobs():
        return "listed in devguard.scheduler.blockedJobs"
    return None


def _mark_ran(job):
    """Advance last_execution so the job is not re-enqueued every tick.

    ``validate()`` forces ``create_log = 1`` for every frequency except "All",
    so the usual write happens inside ``log_status("Start")`` — which we skip.
    Copy the no-log branch instead. ``Document.db_set`` defaults to
    ``commit=False`` and ``bench trigger-scheduler-event`` has no outer commit,
    hence the explicit one.
    """
    import frappe
    from frappe.utils import now_datetime

    job.db_set("last_execution", now_datetime(), update_modified=False)
    frappe.db.commit()
