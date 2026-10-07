import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.common import data_path
from helpers import TREE_NAR_HASH, make_tree, run_cli

REV = "c" * 40


class TestDataPathCommand(unittest.TestCase):
	def test_prints_existing_files(self):
		for rel in ("known-apps.json", "schema/tool-frappe-nix.schema.json", "profiles/minimal.toml"):
			code, out, _ = run_cli("data-path", rel)
			self.assertEqual(code, 0)
			self.assertEqual(Path(out.strip()), data_path(rel))
			self.assertTrue(Path(out.strip()).is_file())

	def test_missing(self):
		code, out, err = run_cli("data-path", "nope.json")
		self.assertEqual((code, out), (2, ""))
		self.assertIn("no such data file", err)


class TestPinPathCommand(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		root = Path(self.tmp.name)
		src = make_tree(root / "src" / f"semgrep-rules-{REV}")
		with tarfile.open(root / "semgrep-rules.tar.gz", "w:gz") as tar:
			tar.add(src, arcname=src.name)
		self.app = root / "app"
		(self.app / "sub").mkdir(parents=True)
		subprocess.run(["git", "-C", str(self.app), "init", "-q"], check=True)
		lock = {
			"version": 7,
			"root": "root",
			"nodes": {
				"root": {"inputs": {"frappe-semgrep-rules": "rules"}},
				"rules": {
					"locked": {
						"type": "github",
						"owner": "frappe",
						"repo": "semgrep-rules",
						"rev": REV,
						"narHash": TREE_NAR_HASH,
					}
				},
			},
		}
		(self.app / "flake.lock").write_text(json.dumps(lock))
		self.patch = mock.patch.dict(os.environ, {"FRAPPE_NIX_PIN_URL": f"file://{root}/{{repo}}.tar.gz"})
		self.patch.start()

	def tearDown(self):
		self.patch.stop()
		self.tmp.cleanup()

	def test_from_a_subdirectory_reads_the_git_roots_lock(self):
		code, out, err = run_cli("pin-path", "frappe-semgrep-rules", cwd=self.app / "sub")
		self.assertEqual(code, 0, err)
		self.assertEqual(
			Path(out.strip()), (self.app / ".dev-dist" / "pins" / f"semgrep-rules-{REV}").resolve()
		)

	def test_an_app_in_a_subdirectory_reads_its_own_lock(self):
		# frappe-nix's layout: the app (tests/fixtures/standards-app) inside a larger
		# repository whose root has a flake.lock of its own without the input.
		outer = Path(self.tmp.name) / "outer"
		app = outer / "tests" / "fixtures" / "app"
		(app / "pkg").mkdir(parents=True)
		subprocess.run(["git", "-C", str(outer), "init", "-q"], check=True)
		(outer / "flake.lock").write_text(json.dumps({"version": 7, "root": "root", "nodes": {"root": {}}}))
		(app / "flake.lock").write_text((self.app / "flake.lock").read_text())
		pin = (app / ".dev-dist" / "pins" / f"semgrep-rules-{REV}").resolve()
		for cwd in (app, app / "pkg"):
			code, out, err = run_cli("pin-path", "frappe-semgrep-rules", cwd=cwd)
			self.assertEqual(code, 0, err)
			self.assertEqual(Path(out.strip()), pin)
		self.assertFalse((outer / ".dev-dist").exists())
		# Above the app, the repository's own lock answers.
		code, _, err = run_cli("pin-path", "frappe-semgrep-rules", cwd=outer / "tests")
		self.assertEqual(code, 2, err)

	def test_unknown_input(self):
		code, _, err = run_cli("pin-path", "pilot", "--lock", str(self.app / "flake.lock"))
		self.assertEqual(code, 2)
		self.assertIn("no input 'pilot'", err)

	def test_no_lock(self):
		code, _, _ = run_cli("pin-path", "pilot", "--lock", str(self.app / "missing.lock"))
		self.assertEqual(code, 3)


if __name__ == "__main__":
	unittest.main()
