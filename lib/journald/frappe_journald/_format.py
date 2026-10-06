"""The journald line format, and how to tell that stderr is the journal.

Pure stdlib; nothing here imports Frappe. The site is read from
``sys.modules`` at format time, never imported, so this module is safe to load
from the ``.pth`` bootstrap before anything else exists.

``runtime/src/frappe_runtime/journald.py`` carries a copy of
:class:`JournaldFormatter` for the runtime's own root logger — the runtime is a
separate distribution and must not import from a graft that a venv may lack.
The graft's test suite renders the same records through both and compares, so
the two cannot drift.
"""

import logging
import os
import sys

#: ``FRAPPE_LOG_LEVEL`` values, as ``services.frappe.logging.level`` writes them.
LEVELS = {
	"debug": logging.DEBUG,
	"info": logging.INFO,
	"warning": logging.WARNING,
	"warn": logging.WARNING,
	"error": logging.ERROR,
	"critical": logging.CRITICAL,
}


def priority(levelno):
	"""Python level -> syslog severity, as sd-daemon(3) prefixes spell it.

	Thresholds rather than a lookup, so the levels in between land on the
	nearest one below: bench's own ``LOG`` level (15) is debug, not info.
	NOTICE (5) has no Python counterpart and is never produced.
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


def journal_stream():
	"""Whether fd 2 *is* the journal stream systemd connected.

	``JOURNAL_STREAM`` is inherited by every child, including ones whose stderr
	was redirected to a pipe or a file, so its presence alone proves nothing.
	systemd.exec(5) documents the check: compare its ``<dev>:<inode>`` with the
	fstat of stderr. Without it a subprocess whose output a parent captures would
	print ``<4>`` prefixes into that capture.
	"""
	value = os.environ.get("JOURNAL_STREAM")
	if not value:
		return False
	try:
		dev, ino = (int(part) for part in value.split(":", 1))
		st = os.fstat(2)
	except (ValueError, OSError):
		return False
	return st.st_dev == dev and st.st_ino == ino


def env_level():
	"""The level ``FRAPPE_LOG_LEVEL`` names, or None when unset or unknown."""
	return LEVELS.get((os.environ.get("FRAPPE_LOG_LEVEL") or "").strip().lower())


def current_site():
	"""The site this record belongs to, as best known at format time.

	``frappe.local.site`` first — one process may serve several sites, and in a
	request or a job that is the precise answer — then ``FRAPPE_SITE``, which
	``services.frappe`` sets on every per-site unit. Frappe is looked up in
	``sys.modules`` rather than imported: a record logged before Frappe was ever
	imported must not be the thing that imports it.
	"""
	frappe = sys.modules.get("frappe")
	local = getattr(frappe, "local", None)
	if local is not None:
		try:
			site = getattr(local, "site", None)
		except Exception:  # a torn-down Local must not lose the record
			site = None
		if site:
			return site
	return os.environ.get("FRAPPE_SITE") or None


class JournaldFormatter(logging.Formatter):
	"""``<N>{module} [{site}] {message}``, with ``<N>`` on every line.

	No timestamp and no level name: journald stamps each entry itself and the
	``<N>`` prefix becomes its PRIORITY field, so both would only be repeated
	text. The site is there because Frappe's own format has no field for it, and
	a line from a process serving several sites is ambiguous without one; it is
	left out, brackets and all, when no site is known.

	journald's stream protocol splits on newline, and the prefix is parsed per
	line — so a traceback formatted as one record would arrive as one error line
	followed by a dozen at the unit's default priority, which a PRIORITY filter
	silently drops. Prefixing every line keeps the whole record at its level.
	"""

	def __init__(self, module=None):
		super().__init__("%(message)s")
		#: Frappe's logger name is ``<module>-<site>``; the module alone is what
		#: its own format prints. None falls back to the record's logger name.
		self.module = module

	def format(self, record):
		text = super().format(record).rstrip("\n")
		head = self.module or record.name
		site = current_site()
		if site:
			head = f"{head} [{site}]"
		prefix = f"<{priority(record.levelno)}>"
		lines = text.split("\n")
		lines[0] = f"{head} {lines[0]}"
		return "\n".join(prefix + line for line in lines)
