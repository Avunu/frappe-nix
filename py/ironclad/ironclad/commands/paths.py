"""``ironclad data-path`` and ``ironclad pin-path``: where a packaged or pinned file is."""

import argparse
from pathlib import Path

from ironclad.common import data_path, repo
from ironclad.common.pins import pin_path
from ironclad.common.report import CLEAN


def run_data_path(args: argparse.Namespace) -> int:
	print(data_path(args.rel))
	return CLEAN


def run_pin_path(args: argparse.Namespace) -> int:
	lock = Path(args.lock) if args.lock else repo.find_up("flake.lock")
	print(pin_path(args.input, lock.resolve()))
	return CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"data-path",
		help="print the absolute path of a file shipped in ironclad/data",
		description="Print the absolute path of ironclad/data/<rel>, e.g. `known-apps.json`.",
	)
	p.add_argument("rel", help="path relative to ironclad/data")
	p.set_defaults(func=run_data_path)

	p = subparsers.add_parser(
		"pin-path",
		help="print a directory holding a flake.lock GitHub input, fetched and narHash-verified",
		description=(
			"Print a directory holding the tree flake.lock pins for <input>: the app's own input "
			"(frappe, erpnext, …) or one frappe-nix pins (frappe-semgrep-rules, marketplace, pilot). "
			"It is the Nix store path when present, else .dev-dist/pins/<repo>-<rev>/, fetched from "
			"GitHub and verified against the lock's narHash (exit 3 on a mismatch)."
		),
	)
	p.add_argument("input", help="the flake input name")
	p.add_argument(
		"--lock",
		help="the flake.lock to read (default: the nearest flake.lock at or above the current directory, within the git work tree)",
	)
	p.set_defaults(func=run_pin_path)
