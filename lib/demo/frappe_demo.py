"""frappe-demo's bench half (docs/app-standards/spec.md §5.4): a repeatable demo site.

Run by lib/sh/frappe-demo.sh with the bench's interpreter, once the bench is up and the site
exists::

    <bench>/env/bin/python frappe_demo.py --site S --sites-path <bench>/sites --app A \\
        --date 2026-01-15 --seed 1 [--erpnext-demo] --demo '<the demo module, as JSON>' \\
        --password <administrator password>

1. Completes the setup wizard with fixed arguments (the demo module's language, country,
   currency, timezone and, when erpnext is installed, company, abbreviation, the calendar
   year as the fiscal year and the standard chart of accounts). A completed wizard is left
   as it is.
2. With ``--erpnext-demo`` and erpnext installed, ``erpnext.setup.demo.setup_demo_data``,
   once (erpnext records the demo company in Global Defaults).
3. ``<app>.demo.setup(ctx)`` when the module exists. The hook runs as Administrator, must be
   idempotent, must not reach external services, must take every date from ``ctx["today"]``
   and must not commit: this script commits once, at the end.
4. ``sites/<site>/demo.json``: ``{date, seed, apps: {name: version}, erpnext_demo}``.

Python's ``random`` is seeded with ``--seed`` first, so a hook (or erpnext's demo) that draws
random numbers draws the same ones every time. Standard library and frappe only: the bench
interpreter has neither frappe-nix-tools nor its dependencies.

Exit codes: 0 ok; 1 the app's hook (or erpnext's demo) raised; 10 an environment error.
"""

import argparse
import datetime
import importlib
import json
import random
import sys
import traceback
from pathlib import Path

ENVIRONMENT = 10
HOOK_FAILED = 1


def wizard_args(demo: dict, date: datetime.date, erpnext: bool, password: str) -> dict:
	"""The setup wizard's arguments: fixed, from the demo module, never from the clock."""
	args = {
		"language": demo["language"],
		"country": demo["country"],
		"currency": demo["currency"],
		"timezone": demo["timezone"],
		"full_name": "Demo User",
		"email": "demo@example.com",
		"password": password,
		"enable_telemetry": 0,
	}
	if erpnext:
		args |= {
			"company_name": demo["company-name"],
			"company_abbr": demo["company-abbr"],
			"fy_start_date": f"{date.year}-01-01",
			"fy_end_date": f"{date.year}-12-31",
			"chart_of_accounts": "Standard",
		}
	return args


def main(argv: list[str] | None = None) -> int:
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument("--site", required=True)
	p.add_argument("--sites-path", required=True)
	p.add_argument("--app", required=True)
	p.add_argument("--date", required=True, help="YYYY-MM-DD: the demo's today")
	p.add_argument("--seed", type=int, required=True)
	p.add_argument("--erpnext-demo", action="store_true")
	p.add_argument("--demo", required=True, help="the resolved demo module, as JSON")
	p.add_argument("--password", default="admin")
	args = p.parse_args(argv)

	try:
		today = datetime.date.fromisoformat(args.date)
		demo = json.loads(args.demo)
		import frappe

		frappe.init(site=args.site, sites_path=args.sites_path)
		frappe.connect()
	except Exception as e:
		print(f"frappe-demo: cannot reach {args.site}: {e}", file=sys.stderr)
		return ENVIRONMENT

	random.seed(args.seed)
	try:
		frappe.set_user("Administrator")
		installed = frappe.get_installed_apps()
		erpnext = "erpnext" in installed
		company = demo["company-name"] if erpnext else None

		if not frappe.is_setup_complete():
			from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

			print("frappe-demo: completing the setup wizard")
			result = setup_complete(wizard_args(demo, today, erpnext, args.password))
			if isinstance(result, dict) and result.get("status") not in (None, "ok"):
				print(f"frappe-demo: the setup wizard answered {result}", file=sys.stderr)
				return ENVIRONMENT
			frappe.set_user("Administrator")

		if args.erpnext_demo and erpnext:
			if frappe.db.get_single_value("Global Defaults", "demo_company"):
				print("frappe-demo: erpnext's demo data is already there")
			else:
				from erpnext.setup.demo import setup_demo_data  # ty: ignore[unresolved-import]

				print("frappe-demo: erpnext's demo data")
				setup_demo_data(company)

		try:
			module = importlib.import_module(f"{args.app}.demo")
		except ModuleNotFoundError as e:
			if e.name not in (f"{args.app}.demo", args.app):
				raise
			module = None
		if module is not None and hasattr(module, "setup"):
			print(f"frappe-demo: {args.app}.demo.setup(ctx)")
			module.setup(
				{
					"today": today,
					"seed": args.seed,
					"company": company,
					"erpnext_demo": bool(args.erpnext_demo and erpnext),
				}
			)
		frappe.db.commit()  # nosemgrep: the one commit, after the hook (which must not commit)
		apps = {name: str(frappe.get_attr(f"{name}.__version__")) for name in sorted(installed)}
	except Exception:
		frappe.db.rollback()
		traceback.print_exc()
		print("frappe-demo: the demo setup raised; nothing was committed", file=sys.stderr)
		return HOOK_FAILED
	finally:
		frappe.destroy()

	record = {
		"date": args.date,
		"seed": args.seed,
		"apps": apps,
		"erpnext_demo": bool(args.erpnext_demo and erpnext),
	}
	out = Path(args.sites_path) / args.site / "demo.json"
	out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
	print(f"frappe-demo: {args.site} is ready ({out})")
	return 0


if __name__ == "__main__":
	sys.exit(main())
