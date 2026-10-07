"""``frappe-nix <command>``: the dispatcher. Every module in ``frappe_nix_tools/commands/`` registers itself."""

import argparse
import importlib
import os
import pkgutil
import sys
import traceback

import frappe_nix_tools.commands
from frappe_nix_tools import __version__
from frappe_nix_tools.common.report import ENVIRONMENT, INVALID, FrappeNixError

DOCS = "https://github.com/Avunu/frappe-nix/tree/main/docs/app-standards"


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		prog="frappe-nix",
		description=f"frappe-nix's app standards tools. See {DOCS}.",
	)
	parser.add_argument("--version", action="version", version=__version__)
	subparsers = parser.add_subparsers(dest="command", metavar="<command>", title="commands")
	for module in sorted(m.name for m in pkgutil.iter_modules(frappe_nix_tools.commands.__path__)):
		importlib.import_module(f"frappe_nix_tools.commands.{module}").register(subparsers)
	return parser


def main(argv: list[str] | None = None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)
	if args.command is None:
		parser.print_help(sys.stderr)
		return INVALID
	try:
		return args.func(args)
	except FrappeNixError as e:
		print(f"frappe-nix {args.command}: {e}", file=sys.stderr)
		return e.code
	except Exception as e:
		# A bug, or a failure no command anticipated: never exit 1, which means drift
		# (spec §3.3). FRAPPE_NIX_DEBUG=1 prints the traceback.
		if os.environ.get("FRAPPE_NIX_DEBUG"):
			traceback.print_exc()
		print(f"frappe-nix {args.command}: internal error: {type(e).__name__}: {e}", file=sys.stderr)
		return ENVIRONMENT


if __name__ == "__main__":
	sys.exit(main())
