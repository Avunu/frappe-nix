"""A throwaway Frappe app in a git repository, and ``frappe-nix`` run against it offline."""

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
authors = [{ name = "Example Org", email = "apps@example.org" }]
description = "A demo app"
requires-python = ">=3.14"
dynamic = ["version"]
dependencies = []

[build-system]
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[tool.coverage.report]
fail_under = 50

[tool.frappe-nix]
schema = 1
profile = "{profile}"
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

# A fictitious org profile like tests/fixtures/profiles/example-org (spec §7): every module the
# managed files need on, Example Org's values, a [[retire]] rule and an [[extra-files]] entry.
EXAMPLE_ORG = """schema = 1
name = "example-org"
description = "Example Org's app standards (a test fixture)"
extends = "recommended@1.0"

[org]
publisher = "Example Org"
email = "apps@example.org"
license = "MIT"
website-url = "https://example.org/apps/{app}/"
docs-url = "https://{app_hyphen}.docs.example.org/"

[org.brand]
tile-color = "#336699"

[ssort]
enable = true

[test-utils]
enable = true

[[retire]]
paths = [".github/workflows/check.yml"]
module = "ci"

[[extra-files]]
path = "SECURITY.md"
template = "SECURITY.md.j2"
strategy = "whole"
module = "hygiene"
header = "none"
"""


def write_profile(root: Path, rel: str = ".standards-profile", text: str = EXAMPLE_ORG) -> Path:
	"""An org profile directory under ``root``: ``profile.toml`` and its ``templates/``."""
	directory = root / rel
	(directory / "templates").mkdir(parents=True, exist_ok=True)
	(directory / "profile.toml").write_text(text)
	(directory / "templates" / "SECURITY.md.j2").write_text(
		"# Security\n\nReport vulnerabilities in {{ app }} to {{ org.email }}.\n"
	)
	return directory


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


def flake_lock(apps: list[str], refs: dict[str, str] | None = None) -> dict:
	"""A lock with frappe-nix on release-1 and each app on version-16 (or ``refs[app]``)."""
	nodes: dict = {"frappe-nix": _github("Avunu", "frappe-nix", "release-1")}
	for app in apps:
		owner, _, name = app.rpartition("/")
		nodes[name] = _github(owner or "frappe", name, (refs or {}).get(name, "version-16"), "b" * 40)
	inputs = {name: name for name in nodes}
	inputs["nixpkgs"] = ["frappe-nix", "nixpkgs"]
	nodes["root"] = {"inputs": inputs}
	return {"nodes": nodes, "root": "root", "version": 7}


class AppCase(unittest.TestCase):
	"""Each test gets ``self.root``: an app with one commit, and a fake tools/uv.lock and yarn.lock
	so an offline sync leaves a tree ``--check`` passes. ``profile`` is the table's profile."""

	required: ClassVar[list[str]] = []
	extra_pyproject = ""
	profile = "recommended"

	def setUp(self) -> None:
		self._tmp = tempfile.TemporaryDirectory()
		self.root = Path(self._tmp.name)
		env = mock.patch.dict(
			os.environ,
			{"FRAPPE_NIX_OFFLINE": "1", "GIT_CONFIG_GLOBAL": os.devnull},
			clear=False,
		)
		env.start()
		self.addCleanup(env.stop)
		for key in ("FRAPPE_NIX_ALLOW_SKEW", "FRAPPE_NIX_URL_OVERRIDE", "GITHUB_HEAD_REF", "CI"):
			os.environ.pop(key, None)
		git(self.root, "init", "-q", "-b", "develop")
		self.write("pyproject.toml", PYPROJECT.replace("{profile}", self.profile) + self.extra_pyproject)
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

	def table(self, extra: str) -> None:
		"""Add lines to ``[tool.frappe-nix]`` (right after its header)."""
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace("[tool.frappe-nix]\n", f"[tool.frappe-nix]\n{extra}", 1),
		)

	def fn(self, *argv: str) -> tuple[int, str, str]:
		return run_cli(*argv, cwd=self.root)

	def fake_locks(self, versions: dict[str, str] | None = None, refs: dict[str, str] | None = None) -> None:
		"""A tools/uv.lock at the floors (or ``versions``), a yarn.lock and a flake.lock, as uv,
		yarn and nix would leave them."""
		from frappe_nix_tools.scaffold import manifest

		floors = dict(manifest.load().floors["uv"])
		floors.update(versions or {})
		body = 'version = 1\nrevision = 3\nrequires-python = ">=3.14"\n'
		for name, version in sorted(floors.items()):
			body += f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
		if (self.root / "tools/pyproject.toml").exists():
			self.write("tools/uv.lock", body)
		self.fake_yarn_lock()
		self.write("flake.lock", json.dumps(flake_lock(["frappe", *self.required], refs)))
		git(self.root, "add", "-A")

	def fake_yarn_lock(self) -> None:
		"""A yarn.lock (v1) with an entry for each dependency package.json declares."""
		try:
			pkg = json.loads(self.read("package.json"))
		except FileNotFoundError:
			return
		body = "# yarn lockfile v1\n"
		for field in ("dependencies", "devDependencies", "optionalDependencies"):
			for name, spec in sorted((pkg.get(field) or {}).items()):
				body += f'\n"{name}@{spec}":\n  version "0.0.0"\n'
		self.write("yarn.lock", body)
		git(self.root, "add", "-A")

	def synced(self) -> None:
		"""Sync, add the locks offline sync can't make, and commit: a clean starting point."""
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		self.fake_locks()
		self.commit()
		code, out, err = self.fn("sync", "--check")
		self.assertEqual(code, 0, out + err)

	def check(self) -> tuple[int, str]:
		code, out, err = self.fn("sync", "--check")
		return code, out + err

	def snapshot(self) -> dict[str, bytes]:
		files = git(self.root, "ls-files", "-co", "--exclude-standard").split()
		return {f: (self.root / f).read_bytes() for f in files if (self.root / f).is_file()}
