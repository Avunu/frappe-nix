# SPDX-License-Identifier: MIT
"""Root logging for the runtime's entry points, journald-aware.

Under systemd, stderr is the journal, which stamps each line itself and reads a
leading ``<N>`` as its PRIORITY. The stock ``%(asctime)s %(levelname)s`` format
repeats the first and loses the second: every line, tracebacks included, lands
at the unit's default of info, below any PRIORITY filter that should catch it.
So when stderr is the journal, the root handler writes
``<N>{logger} [{site}] {message}`` instead — the same line Frappe's own loggers
get from the frappe_journald graft in frappe-nix, and the same format.

:class:`JournaldFormatter` and its helpers are a copy of
``lib/journald/frappe_journald/_format.py`` in frappe-nix, not an import of it:
this is a separate distribution and must run in a virtualenv the graft is not in.
That graft's test suite renders the same records through both copies and
compares, so they cannot drift apart silently. Stdlib only, and nothing here
imports Frappe.
"""

import logging
import os
import sys

STOCK_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def priority(levelno: int) -> int:
	"""Python level -> syslog severity; levels in between round down."""
	if levelno >= logging.CRITICAL:
		return 2
	if levelno >= logging.ERROR:
		return 3
	if levelno >= logging.WARNING:
		return 4
	if levelno >= logging.INFO:
		return 6
	return 7


def journal_stream() -> bool:
	"""Whether fd 2 is the journal stream systemd connected, not merely a child
	that inherited ``JOURNAL_STREAM`` with its stderr redirected elsewhere. The
	check systemd.exec(5) documents: ``<dev>:<inode>`` against fstat(2)."""
	value = os.environ.get("JOURNAL_STREAM")
	if not value:
		return False
	try:
		dev, ino = (int(part) for part in value.split(":", 1))
		st = os.fstat(2)
	except (ValueError, OSError):
		return False
	return st.st_dev == dev and st.st_ino == ino


def current_site() -> str | None:
	"""``frappe.local.site`` inside a request or job, else ``FRAPPE_SITE``. Frappe
	is looked up, never imported."""
	frappe = sys.modules.get("frappe")
	local = getattr(frappe, "local", None)
	if local is not None:
		try:
			site = getattr(local, "site", None)
		except Exception:
			site = None
		if site:
			return site
	return os.environ.get("FRAPPE_SITE") or None


class JournaldFormatter(logging.Formatter):
	"""``<N>{module} [{site}] {message}``, with ``<N>`` on every line, since
	journald parses the prefix per newline and a traceback would otherwise keep
	its priority only on its first line."""

	def __init__(self, module: str | None = None):
		super().__init__("%(message)s")
		self.module = module

	def format(self, record: logging.LogRecord) -> str:
		text = super().format(record).rstrip("\n")
		head = self.module or record.name
		site = current_site()
		if site:
			head = f"{head} [{site}]"
		prefix = f"<{priority(record.levelno)}>"
		lines = text.split("\n")
		lines[0] = f"{head} {lines[0]}"
		return "\n".join(prefix + line for line in lines)


def setup_logging(level: int) -> None:
	"""``logging.basicConfig`` for an entry point: the journald format when stderr
	is the journal, the stock one otherwise."""
	if journal_stream():
		handler = logging.StreamHandler()
		handler.setFormatter(JournaldFormatter())
		logging.basicConfig(level=level, handlers=[handler])
	else:
		logging.basicConfig(level=level, format=STOCK_FORMAT)
