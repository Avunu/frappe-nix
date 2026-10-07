"""``frappe-nix data-path`` and ``frappe-nix pin-path``: where a packaged or pinned file is."""

import argparse
from pathlib import Path

from frappe_nix_tools.common import data_path, repo
from frappe_nix_tools.common.pins import pin_path
from frappe_nix_tools.common.report import CLEAN


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
		help="print the absolute path of a file shipped in frappe_nix_tools/data",
		description="Print the absolute path of frappe_nix_tools/data/<rel>, e.g. `known-apps.json`.",
	)
	p.add_argument("rel", help="path relative to frappe_nix_tools/data")
	p.set_defaults(func=run_data_path)

	p = subparsers.add_parser(
		"pin-path",
		help="print a directory holding a flake.lock input, fetched and narHash-verified",
		description=(
			"Print a directory holding the tree flake.lock pins for <input>: the app's own input "
			"(frappe, erpnext, standards-profile, …) or one frappe-nix pins (frappe-semgrep-rules, "
			"marketplace, pilot). It is the Nix store path when present, else "
			".dev-dist/pins/<name>-<rev>/, fetched (github, gitlab and git inputs) and verified "
			"against the lock's narHash (exit 3 on a mismatch). FRAPPE_NIX_FETCH_TOKEN "
			"authenticates the fetch of a private repository."
		),
	)
	p.add_argument("input", help="the flake input name")
	p.add_argument(
		"--lock",
		help="the flake.lock to read (default: the nearest flake.lock at or above the current directory, within the git work tree)",
	)
	p.set_defaults(func=run_pin_path)
