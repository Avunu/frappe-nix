"""``frappe-nix icon`` (``frappe-icon``): the app's icon pair and its desktop-icon fixture (spec §5.3).

  frappe-icon check [--structural] [--format text|json|github]
  frappe-icon tile
  frappe-icon build [--out .dev-dist/icons] [--write-fixture]

``check`` runs the structural rules (pure Python) and, without ``--structural``, the raster
rules (resvg). ``tile`` writes ``<app>-logo.svg`` from ``<app>-symbolic.svg`` in the profile's
``org.brand`` colours. ``build`` renders ``logo-512.png``, ``favicon-32.png`` and
``favicon-16.png`` (never committed) and, with ``--write-fixture``, the desktop-icon fixture
of an application-type app (S22).

Exit codes: 0 pass; 1 a rule failed (each failure listed); 2 a file is missing or
unparsable, or ``org.brand.tile-color`` is empty; 3 resvg is missing or failed. Module
``icons``: with it off, every subcommand prints a notice and exits 0.
"""

import argparse
import json
import secrets
import sys
from pathlib import Path

from frappe_nix_tools import icon
from frappe_nix_tools.common import repo
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError
from frappe_nix_tools.icon import svg
from frappe_nix_tools.listing import target
from frappe_nix_tools.listing.output import escape_data, escape_property


def _target(args: argparse.Namespace) -> target.Target | None:
	# The app is the current directory (a git work tree), as for sync and compat.
	root = Path.cwd()
	repo.toplevel(root)
	profile = Path(args.profile_path).resolve() if getattr(args, "profile_path", None) else None
	t = target.load(root, profile)
	if not t.modules.get("icons"):
		print("frappe-icon: notice: the icons module is off for this app; nothing to do", file=sys.stderr)
		return None
	return t


def run_check(args: argparse.Namespace) -> int:
	t = _target(args)
	if t is None:
		return CLEAN
	try:
		problems = icon.check(t, structural=args.structural)
	except svg.IconError as e:
		raise ConfigError(str(e)) from e
	code = DRIFT if problems else CLEAN
	if args.format == "json":
		doc = {
			"status": "fail" if problems else "pass",
			"problems": [{"path": p, "problem": m} for p, m in problems],
		}
		sys.stdout.write(json.dumps(doc, indent=2) + "\n")
		return code
	lines = [f"{p}: {m}" for p, m in problems]
	if args.format == "github" and lines:
		token = secrets.token_hex(16)
		print(f"::stop-commands::{token}")
		print("\n".join(lines))
		print(f"::{token}::")
		for p, m in problems:
			print(f"::error file={escape_property(p)}::{escape_data(m)}")
	elif lines:
		print("\n".join(lines))
	what = "structural" if args.structural else "structural and raster"
	print(f"frappe-icon: {len(problems)} problem(s)" if problems else f"frappe-icon: the {what} checks pass")
	return code


def run_tile(args: argparse.Namespace) -> int:
	t = _target(args)
	if t is None:
		return CLEAN
	try:
		text = icon.tile_text(t)
	except svg.IconError as e:
		raise ConfigError(str(e)) from e
	_, logo = icon.images(t)
	path = t.root / logo
	if path.is_file() and path.read_text() == text:
		print(f"frappe-icon: {logo} is up to date")
		return CLEAN
	path.write_text(text)
	print(f"frappe-icon: wrote {logo}")
	return CLEAN


def run_build(args: argparse.Namespace) -> int:
	t = _target(args)
	if t is None:
		return CLEAN
	out = Path(args.out)
	if not out.is_absolute():
		out = t.root / out
	try:
		written = icon.build(t, out)
	except svg.IconError as e:
		raise ConfigError(str(e)) from e
	for path in written:
		print(f"frappe-icon: wrote {path}")
	if args.write_fixture:
		if t.ctx.app_type != "application":
			print(
				f"frappe-icon: notice: the desktop-icon fixture is for application-type apps; {t.name} is"
				f" {t.ctx.app_type!r} (marketplace/listing.toml type)",
				file=sys.stderr,
			)
		elif icon.write_fixture(t):
			print(f"frappe-icon: wrote {icon.fixture_path(t)}")
		else:
			print(f"frappe-icon: {icon.fixture_path(t)} is up to date")
	return CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"icon",
		help="check the app's icons, make its logo tile, render favicons (frappe-icon)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	sub = p.add_subparsers(dest="icon_command", metavar="<subcommand>", required=True)

	def common(q: argparse.ArgumentParser) -> None:
		q.add_argument("--profile-path", help="read the org profile from this directory (profile authors)")

	q = sub.add_parser("check", help="the structural and (without --structural) raster rules")
	q.add_argument("--structural", action="store_true", help="no rasterisation (no-Nix CI)")
	q.add_argument("--format", choices=("text", "json", "github"), default="text")
	common(q)
	q.set_defaults(func=run_check)

	q = sub.add_parser("tile", help="write <app>-logo.svg from <app>-symbolic.svg")
	common(q)
	q.set_defaults(func=run_tile)

	q = sub.add_parser(
		"build", help="render the PNG logo and favicons; --write-fixture writes the desktop icon"
	)
	q.add_argument("--out", default=".dev-dist/icons", help="where the PNGs go (default .dev-dist/icons)")
	q.add_argument(
		"--write-fixture", action="store_true", help="write <app>/desktop_icon/<app>.json (applications)"
	)
	common(q)
	q.set_defaults(func=run_build)
