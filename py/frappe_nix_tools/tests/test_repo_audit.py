"""``frappe-nix repo audit`` against a mocked GitHub (spec §5.7, §7 N4 "release guard under semver", audit rows)."""

import json
import unittest
from unittest import mock

from frappe_nix_tools.commands.repo import audit
from helpers import run_cli
from repo_helpers import RepoCase, write_app

R = "example/demo_app"
SHA = "c" * 40


class TestRepoAudit(RepoCase):
	def _tool(self, root, argv, spec):
		self.tool_calls.append(argv)
		if argv[:2] == ["sync", "--check"]:
			return 0, json.dumps({"status": "clean", "files": []})
		return 0, ""

	def setUp(self) -> None:
		super().setUp()
		patches = [
			mock.patch.object(audit, "run_tool", side_effect=self._tool),
			mock.patch.object(audit, "download_report", return_value=None),
			mock.patch.object(audit, "fetch_url", return_value=None),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)
		self.tool_calls: list[list[str]] = []
		self.gh.routes.update(
			{
				("GET", f"repos/{R}"): {"default_branch": "develop", "private": False},
				("GET", f"repos/{R}/tags"): [
					{"name": "v1.2.0", "commit": {"sha": "d" * 40}},
					{"name": "v1.3.0", "commit": {"sha": SHA}},
					{"name": "assets-version-16", "commit": {"sha": "e" * 40}},
				],
				("GET", f"repos/{R}/git/ref/heads/version-16"): {"object": {"sha": SHA}},
				("GET", f"repos/{R}/releases/tags/v1.3.0"): {"author": {"login": "github-actions[bot]"}},
			}
		)

	def rows(self, extra: str = "") -> tuple[int, dict]:
		self.clones[(R, None)] = write_app(self.tmp / f"app{len(self.clones)}", extra)
		code, out, err = run_cli("repo", "audit", "--repo", R, "--format", "json")
		self.assertIn(code, (0, 1), err)
		doc = json.loads(out)
		return code, {row["id"]: row for row in doc["repos"][0]["rows"]}

	def test_seventeen_rows_and_n_a_for_modules_that_are_off(self):
		_, rows = self.rows()
		self.assertEqual(list(rows), [f"A{i}" for i in range(1, 18)])
		for rid in ("A1", "A3", "A12", "A13", "A14", "A15", "A16", "A17"):
			self.assertEqual(rows[rid]["status"], "n/a", rid)
		self.assertEqual(rows["A4"]["status"], "green")

	def test_semver_release_rows_are_green_with_v1_tags(self):
		_, rows = self.rows()
		self.assertEqual(rows["A2"]["status"], "green", rows["A2"])
		self.assertEqual(rows["A11"]["status"], "green", rows["A11"])
		self.gh.routes[("GET", f"repos/{R}/git/ref/heads/version-16")] = {"object": {"sha": "f" * 40}}
		_, rows = self.rows()
		self.assertEqual(rows["A2"]["status"], "red")

	def test_frappe_major_ignores_v1_tags(self):
		_, rows = self.rows('\n[tool.frappe-nix.releases]\nversion-scheme = "frappe-major"\n')
		self.assertEqual(rows["A11"]["status"], "red")
		self.assertEqual(rows["A2"]["status"], "amber")

	def test_unreadable_data_is_unknown(self):
		_, rows = self.rows()
		self.assertEqual(rows["A6"]["status"], "unknown")
		self.assertEqual(rows["A8"]["status"], "unknown")

	def test_a5_reads_the_gate_jobs(self):
		run = {"id": 9, "conclusion": "success"}
		self.gh.routes[
			("GET", f"repos/{R}/actions/workflows/ci.yml/runs?status=completed&per_page=1&branch=develop")
		] = {"workflow_runs": [run]}
		self.gh.routes[
			("GET", f"repos/{R}/actions/workflows/pr-policy.yml/runs?status=completed&per_page=1")
		] = {"workflow_runs": [run]}
		self.gh.routes[
			("GET", f"repos/{R}/actions/workflows/nightly.yml/runs?status=completed&per_page=1")
		] = {"workflow_runs": [run]}
		self.gh.routes[("GET", f"repos/{R}/actions/runs/9/jobs?per_page=100")] = {
			"jobs": [
				{"name": "ci / lint", "conclusion": "success"},
				{"name": "ci / test", "conclusion": "success"},
			]
		}
		_, rows = self.rows()
		self.assertEqual(rows["A5"]["status"], "green", rows["A5"])
		self.gh.routes[("GET", f"repos/{R}/actions/runs/9/jobs?per_page=100")] = {
			"jobs": [{"name": "ci / lint", "conclusion": "failure"}]
		}
		_, rows = self.rows()
		self.assertEqual(rows["A5"]["status"], "red")

	def test_issue_is_opened_then_updated(self):
		self.clones[(R, None)] = write_app(self.tmp / "app")
		self.gh.routes[("GET", "repos/example/profile/issues?state=open&per_page=100")] = []
		code, _, err = run_cli("repo", "audit", "--repo", R, "--issue", "--issue-repo", "example/profile")
		self.assertIn(code, (0, 1), err)
		posts = [w for w in self.gh.writes() if w[1] == "repos/example/profile/issues"]
		self.assertEqual(posts[0][2]["title"], f"scorecard: {R}")
		self.gh.routes[("GET", "repos/example/profile/issues?state=open&per_page=100")] = [
			{"number": 3, "title": f"scorecard: {R}"}
		]
		run_cli("repo", "audit", "--repo", R, "--issue", "--issue-repo", "example/profile")
		self.assertIn(("PATCH", "repos/example/profile/issues/3"), [w[:2] for w in self.gh.writes()])

	def test_issue_needs_issue_repo(self):
		code, _, _ = run_cli("repo", "audit", "--repo", R, "--issue")
		self.assertEqual(code, 2)


if __name__ == "__main__":
	unittest.main()
