"""``bench get-app``: add an existing app as a git submodule and register it in
the uv workspace ([tool.uv.workspace] members and [tool.uv.sources]) and
sites/apps.txt. Was bash in lib/scripts.nix (PLAN.md, Track D, D3).

The clone is python-devops-nix's lib.sources (`frappe-nix-sources clone`), the
shape shell entry gives every app: a partial clone of the one branch, every
commit and folder, only the files the checkout needs. It is made before the
submodule exists, because the directory under apps/ is the Frappe app's name,
read from the clone, and a submodule's path is fixed when it is added. `git
submodule add` then takes the clone as it stands, and `absorbgitdirs` moves its
git directory under .git/modules, where `git submodule update` would have put
it.
"""

import os
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.config import AppsConfig
from frappe_nix_tools.apps.workspace import register, sync_registry, uv_then_build_deps

USAGE = """\
Usage: bench-get-app [--branch <name>] <url-or-alias>

Adds a Frappe app as a git submodule and integrates it into the workspace.
Without --branch the remote's default branch is used; either way the branch
is recorded in .gitmodules, which is what 'bench-update --pull' follows.

Examples:
  bench-get-app helpdesk                              # → frappe/helpdesk
  bench-get-app frappe/payments                      # owner/repo on GitHub
  bench-get-app https://github.com/frappe/hrms.git   # full URL
  bench-get-app --branch version-16 hrms             # a release branch"""

HOOKS = re.compile(r"^([^/]*)/hooks\.py$")


class UsageError(Exception):
	pass


def parse_args(argv: Sequence[str]) -> tuple[str, str] | None:
	"""(app, branch) from ARGV, or None for --help. UsageError for the rest."""
	app = ""
	branch = ""
	args = list(argv)
	while args:
		arg = args.pop(0)
		if arg in ("-b", "--branch"):
			if not args or not args[0]:
				raise UsageError(f"{arg} needs a branch name")
			branch = args.pop(0)
		elif arg.startswith("--branch="):
			branch = arg.partition("=")[2]
		elif arg in ("-h", "--help"):
			return None
		elif arg.startswith("-"):
			raise UsageError(f"unknown flag: {arg}")
		else:
			if app:
				raise UsageError("one app at a time")
			app = arg
	return app, branch


def resolve_url(app: str) -> str:
	"""A full URL (scheme:// or git@…) as it is; owner/repo on GitHub; a bare
	name, frappe's repository of that name."""
	if "://" in app or app.startswith("git@"):
		return app
	if "/" in app:
		return f"https://github.com/{app}.git"
	return f"https://github.com/frappe/{app}.git"


def repo_name(url: str) -> str:
	"""`basename URL .git`."""
	base = url.rstrip("/").rpartition("/")[2] or url
	if base.endswith(".git") and base != ".git":
		base = base[: -len(".git")]
	return base


def app_packages(clone: Path) -> list[str]:
	"""The top-level packages holding a hooks.py at the clone's HEAD: the folder
	listing, which a partial clone has without any file contents."""
	listing = proc.output(["git", "-C", str(clone), "ls-tree", "-r", "--name-only", "HEAD"])
	return [m.group(1) for line in listing.splitlines() if (m := HOOKS.match(line))]


def app_name(repo: str, packages: Sequence[str]) -> str:
	"""The Frappe app's name: the package holding hooks.py, which is what frappe
	imports (`<app>.hooks`) and sites/apps.txt lists, and not always the
	repository's (frappe/flow_client ships the app `flow`)."""
	if not packages:
		proc.warn(
			f"  ⚠  {repo} has no <package>/hooks.py — it does not look like a Frappe app; keeping the name '{repo}'"
		)
		return repo
	if len(packages) == 1:
		return packages[0]
	if repo not in packages:
		proc.warn(
			f"  ⚠  {repo} has several packages with a hooks.py ({' '.join(packages)}) and none is named for it; keeping the name '{repo}'"
		)
	return repo


def get_app(argv: Sequence[str], config: AppsConfig | None = None) -> int:
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
	app, branch = parsed
	if not app:
		print(USAGE)
		return 1

	config = config or proc.apps_config()
	os.chdir(proc.bench_root())
	url = resolve_url(app)
	repo = repo_name(url)
	if Path("apps", repo).is_dir():
		proc.say(f"Error: App '{repo}' already exists in apps/{repo}")
		return 1

	with tempfile.TemporaryDirectory() as tmp:
		clone = Path(tmp, repo)
		cmd = [config.sources, "clone"]
		if branch:
			# Asked for by name: a remote without it is an error, not its default.
			cmd += ["--branch", branch, "--require-branch"]
		proc.run([*cmd, "--", url, str(clone)])

		name = app_name(repo, app_packages(clone))
		app_dir = f"apps/{name}"
		if Path(app_dir).is_dir():
			proc.say(f"Error: App '{name}' already exists in {app_dir}")
			return 1
		if name != repo:
			proc.say(f"  {repo} is the Frappe app '{name}' ({name}/hooks.py) — adding it as {app_dir}")

		proc.say(f"Adding git submodule: {url} -> {app_dir}{f' ({branch})' if branch else ''}")
		Path("apps").mkdir(exist_ok=True)
		shutil.move(str(clone), app_dir)

	# -b both names the branch and records it in .gitmodules. That record is
	# load-bearing: `bench-update --pull` follows it, and a submodule registered
	# without one is never pulled. Without --branch the clone is the remote's
	# default branch; record whatever that turned out to be, so the checkout and
	# .gitmodules cannot disagree.
	proc.run(["git", "submodule", "add", "-q", *(["-b", branch] if branch else []), "--", url, app_dir])
	proc.run(["git", "submodule", "absorbgitdirs", "--", app_dir])
	# Not --recursive: Frappe apps often ship broken nested submodules that have
	# no production role and would fail init.
	proc.run(["git", "submodule", "update", "--init", "--", app_dir])
	if not branch:
		branch = proc.output(["git", "-C", app_dir, "symbolic-ref", "--short", "HEAD"]).strip()
		proc.run(["git", "config", "-f", ".gitmodules", f"submodule.{app_dir}.branch", branch])
	proc.say(f"  recorded branch '{branch}' in .gitmodules")

	register(config, name)
	sync_registry(config)

	proc.say("Syncing Python dependencies...")
	uv_then_build_deps(config, "sync")

	site = os.environ.get("FRAPPE_SITE") or "<site>"
	proc.say(
		"",
		f"✅ App '{name}' added successfully!",
		"",
		"Next steps:",
		"  1. Restart devenv: direnv reload --no-eval-cache",
		f"  2. Install the app: bench --site {site} install-app {name}",
		"  3. If it ships a package.json but no yarn.lock, resolve one for nix build: bench-update --node-locks",
	)
	return 0


def main(argv: Sequence[str]) -> int:
	return proc.main_guard(lambda: get_app(argv))
