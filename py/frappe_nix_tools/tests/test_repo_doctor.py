"""``frappe-nix repo doctor`` against mocked API responses (spec §5.6, §7 N4 "doctor")."""

import json
import unittest

from helpers import run_cli
from repo_helpers import RepoCase, write_app

R = "example/demo_app"
GATES = ["ci / pr-policy", "ci / lint", "ci / test"]


class TestRepoDoctor(RepoCase):
	def setUp(self) -> None:
		super().setUp()
		self.root = write_app(self.tmp / "app")
		self.gh.routes.update(
			{
				("GET", f"repos/{R}"): {"allow_auto_merge": True},
				("GET", f"repos/{R}/actions/permissions/workflow"): {
					"default_workflow_permissions": "read",
					"can_approve_pull_request_reviews": True,
				},
				("GET", f"repos/{R}/rules/branches/version-16"): [],
				("GET", f"repos/{R}/rules/branches/develop"): [
					{
						"type": "required_status_checks",
						"ruleset_id": 1,
						"parameters": {"required_status_checks": [{"context": c} for c in GATES]},
					}
				],
				("GET", f"repos/{R}/branches/develop"): {"name": "develop", "protected": False},
				("GET", f"repos/{R}/labels"): [{"name": "dependencies"}, {"name": "needs-review"}],
			}
		)

	def doctor(self) -> tuple[int, dict]:
		code, out, err = run_cli("repo", "doctor", "--root", str(self.root), "--format", "json")
		self.assertIn(code, (0, 1), err)
		return code, {r["setting"]: r["status"] for r in json.loads(out)["rows"]}

	def test_everything_ok(self):
		code, rows = self.doctor()
		self.assertEqual(code, 0)
		self.assertEqual(set(rows.values()), {"ok"})
		self.assertIn("Actions may create pull requests (release-please)", rows)
		self.assertIn("GitHub Actions may fast-forward version-16", rows)

	def test_disabled_auto_merge_is_missing(self):
		self.gh.routes[("GET", f"repos/{R}")] = {"allow_auto_merge": False}
		code, rows = self.doctor()
		self.assertEqual(code, 1)
		self.assertEqual(rows["auto-merge is allowed"], "missing")

	def test_disallowed_pull_request_creation_is_missing(self):
		self.gh.routes[("GET", f"repos/{R}/actions/permissions/workflow")][
			"can_approve_pull_request_reviews"
		] = False
		code, rows = self.doctor()
		self.assertEqual(code, 1)
		self.assertEqual(rows["Actions may create pull requests (release-please)"], "missing")

	def test_settings_needing_admin_are_unknown(self):
		self.gh.routes[("GET", f"repos/{R}/actions/permissions/workflow")] = 403
		self.gh.routes[("GET", f"repos/{R}")] = {}
		code, rows = self.doctor()
		self.assertEqual(code, 0)
		self.assertEqual(rows["Actions may create pull requests (release-please)"], "unknown")
		self.assertEqual(rows["auto-merge is allowed"], "unknown")

	def test_unprotected_integration_branch_is_missing(self):
		self.gh.routes[("GET", f"repos/{R}/rules/branches/develop")] = []
		code, rows = self.doctor()
		self.assertEqual(code, 1)
		self.assertEqual(rows["develop requires ci / lint, ci / pr-policy, ci / test"], "missing")

	def test_release_branch_ruleset_without_bypass_is_missing(self):
		self.gh.routes[("GET", f"repos/{R}/rules/branches/version-16")] = [
			{"type": "update", "ruleset_id": 7}
		]
		self.gh.routes[("GET", f"repos/{R}/rulesets/7")] = {"id": 7, "bypass_actors": []}
		_, rows = self.doctor()
		self.assertEqual(rows["GitHub Actions may fast-forward version-16"], "missing")
		self.gh.routes[("GET", f"repos/{R}/rulesets/7")] = {
			"id": 7,
			"bypass_actors": [{"actor_type": "Integration", "actor_id": 15368, "bypass_mode": "always"}],
		}
		_, rows = self.doctor()
		self.assertEqual(rows["GitHub Actions may fast-forward version-16"], "ok")


if __name__ == "__main__":
	unittest.main()
