"""``relock`` (``nix run .#relock`` outside the dev shell): regenerate what a
repository commits on frappe-nix's behalf. frappe-nix's one consumer-facing
relock (python-devops-nix docs/conventions.md, Relock). Was bash in
modules/devenv.nix and lib/relock.nix;
tests/relock.sh is its acceptance suite.

    relock [--uv-only | --node-locks [<app>[/<subdir>]…]] [uv lock flags…]

In a bench, from its root: ``ensure-root`` over its pyproject.toml (what the
shell-entry root sync reconciles), ``uv lock`` there and the managed
build-dependency block after it, then node-locks/ for the apps without a yarn.lock of their own.
In app mode: <lockDir>/uv.lock from a writable copy of the workspace the flake
assembles, and <lockDir>/node-locks/ for the pins without a yarn.lock, both
written back into the repository and staged, since a flake's source tree is
only its tracked files.

A flake app, outside every other output's dependency graph: it has to run when
nothing that touches the Python workspace evaluates, which is exactly when it
is needed (a stale or missing lock fails evaluation). uv, nodejs and yarn come
from its wrapper's PATH, the dev shell's own.
"""

import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.proc import fail, say, warn
from frappe_nix_tools.deps.config import RelockConfig
from frappe_nix_tools.deps.update import UV_LOCK

NAME = "frappe-nix-relock"
COMMAND = "nix run .#relock -- --node-locks"


def help_text(lock_dir: str | None) -> str:
	if lock_dir is None:
		files = """\
  uv.lock        the resolved Python workspace, after pyproject.toml is
                 reconciled with what this frappe-nix expects of it, and
                 the managed [tool.uv.extra-build-dependencies] block
  node-locks/    a fallback yarn.lock for each app and nested frontend that
                 ships none of its own (the ones that do build from it, and
                 nothing is written for them)."""
		when = """\
Run it from the bench root, after an app's pyproject.toml or package.json
moved. bench-update --pull does the same for what a pull moves."""
		what = "this bench commits"
	else:
		files = f"""\
  {lock_dir}/uv.lock     the resolved Python workspace
  {lock_dir}/node-locks/   a fallback yarn.lock for each pinned app and nested
                 frontend that ships none of its own (the ones that
                 do build from it, and nothing is written for them)."""
		when = """\
Run it after `nix flake update` and after editing pyproject.toml.
Both are staged for you: a flake's source tree is only its tracked
files, so an unstaged lock is still invisible to evaluation."""
		what = "this app repo commits"
	return f"""\
Usage: nix run .#relock [--uv-only | --node-locks [<app>[/<subdir>]…]] [uv lock flags…]

Regenerates what {what} on frappe-nix's behalf:

{files}
                 Name a target after --node-locks to force a lock
                 over the yarn.lock it ships — for one that cannot
                 resolve offline.

{when}"""


class _Help(Exception):
	pass


@dataclass
class Args:
	uv: bool = True
	locks: bool = True
	uv_args: list[str] = field(default_factory=list)
	targets: list[str] = field(default_factory=list)


def parse(argv: Sequence[str]) -> Args:
	"""Word by word: a flag this does not know goes to `uv lock`, and so does a
	word while the Python half is on; after --node-locks, words are targets."""
	args = Args()
	for arg in argv:
		if arg == "--uv-only":
			args.locks = False
		elif arg in ("--node-locks", "--node-hashes"):
			if arg == "--node-hashes":
				warn(
					"relock: --node-hashes is now --node-locks (node-offline-hashes.json was replaced by node-locks/)"
				)
			args.uv = False
		elif arg in ("-h", "--help"):
			raise _Help
		elif arg.startswith("-") or args.uv:
			args.uv_args.append(arg)
		else:
			args.targets.append(arg)
	return args


def uv_version() -> str:
	status, out = proc.probe(["uv", "--version"])
	return out.strip() if status == 0 else "uv"


def node_locks(config: RelockConfig, bench: str, locks: str, targets: Sequence[str]) -> None:
	proc.run(
		[
			config.node_locks,
			*(f"--exclude={key}" for key in config.node_nested_frontend_excludes),
			f"--command={COMMAND}",
			bench,
			locks,
			*targets,
		]
	)


def bench_mode(config: RelockConfig, args: Args) -> int:
	if not Path("pyproject.toml").is_file() or not Path("apps").is_dir():
		fail(f"{NAME}: run this from the bench root (pyproject.toml + apps/)")
	if args.uv:
		# What frappe-nix asks of the root first, as the shell-entry root sync
		# does (frappe_nix_tools.apps.root_sync): the command run when the shell
		# will not open has to be the one that fixes what keeps it shut.
		if config.workspace_tool and config.template:
			proc.run(
				[
					config.workspace_tool,
					"ensure-root",
					"--pyproject",
					"pyproject.toml",
					"--template",
					config.template,
				]
			)
		say(f"Re-locking the Python workspace with {uv_version()}…")
		proc.run([*UV_LOCK, *args.uv_args])
		if config.build_deps_sync:
			proc.run([config.build_deps_sync])
	if args.locks:
		say("── Refreshing node-locks/ (fallback locks for apps without a yarn.lock) ──")
		node_locks(config, ".", "node-locks", args.targets)
	if args.uv and args.locks:
		say("✅ uv.lock and node-locks/ updated — commit them (and pyproject.toml), then re-enter the shell.")
	elif args.uv:
		say("✅ uv.lock updated — commit it (and pyproject.toml), then re-enter the shell.")
	else:
		say("✅ node-locks/ updated — commit it, then re-enter the shell.")
	return 0


