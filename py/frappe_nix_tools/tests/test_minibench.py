"""``frappe-nix minibench`` (spec §5.9, S19, §7 N4 "minibench: the layout guard")."""

import json
import tempfile
import unittest
from pathlib import Path

from scaffold_helpers import AppCase, git


class TestGuard(unittest.TestCase):
	def test_missing_layout_fails(self) -> None:
		from helpers import run_cli

		with tempfile.TemporaryDirectory() as d:
			code, _out, err = run_cli("minibench", "--check-only", "--dest", d)
			self.assertEqual(code, 1)
			for part in ("sites", "env", "modules.txt"):
				self.assertIn(part, err)
			(Path(d) / "sites").mkdir()
			(Path(d) / "env").mkdir()
			(Path(d) / "apps/frappe/frappe").mkdir(parents=True)
			self.assertEqual(run_cli("minibench", "--check-only", "--dest", d)[0], 1)
			(Path(d) / "apps/frappe/frappe/modules.txt").write_text("Core\n")
			self.assertEqual(run_cli("minibench", "--check-only", "--dest", d)[0], 0)


class TestBuild(AppCase):
	def setUp(self) -> None:
		super().setUp()
		self._up = tempfile.TemporaryDirectory()
		self.addCleanup(self._up.cleanup)
		up = Path(self._up.name) / "frappe"
		files = {
			"pyproject.toml": "[project]\nname = 'frappe'\n",
			"frappe/modules.txt": "Core\n",
			"frappe/hooks.py": "app_name = 'frappe'\n",
			"frappe/core/doctype/user/user.json": "{}\n",
			"frappe/core/doctype/user/user.py": "# not in the sparse set\n",
			"frappe/big.py": "# not in the sparse set\n",
		}
		for rel, text in files.items():
			(up / rel).parent.mkdir(parents=True, exist_ok=True)
			(up / rel).write_text(text)
		git(up, "init", "-q")
		git(up, "add", "-A")
		git(up, "commit", "-qm", "frappe")
		rev = git(up, "rev-parse", "HEAD").strip()
		lock = {
			"nodes": {
				"frappe": {
					"locked": {"type": "git", "url": f"file://{up}", "rev": rev, "narHash": "sha256-x"}
				},
				"root": {"inputs": {"frappe": "frappe"}},
			},
			"root": "root",
			"version": 7,
		}
		self.write("flake.lock", json.dumps(lock))
		self.write("demo_app/demo_app/doctype/note/note.json", "{}\n")
		self.write("demo_app/demo_app/doctype/note/note.py", "# controller\n")
		self.write("demo_app/demo_app/custom/user.json", "{}\n")
		self.commit()
		self.dest = Path(self._up.name) / "bench"

	def test_doctypes_mode(self) -> None:
		code, out, err = self.fn("minibench", "--mode", "doctypes", "--dest", str(self.dest))
		self.assertEqual(code, 0, out + err)
		apps = self.dest / "apps"
		self.assertTrue((apps / "frappe/frappe/core/doctype/user/user.json").is_file())
		self.assertFalse((apps / "frappe/frappe/big.py").exists())
		self.assertFalse((apps / "frappe/frappe/core/doctype/user/user.py").exists())
		self.assertTrue((apps / "demo_app/demo_app/demo_app/doctype/note/note.json").is_file())
		self.assertTrue((apps / "demo_app/demo_app/demo_app/custom/user.json").is_file())
		self.assertFalse((apps / "demo_app/demo_app/demo_app/doctype/note/note.py").exists())
		self.assertEqual((self.dest / "sites/apps.txt").read_text().split(), ["frappe", "demo_app"])
		self.assertTrue((self.dest / "env").is_dir())

	def test_customizations_mode_is_a_worktree(self) -> None:
		code, out, err = self.fn("minibench", "--mode", "customizations", "--dest", str(self.dest))
		self.assertEqual(code, 0, out + err)
		app = self.dest / "apps/demo_app"
		self.assertTrue((app / ".git").is_file())
		self.assertTrue((app / "demo_app/demo_app/doctype/note/note.py").is_file())
		# A second run replaces the worktree.
		code, out, err = self.fn("minibench", "--mode", "customizations", "--dest", str(self.dest))
		self.assertEqual(code, 0, out + err)


if __name__ == "__main__":
	unittest.main()
