"""The apps a bench can add (``frappe_nix_tools/data/frappe-apps.json``): the TUI's store
(`bench ui`, lib/tui/frappe_nix_tui) reads it, and ``frappe-nix apps-catalog sync`` writes it.

Two sources, merged by repository:

- the marketplace registry (``listing.registry-upstream``, frappe/marketplace): its
  ``apps.json`` (name, title, description, categories, stars, links) and each app's
  ``apps/<name>.json`` releases, whose ``frappe_core`` ranges and ``dependencies`` say which
  Frappe an app supports and what it needs;
- the frappe organisation's own repositories that are apps without a listing (webshop,
  newsletter, …): every unarchived repository with a ``version-<n>`` branch.

For each app the sync records its release branches (``version-<n>``) and its default branch,
read with ``git ls-remote``, so the store can name the branch ``bench get-app --branch``
takes for the bench's Frappe without asking GitHub when it runs. An app's name is the
registry's, or, for an unlisted repository, its ``pyproject.toml``'s ``[project] name``.

Standard library, git, and the GitHub REST API (``GH_TOKEN`` or ``GITHUB_TOKEN`` when set,
for the rate limit).
"""

import json
import os
import re
import subprocess
import tomllib
import urllib.error
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from frappe_nix_tools.common.report import EnvError

REGISTRY = "frappe/marketplace"
ORG = "frappe"
TIMEOUT = 30
VERSION_BRANCH = re.compile(r"^version-(\d+)$")
#: Not an app to add: the framework is in every bench.
SKIP = frozenset({"frappe/frappe"})


def _get(url: str, *, api: bool = False) -> bytes:
	headers = {"User-Agent": "frappe-nix-apps-catalog"}
	token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
	if api and token:
		headers["Authorization"] = f"Bearer {token}"
	if api:
		headers["Accept"] = "application/vnd.github+json"
	request = urllib.request.Request(url, headers=headers)
	try:
		with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
			return response.read()
	except (urllib.error.URLError, TimeoutError, OSError) as e:
		raise EnvError(f"cannot read {url}: {e}") from e


def _json(url: str, *, api: bool = False) -> Any:
	try:
		return json.loads(_get(url, api=api))
	except ValueError as e:
		raise EnvError(f"{url} is not JSON: {e}") from e


