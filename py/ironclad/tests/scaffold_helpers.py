"""A throwaway Frappe app in a git repository, and ``ironclad`` run against it offline."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from helpers import run_cli

PYPROJECT = """[project]
name = "demo_app"
authors = [{ name = "Avunu LLC", email = "mail@avu.nu" }]
description = "A demo app"
requires-python = ">=3.14"
dynamic = ["version"]
dependencies = []

[build-system]
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[tool.coverage.report]
fail_under = 50

[tool.ironclad]
schema = 1
frappe-major = 16
"""

HOOKS = """app_name = "demo_app"
app_title = "Demo App"
app_description = "A demo app for the sync tests"
required_apps = {required}
"""

INIT = """# x-release-please-start-version
__version__ = "16.0.0"
# x-release-please-end
"""


def git(root: Path, *args: str) -> str:
	return subprocess.run(
		["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(root), *args],
		check=True,
		capture_output=True,
		text=True,
	).stdout


REV = "a" * 40


def _github(owner: str, repo: str, ref: str, rev: str = REV) -> dict:
	return {
		"locked": {"type": "github", "owner": owner, "repo": repo, "rev": rev, "narHash": "sha256-x"},
		"original": {"type": "github", "owner": owner, "repo": repo, "ref": ref},
	}


def flake_lock(apps: list[str]) -> dict:
	"""A lock with frappe-nix on release-1 and each app on version-16."""
	nodes: dict = {"frappe-nix": _github("Avunu", "frappe-nix", "release-1")}
	for app in apps:
		nodes[app] = _github("frappe", app, "version-16", "b" * 40)
	inputs = {name: name for name in nodes}
	inputs["nixpkgs"] = ["frappe-nix", "nixpkgs"]
	nodes["root"] = {"inputs": inputs}
	return {"nodes": nodes, "root": "root", "version": 7}


class AppCase(unittest.TestCase):
	"""Each test gets ``self.root``: an app with one commit, and a fake tools/uv.lock and yarn.lock
	so an offline sync leaves a tree ``--check`` passes."""

	required: ClassVar[list[str]] = []
	extra_pyproject = ""

	def setUp(self) -> None:
		self._tmp = tempfile.TemporaryDirectory()
		self.root = Path(self._tmp.name)
		env = mock.patch.dict(
			os.environ,
			{"IRONCLAD_OFFLINE": "1", "GIT_CONFIG_GLOBAL": os.devnull},
			clear=False,
		)
		env.start()
		self.addCleanup(env.stop)
		for key in ("IRONCLAD_ALLOW_SKEW", "IRONCLAD_FRAPPE_NIX_URL", "GITHUB_HEAD_REF"):
			os.environ.pop(key, None)
		git(self.root, "init", "-q", "-b", "develop")
		self.write("pyproject.toml", PYPROJECT + self.extra_pyproject)
		self.write("demo_app/__init__.py", INIT)
		self.write("demo_app/hooks.py", HOOKS.format(required=json.dumps(self.required)))
		self.write("demo_app/modules.txt", "Demo App\n")
		self.commit()

	def tearDown(self) -> None:
		self._tmp.cleanup()

	def write(self, rel: str, text: str) -> Path:
		path = self.root / rel
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(text)
		return path

	def read(self, rel: str) -> str:
		return (self.root / rel).read_text()

	def commit(self) -> None:
		git(self.root, "add", "-A")
		git(self.root, "commit", "-q", "--allow-empty", "-m", "test")

	def ironclad(self, *argv: str) -> tuple[int, str, str]:
		return run_cli(*argv, cwd=self.root)

	def fake_locks(self, versions: dict[str, str] | None = None) -> None:
		"""A tools/uv.lock at the floors (or ``versions``) and a yarn.lock, as uv and yarn would leave them."""
		from ironclad.scaffold import manifest

		floors = dict(manifest.load().floors["uv"])
		floors.update(versions or {})
		body = 'version = 1\nrevision = 3\nrequires-python = ">=3.14"\n'
		for name, version in sorted(floors.items()):
			body += f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
		self.write("tools/uv.lock", body)
		self.write("yarn.lock", "# yarn lockfile v1\n")
		self.write(
			"flake.lock", json.dumps(flake_lock(["frappe", *[r.rsplit("/", 1)[-1] for r in self.required]]))
		)
		git(self.root, "add", "-A")

	def synced(self) -> None:
		"""Sync, add the locks offline sync can't make, and commit: a clean starting point."""
		code, _, err = self.ironclad("sync", "--write")
		self.assertEqual(code, 0, err)
		self.fake_locks()
		self.commit()
		code, out, err = self.ironclad("sync", "--check")
		self.assertEqual(code, 0, out + err)

	def check(self) -> tuple[int, str]:
		code, out, err = self.ironclad("sync", "--check")
		return code, out + err

	def snapshot(self) -> dict[str, bytes]:
		files = git(self.root, "ls-files", "-co", "--exclude-standard").split()
		return {f: (self.root / f).read_bytes() for f in files if (self.root / f).is_file()}
