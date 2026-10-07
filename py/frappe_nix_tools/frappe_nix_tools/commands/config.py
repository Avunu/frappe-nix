"""``frappe-nix config <key>``: print one value of the app's resolved configuration (spec §5.13, §8.4).

The key is a dotted ``[tool.frappe-nix]`` key with every layer and default applied
(``tests.coverage.target``, ``nightly-suites``), ``modules.<module>`` (the effective
switch, after the module rules of §8.2), or ``profile.name|source|rev``.

A list prints one item per line, so a shell can ``while read`` it; a table, or a list item
that is a table, prints as one line of JSON; a boolean prints as ``true``/``false``; an
unset key with no default prints nothing. ``--json`` prints the value as JSON instead. An
unknown key exits 2, and so does an app without ``[tool.frappe-nix]`` (S35).

``--github-output modules`` appends ``<module>=true|false`` for every module to
``$GITHUB_OUTPUT`` (the CI ``cfg`` step, §4.2).
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from frappe_nix_tools.common import config, repo
from frappe_nix_tools.common.report import CLEAN, ConfigError, EnvError


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
	path = Path(args.pyproject) if args.pyproject else repo.find_up("pyproject.toml")
	resolved = config.resolve(
		path,
		lock_path=Path(args.lock) if args.lock else None,
		profile_dir=Path(args.profile_path) if args.profile_path else None,
	)
	for notice in resolved.notices:
		print(f"notice: {notice}", file=sys.stderr)
	if args.github_output:
		if args.github_output != "modules":
			raise ConfigError(f"--github-output takes `modules`, not {args.github_output!r}")
		out = os.environ.get("GITHUB_OUTPUT")
		if not out:
			raise EnvError("--github-output needs $GITHUB_OUTPUT")
		with open(out, "a") as f:
			for module, on in resolved.modules.items():
				f.write(f"{module}={'true' if on else 'false'}\n")
		return CLEAN
	if not args.key:
		raise ConfigError("give a key, or --github-output modules")
	value = resolved.get(args.key)
	if args.json:
		print(json.dumps(value, sort_keys=True))
		return CLEAN
	for line in lines(value):
		print(line)
	return CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"config",
		help="print a resolved configuration value (lists one item per line)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("key", nargs="?", help="a dotted key: tests.coverage.target, modules.ssort, profile.name")
	p.add_argument("--json", action="store_true", help="print the value as JSON")
	p.add_argument("--github-output", metavar="modules", help="append every module switch to $GITHUB_OUTPUT")
	p.add_argument(
		"--pyproject",
		help="the pyproject.toml to read (default: the nearest pyproject.toml at or above the current directory, within the git work tree)",
	)
	p.add_argument(
		"--lock", help="the flake.lock an org profile is pinned in (default: beside the pyproject.toml)"
	)
	p.add_argument(
		"--profile-path", help="read the org profile from this directory instead of its locked input"
	)
	p.set_defaults(func=run)
