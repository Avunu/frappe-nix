"""repo-policy/ (spec §4.8, §4.9, §7 N4): the shipped JSON renders to rulesets GitHub's API takes."""

import json
import tempfile
import unittest
from pathlib import Path

from frappe_nix_tools.common import config, data_path, repo_policy, schema
from frappe_nix_tools.common.report import ConfigError

ALL_GATES = ["pr-policy", "lint", "typecheck", "test", "marketplace"]


def cfg(**over) -> tuple[dict, dict]:
	table = {"schema": 1, "profile": "recommended", "frappe-major": 16, "repo": "example/demo_app"}
	table.update(over)
	r = config.resolve_doc({"tool": {"frappe-nix": table}}, on_github=True)
	return r.cfg, r.modules


def contexts(policy: repo_policy.Policy) -> list[str]:
	for rs in policy.rulesets:
		for rule in rs["rules"]:
			if rule["type"] == "required_status_checks":
				return [c["context"] for c in rule["parameters"]["required_status_checks"]]
	return []


class TestRepoPolicy(unittest.TestCase):
	def validate(self, ruleset: dict) -> None:
		errors = schema.Validator("github-ruleset.schema.json", ruleset["name"]).errors(ruleset)
		self.assertEqual(errors, [])

	def test_shipped_files_are_json(self):
		directory = data_path("repo-policy")
		for path in [directory / "repo-settings.json", *sorted((directory / "rulesets").glob("*.json"))]:
			json.loads(path.read_text())
		self.assertEqual(
			sorted(p.name for p in (directory / "rulesets").glob("*.json")),
			["integration-provisional.json", "integration.json", "release-branch.json", "tags.json"],
		)

	def test_self_policy(self):
		"""frappe-nix's own repo-policy/self (spec §4.10, §7 N6): concrete, and valid for the API."""
		policy = repo_policy.from_dir(repo_policy.self_dir())
		self.assertEqual(
			[rs["name"] for rs in policy.rulesets],
			["main", "release-* (release automation only)", "v* tags"],
		)
		for rs in policy.rulesets:
			self.validate(rs)
		self.assertEqual(contexts(policy), ["lint", "flake", "pr-policy"])
		self.assertEqual(policy.repository["default_branch"], "main")
		self.assertEqual(policy.labels, [])
		main, release, tags = policy.rulesets
		self.assertEqual(main["conditions"]["ref_name"]["include"], ["refs/heads/main"])
		pr = next(r for r in main["rules"] if r["type"] == "pull_request")
		self.assertEqual(pr["parameters"]["required_approving_review_count"], 0)
		self.assertEqual(release["conditions"]["ref_name"]["include"], ["refs/heads/release-*"])
		self.assertEqual(release["bypass_actors"], repo_policy.bypass_actors("github-actions"))
		self.assertEqual(tags["conditions"]["ref_name"]["include"], ["refs/tags/v*"])
		# The same rules as the app defaults they derive from.
		default = repo_policy.default_dir() / "rulesets"
		for mine, theirs in ((release, "release-branch.json"), (tags, "tags.json")):
			self.assertEqual(mine["rules"], json.loads((default / theirs).read_text())["rules"])

	def test_full_rulesets_validate_against_the_api_schema(self):
		c, m = cfg()
		policy = repo_policy.for_app(c, m, ALL_GATES, phase="full", release_branch="version-16")
		self.assertEqual(
			[rs["name"] for rs in policy.rulesets],
			["develop", "version-* (release automation only)", "v* tags"],
		)
		for rs in policy.rulesets:
			self.validate(rs)
		provisional = repo_policy.for_app(c, m, ALL_GATES, phase="provisional", release_branch="version-16")
		self.assertEqual(len(provisional.rulesets), 1)
		self.validate(provisional.rulesets[0])
		self.assertEqual(contexts(provisional), [])

	def test_required_checks_are_the_enabled_gates_in_order(self):
		c, m = cfg()
		policy = repo_policy.for_app(c, m, list(reversed(ALL_GATES)), phase="full", release_branch="")
		self.assertEqual(contexts(policy), [f"ci / {g}" for g in ALL_GATES])
		rule = next(r for r in policy.rulesets[0]["rules"] if r["type"] == "required_status_checks")
		self.assertTrue(
			all(ch["integration_id"] == 15368 for ch in rule["parameters"]["required_status_checks"])
		)
		two = repo_policy.for_app(c, m, ["lint", "test"], phase="full", release_branch="")
		self.assertEqual(contexts(two), ["ci / lint", "ci / test"])
		none = repo_policy.for_app(c, m, [], phase="full", release_branch="")
		self.assertNotIn("required_status_checks", [r["type"] for r in none.rulesets[0]["rules"]])

	def test_explicit_required_checks_must_be_enabled_gates(self):
		c, m = cfg(**{"repo-policy": {"required-checks": ["lint"]}})
		policy = repo_policy.for_app(c, m, ["lint", "test"], phase="full", release_branch="")
		self.assertEqual(contexts(policy), ["ci / lint"])
		c, m = cfg(**{"repo-policy": {"required-checks": ["marketplace"]}})
		with self.assertRaises(ConfigError):
			repo_policy.for_app(c, m, ["lint"], phase="full", release_branch="")

	def test_values_from_the_profile(self):
		c, m = cfg(**{"repo-policy": {"required-approvals": 0, "merge-methods": ["rebase", "squash"]}})
		policy = repo_policy.for_app(c, m, ["lint"], phase="full", release_branch="")
		pr = next(r for r in policy.rulesets[0]["rules"] if r["type"] == "pull_request")["parameters"]
		self.assertEqual(pr["required_approving_review_count"], 0)
		self.assertEqual(pr["allowed_merge_methods"], ["rebase", "squash"])
		self.assertTrue(policy.repository["allow_rebase_merge"])
		self.assertFalse(policy.repository["allow_merge_commit"])

	def test_main_tags_has_no_release_branch_ruleset(self):
		c, m = cfg(releases={"branching": "main+tags"})
		self.assertEqual(c["integration-branch"], "main")
		policy = repo_policy.for_app(c, m, ["lint"], phase="full", release_branch="")
		self.assertEqual([rs["name"] for rs in policy.rulesets], ["main", "v* tags"])

	def test_empty_tag_prefix_and_user_bypass(self):
		c, m = cfg(releases={"tag-prefix": ""}, **{"repo-policy": {"release-bypass": "user:42"}})
		policy = repo_policy.for_app(c, m, ["lint"], phase="full", release_branch="version-16")
		tags = policy.rulesets[-1]
		self.assertEqual(tags["conditions"]["ref_name"]["include"], ["refs/tags/[0-9]*"])
		self.assertEqual(
			policy.rulesets[1]["bypass_actors"],
			[{"actor_type": "User", "actor_id": 42, "bypass_mode": "always"}],
		)
		for rs in policy.rulesets:
			self.validate(rs)

	def test_settings_follow_the_parameters_and_modules(self):
		c, m = cfg(**{"repo-policy": {"manage-default-branch": False, "manage-features": False}})
		policy = repo_policy.for_app(c, m, ["lint"], phase="full", release_branch="")
		self.assertNotIn("default_branch", policy.repository)
		self.assertNotIn("has_wiki", policy.repository)
		self.assertNotIn("homepage", policy.repository)
		self.assertEqual([lb["name"] for lb in policy.labels], ["dependencies", "needs-review"])
		with_docs = repo_policy.for_app(
			c, m, ["lint"], phase="full", release_branch="", documentation="https://d.example.org/"
		)
		self.assertEqual(with_docs.repository["homepage"], "https://d.example.org/")
		c, m = cfg(dependabot={"enable": False})
		self.assertEqual(repo_policy.for_app(c, m, [], phase="full", release_branch="").labels, [])

	def test_org_override(self):
		with tempfile.TemporaryDirectory() as d:
			over = Path(d) / "repo-policy" / "rulesets"
			over.mkdir(parents=True)
			doc = json.loads((data_path("repo-policy") / "rulesets/tags.json").read_text())
			doc["enforcement"] = "evaluate"
			(over / "tags.json").write_text(json.dumps(doc))
			c, m = cfg()
			policy = repo_policy.for_app(c, m, ["lint"], phase="full", release_branch="", overrides=Path(d))
			self.assertEqual(policy.rulesets[-1]["enforcement"], "evaluate")

	def test_unfilled_placeholder_is_a_config_error(self):
		with self.assertRaises(ConfigError):
			repo_policy.fill({"name": "{nope}"}, {}, "x")


if __name__ == "__main__":
	unittest.main()
