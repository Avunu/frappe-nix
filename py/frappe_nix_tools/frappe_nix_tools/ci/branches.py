"""Keeping bot PRs up to date with the integration branch (spec §4.5, ``dependabot.update-branches``).

``update`` runs after every push to the integration branch, and on the sweep's schedule. For each
open PR into the integration branch that a bot opened (Dependabot, GitHub Actions or a GitHub App:
the relock and screenshot PRs) and that is behind it:

- a Dependabot PR whose every commit is Dependabot's gets the comment ``@dependabot rebase``:
  Dependabot then rebases it itself, keeps maintaining it, and its push starts CI like any of
  its pushes. The comment is made once per head: a PR that already has one newer than its head
  commit is left alone, until ``rebase_wait`` has passed without a rebase, when it is updated
  like any other bot PR;
- any other bot PR (a Dependabot PR that carries a relock commit, a relock or screenshot PR) is
  updated through ``PUT repos/{r}/pulls/{n}/update-branch``, which merges the integration branch
  into it, with ``expected_head_sha`` so a PR that moved meanwhile is skipped. With a GitHub App's
  token (``ci.github-app``) that push starts CI. With ``GITHUB_TOKEN`` it raises no event, so
  ``dispatch_ci`` waits for the new head and dispatches ``ci.yml`` and ``pr-policy.yml`` on it
  (``release.ensure_ci``; ``contexts`` replaces those pairs, as for frappe-nix's own repository).

Release-please's PR is never touched: it is a bot's PR, but a person merges it. A PR that is
not behind is skipped, so a run that repeats changes nothing, and at most ``limit`` PRs are
updated per run (the oldest first); the next push or schedule takes the rest. A refusal (a
conflict, or a merge that would change ``.github/workflows/`` with a token that has no
Workflows permission) is a notice, never a failure.
"""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from frappe_nix_tools.ci import notice, release
from frappe_nix_tools.common import github

DEPENDABOT_LOGINS = frozenset({"app/dependabot", "dependabot[bot]"})
DEPENDABOT_COMMENT = "@dependabot rebase"
RELEASE_PREFIX = "release-please--"
RELEASE_LABELS = frozenset({"autorelease: pending", "autorelease: tagged"})


def _login(pr: dict) -> str:
	return (pr.get("author") or {}).get("login", "")


def is_bot_pr(pr: dict) -> bool:
	"""A PR a bot opened, other than release-please's."""
	author = pr.get("author") or {}
	login = author.get("login", "")
	if not (
		author.get("is_bot") or login in release.BOTS or login.startswith("app/") or login.endswith("[bot]")
	):
		return False
	if pr.get("isCrossRepository"):
		return False
	if (pr.get("headRefName") or "").startswith(RELEASE_PREFIX):
		return False
	return not any((label.get("name") or "") in RELEASE_LABELS for label in pr.get("labels") or [])


def behind_by(repo: str, integration: str, sha: str) -> int:
	data = github.get(f"repos/{repo}/compare/{integration}...{sha}") or {}
	return int(data.get("behind_by") or 0)


def _date(text: str) -> datetime | None:
	try:
		return datetime.fromisoformat(text.replace("Z", "+00:00"))
	except (AttributeError, ValueError):
		return None


def _is_dependabots(commit: dict) -> bool:
	author = (commit.get("author") or {}).get("login", "")
	email = ((commit.get("commit") or {}).get("author") or {}).get("email", "")
	return author in DEPENDABOT_LOGINS or email.startswith("49699333+dependabot[bot]@")


class Updater:
	def __init__(
		self,
		repo: str,
		integration: str,
		*,
		dispatch_ci: bool = False,
		contexts: tuple[tuple[str, str], ...] = release.CI_CONTEXTS,
		limit: int = 10,
		rebase_wait: timedelta = timedelta(hours=6),
		now: Callable[[], datetime] = lambda: datetime.now(UTC),
		sleep: Callable[[float], None] = time.sleep,
	) -> None:
		self.repo = repo
		self.integration = integration
		self.dispatch_ci = dispatch_ci
		self.contexts = contexts
		self.limit = limit
		self.rebase_wait = rebase_wait
		self.now = now
		self.sleep = sleep

	def open_prs(self) -> list[dict]:
		res = github.cli(
			"pr", "list", "--repo", self.repo, "--base", self.integration, "--state", "open", "--limit", "200",
			"--json", "number,author,headRefName,headRefOid,isCrossRepository,labels",
		)  # fmt: skip
		if res.code != 0:
			raise github.GitHubError(f"gh pr list: {res.stderr.strip()}")
		return sorted(json.loads(res.stdout or "[]"), key=lambda pr: pr.get("number", 0))

	def rebase_requested(self, number: int, head_date: datetime | None) -> datetime | None:
		"""When ``@dependabot rebase`` was last asked for since the head commit, or ``None``."""
		comments = github.get(f"repos/{self.repo}/issues/{number}/comments", paginate=True) or []
		latest = None
		for c in comments:
			if DEPENDABOT_COMMENT not in (c.get("body") or ""):
				continue
			at = _date(c.get("created_at", ""))
			if at and (head_date is None or at > head_date) and (latest is None or at > latest):
				latest = at
		return latest

	def ask_dependabot(self, pr: dict, commits: list[dict]) -> str:
		head = commits[-1] if commits else {}
		head_date = _date(((head.get("commit") or {}).get("committer") or {}).get("date", ""))
		asked = self.rebase_requested(pr["number"], head_date)
		if asked is None:
			github.request(
				"POST", f"repos/{self.repo}/issues/{pr['number']}/comments", {"body": DEPENDABOT_COMMENT}
			)
			print(f"#{pr['number']}: asked Dependabot to rebase it on {self.integration}")
			return "rebase-requested"
		if self.now() - asked < self.rebase_wait:
			print(f"#{pr['number']}: Dependabot was asked to rebase it at {asked.isoformat()}; waiting")
			return "waiting"
		notice(
			f"#{pr['number']}: Dependabot has not rebased it since {asked.isoformat()}; updating the branch instead"
		)
		return self.update_branch(pr)

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
			if _login(pr) in DEPENDABOT_LOGINS:
				commits = github.get(f"repos/{self.repo}/pulls/{number}/commits", paginate=True) or []
				if commits and all(_is_dependabots(c) for c in commits):
					done[number] = self.ask_dependabot(pr, commits)
					continue
			done[number] = self.update_branch(pr)
		if not done:
			print(f"every bot PR is up to date with {self.integration}")
		return done
