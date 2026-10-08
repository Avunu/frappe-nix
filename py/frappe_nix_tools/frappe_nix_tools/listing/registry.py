"""``frappe-listing registry``: the pull request that lists a release in the registry (spec §5.2).

Needs ``listing.publish`` and ``listing.registry-fork`` (exit 2 otherwise): the fork, the
upstream (``listing.registry-upstream``) and so the branch ``<lowercased fork owner>/<app>``
are the resolved configuration's (S37), never constants.

1. The commit (``--tag``, ``--ref``, or the tag of ``__version__``) is resolved to its full
   SHA, which must be reachable from ``origin/<branch>``.
2. ``check --release --tag`` runs on a worktree of that commit; a failure stops here (exit 1).
3. Upstream ``main`` is cloned shallowly and the fork branch is made from it, so the pull
   request is always up to date with ``main``.
4. The pending entries are the new one plus every fork-branch entry whose commit upstream
   does not list yet.
5. Each goes through the pinned ``tools/add_release.py`` (``APP``, ``BRANCH``, ``COMMIT``,
   ``CHANNEL=stable``; ``--app-dir`` a worktree of the commit).
6. ``--onboard``, or an app upstream doesn't list, adds the ``apps.json`` index entry and an
   empty ``apps/<app>.json`` first.
7. One commit, ``<app>: <version>`` (``<app>: onboard and <version>``), force-pushed to the
   fork branch; 8. ``gh pr create`` (or ``gh pr edit``); 9. the PR's URL.

Nothing leaves the machine without ``--yes``: without it (or with ``--dry-run``) the run stops
before the push and prints the diff, the fork, the branch and the title. ``--refresh`` rebuilds
an open pull request's branch on upstream ``main`` with no new entry, when it is behind.
``FRAPPE_NIX_REGISTRY_URL`` (``{repo}`` filled in with ``<owner>/<name>``) replaces
``https://github.com/{repo}`` for the clone and the fetch, for mirrors and the tests.

Exit codes: 0 opened, updated or nothing to do (or a dry run); 1 the gate failed; 2 the
configuration; 3 a git, gh or network error.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from frappe_nix_tools.common import repo
from frappe_nix_tools.common.report import ConfigError, EnvError
from frappe_nix_tools.listing import check, target
from frappe_nix_tools.listing.target import Target

CHANNEL = "stable"


class GateFailed(Exception):
	"""``check --release`` failed on the release commit (exit 1)."""


@dataclass
class Plan:
	app: str
	upstream: str
	fork: str
	branch: str  # the fork branch
	release_branch: str
	commit: str
	version: str
	onboard: bool
	title: str
	diff: str
	pending: list[str]
	body: str


def repo_url(slug: str) -> str:
	template = os.environ.get("FRAPPE_NIX_REGISTRY_URL", "https://github.com/{repo}")
	return template.replace("{repo}", slug)


def fork_branch(fork: str, app: str) -> str:
	return f"{fork.split('/', 1)[0].lower()}/{app}"


def settings(t: Target, fork: str | None, upstream: str | None) -> tuple[str, str]:
	"""``(fork, upstream)``; exit 2 without ``listing.publish`` or a fork."""
	listing = t.cfg["listing"]
	if not t.modules.get("listing") or not listing["publish"]:
		raise ConfigError(
			"frappe-listing registry needs listing.publish = true (the module publishes nothing otherwise)"
		)
	fork = fork or listing["registry-fork"]
	if not fork:
		raise ConfigError(
			"listing.registry-fork is empty: set it in your profile or [tool.frappe-nix.listing] (<owner>/marketplace)"
		)
	if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", fork):
		raise ConfigError(f"listing.registry-fork {fork!r} is not <owner>/<repo>")
	return fork, upstream or listing["registry-upstream"]


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
	try:
		proc = subprocess.run(
			["git", "-C", str(cwd), *args],
			check=True,
			capture_output=True,
			text=True,
			# Never a credential prompt: a fork branch that does not exist is a failed fetch.
			env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})},
		)
	except subprocess.CalledProcessError as e:
		raise EnvError(f"git {' '.join(args[:3])}: {e.stderr.strip() or e}") from e
	return proc.stdout


def _identity() -> list[str]:
	"""``-c user.name/user.email`` when git has none (a CI runner)."""
	have = subprocess.run(
		["git", "config", "user.email"], capture_output=True, text=True, check=False
	).stdout.strip()
	return [] if have else ["-c", "user.name=frappe-listing", "-c", "user.email=frappe-listing@localhost"]


def _token() -> str:
	token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
	if token:
		return token
	try:
		return subprocess.run(
			["gh", "auth", "token"], capture_output=True, text=True, check=True
		).stdout.strip()
	except (OSError, subprocess.CalledProcessError) as e:
		raise EnvError("frappe-listing registry needs GH_TOKEN, or a `gh auth login`") from e


def _auth_env(token: str) -> dict[str, str]:
	"""The token as an HTTP header in git's environment, never in a URL or an argument."""
	import base64

	basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
	return {
		"GIT_CONFIG_COUNT": "1",
		"GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
		"GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
	}


