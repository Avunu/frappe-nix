"""``bench provision``: create $FRAPPE_SITE and install every app sites/apps.txt
names. Was ``provision-site``, bash in lib/scripts.nix,
which stays as an alias; tests/provision.sh is its acceptance suite.

One-shot: ``bench new-site --force`` drops the site's database, so it is not
the way to pick up an app pinned after the site exists. That is
``reconcile-apps`` (and, in app mode with a fixed defaultSite,
``frappe:apps-reconcile`` runs it on every ``devenv up``).

bench new-site asks for the MariaDB root password with getpass, which reads a
line from stdin when there is no terminal; the dev bench's root has none, so
stdin is the caller's, and frappe-test and frappe-demo pipe it a blank line.
"""

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.config import AppsConfig

HELP = """\
Usage: bench provision [ADMIN_PASSWORD]

Creates $FRAPPE_SITE (bench new-site --force: an existing site's database is
dropped) and installs every app sites/apps.txt names, in that order.
Administrator's password is ADMIN_PASSWORD, or admin. To install apps added
to a site that exists, run reconcile-apps instead.

provision-site is the same command, kept as an alias until frappe-nix 2.0.0."""

#: The port the URL names when neither the lease nor the config has one.
DEFAULT_PORT = "8000"


def apps_to_install(apps_txt: Path) -> list[str]:
	"""The apps sites/apps.txt names, in its order, without frappe (new-site
	installs it) or blank lines."""
	try:
		text = apps_txt.read_text()
	except OSError as e:
		proc.fail(f"bench provision: cannot read {apps_txt}: {e.strerror}")
	return [app for app in (line.strip() for line in text.splitlines()) if app and app != "frappe"]


def web_port(common_site_config: Path) -> str:
	"""The port the site is served on. Read back rather than taken from the
	environment: `devenv up` leases the ports (frappe-ports), so a port resolved
	in a plain shell is the bench's preferred one, which may not be the one
	nginx took. `frappe-nix-ports web` names it while the bench runs; the
	config's webserver_port, which frappe:config writes, is the fallback."""
	status, out = proc.probe(["frappe-nix-ports", "web"])
	if status == 0:
		return out.rstrip("\n")
	try:
		port = json.loads(common_site_config.read_text()).get("webserver_port")
	except (OSError, ValueError, AttributeError):
		port = None
	# jq's `.webserver_port // 8000`, which the bash used: null and false are absent.
	if port is None or port is False:
		return DEFAULT_PORT
	return str(port)


def provision(config: AppsConfig, site: str, admin_password: str) -> int:
	proc.say(
		"⚠  When prompted for the MySQL root password, leave it blank and press Enter.",
		f"Creating site {site}...",
	)
	proc.run(
		[
			config.bench,
			"new-site",
			site,
			"--db-type",
			"mariadb",
			"--db-socket",
			os.environ["FRAPPE_DB_SOCKET"],
			"--db-root-username",
			"root",
			"--admin-password",
			admin_password,
			"--set-default",
			"--force",
		]
	)
	for app in apps_to_install(Path("sites/apps.txt")):
		proc.say(f"Installing app: {app}")
		proc.run([config.bench, "--site", site, "install-app", app])
	proc.say(
		"",
		f"✅ Site {site} provisioned!",
		f"   Admin password: {admin_password}",
		f"   URL: http://127.0.0.1:{web_port(Path('sites/common_site_config.json'))}",
	)
	return 0


def _body(argv: Sequence[str]) -> int:
	args = list(argv)
	if args and args[0] in ("-h", "--help"):
		proc.say(HELP)
		return 0
	# `--` ends the options, so a password that starts with a dash can be given.
	if args and args[0] == "--":
		args = args[1:]
	if len(args) > 1:
		proc.warn(
			f"bench provision: unexpected argument '{args[1]}' (see --help)",
			"  Quote an admin password that has spaces in it.",
		)
		return 2
	root = proc.bench_root()
	site = os.environ.get("FRAPPE_SITE", "")
	if not site:
		proc.fail("ERROR: FRAPPE_SITE is not set. Set it in your .env or devenv config.")
	if not os.environ.get("FRAPPE_DB_SOCKET"):
		proc.fail("bench provision: FRAPPE_DB_SOCKET is not set: run this from the bench's dev shell")
	config = proc.apps_config()
	os.chdir(root)
	# `${1:-admin}`, as the bash had it: an empty password is the default one.
	return provision(config, site, (args[0] if args else "") or "admin")


def main(argv: Sequence[str] | None = None) -> int:
	return proc.main_guard(lambda: _body(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
	sys.exit(main())
