import subprocess
import tempfile
import unittest
from pathlib import Path

from ironclad.common import repo
from ironclad.common.report import EnvError


def git(root, *args):
	subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class TestRepo(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.root = Path(self.tmp.name).resolve()
		git(self.root, "init", "-q")
		(self.root / "pyproject.toml").write_text('[project]\nname = "demo_app"\n')
		(self.root / "demo_app").mkdir()
		(self.root / "demo_app" / "hooks.py").write_text('app_name = "demo_app"\n')
		(self.root / "untracked.txt").write_text("")
		git(self.root, "add", "pyproject.toml", "demo_app/hooks.py")

	def tearDown(self):
		self.tmp.cleanup()

	def test_toplevel(self):
		self.assertEqual(repo.toplevel(self.root / "demo_app"), self.root)
		with tempfile.TemporaryDirectory() as other, self.assertRaises(EnvError):
			repo.toplevel(Path(other))

	def test_ls_files(self):
		self.assertEqual(repo.ls_files(self.root), ["demo_app/hooks.py", "pyproject.toml"])
		self.assertEqual(repo.ls_files(self.root, "*.py"), ["demo_app/hooks.py"])

	def test_app_name(self):
		self.assertEqual(repo.app_name(self.root), "demo_app")
		(self.root / "demo_app" / "hooks.py").unlink()
		with self.assertRaises(EnvError):
			repo.app_name(self.root)

	def test_github_repo(self):
		self.assertEqual(repo.github_repo(self.root, "demo_app"), "Avunu/demo_app")
		for url in (
			"https://github.com/Avunu/carbon_frappe.git",
			"git@github.com:someone/carbon_frappe.git",
			"https://github.com/Avunu/carbon_frappe",
		):
			with self.subTest(url=url):
				subprocess.run(
					["git", "-C", str(self.root), "remote", "remove", "origin"], capture_output=True
				)
				git(self.root, "remote", "add", "origin", url)
				self.assertEqual(repo.github_repo(self.root, "demo_app"), "Avunu/carbon_frappe")


if __name__ == "__main__":
	unittest.main()
