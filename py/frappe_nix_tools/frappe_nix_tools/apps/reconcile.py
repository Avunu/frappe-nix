"""``reconcile-apps``: install every app sites/apps.txt names that the site's
database does not have yet, with frappe-nix.renamedApps and replacedApps around
it (docs/app-standards/spec.md §5.10). Was bash in lib/scripts.nix and
lib/rename/reconcile.nix (PLAN.md, Track D, D3).

It runs as `reconcile-apps [<site>]` by hand and as `devenv up`'s
frappe:apps-reconcile task, and the one rendering has to be right in both. This
is what provision-site leaves undone afterwards: a sibling pinned into the
flake *after* a site already exists never gets installed by anything else
(frappe-nix#32).

The order, on a site that exists:

1.  rename: frappe_rename_app.py for every renamedApps pair, so the renamed app
    is not installed beside its old self (they share a Module Def);
2.  reconcile: install what sites/apps.txt names and the site lacks, but never
    an app replacedApps is replacing (it stays on the bench until every site
    has dropped it);
3.  replace: install each replacing app, then uninstall the app it replaces.

One at a time. On `devenv up` every process that waits on the task runs it
from its own wrapper, so several copies start in the same second, each sees
the same missing apps and each would install them: concurrent `install-app`s
fighting over the site's install_app lock. Each step holds the site's
locks/reconcile-apps.lock (flock(2), the same lock bash's flock took) from
before it looks at the site, so a copy that waited finds the work done. The
lock is the kernel's: it goes when the holder does, however it dies.
"""

import fcntl
import json
import os
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.config import AppsConfig

#: Apps reconcile never installs, besides replacedApps' OLDs: the variable the
#: bash wrapper set for the bash body, still read for a caller that sets it.
SKIP = "FRAPPE_NIX_RECONCILE_SKIP"


@contextmanager
def site_lock(site_dir: Path) -> Generator[None]:
	locks = site_dir / "locks"
	locks.mkdir(parents=True, exist_ok=True)
	with open(locks / "reconcile-apps.lock", "w") as f:
		fcntl.flock(f, fcntl.LOCK_EX)
		yield


def installed_apps(config: AppsConfig, site: str) -> list[str]:
	"""The site's installed apps; none when they cannot be read."""
	status, out = proc.probe([config.bench, "--site", site, "list-apps", "--format", "json"])
	if status != 0:
		return []
	try:
		data = json.loads(out)
	except ValueError:
		return []
	apps = data.get(site) if isinstance(data, dict) else None
	return [a for a in apps if isinstance(a, str)] if isinstance(apps, list) else []


def rename(config: AppsConfig, site: str, site_dir: Path) -> None:
	with site_lock(site_dir):
		proc.run(
			[
				config.python,
				config.rename_tool,
				"--site",
				site,
				"--yes",
				*(f"{p.old}={p.new}" for p in config.renamed_apps),
			],
			cwd=site_dir.parent,
		)


def reconcile(config: AppsConfig, site: str, skip: set[str]) -> int:
	site_dir = Path("sites", site)
	if not site_dir.is_dir():
		proc.say(f"reconcile-apps: sites/{site} does not exist yet — run provision-site first.")
		return 0
	apps_txt = Path("sites/apps.txt")
	if not apps_txt.is_file():
		return 0
	with site_lock(site_dir):
		installed = installed_apps(config, site)
		missing = [
			app
			for app in (line.strip() for line in apps_txt.read_text().splitlines())
			if app and app != "frappe" and app not in skip and app not in installed
		]
		if not missing:
			return 0
		proc.say(
			f"reconcile-apps: sites/apps.txt names app(s) missing from {site}'s installed apps:{''.join(' ' + a for a in missing)}"
		)
		failed = []
		for app in missing:
			proc.say(f"  installing {app}…")
			if proc.attempt([config.bench, "--site", site, "install-app", app]) != 0:
				failed.append(app)
	if failed:
		proc.warn(
			f"reconcile-apps: failed to install:{''.join(' ' + a for a in failed)} — see the error above; continuing."
		)
		return 0
	proc.say(f"reconcile-apps: {site} now has every app in sites/apps.txt installed.")
	return 0


def replace(config: AppsConfig, site: str, site_dir: Path) -> None:
	with site_lock(site_dir):
		installed = installed_apps(config, site)
		for pair in config.replaced_apps:
			if pair.old not in installed:
				continue
			if pair.new not in installed:
				proc.say(f"reconcile-apps: {pair.new} replaces {pair.old} on {site}: installing {pair.new}")
				proc.run([config.bench, "--site", site, "install-app", pair.new])
			proc.say(f"reconcile-apps: {pair.new} replaces {pair.old} on {site}: uninstalling {pair.old}")
			proc.run([config.bench, "--site", site, "uninstall-app", pair.old, "--yes", "--no-backup"])


def reconcile_apps(argv: Sequence[str], config: AppsConfig | None = None) -> int:
	"""The command; CONFIG is read from FRAPPE_NIX_CLI_CONFIG once the arguments
	are known to need it (so --help and usage errors need none)."""
	config = config or proc.apps_config()
	root = proc.bench_root()
	os.chdir(root)
	site = (argv[0] if argv else "") or os.environ.get("FRAPPE_SITE", "")
	site_dir = root / "sites" / site
	on_site = bool(site) and site_dir.is_dir()

	if config.renamed_apps and on_site:
		rename(config, site, site_dir)

	if not site:
		proc.fail(
			"reconcile-apps: FRAPPE_SITE is not set and no site was given.",
			"  Usage: reconcile-apps <site>   (or set FRAPPE_SITE in .env)",
		)
	skip = set(os.environ.get(SKIP, "").split()) | {p.old for p in config.replaced_apps}
	reconcile(config, site, skip)

	if config.replaced_apps and site_dir.is_dir():
		replace(config, site, site_dir)
	return 0


def main(argv: Sequence[str]) -> int:
	return proc.main_guard(lambda: reconcile_apps(argv))
