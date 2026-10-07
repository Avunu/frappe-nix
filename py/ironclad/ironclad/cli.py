"""``ironclad <command>``: the dispatcher. Every module in ``ironclad/commands/`` registers itself."""

import argparse
import importlib
import os
import pkgutil
import sys
import traceback

import ironclad.commands
from ironclad import __version__
from ironclad.common.report import ENVIRONMENT, INVALID, IroncladError


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		prog="ironclad",
		description="The Ironclad platform tools of frappe-nix. See https://frappe-nix.avunu.net/docs/ironclad/.",
	)
	parser.add_argument("--version", action="version", version=__version__)
	subparsers = parser.add_subparsers(dest="command", metavar="<command>", title="commands")
	for module in sorted(m.name for m in pkgutil.iter_modules(ironclad.commands.__path__)):
		importlib.import_module(f"ironclad.commands.{module}").register(subparsers)
	return parser


def main(argv: list[str] | None = None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)
	if args.command is None:
		parser.print_help(sys.stderr)
		return INVALID
	try:
		return args.func(args)
	except IroncladError as e:
		print(f"ironclad {args.command}: {e}", file=sys.stderr)
		return e.code
	except Exception as e:
		# A bug, or a failure no command anticipated: never exit 1, which means drift
		# (spec §3.3). IRONCLAD_DEBUG=1 prints the traceback.
		if os.environ.get("IRONCLAD_DEBUG"):
			traceback.print_exc()
		print(f"ironclad {args.command}: internal error: {type(e).__name__}: {e}", file=sys.stderr)
		return ENVIRONMENT


if __name__ == "__main__":
	sys.exit(main())
