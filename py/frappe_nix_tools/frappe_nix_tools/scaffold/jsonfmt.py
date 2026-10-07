"""JSON and JSONC text in exactly the form oxfmt leaves it (spec §2.10, "already in oxfmt's
output form").

A managed JSON file is compared byte for byte, and sync runs oxfmt over what it writes, so
the emitter has to produce oxfmt's own output with the managed ``.oxfmtrc.jsonc``
(``useTabs``, ``printWidth`` 110). oxfmt follows prettier here:

- an object stays expanded when its source has a line break after ``{``: this emitter
  always expands a non-empty object, so oxfmt keeps it as written;
- an array is one group: on one line when that line (indent counted at the tab width of 2,
  plus the comma after it) fits in 110 columns, otherwise one element per line. An array
  holding an expanded object always breaks;
- JSONC gets a trailing comma after the last member of every broken object and array; JSON
  gets none.
"""

import json
from typing import Any

TAB_WIDTH = 2
PRINT_WIDTH = 110


def _scalar(value: Any) -> str:
	return json.dumps(value, ensure_ascii=False)


def _breaks(value: Any) -> bool:
	"""Whether ``value`` is printed over several lines whatever the width."""
	if isinstance(value, dict):
		return bool(value)
	if isinstance(value, list):
		if any(_breaks(v) for v in value):
			return True
		# prettier's forced break: several elements, every one an array of several.
		return len(value) > 1 and all(isinstance(v, list) and len(v) > 1 for v in value)
	return False


def _inline(value: Any) -> str:
	if isinstance(value, list):
		return "[" + ", ".join(_inline(v) for v in value) + "]"
	if isinstance(value, dict):
		return "{}"
	return _scalar(value)


def _emit(value: Any, level: int, used: int, comma: int, jsonc: bool) -> str:
	"""``value`` printed at indent ``level``, starting ``used`` columns into its line, with
	``comma`` characters following it on that line."""
	pad = "\t" * (level + 1)
	if isinstance(value, dict):
		if not value:
			return "{}"
		items = list(value.items())
		lines = ["{"]
		for i, (key, child) in enumerate(items):
			last = i == len(items) - 1
			sep = "" if last and not jsonc else ","
			head = f"{_scalar(key)}: "
			body = _emit(child, level + 1, (level + 1) * TAB_WIDTH + len(head), len(sep), jsonc)
			lines.append(f"{pad}{head}{body}{sep}")
		lines.append("\t" * level + "}")
		return "\n".join(lines)
	if isinstance(value, list):
		if not value:
			return "[]"
		if not _breaks(value):
			inline = _inline(value)
			if used + len(inline) + comma <= PRINT_WIDTH:
				return inline
		lines = ["["]
		for i, child in enumerate(value):
			last = i == len(value) - 1
			sep = "" if last and not jsonc else ","
			body = _emit(child, level + 1, (level + 1) * TAB_WIDTH, len(sep), jsonc)
			lines.append(f"{pad}{body}{sep}")
		lines.append("\t" * level + "]")
		return "\n".join(lines)
	return _scalar(value)


def dumps(value: Any, *, jsonc: bool = False) -> str:
	"""``value`` as oxfmt-formatted JSON (or JSONC), ending in a newline."""
	return _emit(value, 0, 0, 0, jsonc) + "\n"


def stringify(value: Any) -> str:
	"""``JSON.stringify(value, null, "\\t")`` plus a newline: how sync writes ``package.json``.

	prettier formats ``package.json`` with its ``json-stringify`` parser, which prints every
	non-empty object and array expanded, so this is also oxfmt's form of it.
	"""
	return json.dumps(value, indent="\t", ensure_ascii=False) + "\n"
