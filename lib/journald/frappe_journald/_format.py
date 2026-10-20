"""Frappe's half of the journald line: the site, the level variable, the formatter.

The line itself is python-devops-nix's (``_journald.py`` beside this file, its
python/journald.py, merged in by ``lib.journald.mkGraft``; CONVENTIONS.md,
Journald line format)::

	<N>{module} [{site}] {message}

with ``<N>`` the record's syslog priority on every line of it, ANSI escapes
stripped, and no timestamp or level name, which journald records itself. What
is Frappe's is who the tenant is (the site: :func:`current_site`), that the
logger name printed is Frappe's module rather than its ``<module>-<site>``
logger name, and ``FRAPPE_NIX_LOG_LEVEL``.

Pure stdlib; nothing here imports Frappe. The site is read from ``sys.modules``
at format time, never imported, so this module is safe to load from the
``.pth`` bootstrap before anything else exists.

``runtime/src/frappe_runtime/journald.py`` builds the runtime's own root
formatter the same way, on its own vendored copy of the core: the runtime is a
separate distribution and must not import from a graft that a venv may lack.
The graft's test suite renders the same records through both and compares, so
the two cannot drift.
"""

import os
import sys

from ._journald import LEVELS, JournaldFormatter, journal_stream, parse_level, priority

__all__ = [
	"LEVELS",
	"JournaldFormatter",
	"current_site",
	"env_level",
	"formatter",
	"journal_stream",
	"priority",
]

#: The level's variable, and its name before CONVENTIONS.md's FRAPPE_NIX_* rule
#: (python-devops-nix, Tool-owned variables), still read with a warning through
#: 1.x, until frappe-nix 2.0.0.
ENV_LEVEL = "FRAPPE_NIX_LOG_LEVEL"
OLD_ENV_LEVEL = "FRAPPE_LOG_LEVEL"
_warned_old_level = []


def env_level():
	"""The level ``FRAPPE_NIX_LOG_LEVEL`` names, or None when unset or unknown."""
	value = os.environ.get(ENV_LEVEL)
	if value is None and OLD_ENV_LEVEL in os.environ:
		if not _warned_old_level:
			_warned_old_level.append(True)
			sys.stderr.write(
				f"WARNING frappe_journald {OLD_ENV_LEVEL} is deprecated; set {ENV_LEVEL} instead (the old name goes in frappe-nix 2.0.0)\n"
			)
		value = os.environ[OLD_ENV_LEVEL]
	return parse_level(value)


def current_site():
	"""The site this record belongs to, as best known at format time.

	``frappe.local.site`` first — one process may serve several sites, and in a
	request or a job that is the precise answer — then ``FRAPPE_SITE``, which
	``services.frappe-nix`` sets on every per-site unit. Frappe is looked up in
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


def formatter(module=None):
	"""The journald formatter for one of Frappe's loggers.

	module  what the line names: Frappe's logger name is ``<module>-<site>``, and
	        its own format prints the module alone. None prints the record's
	        logger name.
	"""
	return JournaldFormatter(logger=module, tenant=lambda _record: current_site())
