"""``frappe-nix ci update-branches``: bot PRs that are behind the integration branch are brought up
to date (spec §4.5, ``dependabot.update-branches``), against a fake GitHub."""

import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from frappe_nix_tools.ci import branches
from frappe_nix_tools.common.github import Result
from helpers import run_cli
from test_ci_steps import REPO, Fake, FakeCase

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
HEAD_DATE = "2026-10-31T08:00:00Z"
DEPENDABOT = {"login": "app/dependabot", "is_bot": True}
ACTIONS = {"login": "app/github-actions", "is_bot": True}
APP = {"login": "app/example-ci", "is_bot": True}
PERSON = {"login": "someone", "is_bot": False}


def pr(number, author, head, sha, labels=()):
	return {
		"number": number,
		"author": author,
		"headRefName": head,
		"headRefOid": sha,
		"isCrossRepository": False,
		"labels": [{"name": n} for n in labels],
	}


def commit(login, email, date=HEAD_DATE):
	return {"author": {"login": login}, "commit": {"author": {"email": email}, "committer": {"date": date}}}


DEPENDABOT_COMMIT = commit("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
RELOCK_COMMIT = commit("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")


class UpdateCase(FakeCase):
	def fake(self, prs, behind, commits=None, comments=None, extra=None):
		routes = {}
		for p in prs:
			n, sha = p["number"], p["headRefOid"]
			routes[("GET", f"repos/{REPO}/compare/develop...{sha}")] = {"behind_by": behind.get(n, 0)}
			routes[("GET", f"repos/{REPO}/pulls/{n}/commits")] = (commits or {}).get(n, [DEPENDABOT_COMMIT])
			routes[("GET", f"repos/{REPO}/issues/{n}/comments")] = (comments or {}).get(n, [])
			routes[("POST", f"repos/{REPO}/issues/{n}/comments")] = {}
			routes[("PUT", f"repos/{REPO}/pulls/{n}/update-branch")] = {
				"message": "Updating pull request branch."
			}
		routes.update(extra or {})
		return self.use(Fake(routes, {("pr", "list"): Result(0, json.dumps(prs))}))

	def run_updater(self, **kw):
		kw.setdefault("now", lambda: NOW)
		kw.setdefault("sleep", lambda _s: None)
		with mock.patch("builtins.print"):
			return branches.Updater(REPO, "develop", **kw).run()


class TestWhichPRs(unittest.TestCase):
	def test_bots_but_not_release_please(self):
		self.assertTrue(branches.is_bot_pr(pr(1, DEPENDABOT, "dependabot/uv/x", "a")))
		self.assertTrue(branches.is_bot_pr(pr(2, ACTIONS, "standards/relock-python", "a")))
		self.assertTrue(branches.is_bot_pr(pr(3, APP, "standards/screenshots", "a")))
		self.assertFalse(branches.is_bot_pr(pr(4, PERSON, "feat/x", "a")))
		self.assertFalse(branches.is_bot_pr(pr(5, APP, "release-please--branches--develop", "a")))
		self.assertFalse(branches.is_bot_pr(pr(6, ACTIONS, "other", "a", labels=["autorelease: pending"])))
		fork = pr(7, DEPENDABOT, "dependabot/uv/x", "a")
		fork["isCrossRepository"] = True
		self.assertFalse(branches.is_bot_pr(fork))


class TestUpdate(UpdateCase):
	def test_dependabot_only_commits_get_a_rebase_comment(self):
		prs = [pr(1, DEPENDABOT, "dependabot/uv/x", "a" * 40)]
		fake = self.fake(prs, {1: 2})
		self.assertEqual(self.run_updater(), {1: "rebase-requested"})
		self.assertEqual(
			fake.writes(), [("POST", f"repos/{REPO}/issues/1/comments", {"body": "@dependabot rebase"})]
		)

	def test_a_relock_commit_means_update_branch(self):
		prs = [pr(1, DEPENDABOT, "dependabot/nix/frappe", "a" * 40)]
		fake = self.fake(prs, {1: 1}, commits={1: [DEPENDABOT_COMMIT, RELOCK_COMMIT]})
		self.assertEqual(self.run_updater(), {1: "updated"})
		self.assertEqual(
			fake.writes(),
			[("PUT", f"repos/{REPO}/pulls/1/update-branch", {"expected_head_sha": "a" * 40})],
		)
		self.assertEqual([c for c in fake.clis if c[:2] == ("workflow", "run")], [])

	def test_other_bot_prs_update_and_people_and_current_ones_are_left(self):
		prs = [
			pr(3, APP, "standards/screenshots", "c" * 40),
			pr(2, PERSON, "feat/x", "b" * 40),
			pr(1, ACTIONS, "standards/relock-python", "a" * 40),
			pr(4, APP, "release-please--branches--develop", "d" * 40, labels=["autorelease: pending"]),
		]
		fake = self.fake(prs, {1: 0, 2: 5, 3: 1, 4: 3})
		self.assertEqual(self.run_updater(), {3: "updated"})
		self.assertEqual([w[1] for w in fake.writes()], [f"repos/{REPO}/pulls/3/update-branch"])
		# Not behind, not a bot, or release-please's: never compared further, never written.
		self.assertNotIn(("GET", f"repos/{REPO}/compare/develop...{'b' * 40}", None), fake.requests)
		self.assertNotIn(("GET", f"repos/{REPO}/compare/develop...{'d' * 40}", None), fake.requests)

	def test_a_rebase_already_asked_for_waits_then_updates(self):
		prs = [pr(1, DEPENDABOT, "dependabot/uv/x", "a" * 40)]
		asked = [{"body": "@dependabot rebase", "created_at": "2026-10-31T10:00:00Z"}]
		fake = self.fake(prs, {1: 1}, comments={1: asked})
		self.assertEqual(self.run_updater(), {1: "waiting"})
		self.assertEqual(fake.writes(), [])
		# An old request from before the head commit does not count: ask again.
		stale = [{"body": "@dependabot rebase", "created_at": "2026-10-30T10:00:00Z"}]
		fake = self.fake(prs, {1: 1}, comments={1: stale})
		self.assertEqual(self.run_updater(), {1: "rebase-requested"})
		# Unanswered for longer than the wait: update the branch instead.
		fake = self.fake(prs, {1: 1}, comments={1: asked})
		later = NOW + timedelta(hours=7)
		self.assertEqual(self.run_updater(now=lambda: later), {1: "updated"})
		self.assertEqual([w[0] for w in fake.writes()], ["PUT"])

	def test_a_refusal_is_a_notice(self):
		prs = [pr(1, ACTIONS, "standards/relock-python", "a" * 40)]
		self.fake(prs, {1: 1}, extra={("PUT", f"repos/{REPO}/pulls/1/update-branch"): 422})
		with mock.patch("builtins.print") as p:
			done = branches.Updater(REPO, "develop", now=lambda: NOW, sleep=lambda _s: None).run()
		self.assertEqual(done, {1: "refused"})
		printed = " ".join(str(a) for call in p.call_args_list for a in call.args)
		self.assertIn("::notice::", printed)
		self.assertIn("Workflows write", printed)

	def test_limit(self):
		prs = [pr(n, ACTIONS, f"standards/x{n}", str(n) * 40) for n in range(1, 5)]
		fake = self.fake(prs, {n: 1 for n in range(1, 5)})
		self.assertEqual(self.run_updater(limit=2), {1: "updated", 2: "updated"})
		self.assertEqual(len(fake.writes()), 2)

	def test_without_an_app_ci_is_dispatched_on_the_new_head(self):
		old, new = "a" * 40, "f" * 40
		prs = [pr(1, ACTIONS, "standards/relock-python", old)]
		heads = iter([old, new])
		extra = {("GET", f"repos/{REPO}/pulls/1"): lambda: {"head": {"sha": next(heads)}}}
		for ctx in ("ci / lint", "ci / pr-policy"):
			extra[("GET", f"repos/{REPO}/commits/{new}/check-runs?check_name={ctx.replace(' ', '%20')}")] = {
				"total_count": 0
			}
		for wf in ("ci.yml", "pr-policy.yml"):
			extra[("GET", f"repos/{REPO}/contents/.github/workflows/{wf}?ref={new}")] = {"name": wf}
		fake = self.fake(prs, {1: 1}, extra=extra)
		self.assertEqual(self.run_updater(dispatch_ci=True), {1: "updated"})
		runs = [c[2] for c in fake.clis if c[:2] == ("workflow", "run")]
		self.assertEqual(runs, ["ci.yml", "pr-policy.yml"])

	def test_cli(self):
		prs = [pr(1, DEPENDABOT, "dependabot/uv/x", "a" * 40)]
		self.fake(prs, {1: 0})
		code, out, err = run_cli("ci", "update-branches", "--repo", REPO, "--integration", "develop")
		self.assertEqual(code, 0, out + err)
		self.assertIn("up to date", out)
		code, _, err = run_cli(
			"ci", "update-branches", "--repo", REPO, "--integration", "develop", "--limit", "0"
		)
		self.assertEqual(code, 2, err)


if __name__ == "__main__":
	unittest.main()
