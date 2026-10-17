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
	"""frappe-nix's own pr-policy (spec §4.10, §5.9, §6.3)."""

	def setUp(self) -> None:
		super().setUp()
		git(self.root, "branch", "main")

	def major_free(self, title: str, sha: str) -> tuple[int, str, str]:
		values = {"EVENT_NAME": "pull_request", "BASE_REF": "main", "PR_TITLE": title, "HEAD_SHA": sha}
		with mock.patch.dict(os.environ, values):
			return self.fn("policy", "--pr", "--major-free")

	def test_major_free_uses_base_branch(self) -> None:
		sha = self.branch("feature", "feat: fine")
		# Without --major-free the app's integration branch is develop.
		self.assertEqual(self.pr("main", "feat: fine", sha)[0], 1)
		code, out, err = self.major_free("feat: fine", sha)
		self.assertEqual(code, 0, out + err)

	def test_bang_title_fails(self) -> None:
		sha = self.branch("feature", "feat: fine")
		code, _out, err = self.major_free("feat!: break it", sha)
		self.assertEqual(code, 1)
		self.assertIn("Release-As", err)

	def test_bang_commit_and_breaking_footer_fail(self) -> None:
		sha = self.branch("feature", "fix(api)!: break it")
		self.assertEqual(self.major_free("fix: fine", sha)[0], 1)
		git(self.root, "checkout", "-q", "main")
		sha = self.branch("other", "fix: x\n\nBREAKING CHANGE: y")
		self.assertEqual(self.major_free("fix: x", sha)[0], 1)

	def test_release_as_any_major_passes(self) -> None:
		sha = self.branch("feature", "feat: first release\n\nRelease-As: 1.0.0")
		self.assertEqual(self.major_free("feat: first release", sha)[0], 0)
		self.assertEqual(policy.message_problems("chore: x\n\nRelease-As: 2.0.0", None, no_bang=True), [])


LONG = (
	"fix(deps): bump Avunu/site-docs/.github/workflows/check-docs.yml from "
	"06562bcc4dfe0081a7ad3838a7862b421d0e90eb to c9e636d8f316af89afc0c4d97ac9e5aef5b62f4e "
	"in the github-actions group"
)
TYPES = ("build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test")


class TestDependabotSubjectLength(PolicyCase):
	"""A Dependabot PR's generated subjects (SHA pins) may exceed ``subject_length``; nothing else."""

	def setUp(self) -> None:
		super().setUp()
		self.committed.side_effect = self.fake_committed

	def fake_committed(self, root, *args: str, long_subject: bool = False) -> tuple[bool, str]:
		"""``committed`` under a conventional, subject_length = 100 configuration."""
		if args[0] == "--commit-file":
			text = open(args[1]).read()
		else:
			text = git(root, "log", "-1", "--format=%B", args[0])
		subject = text.splitlines()[0]
		if subject.split(":", 1)[0].split("(", 1)[0].rstrip("!") not in TYPES:
			return False, "Disallowed type"
		if len(subject) > 100 and not long_subject:
			return False, f"Commit subject is too long, {len(subject)} exceeds the max length of 100"
		return True, ""

	def dependabot_branch(self, subject: str) -> str:
		git(self.root, "checkout", "-q", "-b", "dependabot/x")
		self.write("demo_app/x.py", "# bump\n")
		git(self.root, "add", "-A")
		git(
			self.root,
			"-c",
			"user.name=dependabot[bot]",
			"-c",
			"user.email=49699333+dependabot[bot]@users.noreply.github.com",
			"commit",
			"-q",
			"-m",
			subject,
		)
		return git(self.root, "rev-parse", "HEAD").strip()

	def test_long_dependabot_title_and_commit_pass(self) -> None:
		self.assertGreater(len(LONG), 100)
		sha = self.dependabot_branch(LONG)
		code, out, err = self.pr("develop", LONG, sha, PR_AUTHOR="dependabot[bot]")
		self.assertEqual(code, 0, out + err)

	def test_long_title_from_a_human_fails(self) -> None:
		sha = self.branch("feature", "fix: short")
		code, _out, err = self.pr("develop", LONG, sha, PR_AUTHOR="someone")
		self.assertEqual(code, 1)
		self.assertIn("title: committed: Commit subject is too long", err)

	def test_long_human_commit_in_a_dependabot_pr_fails(self) -> None:
		sha = self.branch("feature", LONG)
		code, _out, err = self.pr("develop", "fix(deps): bump x", sha, PR_AUTHOR="dependabot[bot]")
		self.assertEqual(code, 1)
		self.assertIn("too long", err)

	def test_dependabot_bang_title_fails(self) -> None:
		git(self.root, "branch", "main")
		sha = self.dependabot_branch("fix(deps): bump x")
		values = {
			"EVENT_NAME": "pull_request",
			"BASE_REF": "main",
			"PR_TITLE": "fix(deps)!: bump x",
			"HEAD_SHA": sha,
			"PR_AUTHOR": "dependabot[bot]",
		}
		with mock.patch.dict(os.environ, values):
			code, _out, err = self.fn("policy", "--pr", "--major-free")
		self.assertEqual(code, 1)
		self.assertIn("`!` marks a major bump", err)

	def test_dependabot_commit_with_bad_type_fails(self) -> None:
		sha = self.dependabot_branch("bump: x from 1 to 2")
		code, _out, err = self.pr("develop", "fix(deps): bump x", sha, PR_AUTHOR="dependabot[bot]")
		self.assertEqual(code, 1)
		self.assertIn("Disallowed type", err)

	def test_dispatch_reads_the_author_from_the_lookup(self) -> None:
		sha = self.dependabot_branch(LONG)
		self.prs = [
			{
				"number": 3,
				"title": LONG,
				"baseRefName": "develop",
				"headRefOid": sha,
				"author": {"login": "app/dependabot", "is_bot": True},
			}
		]
		env = {"EVENT_NAME": "workflow_dispatch", "REF_NAME": "dependabot/x", "HEAD_SHA": sha}
		env |= {"BASE_REF": "", "PR_TITLE": "", "PR_AUTHOR": ""}
		with mock.patch.dict(os.environ, env):
			code, out, err = self.fn("policy", "--pr")
		self.assertEqual(code, 0, out + err)
		self.prs[0]["author"] = {"login": "someone"}
		with mock.patch.dict(os.environ, env):
			self.assertEqual(self.fn("policy", "--pr")[0], 1)


class TestAnySubjectLength(unittest.TestCase):
	def test_turns_off_subject_length_only(self) -> None:
		dumped = 'subject_length = 100\nstyle = "conventional"\nline_length = 0\n'
		self.assertEqual(
			policy.any_subject_length(dumped), 'subject_length = 0\nstyle = "conventional"\nline_length = 0\n'
		)
		self.assertEqual(policy.any_subject_length('style = "x"\n'), 'subject_length = 0\nstyle = "x"\n')

	def test_dependabot_logins(self) -> None:
		self.assertTrue(policy.is_dependabot("dependabot[bot]"))
		self.assertTrue(policy.is_dependabot("app/dependabot"))
		self.assertFalse(policy.is_dependabot("dependabot-impostor"))
		self.assertFalse(policy.is_dependabot(""))


if __name__ == "__main__":
	unittest.main()
