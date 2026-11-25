"""``reconcile-apps``: install every app sites/apps.txt names that the site's
database does not have yet, with frappe-nix.renamedApps and replacedApps around
it (docs/app-standards/spec.md §5.10). Was bash in lib/scripts.nix and
lib/rename/reconcile.nix.

It runs as `reconcile-apps [<site>]` by hand and as `devenv up`'s
frappe:apps-reconcile task, and the one rendering has to be right in both. This
is what `bench provision` leaves undone afterwards: a sibling pinned into the
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

A copy that waits for the lock says so and polls for it. A copy stops, with
status 143, instead of starting its next step once it has been sent SIGTERM or
SIGHUP, or once the run that started it is gone: a copy can outlive that run,
and then nothing waits for the work it would do. devenv cancels a task with
SIGTERM to the task's process scope and SIGKILL after a grace period, so a step
in progress can be cut short by that; the copy decides only what starts next.
"""

import fcntl
import json
import os
import signal
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.config import AppsConfig

#: Apps reconcile never installs, besides replacedApps' OLDs: the variable the
#: bash wrapper set for the bash body, still read for a caller that sets it.
SKIP = "FRAPPE_NIX_RECONCILE_SKIP"

#: Seconds between tries at a lock another copy holds.
LOCK_POLL = 1.0

#: The status of a copy that stopped instead of starting its next step (128 + SIGTERM).
STOPPED = 143


class Stopped(Exception):
	"""This copy was asked to stop, and stopped before its next step."""


#: What stop_requested() reads: the signal that asked for a stop, and the process that
#: started this copy with that process's own parent (None where /proc cannot say).
_STOP: dict[str, Any] = {"signal": None, "starter": None}


def _on_stop(signum: int, _frame: object) -> None:
	_STOP["signal"] = signum


def _ppid(pid: int) -> int | None:
	"""The parent of PID, read from /proc (Linux); None where it cannot be read."""
	try:
		stat = Path(f"/proc/{pid}/stat").read_text()
	except OSError:
		return None
	# The command name sits in parentheses and may hold spaces: the fields follow its last ")".
	return int(stat.rsplit(")", 1)[1].split()[1])


def stop_requested() -> str:
	"""Why this copy should start no more work, or "" to carry on: a SIGTERM or
	SIGHUP has come, or the run that started it is gone. The starter is checked,
	and so is the starter's parent: a copy whose starter is orphaned keeps the same
	parent pid, so the copy's own parent alone does not show the run is gone."""
	if _STOP["signal"] is not None:
		return f"signal {signal.Signals(_STOP['signal']).name}"
	starter = _STOP["starter"]
	if starter is not None:
		pid, grandparent = starter
		if os.getppid() != pid or (grandparent is not None and _ppid(pid) != grandparent):
			return "the process that started it is gone"
	return ""


def checkpoint(before: str) -> None:
	"""Raise Stopped rather than start BEFORE, when stop_requested() says so."""
	why = stop_requested()
	if why:
		raise Stopped(f"reconcile-apps: stopped before {before}: {why}")


@contextmanager
def stopping() -> Generator[None]:
	"""For one run: record the process that started this copy (and its parent), and
	catch SIGTERM and SIGHUP instead of dying at once, so the copy stops before its
	next step rather than in the middle of a bookkeeping write. The handlers found
	are restored afterwards."""
	_STOP["signal"] = None
	starter = os.getppid()
	_STOP["starter"] = (starter, _ppid(starter))
	saved: list[tuple[int, Any]] = []
	try:
		for sig in (signal.SIGTERM, signal.SIGHUP):
			saved.append((sig, signal.signal(sig, _on_stop)))
	except ValueError:  # not the main thread: no handler, but the parent check still applies
		pass
	try:
		yield
	finally:
		for sig, handler in saved:
			if handler is not None:
				signal.signal(sig, handler)
		_STOP["signal"] = None
		_STOP["starter"] = None


@contextmanager
def site_lock(site_dir: Path) -> Generator[None]:
	"""The site's reconcile lock, held for the block. A copy that finds it held
	says so and polls for it; a stop ends the wait without the lock, and a stop
	that arrives once the lock is taken ends the block before its work."""
	site = site_dir.name
	locks = site_dir / "locks"
	locks.mkdir(parents=True, exist_ok=True)
	with open(locks / "reconcile-apps.lock", "w") as f:
		began = time.monotonic()
		waiting = False
		while True:
			try:
				fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
				break
			except BlockingIOError:
				if not waiting:
					proc.say(
						f"reconcile-apps: {site}'s reconcile lock is held by another copy; waiting for it"
					)
					waiting = True
				checkpoint(f"waiting for {site}'s reconcile lock")
				time.sleep(LOCK_POLL)
		checkpoint(f"using {site}'s reconcile lock")
		if waiting:
			proc.say(f"reconcile-apps: got {site}'s reconcile lock after {time.monotonic() - began:.0f} s")
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
		proc.say(f"reconcile-apps: sites/{site} does not exist yet — run bench provision first.")
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
			checkpoint(f"installing {app}")
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
				checkpoint(f"installing {pair.new}")
				proc.say(f"reconcile-apps: {pair.new} replaces {pair.old} on {site}: installing {pair.new}")
				proc.run([config.bench, "--site", site, "install-app", pair.new])
			checkpoint(f"uninstalling {pair.old}")
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

	with stopping():
		try:
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
		except Stopped as e:
			proc.say(str(e))
			return STOPPED
	return 0


def main(argv: Sequence[str]) -> int:
	return proc.main_guard(lambda: reconcile_apps(argv))