def resolve_commit(t: Target, tag: str | None, ref: str | None, branch: str) -> tuple[str, str]:
	"""``(full sha, tag)``; the commit must be reachable from ``origin/<branch>``."""
	prefix = t.cfg["releases"]["tag-prefix"] if t.modules.get("releases") else "v"
	if not tag and not ref:
		tag = f"{prefix}{t.ctx.version}"
	what = tag or ref or ""
	try:
		sha = repo.git(t.root, "rev-parse", "--verify", f"{what}^{{commit}}").strip()
	except EnvError as e:
		raise EnvError(f"{what} is not a commit here (fetch the tags: git fetch --tags origin)") from e
	try:
		repo.git(t.root, "merge-base", "--is-ancestor", sha, f"origin/{branch}")
	except EnvError as e:
		raise EnvError(f"{what} ({sha[:12]}) is not reachable from origin/{branch}") from e
	if not tag:
		found = repo.git(t.root, "tag", "--points-at", sha).split()
		tag = next((name for name in found if name.startswith(prefix)), "")
	return sha, tag


def gate(t: Target, sha: str, tag: str) -> None:
	"""``check --release --tag`` on a worktree of the release commit."""
	with tempfile.TemporaryDirectory(prefix="frappe-listing-release-") as tmp:
		tree = Path(tmp) / t.name
		repo.git(t.root, "worktree", "add", "--detach", str(tree), sha)
		try:
			# The pins and the report stay with the app's own checkout.
			released = target.load(tree)
			outcome = check.run(released, check.Options(release=True, tag=tag))
		finally:
			repo.git(t.root, "worktree", "remove", "--force", str(tree))
	failed = [r for r in outcome.results if r.level == "error"]
	if failed:
		lines = "\n".join(f"  {r.rule} {r.path}: {r.message}" for r in failed)
		raise GateFailed(f"check --release --tag {tag} fails on {sha[:12]}:\n{lines}")


def index_entry(t: Target, branch: str) -> dict:
	listing = t.listing
	repo_slug = t.ctx.repo
	return {
		"name": t.name,
		"title": listing.get("title", t.ctx.title),
		"description": listing.get("tagline", t.ctx.tagline),
		"repo": f"https://github.com/{repo_slug}",
		"logo_url": f"https://raw.githubusercontent.com/{repo_slug}/{branch}/{t.name}/public/images/{t.name}-logo.svg",
		"website": listing.get("website", ""),
		"documentation": listing.get("documentation", ""),
		"category": listing.get("category", ""),
		"categories": listing.get("categories", []),
		"stars": int((listing.get("registry") or {}).get("stars", 0)),
		"releases": f"apps/{t.name}.json",
	}


def _indent(text: str) -> int | str:
	m = re.search(r"\n([ \t]+)\S", text)
	if not m:
		return 2
	return "\t" if m[1].startswith("\t") else len(m[1])


def onboard(clone: Path, t: Target, branch: str) -> None:
	"""Add the app to ``apps.json`` (keeping its indentation) and an empty ``apps/<app>.json``."""
	index = clone / "apps.json"
	text = index.read_text()
	apps = json.loads(text)
	if not any(a.get("name") == t.name for a in apps):
		apps.append(index_entry(t, branch))
		index.write_text(json.dumps(apps, indent=_indent(text), ensure_ascii=False) + "\n")
	releases = clone / "apps" / f"{t.name}.json"
	if not releases.exists():
		releases.parent.mkdir(parents=True, exist_ok=True)
		releases.write_text(json.dumps({"name": t.name, "releases": []}, indent=2, ensure_ascii=False) + "\n")


def add_release(t: Target, marketplace: Path, clone: Path, branch: str, commit: str) -> None:
	"""The pinned ``tools/add_release.py`` for one commit, from a worktree of it."""
	tool = marketplace / "tools" / "add_release.py"
	with tempfile.TemporaryDirectory(prefix="frappe-listing-app-") as tmp:
		tree = Path(tmp) / t.name
		repo.git(t.root, "worktree", "add", "--detach", str(tree), commit)
		try:
			proc = subprocess.run(
				[sys.executable, str(tool), "--app-dir", str(tree), "--registry", str(clone)],
				env={**os.environ, "APP": t.name, "BRANCH": branch, "COMMIT": commit, "CHANNEL": CHANNEL},
				capture_output=True,
				text=True,
				check=False,
			)
		finally:
			repo.git(t.root, "worktree", "remove", "--force", str(tree))
	if proc.returncode != 0:
		raise EnvError(
			f"tools/add_release.py for {commit[:12]} failed: {proc.stderr.strip() or proc.stdout.strip()}"
		)


