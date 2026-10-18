"""relock-plan and relock-push's helpers (spec §4.5, S29, §7 N4 "relock patch" and "relock-plan")."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.ci import relock
from frappe_nix_tools.common.repo import NO_AUTO_MAINTENANCE
from helpers import run_cli


def git(root: Path, *args: str) -> str:
	return subprocess.run(
		["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *NO_AUTO_MAINTENANCE, *args],
		check=True,
		capture_output=True,
		text=True,
	).stdout


def lock(**revs: str) -> str:
	nodes = {"root": {"inputs": {name: name for name in revs}}}
	for name, rev in revs.items():
		nodes[name] = {"locked": {"type": "github", "owner": "example", "repo": name, "rev": rev}}
	return json.dumps({"root": "root", "version": 7, "nodes": nodes}, indent=2) + "\n"


class Repo(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		self.root = Path(self.tmp.name) / "app"
		self.root.mkdir()
		patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null", "HOME": self.tmp.name})
		patch.start()
		self.addCleanup(patch.stop)
		git(self.root, "init", "-q", "-b", "develop")
		(self.root / "flake.lock").write_text(lock(**{"frappe": "1" * 40, "frappe-nix": "2" * 40}))
		(self.root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
		(self.root / ".github/workflows").mkdir(parents=True)
		(self.root / ".github/workflows/ci.yml").write_text("name: ci\n")
		git(self.root, "add", "-A")
		git(self.root, "commit", "-qm", "base")
		self.base = git(self.root, "rev-parse", "HEAD").strip()


class TestRelockInputs(Repo):
	def bump(self, **revs):
		(self.root / "flake.lock").write_text(lock(**revs))
		git(self.root, "commit", "-qam", "bump")

	def inputs(self):
		return run_cli("ci", "relock-inputs", "--base", self.base, cwd=self.root)

	def test_frappe_bump_is_ok(self):
		self.bump(**{"frappe": "3" * 40, "frappe-nix": "2" * 40})
		code, out, _ = self.inputs()
		self.assertEqual((code, out.splitlines()[0]), (0, "ok"))

	def test_frappe_nix_moved(self):
		self.bump(**{"frappe": "1" * 40, "frappe-nix": "4" * 40})
		_, out, _ = self.inputs()
		self.assertEqual(out.splitlines()[0], "rollout-input-moved")

	def test_standards_profile_moved(self):
		self.assertEqual(
			relock.moved_inputs(
				lock(**{"frappe-nix": "2" * 40, "standards-profile": "5" * 40}),
				lock(**{"frappe-nix": "2" * 40, "standards-profile": "6" * 40}),
			),
			["standards-profile"],
		)
		out = Path(self.tmp.name) / "out"
		(self.root / "flake.lock").write_text(lock(**{"frappe-nix": "2" * 40, "standards-profile": "6" * 40}))
		git(self.root, "commit", "-qam", "add profile")
		with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}):
			_, stdout, _ = self.inputs()
		self.assertEqual(stdout.splitlines()[0], "rollout-input-moved")
		self.assertIn("state=rollout-input-moved\n", out.read_text())
		self.assertIn("moved=standards-profile\n", out.read_text())


class TestRelockPatch(Repo):
	def test_patch_reproduces_with_apply_index(self):
		# sync stages a change and writes a new, untracked node-lock seed
		(self.root / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '1'\n")
		git(self.root, "add", "pyproject.toml")
		seed = self.root / "nix/node-locks/x/yarn.lock"
		seed.parent.mkdir(parents=True)
		seed.write_bytes(b"# yarn lockfile v1\n\x00binary\n")
		(self.root / "flake.lock").write_text(lock(**{"frappe": "9" * 40, "frappe-nix": "2" * 40}))
		patch = Path(self.tmp.name) / "relock.patch"
		code, out, err = run_cli("ci", "relock-patch", "--out", str(patch), cwd=self.root)
		self.assertEqual((code, out.strip()), (0, "empty=false"), err)
		want = {
			p: (self.root / p).read_bytes()
			for p in ("pyproject.toml", "flake.lock", "nix/node-locks/x/yarn.lock")
		}

		clone = Path(self.tmp.name) / "clone"
		subprocess.run(["git", "clone", "-q", str(self.root), str(clone)], check=True)
		git(clone, "checkout", "-q", self.base)
		git(clone, "apply", "--index", str(patch))
		for p, data in want.items():
			self.assertEqual((clone / p).read_bytes(), data, p)
		self.assertEqual(git(clone, "diff", "--cached", "--name-only").split(), sorted(want))

	def test_empty(self):
		patch = Path(self.tmp.name) / "relock.patch"
		code, out, _ = run_cli("ci", "relock-patch", "--out", str(patch), cwd=self.root)
		self.assertEqual((code, out.strip()), (0, "empty=true"))

	def test_workflow_patch_refused(self):
		(self.root / ".github/workflows/ci.yml").write_text("name: changed\n")
		patch = Path(self.tmp.name) / "relock.patch"
		code, out, _ = run_cli("ci", "relock-patch", "--out", str(patch), cwd=self.root)
		self.assertEqual(code, 1)
		self.assertIn("::error::relock would change a workflow", out)
		self.assertIn("run frappe-nix repo rollout", out)


class TestRefuseWorkflows(Repo):
	def test_range_and_index(self):
		(self.root / "README.md").write_text("x\n")
		git(self.root, "add", "-A")
		git(self.root, "commit", "-qm", "docs")
		self.assertEqual(run_cli("ci", "refuse-workflows", "--base", self.base, cwd=self.root)[0], 0)
		(self.root / ".github/workflows/new.yml").write_text("name: new\n")
		git(self.root, "add", "-A")
		code, out, _ = run_cli("ci", "refuse-workflows", "--cached", cwd=self.root)
		self.assertEqual(code, 1)
		self.assertIn(".github/workflows/new.yml", out)
		git(self.root, "commit", "-qm", "wf")
		self.assertEqual(run_cli("ci", "refuse-workflows", "--base", self.base, cwd=self.root)[0], 1)

	def test_subdirectory_app(self):
		self.assertEqual(relock.workflow_paths(["tests/fixtures/app/.github/workflows/ci.yml", "a.py"]),
			["tests/fixtures/app/.github/workflows/ci.yml"])  # fmt: skip


if __name__ == "__main__":
	unittest.main()
