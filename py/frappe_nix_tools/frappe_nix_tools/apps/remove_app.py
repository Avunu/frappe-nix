"""``bench remove-app``: the inverse of get-app. Was bash in lib/scripts.nix.

It replaces the real subcommand, which moves apps/<app> aside, edits
sites/apps.txt in place and pip-uninstalls into a read-only venv — none of which
fits a bench whose apps are submodules and whose registry is derived from
pyproject.toml.

Bench-level only, like the real one. `bench uninstall-app` — untouched, it
passes straight through the dispatcher — takes an app out of one site's
database; this takes it out of the bench, and refuses while any site still has
it installed.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.config import AppsConfig
from frappe_nix_tools.apps.workspace import build_deps, sync_registry

USAGE = """\
Usage: bench-remove-app [--force] [--no-backup] <app>

Removes <app> from this bench: deinits its git submodule and drops it from
.gitmodules (staging only that — any other uncommitted .gitmodules edit
stays as it was), removes it from the uv workspace (pyproject.toml),
regenerates sites/apps.txt and sites/apps.json, drops node-locks/<app>
and sites/assets/<app>, and relocks (uv lock).

Refuses while any site in this bench has <app> installed — take it out of
each first with `bench --site <site> uninstall-app <app>` — and while the
submodule's work tree has uncommitted changes.

  --force      skip both checks
  --no-backup  do not move a vendored app to .frappe-nix-backup/ first
               (a submodule is never backed up: `bench get-app` re-adds it)"""


class UsageError(Exception):
	pass


def parse_args(argv: Sequence[str]) -> tuple[str, bool, bool] | None:
	"""(app, force, backup) from ARGV, or None for --help."""
	app = ""
	force = False
	backup = True
	for arg in argv:
		if arg == "--force":
			force = True
		elif arg == "--no-backup":
			backup = False
		elif arg in ("-h", "--help"):
			return None
		elif arg.startswith("-"):
			raise UsageError(f"unknown flag: {arg}")
		else:
			if app:
				raise UsageError("one app at a time")
			app = arg
	return app, force, backup


def kind_of(config: AppsConfig, app: str) -> str:
	"""What frappe-nix-workspace's classifier calls apps/APP (submodule,
	submodule-uninitialized, local, nested-repo, …), or "" for a name it does
	not know: not under apps/, not in .gitmodules."""
	listing = proc.output([config.workspace, "apps", "--apps-dir", "apps"])
	for line in listing.splitlines():
		fields = line.split("\t")
		if fields[0] == app and len(fields) > 1:
			return fields[1]
	return ""


def installed_on(config: AppsConfig, site: str, app: str) -> bool | None:
	"""Whether SITE has APP installed, by the query reconcile-apps and restore
	use; None when its list cannot be read (database down, broken site)."""
	status, out = proc.probe([config.bench, "--site", site, "list-apps", "--format", "json"])
	if status != 0:
		return None
	try:
		data = json.loads(out)
	except ValueError:
		return None
	if not isinstance(data, dict):
		return None
	apps = data.get(site)
	if apps is None:
		apps = []
	if not isinstance(apps, list):
		return None
	return app in apps


def submodule_name(app_dir: str) -> str:
	"""The .gitmodules section for APP_DIR, which .git/config and .git/modules/
	are keyed on — not always its path."""
	_, out = proc.probe(["git", "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"])
	for line in out.splitlines():
		key, _, path = line.partition(" ")
		if path == app_dir:
			return key.removeprefix("submodule.").removesuffix(".path")
	return app_dir


def refuse_if_in_use(config: AppsConfig, app: str, app_dir: str, kind: str) -> None:
	# A site whose list cannot be read does not count as "not installed": that
	# is exactly when pulling the code out from under it would go unnoticed
	# until its next `bench migrate` dies on the import.
	installed: list[str] = []
	unreadable: list[str] = []
	for cfg in sorted(Path("sites").glob("*/site_config.json")):
		site = cfg.parent.name
		hit = installed_on(config, site, app)
		if hit is None:
			unreadable.append(site)
		elif hit:
			installed.append(site)
	if installed:
		proc.fail(
			f"bench-remove-app: {app} is still installed on:{''.join(' ' + s for s in installed)}",
			"Uninstall it from each site first:",
			*(f"    bench --site {s} uninstall-app {app}" for s in installed),
			"or pass --force to remove it anyway.",
		)
	if unreadable:
		proc.fail(
			f"bench-remove-app: could not read the installed apps of:{''.join(' ' + s for s in unreadable)}",
			"(is the database up? devenv up) — not removing " + app + " without knowing",
			"no site still runs it. Pass --force to remove it anyway.",
		)
	# deinit -f below discards the submodule's work tree wholesale. Its own
	# check for local changes is a dry-run `git rm`, which dies on any unstaged
	# .gitmodules edit, so git status is asked directly.
	if kind == "submodule":
		_, dirty = proc.probe(["git", "-C", app_dir, "status", "--porcelain"])
		dirty = dirty.rstrip("\n")
		if dirty:
			proc.fail(
				f"bench-remove-app: {app_dir} has uncommitted changes that removing it would discard:",
				*dirty.splitlines()[:20],
				"Commit or discard them, or pass --force to remove it anyway.",
			)


def remove_submodule(app_dir: str, kind: str) -> None:
	name = submodule_name(app_dir)
	proc.say(f"Removing the {app_dir} submodule…")
	# Plumbing, not `git rm`: git rm — --cached included — refuses outright
	# while .gitmodules has any unstaged edit, the usual state of a bench
	# mid-work. vendor_app (frappe_init/apps.py) sidesteps it the same way.
	if kind == "submodule":
		proc.run(["git", "submodule", "deinit", "-q", "-f", "--", app_dir])
	proc.attempt(["git", "config", "--local", "--remove-section", f"submodule.{name}"], quiet=True)
	proc.run(["git", "update-index", "--force-remove", "--", app_dir])
	try:
		os.rmdir(app_dir)
	except OSError:
		pass
	# .gitmodules' working copy and index copy each lose the section on their
	# own, so exactly this removal is staged next to the gitlink's — what
	# `git rm` would stage — and every other edit stays where it was.
	proc.attempt(["git", "config", "-f", ".gitmodules", "--remove-section", f"submodule.{name}"], quiet=True)
	status, staged = proc.probe(["git", "show", ":.gitmodules"])
	if status == 0:
		with tempfile.NamedTemporaryFile("w", delete=False) as f:
			f.write(staged)
		try:
			if (
				proc.attempt(
					["git", "config", "-f", f.name, "--remove-section", f"submodule.{name}"], quiet=True
				)
				== 0
			):
				blob = proc.output(["git", "hash-object", "-w", f.name]).strip()
				proc.run(["git", "update-index", "--cacheinfo", f"100644,{blob},.gitmodules"])
		finally:
			os.unlink(f.name)
	# Its clone. Left behind, it also makes a later `bench get-app` of the same
	# app refuse ("A git directory for … is found locally").
	gitdir = proc.output(["git", "rev-parse", "--git-path", f"modules/{name}"]).strip()
	shutil.rmtree(gitdir, ignore_errors=True)
	if os.path.lexists(app_dir):
		proc.warn(f"  ⚠  {app_dir} still holds files git never tracked; left in place")


def remove_local(app: str, app_dir: str, backup: bool) -> None:
	# A local app has no .git of its own — that is what tells it apart from a
	# nested repository — so its content lives nowhere but here and, the
	# committed part of it only, in the bench's history.
	if backup:
		Path(".frappe-nix-backup").mkdir(exist_ok=True)
		dest = f".frappe-nix-backup/{app}-{time.strftime('%Y%m%d-%H%M%S')}"
		proc.run(["git", "rm", "-r", "-q", "-f", "--cached", "--ignore-unmatch", "--", app_dir])
		shutil.move(app_dir, dest)
		proc.say(f"  moved to {dest} — restore with: mv {dest} {app_dir} && git add {app_dir}")
	else:
		proc.run(["git", "rm", "-r", "-q", "-f", "--ignore-unmatch", "--", app_dir])
		shutil.rmtree(app_dir, ignore_errors=True)


def remove_app(argv: Sequence[str], config: AppsConfig | None = None) -> int:
	"""The command; CONFIG is read from FRAPPE_NIX_CLI_CONFIG once the arguments
	are known to need it (so --help and usage errors need none)."""
	try:
		parsed = parse_args(argv)
	except UsageError as e:
		print(USAGE, file=sys.stderr)
		print("", file=sys.stderr)
		print(f"error: {e}", file=sys.stderr)
		return 1
	if parsed is None:
		print(USAGE)
		return 0
	app, force, backup = parsed
	if not app:
		print(USAGE, file=sys.stderr)
		return 1
	if app == "frappe":
		proc.fail("bench-remove-app: frappe is the framework every other app runs on — it cannot be removed.")
	if app in (".", "..") or "/" in app:
		proc.fail(f"bench-remove-app: '{app}' is not an app name.")

	config = config or proc.apps_config()
	os.chdir(proc.bench_root())
	app_dir = f"apps/{app}"

	# Identity, before anything moves. The dist name is the [tool.uv.sources]
	# key, which can differ from the directory's (a fork's own [project].name)
	# and is unreadable once the directory is gone.
	kind = kind_of(config, app)
	if not kind and f'"{app_dir}"' not in Path("pyproject.toml").read_text():
		proc.fail(
			f"bench-remove-app: no app '{app}' in this bench — not under apps/, not in",
			".gitmodules, not a uv workspace member.",
		)
	dist_name = ""
	if Path(app_dir).is_dir():
		dist_name = proc.output([config.workspace, "dist-name", "--app-dir", app_dir]).strip()

	if not force:
		refuse_if_in_use(config, app, app_dir, kind)

	if not kind:
		proc.say(
			f"  {app_dir} is not on disk and not in .gitmodules — only its workspace registration is left."
		)

	# Out of git.
	if kind in ("submodule", "submodule-uninitialized"):
		remove_submodule(app_dir, kind)
	elif kind == "local":
		remove_local(app, app_dir, backup)
	elif kind == "nested-repo":
		proc.fail(
			f"bench-remove-app: {app_dir} is a git repository but not a registered submodule —",
			"not removing it automatically. By hand:",
			f"    git rm --cached {app_dir} && rm -rf {app_dir}",
		)

	# Out of the workspace, the registry and the build inputs.
	proc.say(f"Removing {app} from the uv workspace…")
	proc.run(
		[
			config.workspace,
			"remove-app",
			"--pyproject",
			"pyproject.toml",
			"--app",
			app,
			"--source-name",
			dist_name,
		]
	)
	sync_registry(config)

	locks = Path("node-locks", app)
	if locks.is_dir():
		shutil.rmtree(locks)
		proc.say(f"  - node-locks/{app}")
	# A symlink into apps/<app>/<app>/public — dangling now.
	assets = Path("sites/assets", app)
	if assets.is_symlink():
		assets.unlink()
		proc.say(f"  - sites/assets/{app}")

	proc.say("Re-locking the Python workspace…")
	if proc.attempt(["uv", "lock"]) != 0:
		proc.fail("  ⚠  uv lock failed — the dev shell will not evaluate until it resolves. Re-run: uv lock")
	build_deps(config)

	proc.say(
		"",
		f"✅ {app} removed from the bench.",
		"   Commit the staged removal with pyproject.toml, uv.lock and sites/apps.json,",
		"   then: direnv reload --no-eval-cache",
	)
	return 0


def main(argv: Sequence[str]) -> int:
	return proc.main_guard(lambda: remove_app(argv))
