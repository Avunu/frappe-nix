"""``frappe-nix repo apply`` against a mocked GitHub (spec §5.6, §7 N4 "apply" and "required checks")."""

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.commands.repo import apply, targets
from helpers import run_cli
from repo_helpers import ONLY_LINT_TEST, REPO_POLICY_ON, RepoCase, write_app

R = "example/demo_app"

PROFILE = """schema = 1
name = "example-org"
extends = "recommended@1.0"

[repo-policy]
enable = true
required-approvals = 0
"""


def matching_routes(policy) -> dict:
	"""What a repository already set up exactly as ``policy`` says answers."""
	repository = copy.deepcopy(policy.repository)
	routes = {
		("GET", f"repos/{R}"): {**repository, "private": False, "full_name": R},
		("GET", f"repos/{R}/branches/develop"): {"name": "develop"},
		("GET", f"repos/{R}/actions/permissions/workflow"): dict(policy.workflow_permissions),
		("GET", f"repos/{R}/vulnerability-alerts"): None,
		("GET", f"repos/{R}/automated-security-fixes"): {"enabled": True, "paused": False},
		("GET", f"repos/{R}/labels"): [
			{**lb, "id": i, "default": False} for i, lb in enumerate(policy.labels)
		],
		("GET", f"repos/{R}/rulesets"): [
			{"id": 100 + i, "name": rs["name"]} for i, rs in enumerate(policy.rulesets)
		],
	}
	for i, rs in enumerate(policy.rulesets):
		served = copy.deepcopy(rs)
		served.update({"id": 100 + i, "source": R, "source_type": "Repository", "node_id": "x"})
		served["rules"] = list(reversed(served["rules"]))
		routes[("GET", f"repos/{R}/rulesets/{100 + i}")] = served
	return routes


