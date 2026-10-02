"""Make every Frappe and bench log line a well-formed journald entry.

Loaded at interpreter start from ``zzz-frappe-journald.pth`` inside the
virtualenv, like ``frappe_unixsock``, so it reaches every process
``services.frappe`` starts — ``frappe-runtime``, gunicorn, ``bench worker``,
``bench schedule``, ``bench migrate`` — without an app install or a
``site_config.json`` edit.

A host shipping the journal to a log store filters on PRIORITY and on the
unit's ``APP_*`` fields, and reads nothing from files. Stock Frappe fails both:
its stream format has no priority prefix, so every line — tracebacks included —
lands at the unit's default of info; and bench appends to ``logs/bench.log``
regardless of ``FRAPPE_STREAM_LOGGING``. See :mod:`._patches` for the two
corrections and :mod:`._format` for the line format.

Inert unless stderr is actually the journal (``JOURNAL_STREAM``, verified — see
:func:`._format.journal_stream`). A terminal, the development shell, the OCI
images and a captured subprocess all keep Frappe's stock output byte for byte,
so this ships to both virtualenvs with nothing to turn off.
"""

from ._format import JournaldFormatter, journal_stream

__all__ = ["JournaldFormatter", "install", "journal_stream"]

_INSTALLED = False


def install():
    """Hook the two patch targets when stderr is the journal. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    if not journal_stream():
        return

    from ._hook import on_import
    from ._patches import patch_bench_utils, patch_frappe_logger

    on_import("frappe.utils.logger", patch_frappe_logger)
    on_import("bench.utils", patch_bench_utils)


# NB: install() is called by the .pth bootstrap, not here — see frappe_unixsock.
