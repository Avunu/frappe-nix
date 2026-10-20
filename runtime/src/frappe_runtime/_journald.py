"""journald-native log lines for a Python application under systemd.

The core both products' formatters share (PLAN.md, Phase 6): frappe-nix's
frappe_journald and odoo-nix's journald shim, which grew apart in parallel.
What is here knows nothing about the framework. The product keeps its own
patching into the framework's logging (which logger, which handler, when) and
says what the tenant of a record is: Frappe's site, Odoo's database.

A line leaves as

    <N>{logger} [{tenant}] {message}

(CONVENTIONS.md, Journald line format). ``<N>`` is the record's syslog
priority, which journald's stream transport reads off the start of each line
(sd-daemon(3), ``SyslogLevelPrefix=``, on by default) and stores as PRIORITY.
There is no timestamp, pid or level name: journald records all three itself.
``[{tenant}]`` is left out, brackets and all, when no tenant is known.

journald splits a stream on newlines and reads the prefix per line, so a
traceback formatted as one record would arrive as one error line followed by a
dozen at the unit's default priority, which a PRIORITY filter silently drops.
Every line of a record carries its prefix.

ANSI escapes are stripped: a framework colours its level names on a terminal
(and some messages carry their own colours), and the journal would store the
escapes raw.

Nothing changes unless stderr *is* the journal (:func:`journal_stream`), so
the same environment run from a shell, a container or a captured subprocess
keeps the framework's own format.

Standard library only, written to Python 3.11: it loads into the
application's own interpreter (PLAN.md, Python versions). A product vendors
it, by ``lib.journald.mkGraft`` into its graft as ``_journald.py`` or by
``lib.journald.module`` anywhere else, and imports nothing of it from this
repository at run time.
"""

import logging
import os
import re
from collections.abc import Callable

#: Level names as a ``<P>_NIX_LOG_LEVEL`` variable or a ``logging.level``
#: option spells them.
LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

# CSI sequences (colours, cursor movement), OSC sequences (titles,
# hyperlinks) ended by BEL or ST, character-set designations, and the
# remaining two-byte escapes.
_ANSI = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()*+][ -~]|[@-Z\\-_])"
)


def parse_level(value: str | None) -> int | None:
    """The level a name in :data:`LEVELS` stands for, or None."""
    return LEVELS.get((value or "").strip().lower())


def priority(levelno: int) -> int:
    """A Python level as a syslog severity: 2, 3, 4, 6 or 7.

    By threshold, not lookup, so a framework's own levels in between land on
    the nearest one below: bench's LOG (15) and Odoo's DEBUG_RPC (9) are
    debug, Odoo's RUNBOT (25) info. NOTICE (5) has no Python counterpart and
    is never produced.
    """
    if levelno >= logging.CRITICAL:
        return 2
    if levelno >= logging.ERROR:
        return 3
    if levelno >= logging.WARNING:
        return 4
    if levelno >= logging.INFO:
        return 6
    return 7


def journal_stream(fd: int = 2) -> bool:
    """Whether ``fd`` (stderr by default) is the journal stream systemd connected.

    ``JOURNAL_STREAM`` is inherited by every child, including ones whose stderr
    was redirected to a pipe or a file, so its presence alone proves nothing.
    systemd.exec(5) documents the check: compare its ``<dev>:<inode>`` with
    the fstat of the descriptor. Without it a subprocess whose output a parent
    captures would print ``<4>`` prefixes into that capture.
    """
    value = os.environ.get("JOURNAL_STREAM")
    if not value:
        return False
    try:
        dev, ino = (int(part) for part in value.split(":", 1))
        st = os.fstat(fd)
    except (ValueError, OSError):
        return False
    return (st.st_dev, st.st_ino) == (dev, ino)


def strip_ansi(text: str) -> str:
    """``text`` without terminal escape sequences."""
    return _ANSI.sub("", text)


def prefix_lines(text: str, prio: int) -> str:
    """``<prio>`` in front of every line of ``text``."""
    prefix = f"<{prio}>"
    return "\n".join(prefix + line for line in text.split("\n"))


class JournaldFormatter(logging.Formatter):
    """``<N>{logger} [{tenant}] {message}``, ``<N>`` on every line.

    logger  a fixed name to print instead of the record's logger name. Frappe
            names its loggers ``<module>-<site>``, and prints the module
            alone; None prints ``record.name``.
    tenant  ``tenant(record)``: the site or database the record belongs to,
            or None. Called at format time, so it may read the framework's
            request-local state; an exception in it drops the tenant, never
            the record.

    A product adds what its framework puts after a message (Odoo's
    ``perf_info``) by overriding :meth:`message`.
    """

    def __init__(
        self,
        logger: str | None = None,
        tenant: Callable[[logging.LogRecord], str | None] | None = None,
    ) -> None:
        super().__init__("%(message)s")
        self.logger = logger
        self.tenant = tenant

    def message(self, record: logging.LogRecord) -> str:
        """The record's message, with its traceback and stack if it has them."""
        return super().format(record)

    def head(self, record: logging.LogRecord) -> str:
        """``{logger} [{tenant}]``, or ``{logger}`` with no tenant known."""
        head = self.logger or record.name
        tenant = None
        if self.tenant is not None:
            try:
                tenant = self.tenant(record)
            except Exception:  # noqa: BLE001 -- a torn-down request must not lose the record
                tenant = None
        return f"{head} [{tenant}]" if tenant else head

    def format(self, record: logging.LogRecord) -> str:
        message = self.message(record).rstrip("\n")
        text = f"{self.head(record)} {message}"
        return prefix_lines(strip_ansi(text), priority(record.levelno))
