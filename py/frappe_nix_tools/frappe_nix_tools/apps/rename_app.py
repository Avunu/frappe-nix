"""``bench rename-app``: the bench's half of renaming an app.

Renaming an app (`esign` to `esign_webforms`) is three jobs, and `frappe-rename-app`
does two of them: the package in the app's own repository (`frappe-rename-app code`)
and the sites that have it installed (`renamedApps`, `frappe-rename-app --site`). The
third is the bench's, and a `git pull` of the renamed repository does none of it: the
apps/<old> submodule, its .gitmodules entry and its clone, the uv workspace member and
source, the registry, the fallback node locks.

Pulling leaves the old directory behind as an untracked repository and the new path
empty, and the Frappe app's name has to be the directory's: the registry skips an app
whose package is not named for its directory. Doing it by hand is removing the
submodule and adding the new one, which downloads the app again, loses its
node_modules, and leaves the old clone under .git/modules and the old section in
.git/config. This moves what is there instead.

Git's own `git mv` is not used for the submodule, for the reason bench-remove-app
does not use `git rm`: it refuses outright while .gitmodules has any unstaged edit,
which is the usual state of a bench mid-work. The same plumbing does the move, and
.gitmodules' working copy and index copy each get exactly this change and no other.
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
from frappe_nix_tools.apps.get_app import repo_name, resolve_url
from frappe_nix_tools.apps.remove_app import kind_of, submodule_name
from frappe_nix_tools.apps.workspace import build_deps, register, sync_registry

USAGE = """\
Usage: bench-rename-app [--url <url>] [--branch <name>] [--no-pull] <old> <new>

Renames an app in this bench: apps/<old> becomes apps/<new>, with its git
submodule (the checkout, its clone under .git/modules, its .gitmodules entry and
.git/config section), its uv workspace member and source, sites/apps.txt and
sites/apps.json. The checkout keeps its node_modules and its history; nothing is
downloaded again.

