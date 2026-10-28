"""frappe-runtime locked at the frappe-nix revision flake.lock pins (deps/runtime_pin.py)."""

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from frappe_nix_tools.deps import runtime_pin

REV = "fe7724052a3be7995971325add0fa130dcad2efc"
URL = "https://github.com/Avunu/frappe-nix"
PYPROJECT = f"""\
[project]
name = "x-root"
version = "0.1.0"
dependencies = ["frappe-runtime"]

[tool.uv.sources]
# A placeholder for uv's resolver.
frappe-runtime = {{ git = "{URL}", subdirectory = "runtime" }}
"""
PINNED_LOCK = f"""\
[[package]]
name = "frappe-runtime"
version = "0.1.0"
source = {{ git = "{URL}?subdirectory=runtime&rev={REV}#{REV}" }}

[package.metadata]
requires-dist = [{{ name = "frappe-runtime", git = "{URL}?subdirectory=runtime&rev={REV}" }}]
"""
UNPINNED_LOCK = f"""\
[[package]]
name = "frappe-runtime"
version = "0.1.0"
source = {{ git = "{URL}?subdirectory=runtime#{REV}" }}

[package.metadata]
requires-dist = [{{ name = "frappe-runtime", git = "{URL}?subdirectory=runtime" }}]
"""


def flake_lock(locked: dict) -> str:
	return json.dumps(
		{
			"version": 7,
			"root": "root",
			"nodes": {"root": {"inputs": {"frappe-nix": "frappe-nix"}}, "frappe-nix": {"locked": locked}},
		}
	)


GITHUB = {"type": "github", "owner": "Avunu", "repo": "frappe-nix", "rev": REV}


class RuntimePin(unittest.TestCase):
	def setUp(self):
		tmp = tempfile.TemporaryDirectory()
		self.addCleanup(tmp.cleanup)
		self.root = Path(tmp.name)
		(self.root / "pyproject.toml").write_text(PYPROJECT)

	def lock(self, locked: dict) -> None:
		(self.root / "flake.lock").write_text(flake_lock(locked))

	def test_pins_for_the_lock_and_restores(self):
		self.lock(GITHUB)
		with runtime_pin.runtime_at_pin(self.root) as rev:
			self.assertEqual(rev, REV)
			source = tomllib.loads((self.root / "pyproject.toml").read_text())["tool"]["uv"]["sources"]
			self.assertEqual(source["frappe-runtime"]["rev"], REV)
			(self.root / "uv.lock").write_text(PINNED_LOCK)
		self.assertEqual((self.root / "pyproject.toml").read_text(), PYPROJECT)
		self.assertEqual((self.root / "uv.lock").read_text(), UNPINNED_LOCK)

	def test_restores_after_a_failed_lock(self):
		self.lock(GITHUB)
		with self.assertRaises(RuntimeError), runtime_pin.runtime_at_pin(self.root):
			raise RuntimeError("uv lock failed")
		self.assertEqual((self.root / "pyproject.toml").read_text(), PYPROJECT)

	def test_flake_lock_from_another_directory(self):
		work = self.root / "work"
		work.mkdir()
		(work / "pyproject.toml").write_text(PYPROJECT)
		self.lock(GITHUB)
		with runtime_pin.runtime_at_pin(work, self.root) as rev:
			self.assertEqual(rev, REV)

	def test_nothing_to_pin(self):
		cases = {
			"no flake.lock": None,
			"a path input": {"type": "path", "path": "/src/frappe-nix"},
			"a fork": {**GITHUB, "owner": "someone"},
			"another repository": {**GITHUB, "repo": "frappe-runtime"},
		}
		for label, locked in cases.items():
			with self.subTest(label):
				(self.root / "flake.lock").unlink(missing_ok=True)
				if locked is not None:
					self.lock(locked)
				with runtime_pin.runtime_at_pin(self.root) as rev:
					self.assertIsNone(rev)
					self.assertEqual((self.root / "pyproject.toml").read_text(), PYPROJECT)

	def test_a_source_with_its_own_revision_is_left_alone(self):
		self.lock(GITHUB)
		for extra in (
			'rev = "0123456789012345678901234567890123456789"',
			'tag = "v1.5.0"',
			'branch = "main"',
		):
			with self.subTest(extra):
				own = PYPROJECT.replace('subdirectory = "runtime" }', f'subdirectory = "runtime", {extra} }}')
				(self.root / "pyproject.toml").write_text(own)
				with runtime_pin.runtime_at_pin(self.root) as rev:
					self.assertIsNone(rev)
				self.assertEqual((self.root / "pyproject.toml").read_text(), own)

	def test_a_source_it_cannot_edit_is_left_alone(self):
		self.lock(GITHUB)
		table = PYPROJECT.replace(
			f'frappe-runtime = {{ git = "{URL}", subdirectory = "runtime" }}',
			f'[tool.uv.sources.frappe-runtime]\ngit = "{URL}"\nsubdirectory = "runtime"',
		).replace("[tool.uv.sources]\n# A placeholder for uv's resolver.\n", "")
		(self.root / "pyproject.toml").write_text(table)
		with runtime_pin.runtime_at_pin(self.root) as rev:
			self.assertIsNone(rev)
		self.assertEqual((self.root / "pyproject.toml").read_text(), table)

	def test_unpin_lock_without_a_subdirectory(self):
		lock = self.root / "uv.lock"
		lock.write_text(f'source = {{ git = "{URL}?rev={REV}#{REV}" }}\n')
		runtime_pin.unpin_lock(lock, URL, REV)
		self.assertEqual(lock.read_text(), f'source = {{ git = "{URL}#{REV}" }}\n')


if __name__ == "__main__":
	unittest.main()
