"""``frappe-nix policy`` (spec §5.9, §4.3, §7 N4 "policy")."""

import os
import unittest
from unittest import mock

from frappe_nix_tools.commands import policy
from scaffold_helpers import AppCase, git

FRAPPE_MAJOR = '\n[tool.frappe-nix.releases]\nversion-scheme = "frappe-major"\n'


class TestMessageRules(unittest.TestCase):
	def test_frappe_major_rejects(self) -> None:
		for msg in (
			"feat!: x",
			"fix(api)!: y",
			"fix: y\n\nBREAKING CHANGE: z",
			"fix: y\n\nBREAKING-CHANGE: z",
			"chore: x\n\nRelease-As: 17.0.0",
		):
			self.assertTrue(policy.message_problems(msg, 16), msg)

	def test_frappe_major_accepts(self) -> None:
		for msg in (
			"chore(deps): bump foo from 1 to 2",
			"chore(develop): release 16.1.0",
			"feat: x\n\nRelease-As: 16.0.0",
		):
			self.assertEqual(policy.message_problems(msg, 16), [], msg)

	def test_semver_accepts_bang(self) -> None:
		self.assertEqual(policy.message_problems("feat!: x", None), [])
		self.assertEqual(policy.message_problems("feat: x\n\nBREAKING CHANGE: y", None), [])

	def test_comment_lines_are_ignored(self) -> None:
		self.assertEqual(policy.message_problems("# feat!: in a comment\nfix: x", 16), [])


class PolicyCase(AppCase):
	def setUp(self) -> None:
		super().setUp()
		patcher = mock.patch.object(policy, "run_committed", return_value=(True, ""))
		self.committed = patcher.start()
		self.addCleanup(patcher.stop)
		self.heads: list[dict] = []
		self.prs: list[dict] = []

		def gh_json(*args: str) -> list[dict]:
			if "--head" in args:
				return self.prs
			return self.heads

		patcher = mock.patch.object(policy, "gh_json", side_effect=gh_json)
		patcher.start()
		self.addCleanup(patcher.stop)

	def branch(self, name: str, subject: str) -> str:
		git(self.root, "checkout", "-q", "-b", name)
		self.write("demo_app/x.py", f"# {name}\n")
		git(self.root, "add", "-A")
		git(self.root, "commit", "-q", "-m", subject)
		return git(self.root, "rev-parse", "HEAD").strip()

	def pr(self, base: str, title: str, head_sha: str, **env: str) -> tuple[int, str, str]:
		values = {
			"EVENT_NAME": "pull_request",
			"BASE_REF": base,
			"PR_TITLE": title,
			"HEAD_SHA": head_sha,
			**env,
		}
		with mock.patch.dict(os.environ, values):
			return self.fn("policy", "--pr")


class TestCommitMsg(PolicyCase):
	def test_semver_hook_accepts_bang(self) -> None:
		self.write("msg", "feat!: x\n")
		self.assertEqual(self.fn("policy", "--commit-msg-file", str(self.root / "msg"))[0], 0)


class TestCommitMsgFrappeMajor(PolicyCase):
	extra_pyproject = FRAPPE_MAJOR

	def test_rejects_bang_and_wrong_release_as(self) -> None:
		self.write("msg", "feat!: x\n")
		code, _out, err = self.fn("policy", "--commit-msg-file", str(self.root / "msg"))
		self.assertEqual(code, 1, err)
		self.write("msg", "chore: x\n\nRelease-As: 17.0.0\n")
		self.assertEqual(self.fn("policy", "--commit-msg-file", str(self.root / "msg"))[0], 1)
		self.write("msg", "chore(deps): bump x\n")
		self.assertEqual(self.fn("policy", "--commit-msg-file", str(self.root / "msg"))[0], 0)

	def test_pr_commit_with_bang_fails(self) -> None:
		sha = self.branch("feature", "feat!: break it")
		code, _out, err = self.pr("develop", "feat: fine title", sha)
		self.assertEqual(code, 1)
		self.assertIn("break it", err)

	def test_pr_title_with_bang_fails(self) -> None:
		sha = self.branch("feature", "feat: fine")
		code, _out, err = self.pr("develop", "feat!: title", sha)
		self.assertEqual(code, 1)
		self.assertIn("title", err)