class TestRepoApply(RepoCase):
	def app(self, extra: str = REPO_POLICY_ON) -> Path:
		root = write_app(self.tmp / "clone", extra)
		self.clones[(R, None)] = root
		return root

	def policy(self, phase: str = "full"):
		t = targets.load(R, {}, {})
		return apply.plan_policy(t, phase, "")

	def test_dry_run_on_a_matching_repository_writes_nothing(self):
		self.app()
		self.gh.routes.update(matching_routes(self.policy()))
		code, out, err = run_cli("repo", "apply", R, "--dry-run")
		self.assertEqual(code, 0, err)
		self.assertEqual(self.gh.writes(), [])
		self.assertEqual(json.loads(out)["changes"], [])
		code, out, err = run_cli("repo", "apply", R)
		self.assertEqual(code, 0, err)
		self.assertEqual(self.gh.writes(), [])
		self.assertIn("already matching", out)

	def test_dry_run_reports_differences_without_writing(self):
		self.app()
		routes = matching_routes(self.policy())
		routes[("GET", f"repos/{R}")]["allow_merge_commit"] = True
		routes[("GET", f"repos/{R}/rulesets")] = []
		self.gh.routes.update(routes)
		code, out, err = run_cli("repo", "apply", R, "--dry-run")
		self.assertEqual(code, 0, err)
		self.assertEqual(self.gh.writes(), [])
		calls = [c["call"] for c in json.loads(out)["changes"]]
		self.assertIn(f"PATCH repos/{R}", calls)
		self.assertEqual(calls.count(f"POST repos/{R}/rulesets"), 3)

	def test_required_checks_follow_the_gates(self):
		self.app(REPO_POLICY_ON + ONLY_LINT_TEST)
		routes = matching_routes(self.policy())
		routes[("GET", f"repos/{R}/rulesets")] = []
		self.gh.routes.update(routes)
		code, _, err = run_cli("repo", "apply", R)
		self.assertEqual(code, 0, err)
		posted = [b for m, e, b in self.gh.writes() if e == f"repos/{R}/rulesets"]
		self.assertEqual([p["name"] for p in posted], ["develop"])  # no releases: no release branch, no tags
		rule = next(r for r in posted[0]["rules"] if r["type"] == "required_status_checks")
		self.assertEqual(
			[c["context"] for c in rule["parameters"]["required_status_checks"]], ["ci / lint", "ci / test"]
		)
		pr = next(r for r in posted[0]["rules"] if r["type"] == "pull_request")
		self.assertEqual(pr["parameters"]["required_approving_review_count"], 1)

	def test_repo_policy_off_makes_no_call(self):
		self.app(extra="")
		code, _, err = run_cli("repo", "apply", R)
		self.assertEqual(code, 0)
		self.assertIn("repo-policy is off", err)
		self.assertEqual(self.gh.calls, [])

	def test_all_without_fleet_is_exit_2(self):
		code, _, err = run_cli("repo", "apply", "--all")
		self.assertEqual(code, 2)
		self.assertIn("--fleet", err)

	def test_private_fleet_entry_refuses_full_phase(self):
		fleet = self.tmp / "fleet.json"
		fleet.write_text(json.dumps({"schema": 1, "apps": [{"repo": R, "app": "demo_app", "private": True}]}))
		self.app()
		code, _, err = run_cli("repo", "apply", "--all", "--fleet", str(fleet), "--phase", "full")
		self.assertEqual(code, 1)
		self.assertIn("private", err)
		self.assertEqual(self.gh.calls, [])

	def test_private_repo_rulesets_201_and_403(self):
		self.app()
		routes = matching_routes(self.policy())
		routes[("GET", f"repos/{R}")]["private"] = True
		routes[("GET", f"repos/{R}/rulesets")] = []
		self.gh.routes.update(routes)
		code, _, err = run_cli("repo", "apply", R)
		self.assertEqual(code, 0, err)
		self.assertEqual(len([1 for m, e, _ in self.gh.writes() if e == f"repos/{R}/rulesets"]), 3)
		self.gh.routes[("POST", f"repos/{R}/rulesets")] = 403
		code, _, err = run_cli("repo", "apply", R)
		self.assertEqual(code, 1)
		self.assertIn("does not support rulesets on private repositories", err)

	def test_not_opted_in_fleet_entry_gets_the_provisional_policy(self):
		root = write_app(self.tmp / "bare", opted_in=False, branch="main")
		self.clones[(R, None)] = root
		profile_dir = self.tmp / "profile"
		profile_dir.mkdir()
		(profile_dir / "profile.toml").write_text(PROFILE)
		fleet = self.tmp / "fleet.json"
		fleet.write_text(
			json.dumps(
				{
					"schema": 1,
					"profile": "github:example/standards-profile/v1",
					"apps": [{"repo": R, "app": "demo_app"}],
				}
			)
		)
		self.gh.routes[("GET", f"repos/{R}")] = {"default_branch": "main", "private": False}
		self.gh.routes[("GET", f"repos/{R}/actions/permissions/workflow")] = {}
		self.gh.routes[("GET", f"repos/{R}/labels")] = []
		self.gh.routes[("GET", f"repos/{R}/rulesets")] = []
		with mock.patch.object(targets, "fetch_profile", return_value=profile_dir) as fetch:
			code, _, err = run_cli(
				"repo",
				"apply",
				"--all",
				"--fleet",
				str(fleet),
				"--phase",
				"provisional",
				"--rename-default-branch",
			)
			self.assertEqual(code, 0, err)
			fetch.assert_called_with("github:example/standards-profile/v1")
		writes = self.gh.writes()
		self.assertEqual(writes[0][:2], ("POST", f"repos/{R}/branches/main/rename"))
		self.assertEqual(writes[0][2], {"new_name": "develop"})
		self.assertIn(("PATCH", f"repos/{R}"), [w[:2] for w in writes])
		self.assertEqual(len([w for w in writes if w[1] == f"repos/{R}/labels"]), 2)
		rulesets = [w[2] for w in writes if w[1] == f"repos/{R}/rulesets"]
		self.assertEqual(len(rulesets), 1)
		self.assertEqual(rulesets[0]["conditions"]["ref_name"]["include"], ["refs/heads/develop"])
		self.assertNotIn("required_status_checks", [r["type"] for r in rulesets[0]["rules"]])
		pr = next(r for r in rulesets[0]["rules"] if r["type"] == "pull_request")
		self.assertEqual(pr["parameters"]["required_approving_review_count"], 0)
		self.gh.calls.clear()
		with mock.patch.object(targets, "fetch_profile", return_value=profile_dir):
			code, _, err = run_cli("repo", "apply", "--all", "--fleet", str(fleet), "--phase", "full")
		self.assertEqual(code, 2)
		self.assertIn("--phase provisional", err)
		self.assertEqual(self.gh.writes(), [])

	def test_missing_integration_branch_without_rename_fails(self):
		self.app()
		routes = matching_routes(self.policy())
		del routes[("GET", f"repos/{R}/branches/develop")]
		self.gh.routes.update(routes)
		code, _, err = run_cli("repo", "apply", R)
		self.assertEqual(code, 1)
		self.assertIn("--rename-default-branch", err)

	def test_policy_dir_reads_a_profile_repository_layout(self):
		d = self.tmp / "repo-policy"
		d.mkdir()
		(d / "repo-settings.json").write_text(json.dumps({"repository": {"default_branch": "main"}}))
		release = {
			"name": "v* (release automation only)",
			"target": "branch",
			"enforcement": "active",
			"conditions": {"ref_name": {"include": ["refs/heads/v*"], "exclude": []}},
			"bypass_actors": [{"actor_type": "Integration", "actor_id": 15368, "bypass_mode": "always"}],
			"rules": [{"type": "update", "parameters": {"update_allows_fetch_and_merge": False}}],
		}
		(d / "release.json").write_text(json.dumps(release))
		(d / "main.json").write_text(json.dumps({**release, "name": "main", "bypass_actors": []}))
		P = "example/standards-profile"
		self.gh.routes[("GET", f"repos/{P}")] = {"default_branch": "main"}
		self.gh.routes[("GET", f"repos/{P}/rulesets")] = []
		code, _, err = run_cli("repo", "apply", P, "--policy-dir", str(d))
		self.assertEqual(code, 0, err)
		posted = [w[2] for w in self.gh.writes() if w[1] == f"repos/{P}/rulesets"]
		self.assertEqual(sorted(p["name"] for p in posted), ["main", "v* (release automation only)"])
		self.assertIn(["refs/heads/v*"], [p["conditions"]["ref_name"]["include"] for p in posted])

	def test_bypass_422_prints_the_fallbacks(self):
		self.app()
		routes = matching_routes(self.policy())
		routes[("GET", f"repos/{R}/rulesets")] = []
		routes[("POST", f"repos/{R}/rulesets")] = lambda body: 422 if body["bypass_actors"] else {}
		self.gh.routes.update(routes)
		code, _, err = run_cli("repo", "apply", R, "--rulesets-only")
		self.assertEqual(code, 1)
		self.assertIn("user:<id>", err)


class TestNormalise(unittest.TestCase):
	def test_github_response_equals_the_request(self):
		rs = {
			"name": "develop",
			"target": "branch",
			"enforcement": "active",
			"conditions": {"ref_name": {"include": ["refs/heads/develop"], "exclude": []}},
			"bypass_actors": [],
			"rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
		}
		served = {**rs, "id": 1, "rules": [{"type": "non_fast_forward"}, {"type": "deletion"}], "_links": {}}
		self.assertEqual(apply.normalise(served, rs), apply.normalise(rs))


if __name__ == "__main__":
	unittest.main()
