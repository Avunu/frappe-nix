"""The pin of each reusable workflow call (common.workflow_pins): a SHA for any workflow, an exact
release tag only for one that ``workflow-lint.release-tag-workflows`` lists, never a branch; steps
are not this check's."""

import unittest

from frappe_nix_tools.common import workflow_pins

SHA = "fd0f83b9bff27149371008e215e526ed1c261731"
DOCS = "example-org/docs-tool/.github/workflows/check-docs.yml"
LISTED = [DOCS, "example-org/gitops/*"]


def call(uses: str) -> str:
	return f"on: push\njobs:\n  docs:\n    uses: {uses}\n"


class TestWorkflowPins(unittest.TestCase):
	def pins(self, uses: str, listed=LISTED) -> list[str]:
		return workflow_pins.findings(call(uses), ".github/workflows/docs.yml", listed)

	def test_a_sha_passes_for_any_workflow(self):
		self.assertEqual(self.pins(f"{DOCS}@{SHA} # v0.4.0"), [])
		self.assertEqual(self.pins(f"acme/tools/.github/workflows/x.yml@{SHA} # v1.2.3"), [])
		self.assertEqual(self.pins(f"{DOCS}@{SHA}", listed=[]), [])

	def test_an_exact_release_tag_passes_for_a_listed_workflow(self):
		self.assertEqual(self.pins(f"{DOCS}@v0.4.0"), [])
		# An entry may be a glob, as zizmor's policy keys are.
		self.assertEqual(self.pins("example-org/gitops/.github/workflows/dependabot-x.yml@v1.0.0"), [])

	def test_a_release_tag_is_refused_for_an_unlisted_workflow(self):
		(line,) = self.pins("acme/tools/.github/workflows/x.yml@v1.2.3")
		self.assertIn(
			"jobs.docs.uses acme/tools/.github/workflows/x.yml@v1.2.3 is not pinned by a full commit SHA",
			line,
		)
		self.assertNotIn("release tag", line)

	def test_the_built_in_default_lists_nothing(self):
		# frappe-nix names no organisation's workflows (S37): with the default, a tag is refused.
		(line,) = self.pins(f"{DOCS}@v0.4.0", listed=[])
		self.assertIn("is not pinned by a full commit SHA", line)
		self.assertNotIn("release tag", line)

	def test_a_branch_is_refused_even_for_a_listed_workflow(self):
		(line,) = self.pins(f"{DOCS}@main")
		self.assertIn("is not pinned by a full commit SHA or exact release tag (vX.Y.Z)", line)

	def test_a_major_only_tag_is_not_an_exact_release_tag(self):
		(line,) = self.pins(f"{DOCS}@v0")
		self.assertIn("exact release tag", line)

	def test_a_local_call_and_a_step_are_not_this_checks_business(self):
		self.assertEqual(self.pins("./.github/workflows/local.yml"), [])
		step = "on: push\njobs:\n  j:\n    steps:\n      - uses: actions/checkout@v7.0.1\n"
		self.assertEqual(workflow_pins.findings(step, "ci.yml", LISTED), [])

	def test_unreadable_yaml_is_a_finding(self):
		(line,) = workflow_pins.findings("jobs: [unclosed\n", "broken.yml")
		self.assertTrue(line.startswith("broken.yml: "))
