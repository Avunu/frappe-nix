"""The app repository: its root, its tracked files, its package and its GitHub name.

Everything discovered is read from ``git ls-files``, so the answer is the same on every
machine (spec §2.2).
"""

import re
import subprocess
import tomllib
from pathlib import Path

from ironclad.common.report import EnvError

_REMOTE = re.compile(r"(?:github\.com[:/])?(?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def git(root: Path, *args: str) -> str:
	"""Run ``git -C root <args>`` and return its stdout; a failure is an ``EnvError``."""
	try:
		proc = subprocess.run(
			["git", "-C", str(root), *args],
			check=True,
			capture_output=True,
			text=True,
		)
	except FileNotFoundError as e:
		raise EnvError("git is not on PATH") from e
	except subprocess.CalledProcessError as e:
		raise EnvError(f"git {' '.join(args)}: {e.stderr.strip() or e}") from e
	return proc.stdout


def toplevel(start: Path | None = None) -> Path:
	"""The root of the git work tree that contains ``start`` (default: the current directory)."""
	return Path(git(start or Path.cwd(), "rev-parse", "--show-toplevel").strip())


def find_up(name: str, start: Path | None = None) -> Path:
	"""The app's ``name`` (``flake.lock``, ``pyproject.toml``): the nearest one at or above ``start``.

	``start`` defaults to the current directory. The search stops at the git work tree's root,
	so an app in a subdirectory of a larger repository (frappe-nix's own fixture app) reads
	its own file, not the repository's. When no directory up to the root has one, the
	answer is the root's (or, outside git, ``start``'s), and reading it reports it missing.
	"""
	here = (start or Path.cwd()).resolve()
	try:
		top = toplevel(here).resolve()
	except EnvError:
		return here / name
	for directory in (here, *here.parents):
		if (directory / name).is_file():
			return directory / name
		if directory == top:
			break
	return top / name


def ls_files(root: Path, *pathspecs: str) -> list[str]:
	"""The tracked paths under ``root``, sorted, optionally narrowed by git pathspecs."""
	out = git(root, "ls-files", "-z", "--", *pathspecs)
	return sorted(p for p in out.split("\0") if p)


def app_name(root: Path) -> str:
	"""The app's package name: ``[project].name``, which must name a package with ``hooks.py``."""
	try:
		doc = tomllib.loads((root / "pyproject.toml").read_text())
	except FileNotFoundError as e:
		raise EnvError(f"not an app: {root}/pyproject.toml does not exist") from e
	except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
		raise EnvError(f"not an app: {root}/pyproject.toml: {e}") from e
	name = doc.get("project", {}).get("name")
	if not isinstance(name, str) or not name:
		raise EnvError(f"not an app: {root}/pyproject.toml has no [project].name")
	if not (root / name / "hooks.py").is_file():
		raise EnvError(f"not an app: {root}/{name}/hooks.py does not exist")
	return name


def github_repo(root: Path, app: str) -> str:
	"""``Avunu/<repo>``: origin's repository name under the Avunu owner, else ``Avunu/<app>``."""
	try:
		url = git(root, "remote", "get-url", "origin").strip()
	except EnvError:
		return f"Avunu/{app}"
	match = _REMOTE.search(url)
	return f"Avunu/{match['repo']}" if match else f"Avunu/{app}"