This is the bench's half of a rename. The other two are `frappe-rename-app`'s:
the package in the app's repository (`frappe-rename-app code --from <old> --to
<new>`, pushed first) and the sites that have <old> installed (renamedApps in
flake.nix, or `frappe-rename-app --site`).

  --url <url>      the renamed repository. Without it, the URL .gitmodules has
                   with a last component of <old> replaced by <new>; a URL that
                   does not end in <old> is kept as it is
  --branch <name>  the branch .gitmodules records (bench-update --pull follows
                   it). Without it, the one it already has
  --no-pull        do not move the checkout to the branch's tip. By default the
                   tip is fetched from the new URL and checked out, which is
                   what carries the renamed package; with this the checkout is
                   renamed where it is, and must already hold <new>/hooks.py

Nothing is changed until the tip has been fetched and shown to hold the renamed
package (<new>/hooks.py), so a failed fetch or an unrenamed repository leaves the
bench as it was."""

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UsageError(Exception):
	pass


def parse_args(argv: Sequence[str]) -> tuple[str, str, str, str, bool] | None:
	"""(old, new, url, branch, pull) from ARGV, or None for --help."""
	names: list[str] = []
	url = ""
	branch = ""
	pull = True
	args = list(argv)
	while args:
		arg = args.pop(0)
		if arg in ("-h", "--help"):
			return None
		if arg == "--no-pull":
			pull = False
		elif arg in ("--url", "--branch", "-b"):
			if not args or not args[0]:
				raise UsageError(f"{arg} needs a value")
			value = args.pop(0)
			if arg == "--url":
				url = value
			else:
				branch = value
		elif arg.startswith("--url="):
			url = arg.partition("=")[2]
		elif arg.startswith("--branch="):
			branch = arg.partition("=")[2]
		elif arg.startswith("-"):
			raise UsageError(f"unknown flag: {arg}")
		else:
			names.append(arg)
	if len(names) != 2:
		raise UsageError("give the old name and the new one")
	return names[0], names[1], url, branch, pull


def renamed_url(url: str, old: str, new: str) -> str:
	"""URL with its last component replaced by NEW when that is OLD (".git" and a
	trailing slash kept), else URL as it was: a repository that was not named for
	its app has nothing to infer a new name from."""
	match = re.match(r"^(?P<head>.*[/:])(?P<repo>[^/:]+?)(?P<tail>\.git/?|/)?$", url)
	if match and match["repo"] == old:
		return f"{match['head']}{new}{match['tail'] or ''}"
	return url


def package_in_tree(app_dir: str, rev: str, app: str) -> bool:
	"""Whether REV's tree has APP/hooks.py — the folder listing, which a partial
	clone has without any file contents."""
	status, listing = proc.probe(
		["git", "-C", app_dir, "ls-tree", "--name-only", rev, "--", f"{app}/hooks.py"]
	)
	return status == 0 and bool(listing.strip())


def gitmodules_value(name: str, key: str) -> str:
	status, value = proc.probe(["git", "config", "-f", ".gitmodules", "--get", f"submodule.{name}.{key}"])
	return value.strip() if status == 0 else ""


def fetch_tip(app_dir: str, url: str, branch: str) -> str:
	"""BRANCH of URL, fetched into the clone at APP_DIR the way bench-update does
	(commits and folders, no file contents, when the clone is a partial one), and
	its commit."""
	_, promisor = proc.probe(["git", "-C", app_dir, "config", "--get", "remote.origin.promisor"])
	partial = ["--filter=blob:none"] if promisor.strip() == "true" else []
	proc.say(f"Fetching {branch} from {url}…")
	status = proc.attempt(
		["git", "-C", app_dir, "fetch", "-q", *partial, "--no-recurse-submodules", url, branch]
	)
	if status != 0:
		proc.fail(
			f"bench-rename-app: could not fetch '{branch}' from {url} — nothing was changed.",
			"If the repository has not been renamed yet, do that first; if it has, pass its",
			"address with --url.",
		)
	return proc.output(["git", "-C", app_dir, "rev-parse", "FETCH_HEAD^{commit}"]).strip()


def edit_gitmodules(file: str, old_name: str, new_name: str, new_dir: str, url: str, branch: str) -> bool:
	"""The rename's one change to a .gitmodules at FILE: the section renamed, and
	its path, url and branch set. False when FILE has no section to change."""
	if proc.attempt(["git", "config", "-f", file, "--get", f"submodule.{old_name}.path"], quiet=True) != 0:
		return False
	if new_name != old_name:
		proc.run(
			[
				"git",
				"config",
				"-f",
				file,
				"--rename-section",
				f"submodule.{old_name}",
				f"submodule.{new_name}",
			]
		)
	section = f"submodule.{new_name}"
	proc.run(["git", "config", "-f", file, f"{section}.path", new_dir])
	if url:
		proc.run(["git", "config", "-f", file, f"{section}.url", url])
	if branch:
		proc.run(["git", "config", "-f", file, f"{section}.branch", branch])
	return True


def stage_gitmodules(old_name: str, new_name: str, new_dir: str, url: str, branch: str) -> None:
	"""The same edit to .gitmodules' index copy, so exactly this rename is staged
	next to the gitlink's — what `git mv` would stage — and every other edit stays
	where it was, unstaged."""
	status, staged = proc.probe(["git", "show", ":.gitmodules"])
	if status != 0:
		return
	with tempfile.NamedTemporaryFile("w", delete=False) as f:
		f.write(staged)
	try:
		if edit_gitmodules(f.name, old_name, new_name, new_dir, url, branch):
			blob = proc.output(["git", "hash-object", "-w", f.name]).strip()
			proc.run(["git", "update-index", "--cacheinfo", f"100644,{blob},.gitmodules"])
	finally:
		os.unlink(f.name)


def clone_of(app_dir: str) -> Path | None:
	"""The git directory a submodule checkout's `.git` file points at, as a path
	from the bench root; None for a checkout that keeps its own .git directory (or
	has none), which moves with it."""
	dotgit = Path(app_dir, ".git")
	if not dotgit.is_file():
		return None
	text = dotgit.read_text().strip()
	if not text.startswith("gitdir:"):
		return None
	return Path(os.path.normpath(Path(app_dir) / text[len("gitdir:") :].strip()))


def link(app_dir: str, gitdir: Path) -> None:
	"""A checkout and its clone aimed at each other: the checkout's `.git` file
	(relative to the checkout) and the clone's core.worktree (relative to the
	clone). What `git mv` and `git submodule absorbgitdirs` write; nothing else
	moves a checkout without leaving both naming where it was."""
	Path(app_dir, ".git").write_text(
		f"gitdir: {os.path.relpath(gitdir.resolve(), Path(app_dir).resolve())}\n"
	)
	worktree = os.path.relpath(Path(app_dir).resolve(), gitdir.resolve())
	proc.run(["git", "config", "--file", str(gitdir / "config"), "core.worktree", worktree])


def move_submodule(
	old_dir: str, new_dir: str, old_name: str, new_name: str, url: str, branch: str, pin: str
) -> None:
	"""Everything git keeps about the submodule, moved from OLD_DIR to NEW_DIR."""
	proc.say(f"Moving the {old_dir} submodule to {new_dir}…")
	# Read while the checkout is still where its `.git` file was written for.
	gitdir = clone_of(old_dir)
	os.rename(old_dir, new_dir)
	if new_name != old_name:
		old_git = proc.output(["git", "rev-parse", "--git-path", f"modules/{old_name}"]).strip()
		new_git = proc.output(["git", "rev-parse", "--git-path", f"modules/{new_name}"]).strip()
		# The clone is renamed with the submodule, when it is the one under that
		# name: left behind, it makes a later app at the old name look as if this
		# bench had set it up before.
		if gitdir is not None and gitdir.resolve() == Path(old_git).resolve() and gitdir.is_dir():
			Path(new_git).parent.mkdir(parents=True, exist_ok=True)
			os.rename(old_git, new_git)
			gitdir = Path(new_git)
		proc.attempt(
			[
				"git",
				"config",
				"--local",
				"--rename-section",
				f"submodule.{old_name}",
				f"submodule.{new_name}",
			],
			quiet=True,
		)
	if gitdir is not None and gitdir.is_dir():
		link(new_dir, gitdir)
	# The working copy gets the edit even if the index copy has no section yet (a
	# submodule added and not committed).
	edit_gitmodules(".gitmodules", old_name, new_name, new_dir, url, branch)
	stage_gitmodules(old_name, new_name, new_dir, url, branch)
	# Plumbing, for the reason in the module docstring. The gitlink: the pin the
	# bench recorded, or the commit the checkout is about to sit at.
	proc.run(["git", "update-index", "--force-remove", "--", old_dir])
	proc.run(["git", "update-index", "--add", "--cacheinfo", f"160000,{pin},{new_dir}"])
	# .git/config's url and the clone's remote.origin.url, from the .gitmodules just written.
	proc.attempt(["git", "submodule", "sync", "-q", "--", new_dir], quiet=True)


def rename_app(argv: Sequence[str], config: AppsConfig | None = None) -> int:
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
	old, new, url_arg, branch_arg, pull = parsed
	if "frappe" in (old, new):
		proc.fail("bench-rename-app: frappe is the framework every other app runs on — it cannot be renamed.")
	for name in (old, new):
		if name in (".", "..") or "/" in name:
			proc.fail(f"bench-rename-app: '{name}' is not an app name.")
	if not NAME.match(new):
		proc.fail(
			f"bench-rename-app: '{new}' is not a valid app name: it is a Python package, so letters, digits and _."
		)
	if old == new:
		proc.fail(f"bench-rename-app: '{old}' is already called that.")

	config = config or proc.apps_config()
	os.chdir(proc.bench_root())
	old_dir, new_dir = f"apps/{old}", f"apps/{new}"

	kind = kind_of(config, old)
	if not kind and f'"{old_dir}"' not in Path("pyproject.toml").read_text():
		proc.fail(
			f"bench-rename-app: no app '{old}' in this bench — not under apps/, not in .gitmodules, not a uv workspace member."
		)
	if kind == "nested-repo":
		proc.fail(
			f"bench-rename-app: {old_dir} is a git repository but not a registered submodule —",
			"not renaming it automatically. Register it (bench-get-app) or vendor it (frappe-init --migrate) first.",
		)
	if kind == "submodule-uninitialized":
		proc.fail(
			f"bench-rename-app: {old_dir} is registered but not checked out — there is nothing to move.",
			f"Check it out first: git submodule update --init -- {old_dir}",
		)
	if kind not in ("submodule", "local"):
		proc.fail(
			f"bench-rename-app: {old_dir} is not on disk, so there is nothing to move. bench-remove-app clears a leftover registration."
		)
	if (
		os.path.lexists(new_dir)
		or kind_of(config, new)
		or f'"{new_dir}"' in Path("pyproject.toml").read_text()
	):
		proc.fail(f"bench-rename-app: {new_dir} already exists in this bench.")
	mapped = {pair.old: pair.new for pair in config.renamed_apps}
	if mapped.get(old, new) != new:
		proc.fail(
			f"bench-rename-app: flake.nix's renamedApps says {old} is renamed to {mapped[old]}, not {new}."
		)

	# Identity, before anything moves: the dist name is the [tool.uv.sources] key,
	# unreadable once the directory is gone.
	old_dist = proc.output([config.workspace, "dist-name", "--app-dir", old_dir]).strip()

	old_name = new_name = url = branch = pin = tip = ""
	if kind == "submodule":
		old_name = submodule_name(old_dir)
		# A submodule named for its path moves its name too; one named otherwise
		# (a name somebody chose) keeps it.
		new_name = new_dir if old_name == old_dir else old_name
		old_url = gitmodules_value(old_name, "url")
		url = resolve_url(url_arg) if url_arg else renamed_url(old_url, old, new)
		if not url_arg and url == old_url:
			proc.say(
				f"  {old_url} does not end in '{old}' — keeping that URL (pass --url if the repository was renamed)"
			)
		branch = branch_arg or gitmodules_value(old_name, "branch")
		fields = proc.output(["git", "ls-files", "-s", "--", old_dir]).split()
		pin = (
			fields[1] if len(fields) > 1 else proc.output(["git", "-C", old_dir, "rev-parse", "HEAD"]).strip()
		)

		if pull and not branch:
			proc.say(
				f"  {old_dir} follows no branch in .gitmodules — not moving the checkout (pass --branch to choose one)"
			)
		elif pull:
			if proc.output(["git", "-C", old_dir, "status", "--porcelain", "--untracked-files=no"]).strip():
				proc.fail(
					f"bench-rename-app: {old_dir} has uncommitted changes that moving it to the tip would conflict with.",
					"Commit or discard them, or pass --no-pull to rename the checkout where it is.",
				)
			tip = fetch_tip(old_dir, url, branch)
			if (
				proc.attempt(["git", "-C", old_dir, "merge-base", "--is-ancestor", "HEAD", tip], quiet=True)
				!= 0
			):
				proc.fail(
					f"bench-rename-app: {old_dir}'s checkout is not an ancestor of {branch} at {url} — it has commits that",
					"branch lacks, or the repository was replaced rather than renamed. Nothing was changed.",
					f"Move the checkout where you want it (git -C {old_dir} checkout …), then run this with --no-pull.",
				)
			if not package_in_tree(old_dir, tip, new):
				proc.fail(
					f"bench-rename-app: {branch} at {url} has no {new}/hooks.py — it does not carry the package rename.",
					f"Rename the package in the app's repository and push it: frappe-rename-app code --from {old} --to {new}",
					"Nothing was changed.",
				)
			pin = tip
		if not tip and not Path(old_dir, new, "hooks.py").is_file():
			proc.fail(
				f"bench-rename-app: {old_dir} has no {new}/hooks.py — its checkout does not carry the package rename.",
				"The registry skips an app whose package is not named for its directory. Nothing was changed.",
			)
	elif not Path(old_dir, new, "hooks.py").is_file():
		proc.fail(
			f"bench-rename-app: {old_dir} has no {new}/hooks.py — rename its package first:",
			f"    frappe-rename-app code --from {old} --to {new}",
			"The registry skips an app whose package is not named for its directory. Nothing was changed.",
		)

	# From here the bench changes.
	if kind == "submodule":
		move_submodule(old_dir, new_dir, old_name, new_name, url, branch, pin)
		if tip:
			proc.run(["git", "-C", new_dir, "checkout", "-q", "-B", branch, tip])
	else:
		proc.say(f"Moving {old_dir} to {new_dir}…")
		os.rename(old_dir, new_dir)
		proc.run(["git", "add", "-A", "--", old_dir, new_dir])

	proc.say(f"Renaming {old} to {new} in the uv workspace…")
	proc.run(
		[
			config.workspace,
			"remove-app",
			"--pyproject",
			"pyproject.toml",
			"--app",
			old,
			"--source-name",
			old_dist,
		]
	)
	register(config, new)
	sync_registry(config)

	locks = Path("node-locks", old)
	if locks.is_dir():
		proc.run(["git", "rm", "-r", "-q", "-f", "--ignore-unmatch", "--", str(locks)])
		shutil.rmtree(locks, ignore_errors=True)
		proc.say(
			f"  - node-locks/{old} (it is keyed on the package.json, which the rename changed: bench-update --node-locks makes it again)"
		)
	# A symlink into apps/<old>/<old>/public — dangling now.
	assets = Path("sites/assets", old)
	if assets.is_symlink():
		assets.unlink()
		proc.say(f"  - sites/assets/{old}")

	proc.say("Re-locking the Python workspace…")
	if proc.attempt(["uv", "lock"]) != 0:
		proc.fail("  ⚠  uv lock failed — the dev shell will not evaluate until it resolves. Re-run: uv lock")
	build_deps(config)

	steps = ["   Commit the staged move with pyproject.toml, uv.lock and sites/apps.json."]
	if kind == "submodule" and not tip:
		# --no-pull (or no branch to pull): the pin the bench recorded was kept, and the
		# checkout may be somewhere else. The flake builds the pin, not the checkout.
		head = proc.output(["git", "-C", new_dir, "rev-parse", "HEAD"]).strip()
		if head != pin:
			steps.append(
				f"   {new_dir} is at {head[:12]} but the bench records {pin[:12]}: git add {new_dir} to record it."
			)
	if mapped.get(old) != new:
		steps += [
			f"   The dev site still records {old}. Name the rename in flake.nix, so `devenv up` renames it",
			f"   on the site before it installs anything ({new} beside {old} would fail on the Module Def they share):",
			f'       frappe-nix.renamedApps = {{ {old} = "{new}"; }};',
			f"   or by hand: frappe-rename-app --site <site> {old}={new}",
		]
	steps.append("   Then: direnv reload --no-eval-cache")
	proc.say("", f"✅ {old} renamed to {new} in the bench.", *steps)
	return 0


def main(argv: Sequence[str]) -> int:
	return proc.main_guard(lambda: rename_app(argv))