def _released(path: Path) -> set[str]:
	try:
		doc = json.loads(path.read_text())
	except (FileNotFoundError, json.JSONDecodeError):
		return set()
	return {r.get("commit") for r in doc.get("releases", []) if isinstance(r, dict)}


def prepare(
	t: Target,
	clone: Path,
	*,
	fork: str,
	upstream: str,
	release_branch: str,
	commit: str | None,
	version: str,
	onboarding: bool,
) -> Plan:
	"""Steps 4 to 6 in ``clone`` (``fresh_clone``'s); nothing is pushed."""
	marketplace, _ = check.marketplace_tree(t)
	branch = fork_branch(fork, t.name)
	upstream_released = _released(clone / "apps" / f"{t.name}.json")
	# The fork branch's own entries that upstream has not listed yet.
	pending: list[str] = []
	try:
		_git(clone, "fetch", "--quiet", "--depth", "1", repo_url(fork), f"refs/heads/{branch}")
		fork_doc = json.loads(_git(clone, "show", f"FETCH_HEAD:apps/{t.name}.json"))
		pending += [
			r["commit"]
			for r in fork_doc.get("releases", [])
			if isinstance(r, dict) and r.get("commit") not in upstream_released
		]
	except (EnvError, json.JSONDecodeError):
		pass  # no fork branch yet
	if commit and commit not in pending and commit not in upstream_released:
		pending.append(commit)
	listed = any(a.get("name") == t.name for a in json.loads((clone / "apps.json").read_text()))
	onboarded = onboarding or not listed
	if onboarded:
		onboard(clone, t, release_branch)
	for sha in pending:
		try:
			repo.git(t.root, "cat-file", "-e", f"{sha}^{{commit}}")
		except EnvError as e:
			raise EnvError(
				f"the fork branch lists {sha[:12]}, which this clone lacks: git fetch origin"
			) from e
		add_release(t, marketplace, clone, release_branch, sha)
	_git(clone, "add", "-A")
	diff = _git(clone, "diff", "--cached")
	title = f"{t.name}: {'onboard and ' if onboarded else ''}{version}"
	body = (
		f"Lists {t.name} {version} ({', '.join(c[:12] for c in pending) or 'no new commit'}) from"
		f" https://github.com/{t.ctx.repo}.\n\nPending versions: {len(pending)}. `frappe-listing check --release`"
		" passed on the release commit, with an empty semgrep baseline.\n\nOpened by `frappe-listing registry`."
	)
	return Plan(
		t.name,
		upstream,
		fork,
		branch,
		release_branch,
		commit or "",
		version,
		onboarded,
		title,
		diff,
		pending,
		body,
	)


def fresh_clone(clone: Path, upstream: str, branch: str) -> None:
	"""Step 3: upstream ``main``, shallow, on a new branch ``branch``."""
	_git(clone.parent, "clone", "--quiet", "--depth", "1", "--branch", "main", repo_url(upstream), str(clone))
	_git(clone, "checkout", "--quiet", "-b", branch)


def publish(plan: Plan, clone: Path) -> str:
	"""Steps 7 to 9: commit, force-push to the fork, open or update the pull request."""
	token = _token()
	_git(clone, *_identity(), "commit", "--quiet", "-m", plan.title)
	_git(
		clone,
		"push",
		"--quiet",
		"--force",
		repo_url(plan.fork),
		f"HEAD:refs/heads/{plan.branch}",
		env=_auth_env(token),
	)
	head = f"{plan.fork.split('/', 1)[0]}:{plan.branch}"
	env = {**os.environ, "GH_TOKEN": token}
	existing = subprocess.run(
		[
			"gh",
			"pr",
			"list",
			"--repo",
			plan.upstream,
			"--head",
			plan.branch,
			"--state",
			"open",
			"--json",
			"url",
		],
		capture_output=True,
		text=True,
		env=env,
		check=False,
	)
	urls = [p["url"] for p in json.loads(existing.stdout or "[]")] if existing.returncode == 0 else []
	if urls:
		argv = ["gh", "pr", "edit", urls[0], "--title", plan.title, "--body", plan.body]
	else:
		argv = ["gh", "pr", "create", "--repo", plan.upstream, "--base", "main", "--head", head]
		argv += ["--title", plan.title, "--body", plan.body]
	proc = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
	if proc.returncode != 0:
		raise EnvError(f"gh pr {'edit' if urls else 'create'}: {proc.stderr.strip()}")
	return urls[0] if urls else proc.stdout.strip().splitlines()[-1]


def behind(clone: Path, fork: str, branch: str) -> bool:
	"""Whether the fork branch exists and lacks upstream ``main``'s tip (``--refresh``)."""
	tip = _git(clone, "rev-parse", "HEAD").strip()
	try:
		_git(clone, "fetch", "--quiet", repo_url(fork), f"refs/heads/{branch}")
	except EnvError:
		return False
	try:
		_git(clone, "merge-base", "--is-ancestor", tip, "FETCH_HEAD")
	except EnvError:
		return True
	return False
