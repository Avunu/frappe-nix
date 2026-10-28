"""``frappe-nix apps-catalog sync``: refresh ``frappe_nix_tools/data/frappe-apps.json``, the
apps `frappe ui`'s store offers (frappe_nix_tools.apps_catalog).

Exit codes: 0 written; 3 the registry, GitHub or git could not be read.
"""

import argparse
import sys
from pathlib import Path

from frappe_nix_tools import apps_catalog
from frappe_nix_tools.common.report import CLEAN

#: The checkout's copy, which the scheduled refresh commits.
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "frappe-apps.json"


def run_sync(args: argparse.Namespace) -> int:
	out = Path(args.out)
	apps = apps_catalog.sync(out, upstream=args.registry, org=args.org)
	listed = sum(1 for a in apps if a["listed"])
	print(
		f"{out}: {len(apps)} apps ({listed} listed in {args.registry}, {len(apps) - listed} more from {args.org})",
		file=sys.stderr,
	)
	return CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"apps-catalog",
		help="the apps `frappe ui`'s store offers",
		description="The apps `frappe ui`'s store offers: frappe_nix_tools/data/frappe-apps.json.",
	)
	sub = p.add_subparsers(dest="action", required=True)
	s = sub.add_parser(
		"sync",
		help="rewrite the catalog from the marketplace registry and the frappe organisation",
		description=(
			"Rewrite the catalog: the registry's apps (with their releases' Frappe ranges and "
			"dependencies) and the organisation's unlisted app repositories (any with a "
			"version-<n> branch), each with its release and default branches. GH_TOKEN or "
			"GITHUB_TOKEN, when set, lifts GitHub's rate limit."
		),
	)
	s.add_argument("--out", default=str(DEFAULT_OUT), help="where to write it (default: the package's copy)")
	s.add_argument("--registry", default=apps_catalog.REGISTRY, help="the registry, <owner>/<repo>")
	s.add_argument("--org", default=apps_catalog.ORG, help="the organisation whose unlisted apps are added")
	s.set_defaults(func=run_sync)
