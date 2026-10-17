"""The release flow's dispatches and fast-forward, and the bot-PR sweep (spec §4.4, §4.5, S13, S29).

A ``GITHUB_TOKEN`` push or PR raises no workflow run, so the release PR (opened by
release-please) and bot PRs (Dependabot, relock, screenshots) get their checks by dispatch:

- ``ensure_ci`` checks each required context on the head SHA on its own: when ``ci / lint``
  is absent it dispatches ``ci.yml``, when ``ci / pr-policy`` is absent (and ``pr-policy.yml``
  exists there) ``pr-policy.yml``. A dispatch that failed or a concurrency group cancelled is
  therefore retried by the next run;
- ``dispatch_ci`` does that for the open release PR (``autorelease: pending``), accepting only
  release-please's own branch names;
- ``sweep`` does it for every open PR whose author is Dependabot or GitHub Actions;
- ``fast_forward`` moves the release branch to the release commit with a REST PATCH (or
  creates it on the first release), which GitHub refuses unless it is a fast-forward, then
  reads it back and fails unless it equals the tag this run created, peeled (the D8 guard).
  Under ``frappe-major`` the tag must also start with ``<tag-prefix><major>.``; that is checked
  before anything is written.
"""

import json
from dataclasses import dataclass, field

from frappe_nix_tools.ci import error, notice
from frappe_nix_tools.common import github

CI_CONTEXTS = (("ci / lint", "ci.yml"), ("ci / pr-policy", "pr-policy.yml"))
BOTS = {"app/dependabot", "dependabot[bot]", "app/github-actions", "github-actions[bot]"}


def release_branches(integration: str, package: str = "") -> set[str]:
	"""The head branch names release-please gives its PR."""
	names = {f"release-please--branches--{integration}"}
	if package:
		names.add(f"release-please--branches--{integration}--components--{package}")
	return names


def _has_check(repo: str, sha: str, context: str) -> bool:
	data = github.get(f"repos/{repo}/commits/{sha}/check-runs?check_name={context.replace(' ', '%20')}")
	return bool((data or {}).get("total_count") or (data or {}).get("check_runs"))


def _workflow_exists(repo: str, workflow: str, sha: str) -> bool:
	return github.get_or_none(f"repos/{repo}/contents/.github/workflows/{workflow}?ref={sha}") is not None


def ensure_ci(repo: str, ref: str, sha: str) -> list[str]:
	"""Dispatch each workflow whose context is absent on ``sha``; returns what was dispatched."""
	out = []
	for context, workflow in CI_CONTEXTS:
		if _has_check(repo, sha, context):
			continue
		if not _workflow_exists(repo, workflow, sha):
			continue
		res = github.cli("workflow", "run", workflow, "--repo", repo, "--ref", ref)
		if res.code != 0:
			notice(f"could not dispatch {workflow} on {ref}: {res.stderr.strip()}")
			continue
		print(f"dispatched {workflow} on {ref} ({context} absent on {sha[:12]})")
		out.append(workflow)
	return out


def _pr_list(*args: str) -> list[dict]:
	res = github.cli("pr", "list", *args)
	if res.code != 0:
		raise github.GitHubError(f"gh pr list: {res.stderr.strip()}")
	return json.loads(res.stdout or "[]")


def dispatch_ci(repo: str, integration: str, package: str = "") -> list[str]:
	prs = _pr_list(
		"--repo", repo, "--base", integration, "--label", "autorelease: pending", "--state", "open",
		"--json", "number,headRefName,headRefOid",
	)  # fmt: skip
	allowed = release_branches(integration, package)
	out = []
	found = False
	for pr in prs:
		if pr.get("headRefName") not in allowed:
			print(f"skipping #{pr.get('number')}: {pr.get('headRefName')} is not release-please's branch")
			continue
		found = True
		out += ensure_ci(repo, pr["headRefName"], pr["headRefOid"])
	if not found:
		print("no open release PR")
	return out


def sweep(repo: str) -> list[str]:
	prs = _pr_list("--repo", repo, "--state", "open", "--json", "number,author,headRefName,headRefOid")
	out = []
	for pr in prs:
		author = (pr.get("author") or {}).get("login", "")
		if author not in BOTS:
			continue
		out += ensure_ci(repo, pr["headRefName"], pr["headRefOid"])
	return out


@dataclass
class FastForward:
	repo: str
	branch: str
	sha: str
	tag: str
	version_scheme: str = "semver"
	tag_prefix: str = "v"
	frappe_major: int = 0
	pilot_assets: bool = False
	dry_run: bool = False
	calls: list[str] = field(default_factory=list)

	def _write(self, method: str, endpoint: str, body: dict) -> None:
		self.calls.append(f"{method} {endpoint} {json.dumps(body, sort_keys=True)}")
		if self.dry_run:
			print(f"dry run: {method} {endpoint} {json.dumps(body, sort_keys=True)}")
			return
		github.request(method, endpoint, body)

	def peeled(self, tag: str) -> str:
		obj = (github.get(f"repos/{self.repo}/git/ref/tags/{tag}") or {}).get("object") or {}
		for _ in range(10):
			if obj.get("type") != "tag":
				return obj.get("sha", "")
			obj = (github.get(f"repos/{self.repo}/git/tags/{obj['sha']}") or {}).get("object") or {}
		return ""

	def scheme_ok(self) -> bool:
		if self.version_scheme != "frappe-major":
			return True
		want = f"{self.tag_prefix}{self.frappe_major}."
		if not self.tag.startswith(want):
			error(
				f"release tag {self.tag} does not start with {want}: under version-scheme = frappe-major"
				f" {self.branch} carries only {want}* releases"
			)
			return False
		return True

	def run(self) -> int:
		if not self.scheme_ok():
			return 1
		endpoint = f"repos/{self.repo}/git/ref/heads/{self.branch}"
		if github.get_or_none(endpoint) is not None:
			self._write(
				"PATCH", f"repos/{self.repo}/git/refs/heads/{self.branch}", {"sha": self.sha, "force": False}
			)
		else:
			self._write(
				"POST", f"repos/{self.repo}/git/refs", {"ref": f"refs/heads/{self.branch}", "sha": self.sha}
			)
		head = ((github.get_or_none(endpoint) or {}).get("object") or {}).get("sha", "")
		tagged = self.peeled(self.tag)
		if not head or head != tagged:
			error(
				f"{self.branch} is at {head[:12] or '(missing)'} but {self.tag} is {tagged[:12] or '(missing)'}:"
				f" the release branch must equal the newest release tag (D8)"
			)
			return 1
		print(f"{self.branch} = {self.tag} = {head}")
		if self.pilot_assets:
			self.calls.append(f"workflow run assets.yml --ref {self.branch}")
			if self.dry_run:
				print(f"dry run: gh workflow run assets.yml --ref {self.branch}")
			else:
				res = github.cli("workflow", "run", "assets.yml", "--repo", self.repo, "--ref", self.branch)
				if res.code != 0:
					error(f"could not dispatch assets.yml on {self.branch}: {res.stderr.strip()}")
					return 1
		return 0