class TestPrBases(PolicyCase):
	def test_integration_base_passes(self) -> None:
		sha = self.branch("feature", "feat: x")
		code, out, err = self.pr("develop", "feat: x", sha)
		self.assertEqual(code, 0, out + err)
		self.assertTrue(self.committed.called)

	def test_stacked_base_passes_when_allowed(self) -> None:
		git(self.root, "branch", "parent-branch")
		sha = self.branch("feature", "feat: x")
		self.heads = [{"headRefName": "parent-branch"}]
		self.assertEqual(self.pr("parent-branch", "feat: x", sha)[0], 0)

	def test_unknown_base_fails_with_retarget(self) -> None:
		sha = self.branch("feature", "feat: x")
		code, _out, err = self.pr("elsewhere", "feat: x", sha, GITHUB_REF="refs/pull/7/merge")
		self.assertEqual(code, 1)
		self.assertIn("retarget: gh pr edit 7 --base develop", err)


class TestPrNoStacking(PolicyCase):
	extra_pyproject = '\n[tool.frappe-nix.commits]\nallow-stacked = false\npr-allowed-bases = ["version-*"]\n'

	def test_stacked_base_fails(self) -> None:
		git(self.root, "branch", "parent-branch")
		sha = self.branch("feature", "feat: x")
		self.heads = [{"headRefName": "parent-branch"}]
		self.assertEqual(self.pr("parent-branch", "feat: x", sha)[0], 1)

	def test_allowed_base_glob_passes(self) -> None:
		git(self.root, "branch", "version-16")
		sha = self.branch("feature", "fix: x")
		self.assertEqual(self.pr("version-16", "fix: x", sha)[0], 0)


class TestPrMainTags(PolicyCase):
	extra_pyproject = '\n[tool.frappe-nix.releases]\nbranching = "main+tags"\n'

	def setUp(self) -> None:
		super().setUp()
		self.table('integration-branch = "main"\n')
		self.commit()
		git(self.root, "branch", "main")

	def test_develop_base_fails_naming_main(self) -> None:
		sha = self.branch("feature", "feat: x")
		code, _out, err = self.pr("develop", "feat: x", sha)
		self.assertEqual(code, 1)
		self.assertIn("--base main", err)


class TestDispatch(PolicyCase):
	def test_no_pr_passes(self) -> None:
		sha = self.branch("feature", "feat: x")
		env = {
			"EVENT_NAME": "workflow_dispatch",
			"REF_NAME": "feature",
			"HEAD_SHA": sha,
			"BASE_REF": "",
			"PR_TITLE": "",
		}
		with mock.patch.dict(os.environ, env):
			self.assertEqual(self.fn("policy", "--pr")[0], 0)

	def test_stale_dispatch_fails(self) -> None:
		sha = self.branch("feature", "feat: x")
		self.prs = [{"number": 3, "title": "feat: x", "baseRefName": "develop", "headRefOid": "0" * 40}]
		env = {
			"EVENT_NAME": "workflow_dispatch",
			"REF_NAME": "feature",
			"HEAD_SHA": sha,
			"BASE_REF": "",
			"PR_TITLE": "",
		}
		with mock.patch.dict(os.environ, env):
			code, _out, err = self.fn("policy", "--pr")
		self.assertEqual(code, 1)
		self.assertIn("stale dispatch", err)

	def test_dispatch_takes_title_and_base_from_the_pr(self) -> None:
		sha = self.branch("feature", "feat: x")
		self.prs = [{"number": 3, "title": "feat: x", "baseRefName": "develop", "headRefOid": sha}]
		env = {
			"EVENT_NAME": "workflow_dispatch",
			"REF_NAME": "feature",
			"HEAD_SHA": sha,
			"BASE_REF": "",
			"PR_TITLE": "",
		}
		with mock.patch.dict(os.environ, env):
			self.assertEqual(self.fn("policy", "--pr")[0], 0)


class TestMajorFree(PolicyCase):
	def test_major_free_uses_base_branch(self) -> None:
		git(self.root, "branch", "main")
		sha = self.branch("feature", "feat!: allowed here")
		code, _o, err = self.pr("main", "feat!: allowed", sha)
		# Without --major-free the app's integration branch is develop.
		self.assertEqual(code, 1, err)
		values = {
			"EVENT_NAME": "pull_request",
			"BASE_REF": "main",
			"PR_TITLE": "feat!: allowed",
			"HEAD_SHA": sha,
		}
		with mock.patch.dict(os.environ, values):
			self.assertEqual(self.fn("policy", "--pr", "--major-free")[0], 0)


if __name__ == "__main__":
	unittest.main()
