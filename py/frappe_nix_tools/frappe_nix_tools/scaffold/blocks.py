"""The ``blocks`` strategy: only the text between markers is sync's (spec §3.2).

- ``.gitignore``: the ``# >>> frappe-nix >>>`` block ``frappe-init`` has always written
  (``lib/sh/template.sh``), its markers kept as they are, its body
  ``frappe_nix_tools/data/templates/gitignore.block``;
- ``<app>/__init__.py``: the ``x-release-please`` version block (§2.13). When ``releases``
  turns off, only the markers go: the version line is the app's.
"""

import re

from frappe_nix_tools.common.report import ConfigError

GITIGNORE_BEGIN = "# >>> frappe-nix >>> (managed block — edits here are overwritten)"
GITIGNORE_END = "# <<< frappe-nix <<<"

INIT_BEGIN = "# x-release-please-start-version"
INIT_END = "# x-release-please-end"
_VERSION = re.compile(r"""^__version__\s*=\s*(['"])(?P<v>[^'"]*)\1\s*(#.*)?$""")


def gitignore(current: str | None, body: str, path: str = ".gitignore") -> str:
	"""``current`` with the managed block set to ``body`` (appended when there is none).

	Byte-compatible with ``install_gitignore_block`` in ``lib/sh/template.sh``.
	"""
	body = body.rstrip("\n")
	if current is None:
		return f"{GITIGNORE_BEGIN}\n{body}\n{GITIGNORE_END}\n"
	lines = current.split("\n")
	if GITIGNORE_BEGIN in lines:
		start = lines.index(GITIGNORE_BEGIN)
		if GITIGNORE_END not in lines[start:]:
			raise ConfigError(f"{path}: the frappe-nix block opened at line {start + 1} is never closed")
		end = lines.index(GITIGNORE_END, start)
		return "\n".join([*lines[: start + 1], body, *lines[end:]])
	head = current if current.endswith("\n") or not current else current + "\n"
	return f"{head}{GITIGNORE_BEGIN}\n{body}\n{GITIGNORE_END}\n"


def init_py(current: str | None, default_version: str = "0.1.0") -> tuple[str, list[str]]:
	"""``<app>/__init__.py`` in block form, and the lines that break the side-effect rule.

	Every form of the version line becomes the block, keeping the value: a line with a
	trailing ``# x-release-please-version`` marker, a bare ``__version__ = "…"``, or an existing
	block. Outside the block only comments and blank lines may remain (§2.13); anything else
	is reported, never removed.
	"""
	lines = (current or "").split("\n")
	if lines and lines[-1] == "":
		lines.pop()
	version = None
	kept: list[str] = []
	at: int | None = None
	inside = False
	for line in lines:
		stripped = line.strip()
		if stripped == INIT_BEGIN:
			inside = True
			if at is None:
				at = len(kept)
			continue
		if stripped == INIT_END and inside:
			inside = False
			continue
		m = _VERSION.match(stripped)
		if m:
			if version is None:
				version = m["v"]
			if at is None:
				at = len(kept)
			continue
		if inside:
			# Something else inside the block: keep it outside, where the rule below sees it.
			kept.append(line)
			continue
		kept.append(line)
	block = [INIT_BEGIN, f'__version__ = "{version or default_version}"', INIT_END]
	if at is None:
		at = len(kept)
		while at > 0 and not kept[at - 1].strip():
			at -= 1
	out = [*kept[:at], *block, *kept[at:]]
	offending = [line for line in kept if line.strip() and not line.lstrip().startswith("#")]
	return "\n".join(out) + "\n", offending


def init_py_unblocked(current: str) -> str:
	"""``<app>/__init__.py`` without the release-please markers (``releases`` turned off).

	The version line between them stays: ``__version__`` is the app's, and the registry and
	bench read it."""
	lines = current.split("\n")
	kept = [line for line in lines if line.strip() not in (INIT_BEGIN, INIT_END)]
	return "\n".join(kept)
