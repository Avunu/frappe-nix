"""``frappe-nix repo rollout``: arguments and the dry-run plan (spec §5.8)."""

import unittest
from unittest import mock

from frappe_nix_tools.commands.repo import rollout, targets
from helpers import run_cli
from repo_helpers import RepoCase, write_app

R = "example/demo_app"


class TestRepoRollout(RepoCase):
	def setUp(self) -> None:
		super().setUp()
		self.clones[(R, None)] = write_app(self.tmp / "app")
		for name in ("clone_branch",):
			p = mock.patch.object(targets, name, return_value="develop")
			p.start()
			self.addCleanup(p.stop)
		p = mock.patch.object(rollout, "sh", side_effect=AssertionError("dry-run ran a command"))
		p.start()
		self.addCleanup(p.stop)

	def test_needs_a_target_release(self):
		code, _, err = run_cli("repo", "rollout", "--repo", R)
		self.assertEqual(code, 2)
		self.assertIn("--to", err)

	def test_to_must_be_a_release_tag(self):
		code, _, _ = run_cli("repo", "rollout", "--repo", R, "--to", "main")
		self.assertEqual(code, 2)

	def test_all_needs_fleet(self):
		code, _, _ = run_cli("repo", "rollout", "--all", "--to", "v1.2.0")
		self.assertEqual(code, 2)

	def test_dry_run_prints_the_plan(self):
		code, out, err = run_cli("repo", "rollout", "--repo", R, "--to", "v1.2.0", "--dry-run")
		self.assertEqual(code, 0, err)
		self.assertIn("nix flake update frappe-nix", out)
		self.assertIn("nix run --no-pure-eval .#frappe-init -- --sync", out)
		self.assertIn("chore(deps): frappe-nix v1.2.0", out)
		self.assertIn("standards/frappe-nix-v1.2.0", out)
		self.assertIn("gh pr merge --auto --squash", out)
		self.assertEqual(self.gh.calls, [])
		code, out, _ = run_cli(
			"repo", "rollout", "--repo", R, "--to", "v1.2.0", "--dry-run", "--no-auto-merge"
		)
		self.assertNotIn("gh pr merge", out)

	def test_profile_to_skips_a_builtin_profile(self):
		code, out, err = run_cli("repo", "rollout", "--repo", R, "--profile-to", "latest", "--dry-run")
		self.assertEqual(code, 0, err)
		self.assertIn("skipped", out)


if __name__ == "__main__":
	unittest.main()
