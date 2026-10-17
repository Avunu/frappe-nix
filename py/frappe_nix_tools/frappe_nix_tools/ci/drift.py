"""The nightly ``drift`` reminders (spec §4.6, S5, S38).

frappe-nix and an org profile never move by Dependabot: their bump can rewrite workflow
files, which ``GITHUB_TOKEN`` cannot push. Their reminders are two issues the nightly job
keeps current:

- "frappe-nix vX.Y.Z available" while the frappe-nix the callers pin (their ``# vX.Y.Z``
  comment) is older than the latest release, with the drift summary and the command
  ``frappe-nix repo rollout --to vX.Y.Z --repo <repo>``; closed once it is current;
- "standards profile update available" while the locked ``standards-profile`` rev differs
  from the head of the ref ``profile`` names, with ``frappe-nix repo rollout --profile-to
  latest --repo <repo>``; closed once it is current.
"""

import json
import re
import subprocess
from pathlib import Path

from packaging.version import InvalidVersion, Version

from frappe_nix_tools.common import github
from frappe_nix_tools.common.report import ConfigError
from frappe_nix_tools.scaffold import defaults

FRAPPE_NIX_TITLE = re.compile(r"^frappe-nix v\S+ available$")
PROFILE_TITLE = "standards profile update available"


def ls_remote(url: str, ref: str) -> str:
	"""The commit ``ref`` names on ``url`` (empty when it can't be read)."""
	proc = subprocess.run(["git", "ls-remote", url, ref], capture_output=True, text=True, check=False)
	for line in proc.stdout.splitlines():
		sha, _, name = line.partition("\t")
		if name in (ref, f"refs/heads/{ref}", f"refs/tags/{ref}", "HEAD"):
			return sha
	return proc.stdout.split("\t", 1)[0].strip() if proc.stdout.strip() else ""


def profile_remote(lock: dict) -> tuple[str, str, str] | None:
	"""``(url, ref, locked rev)`` of the root input ``standards-profile``, when there is one."""
	nodes = lock.get("nodes", {})
	name = nodes.get(lock.get("root", "root"), {}).get("inputs", {}).get("standards-profile")
	if not isinstance(name, str) or name not in nodes:
		return None
	node = nodes[name]
	original, locked = node.get("original", {}), node.get("locked", {})
	kind = original.get("type")
	if kind == "github":
		url = f"https://{original.get('host', 'github.com')}/{original['owner']}/{original['repo']}"
	elif kind == "gitlab":
		url = f"https://{original.get('host', 'gitlab.com')}/{original['owner']}/{original['repo']}"
	elif kind == "git":
		url = original.get("url", "")
	else:
		return None
	return url, original.get("ref") or "HEAD", locked.get("rev", "")


def _issues(repo: str) -> list[dict]:
	items = github.get(f"repos/{repo}/issues?state=open&per_page=100", paginate=True) or []
	return [i for i in items if "pull_request" not in i]


def keep_issue(repo: str, match, title: str, body: str | None) -> str:
	"""Open or update the issue ``match`` finds with ``title``/``body``; ``body=None`` closes it."""
	found = [i for i in _issues(repo) if match(i.get("title", ""))]
	if body is None:
		for issue in found:
			github.request("PATCH", f"repos/{repo}/issues/{issue['number']}", {"state": "closed"})
		return "closed" if found else "none"
	if found:
		github.request("PATCH", f"repos/{repo}/issues/{found[0]['number']}", {"title": title, "body": body})
		return "updated"
	github.request("POST", f"repos/{repo}/issues", {"title": title, "body": body})
	return "opened"


def _version(text: str) -> Version | None:
	try:
		return Version(text.removeprefix("v"))
	except InvalidVersion:
		return None


def run(root: Path, repo: str, latest: str, summary: str = "", check_profile: bool = True) -> dict[str, str]:
	out: dict[str, str] = {}
	want = _version(latest)
	if want is None:
		raise ConfigError(f"--latest {latest!r} is not a version")
	pinned = defaults.head_version(root)
	if pinned is not None and pinned < want:
		tag = f"v{want}"
		body = "\n".join(
			[
				f"This app's workflows pin frappe-nix v{pinned}; v{want} is released.",
				"",
				"Roll it out (it rewrites the caller workflows, so it needs a token with the `workflow` scope):",
				"",
				"```",
				f"frappe-nix repo rollout --to {tag} --repo {repo}",
				"```",
				*(
					["", "Drift against the latest release:", "", "```", summary.rstrip(), "```"]
					if summary.strip()
					else []
				),
			]
		)
		out["frappe-nix"] = keep_issue(repo, FRAPPE_NIX_TITLE.match, f"frappe-nix {tag} available", body)
	else:
		out["frappe-nix"] = keep_issue(repo, FRAPPE_NIX_TITLE.match, "", None)
	if check_profile:
		try:
			lock = json.loads((root / "flake.lock").read_text())
		except (OSError, json.JSONDecodeError):
			lock = {}
		remote = profile_remote(lock)
		if remote is not None:
			url, ref, rev = remote
			head = ls_remote(url, ref)
			match = lambda t: t == PROFILE_TITLE  # noqa: E731
			if head and rev and head != rev:
				body = "\n".join(
					[
						f"The locked standards profile is {rev[:12]}; {ref} of {url} is at {head[:12]}.",
						"",
						"```",
						f"frappe-nix repo rollout --profile-to latest --repo {repo}",
						"```",
					]
				)
				out["profile"] = keep_issue(repo, match, PROFILE_TITLE, body)
			elif head:
				out["profile"] = keep_issue(repo, match, PROFILE_TITLE, None)
	return out
