"""``ironclad config <key>``: print one ``[tool.ironclad]`` value, defaults applied (spec §4.6).

A list prints one item per line, so a shell can ``while read`` it; a table, or a list item
that is a table, prints as one line of JSON; a boolean prints as ``true``/``false``; an
unset key with no default prints nothing. An unknown key exits 2.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from ironclad.common import pyproject, repo
from ironclad.common.report import CLEAN, EnvError


def _line(value: Any) -> str:
	if isinstance(value, bool):
		return "true" if value else "false"
	if isinstance(value, dict | list):
		return json.dumps(value, sort_keys=True)
	return str(value)


def lines(value: Any) -> list[str]:
	"""How ``value`` prints: one line per list item, otherwise one line (none when unset)."""
	if value is None:
		return []
	if isinstance(value, list):
		return [_line(item) for item in value]
	return [_line(value)]


def run(args: argparse.Namespace) -> int:
	if args.pyproject:
		path = Path(args.pyproject)
	else:
		try:
			path = repo.toplevel() / "pyproject.toml"
		except EnvError:
			path = Path.cwd() / "pyproject.toml"
	cfg = pyproject.config(pyproject.load(path))
	for line in lines(pyproject.get(cfg, args.key)):
		print(line)
	return CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"config",
		help="print a [tool.ironclad] value (lists one item per line)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("key", help="a [tool.ironclad] key, dotted for nested tables (test.setup)")
	p.add_argument("--pyproject", help="the pyproject.toml to read (default: the git root's)")
	p.set_defaults(func=run)
