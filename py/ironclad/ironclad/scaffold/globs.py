"""Path globs over ``git ls-files`` output.

One dialect everywhere sync matches a tracked path (spec §2.2) and turns a glob into a
regular expression for a tool (``glob_to_regex``, §2.14): ``**`` is any run of characters
including ``/``, ``*`` and ``?`` stay inside one path segment. A ``**/`` prefix or a
``/**/`` infix also matches no directory at all, so ``**/web_form/**`` matches
``web_form/x.js`` and ``a/**/b.js`` matches ``a/b.js``.
"""

import re
from functools import cache


def _translate(glob: str) -> str:
	out = []
	i = 0
	while i < len(glob):
		c = glob[i]
		if glob.startswith("**/", i):
			out.append("(?:.*/)?")
			i += 3
		elif glob.startswith("**", i):
			out.append(".*")
			i += 2
		elif c == "*":
			out.append("[^/]*")
			i += 1
		elif c == "?":
			out.append("[^/]")
			i += 1
		else:
			out.append(re.escape(c))
			i += 1
	return "".join(out)


@cache
def _compiled(glob: str) -> re.Pattern[str]:
	return re.compile(_translate(glob) + r"\Z")


def match(glob: str, path: str) -> bool:
	"""Whether the tracked ``path`` (relative, ``/``-separated) matches ``glob``."""
	return _compiled(glob).match(path) is not None


def match_any(globs: list[str] | tuple[str, ...], path: str) -> bool:
	return any(match(g, path) for g in globs)


def select(globs: list[str] | tuple[str, ...], paths: list[str]) -> list[str]:
	"""The paths matching any of ``globs``, in their order."""
	return [p for p in paths if match_any(globs, p)]


def glob_to_regex(glob: str) -> str:
	"""The fixed ``glob_to_regex`` Jinja filter (spec §2.14): for prek's ``exclude`` regexes.

	``**`` becomes ``.*``, ``*`` becomes ``[^/]*``, everything else is escaped, and the
	result is anchored at the end with ``$``. The start is the caller's: prek's global
	exclude puts every alternative after one ``^``.
	"""
	out = []
	i = 0
	while i < len(glob):
		if glob.startswith("**", i):
			out.append(".*")
			i += 2
		elif glob[i] == "*":
			out.append("[^/]*")
			i += 1
		elif glob[i] == "?":
			out.append("[^/]")
			i += 1
		else:
			out.append(re.escape(glob[i]))
			i += 1
	return "".join(out) + "$"