def _writable(root: str) -> None:
	"""`chmod -R u+w`: a copy of the store keeps its 0444 and 0555 modes."""
	os.chmod(root, os.stat(root).st_mode | 0o200)
	for parent, dirs, files in os.walk(root):
		for name in [*dirs, *files]:
			path = os.path.join(parent, name)
			if not os.path.islink(path):
				os.chmod(path, os.stat(path).st_mode | 0o200)


def _git(*args: str, cwd: str) -> tuple[int, str]:
	return proc.probe(["git", "-C", cwd, *args])


def _same(a: str, b: str) -> bool:
	import filecmp

	return filecmp.cmp(a, b, shallow=False)


def app_mode(config: RelockConfig, args: Args) -> int:
	lock_dir = config.lock_dir
	lock_rel = f"{lock_dir}/uv.lock"
	locks_rel = f"{lock_dir}/node-locks"
	workspace = config.workspace or ""
	status, top = proc.probe(["git", "rev-parse", "--show-toplevel"])
	root = top.strip() if status == 0 and top.strip() else os.getcwd()
	lockdir = os.path.join(root, lock_dir)
	os.makedirs(lockdir, exist_ok=True)
	lock = os.path.join(lockdir, "uv.lock")

	if args.uv:
		# A writable copy of the workspace this flake assembles, minus uv.lock.
		# Everything uv reads — the generated root pyproject.toml, every
		# apps/*/pyproject.toml — is exactly what evaluation sees, so what
		# resolves here is what the build would have resolved. The result
		# travels: uv.lock records workspace members by relative path and carries
		# no hash for them.
		work = tempfile.mkdtemp()
		try:
			shutil.copytree(workspace, work, symlinks=True, dirs_exist_ok=True)
			_writable(work)
			# Seed the previous resolution so uv only moves what has to move.
			if os.path.isfile(lock):
				shutil.copyfile(lock, os.path.join(work, "uv.lock"))
				os.chmod(os.path.join(work, "uv.lock"), 0o644)
			cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.environ.get("HOME", ""), ".cache")
			os.environ.setdefault("UV_CACHE_DIR", os.path.join(cache, "uv"))
			say(f"Re-locking the Python workspace with {uv_version()}…")
			proc.run([*UV_LOCK, *args.uv_args], cwd=work)
			fresh = os.path.join(work, "uv.lock")
			if os.path.isfile(lock) and _same(fresh, lock):
				say(f"  {lock_rel} already current")
			else:
				shutil.copyfile(fresh, lock)
				os.chmod(lock, 0o644)
				say(f"  + {lock_rel}")
		finally:
			_writable(work)
			shutil.rmtree(work, ignore_errors=True)

	if args.locks:
		# Over the assembled workspace: its apps/ are the pinned inputs, mirrored
		# as symlinks the generator reads through.
		say(f"── Refreshing {locks_rel} (fallback locks for pins without a yarn.lock) ──")
		node_locks(config, workspace, os.path.join(lockdir, "node-locks"), args.targets)

	if os.path.isfile(lock) and _git("ls-files", "--error-unmatch", "--", lock_rel, cwd=root)[0] != 0:
		if proc.attempt(["git", "-C", root, "add", "--", lock_rel]) == 0:
			say(f"  staged {lock_rel} (untracked, it is invisible to the flake)")
	if os.path.isdir(os.path.join(lockdir, "node-locks")):
		_, untracked = _git("ls-files", "--others", "--exclude-standard", "--", locks_rel, cwd=root)
		if untracked.strip() and proc.attempt(["git", "-C", root, "add", "--", locks_rel]) == 0:
			say(f"  staged new files under {locks_rel}/ (untracked, they are invisible to the flake)")

	say(f"✅ commit {lock_dir}/, then re-enter the shell.")
	return 0


def load() -> RelockConfig:
	from frappe_nix_tools.cli_config import load_dataclass, read_json

	return load_dataclass(RelockConfig, read_json(None, hint="run this as `nix run .#relock`"), "relock")


def _body(argv: Sequence[str]) -> int:
	config = load()
	try:
		args = parse(argv)
	except _Help:
		say(help_text(config.lock_dir if config.app_mode else None))
		return 0
	return app_mode(config, args) if config.app_mode else bench_mode(config, args)


def main(argv: Sequence[str] | None = None) -> int:
	return proc.main_guard(lambda: _body(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
	sys.exit(main())
