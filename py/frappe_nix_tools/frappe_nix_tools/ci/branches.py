"""Keeping bot PRs up to date with the integration branch (spec §4.5, ``dependabot.update-branches``).

``update`` runs after every push to the integration branch, and on the sweep's schedule. For each
open bot maintenance PR into the integration branch (Dependabot's, and the ``standards/*``
branches GitHub Actions or a GitHub App push: the weekly relock and the screenshots) that is
behind it, ``PUT repos/{r}/pulls/{n}/update-branch`` merges the integration branch into it, with
``expected_head_sha`` so a PR that moved meanwhile is skipped. With a GitHub App's token
(``ci.github-app``) that push starts CI. With ``GITHUB_TOKEN`` it raises no event, so
``dispatch_ci`` waits for the new head and dispatches ``ci.yml`` and ``pr-policy.yml`` on it
(``release.ensure_ci``; ``contexts`` replaces those pairs, as for frappe-nix's own repository).

Dependabot's PRs are updated the same way. ``@dependabot rebase`` would keep Dependabot
maintaining the PR, but Dependabot takes commands only from a person with push access: from a
GitHub App or ``github-actions[bot]`` it answers "only users with push access can use that
command" and does nothing. Once the branch is updated, Dependabot stops rebasing that PR, so a
later conflict needs a person; auto-merge usually lands the PR before that.

Release-please's PR is never touched: it is a bot's PR, but a person merges it; nor is any
other bot's (a coding agent's PR is a person's work). A PR that is
not behind is skipped, so a run that repeats changes nothing, and at most ``limit`` PRs are
updated per run (the oldest first); the next push or schedule takes the rest. A refusal (a
conflict, or a merge that would change ``.github/workflows/`` with a token that has no
Workflows permission) is a notice, never a failure.
"""

import json
import time
from collections.abc import Callable

from frappe_nix_tools.ci import notice, release
from frappe_nix_tools.common import github

DEPENDABOT_LOGINS = frozenset({"app/dependabot", "dependabot[bot]"})
BOT_BRANCHES = "standards/"
RELEASE_PREFIX = "release-please--"
RELEASE_LABELS = frozenset({"autorelease: pending", "autorelease: tagged"})


def is_bot_pr(pr: dict) -> bool:
	"""A bot maintenance PR: Dependabot's, or a bot's on a ``standards/`` branch; never
	release-please's, nor a fork's."""
	author = pr.get("author") or {}
	login = author.get("login", "")
	head = pr.get("headRefName") or ""
	if pr.get("isCrossRepository") or head.startswith(RELEASE_PREFIX):
		return False
	if any((label.get("name") or "") in RELEASE_LABELS for label in pr.get("labels") or []):
		return False
	if login in DEPENDABOT_LOGINS:
		return True
	is_bot = (
		author.get("is_bot") or login in release.BOTS or login.startswith("app/") or login.endswith("[bot]")
	)
	return bool(is_bot) and head.startswith(BOT_BRANCHES)


def behind_by(repo: str, integration: str, sha: str) -> int:
	data = github.get(f"repos/{repo}/compare/{integration}...{sha}") or {}
	return int(data.get("behind_by") or 0)


class Updater:
	def __init__(
		self,
		repo: str,
		integration: str,
		*,
		dispatch_ci: bool = False,
		contexts: tuple[tuple[str, str], ...] = release.CI_CONTEXTS,
		limit: int = 10,
		sleep: Callable[[float], None] = time.sleep,
	) -> None:
		self.repo = repo
		self.integration = integration
		self.dispatch_ci = dispatch_ci
		self.contexts = contexts
		self.limit = limit
		self.sleep = sleep

	def open_prs(self) -> list[dict]:
		res = github.cli(
			"pr", "list", "--repo", self.repo, "--base", self.integration, "--state", "open", "--limit", "200",
			"--json", "number,author,headRefName,headRefOid,isCrossRepository,labels",
		)  # fmt: skip
		if res.code != 0:
			raise github.GitHubError(f"gh pr list: {res.stderr.strip()}")
		return sorted(json.loads(res.stdout or "[]"), key=lambda pr: pr.get("number", 0))

	def update_branch(self, pr: dict) -> str:
		number, sha = pr["number"], pr["headRefOid"]
		try:
			github.request(
				"PUT", f"repos/{self.repo}/pulls/{number}/update-branch", {"expected_head_sha": sha}
			)
		except github.GitHubError as e:
			notice(
				f"#{number}: could not update it from {self.integration} ({e}). A conflict needs a person;"
				" a merge that changes .github/workflows/ needs a GitHub App with Workflows write"
			)
			return "refused"
		print(f"#{number}: merged {self.integration} into {pr['headRefName']}")
		if self.dispatch_ci:
			self.ensure_ci(pr)
		return "updated"

	def ensure_ci(self, pr: dict, tries: int = 10, delay: float = 3.0) -> None:
		"""A ``GITHUB_TOKEN`` update raises no event: dispatch CI on the new head once it exists."""
		for _ in range(tries):
			self.sleep(delay)
			head = ((github.get(f"repos/{self.repo}/pulls/{pr['number']}") or {}).get("head") or {}).get(
				"sha", ""
			)
			if head and head != pr["headRefOid"]:
				release.ensure_ci(self.repo, pr["headRefName"], head, self.contexts)
				return
		notice(f"#{pr['number']}: its new head did not appear yet; the sweep dispatches CI on it later")

	def run(self) -> dict[int, str]:
		done: dict[int, str] = {}
		acted = 0
		for pr in self.open_prs():
			if not is_bot_pr(pr):
				continue
			number = pr["number"]
			behind = behind_by(self.repo, self.integration, pr["headRefOid"])
			if not behind:
				continue
			if acted >= self.limit:
				notice(f"updated {self.limit} PRs in this run; #{number} and the rest wait for the next one")
				break
			acted += 1
			done[number] = self.update_branch(pr)
		if not done:
			print(f"every bot PR is up to date with {self.integration}")
		return done
