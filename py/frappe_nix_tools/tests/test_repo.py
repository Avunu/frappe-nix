import subprocess
import tempfile
import unittest
from pathlib import Path

from frappe_nix_tools.common import repo
from frappe_nix_tools.common.report import EnvError


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

	def test_origin_repo(self):
		self.assertIsNone(repo.origin_repo(self.root))
		git(self.root, "remote", "add", "origin", "https://github.com/example/my_app.git")
		self.assertEqual(repo.origin_repo(self.root), ("github.com", "example/my_app"))

	def test_parse_remote(self):
		for url, expected in (
			("https://github.com/example/my_app.git", ("github.com", "example/my_app")),
			("https://github.com/example/my_app", ("github.com", "example/my_app")),
			("git@github.com:someone/my_app.git", ("github.com", "someone/my_app")),
			(
				"ssh://git@gitlab.example.org:2222/group/sub/my_app.git",
				("gitlab.example.org", "group/sub/my_app"),
			),
			("https://token@GitLab.com/group/my_app/", ("gitlab.com", "group/my_app")),
			("/srv/git/my_app.git", None),
			("https://github.com/my_app", None),
			("https://github.com/a/../b", None),
		):
			with self.subTest(url=url):
				self.assertEqual(repo.parse_remote(url), expected)


if __name__ == "__main__":
	unittest.main()