def _repo(url: str) -> str:
	"""``owner/name`` of a GitHub URL, or "" for another host."""
	m = re.match(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", url.strip())
	return m.group(1) if m else ""


def registry(upstream: str = REGISTRY) -> list[dict[str, Any]]:
	"""The registry's apps, each with its releases."""
	raw = f"https://raw.githubusercontent.com/{upstream}/main"
	index = _json(f"{raw}/apps.json")
	if not isinstance(index, list):
		raise EnvError(f"{raw}/apps.json is not a list of apps")

	def releases(entry: dict[str, Any]) -> list[dict[str, Any]]:
		path = entry.get("releases") or f"apps/{entry['name']}.json"
		try:
			doc = _json(f"{raw}/{path}")
		except EnvError:
			return []
		return [r for r in doc.get("releases", []) if isinstance(r, dict)]

	with ThreadPoolExecutor(16) as pool:
		all_releases = list(pool.map(releases, index))
	return [{**entry, "releases": rels} for entry, rels in zip(index, all_releases, strict=True)]


def org_repos(org: str = ORG) -> list[dict[str, Any]]:
	"""The organisation's unarchived, non-fork repositories."""
	out: list[dict[str, Any]] = []
	page = 1
	while True:
		batch = _json(
			f"https://api.github.com/orgs/{org}/repos?per_page=100&type=sources&page={page}", api=True
		)
		if not batch:
			return [r for r in out if not r.get("archived") and not r.get("fork")]
		out.extend(batch)
		page += 1


def branches(repo: str) -> tuple[str, list[str]]:
	"""(default branch, the ``version-<n>`` branches, newest first) of ``owner/name``."""
	result = subprocess.run(
		["git", "ls-remote", "--symref", f"https://github.com/{repo}.git", "HEAD", "refs/heads/version-*"],
		capture_output=True,
		text=True,
		timeout=120,
		check=False,
		env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
	)
	default = ""
	found: list[tuple[int, str]] = []
	for line in result.stdout.splitlines():
		if line.startswith("ref: refs/heads/"):
			default = line.split()[1].removeprefix("refs/heads/")
			continue
		ref = line.split("\t")[-1].removeprefix("refs/heads/")
		if m := VERSION_BRANCH.match(ref):
			found.append((int(m.group(1)), ref))
	return default, [ref for _, ref in sorted(found, reverse=True)]


def app_name(repo: str, branch: str) -> str:
	"""The Python package a repository's ``pyproject.toml`` names, or the repository's
	name with ``-`` as ``_``."""
	try:
		data = tomllib.loads(
			_get(f"https://raw.githubusercontent.com/{repo}/{branch}/pyproject.toml").decode()
		)
		name = data.get("project", {}).get("name")
		if isinstance(name, str) and name:
			return name.replace("-", "_")
	except (EnvError, tomllib.TOMLDecodeError, UnicodeDecodeError):
		pass
	return repo.split("/")[1].replace("-", "_")


def _release(r: dict[str, Any]) -> dict[str, Any]:
	keep = ("version", "branch", "frappe_core", "dependencies", "channel")
	return {k: r[k] for k in keep if k in r}


def build(
	listed: Iterable[dict[str, Any]], org: Iterable[dict[str, Any]], *, org_name: str = ORG
) -> list[dict[str, Any]]:
	"""The catalog's apps from the registry's (``listed``) and the organisation's
	repositories (``org``), with their branches read from git."""
	apps: dict[str, dict[str, Any]] = {}
	for entry in listed:
		repo = _repo(str(entry.get("repo", "")))
		if not repo or repo.lower() in SKIP:
			continue
		apps[repo.lower()] = {
			"name": entry["name"],
			"repo": repo,
			"title": entry.get("title") or entry["name"],
			"description": " ".join(str(entry.get("description") or "").split()),
			"category": entry.get("category", ""),
			"categories": [c for c in entry.get("categories", []) if c],
			"stars": entry.get("stars", 0),
			"website": entry.get("website", ""),
			"documentation": entry.get("documentation", ""),
			"logo": entry.get("logo_url", ""),
			"listed": True,
			"releases": [_release(r) for r in entry.get("releases", [])],
		}
	candidates = [
		r
		for r in org
		if f"{org_name}/{r['name']}".lower() not in apps and f"{org_name}/{r['name']}".lower() not in SKIP
	]
	repos = [a["repo"] for a in apps.values()] + [f"{org_name}/{r['name']}" for r in candidates]
	with ThreadPoolExecutor(16) as pool:
		heads = dict(zip(repos, pool.map(branches, repos), strict=True))
	unlisted = []
	for r in candidates:
		repo = f"{org_name}/{r['name']}"
		default, versions = heads[repo]
		if not versions:
			continue  # not an app (a tool, docs, a fork of something else)
		unlisted.append((r, repo, versions[0]))
	with ThreadPoolExecutor(16) as pool:
		names = list(pool.map(lambda c: app_name(c[1], c[2]), unlisted))
	for (r, repo, _), name in zip(unlisted, names, strict=True):
		apps[repo.lower()] = {
			"name": name,
			"repo": repo,
			"title": r["name"].replace("_", " ").replace("-", " ").title(),
			"description": " ".join(str(r.get("description") or "").split()),
			"category": "",
			"categories": ["Official"],
			"stars": r.get("stargazers_count", 0),
			"website": r.get("homepage") or "",
			"documentation": "",
			"logo": "",
			"listed": False,
			"releases": [],
		}
	for key, app in apps.items():
		default, versions = heads[app["repo"]]
		app["default_branch"] = default
		app["branches"] = versions
		if (
			app["listed"]
			and "Official" not in app["categories"]
			and app["repo"].lower().startswith(f"{org_name}/")
		):
			app["categories"].append("Official")
		apps[key] = app
	return sorted(apps.values(), key=lambda a: a["name"])


def sync(out: Path, *, upstream: str = REGISTRY, org: str = ORG) -> list[dict[str, Any]]:
	apps = build(registry(upstream), org_repos(org), org_name=org)
	doc = {
		"generated": datetime.now(UTC).strftime("%Y-%m-%d"),
		"sources": [f"github.com/{upstream}", f"github.com/{org}"],
		"apps": apps,
	}
	out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
	return apps
