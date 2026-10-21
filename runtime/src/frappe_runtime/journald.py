# SPDX-License-Identifier: MIT
"""Root logging for the runtime's entry points, journald-aware.

Under systemd, stderr is the journal, which stamps each line itself and reads a
leading ``<N>`` as its PRIORITY. The stock ``%(asctime)s %(levelname)s`` format
repeats the first and loses the second: every line, tracebacks included, lands
at the unit's default of info, below any PRIORITY filter that should catch it.
So when stderr is the journal, the root handler writes
``<N>{logger} [{site}] {message}`` instead — the same line Frappe's own loggers
get from the frappe_journald graft in frappe-nix, and the same format
(python-devops-nix CONVENTIONS.md, Journald line format).

The formatter core is ``_journald.py`` beside this file: python-devops-nix's
python/journald.py, vendored byte for byte (its ``lib.journald.module``). This
is a separate distribution that also installs from git without Nix, so the copy
is committed rather than merged in at build time, and frappe-nix's journald
check fails when it differs from the core the flake pins. Stdlib only, and
nothing here imports Frappe.
"""

import logging
import os
import sys

from ._journald import JournaldFormatter, journal_stream, priority

__all__ = ["JournaldFormatter", "current_site", "formatter", "journal_stream", "priority", "setup_logging"]

STOCK_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


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


def formatter(module: str | None = None) -> JournaldFormatter:
	"""``<N>{module or logger} [{site}] {message}``, ``<N>`` on every line."""
	return JournaldFormatter(logger=module, tenant=lambda _record: current_site())


def setup_logging(level: int) -> None:
	"""``logging.basicConfig`` for an entry point: the journald format when stderr
	is the journal, the stock one otherwise."""
	if journal_stream():
		handler = logging.StreamHandler()
		handler.setFormatter(formatter())
		logging.basicConfig(level=level, handlers=[handler])
	else:
		logging.basicConfig(level=level, format=STOCK_FORMAT)
