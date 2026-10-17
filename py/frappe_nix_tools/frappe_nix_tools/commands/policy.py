"""``frappe-nix policy``: the commit-msg hook and the ``pr-policy`` check (spec §4.3, §5.9, S12).

``--commit-msg-file F`` is the commit-msg hook. Under ``releases.version-scheme =
"frappe-major"`` (with ``releases`` on) it rejects a ``type!:`` subject, a
``BREAKING[ -]CHANGE:`` footer and a ``Release-As: X.y.z`` whose major is not
``frappe-major``. Under ``"semver"``, with ``releases`` off, or in an app that has not
opted in, it rejects nothing (``committed`` checks the rest).

``--pr`` runs in CI (``app-pr-policy.yml``), from the environment the reusable workflow
passes: ``INTEGRATION``, ``EVENT_NAME``, ``BASE_REF``, ``HEAD_SHA``, ``PR_TITLE``,
``REF_NAME`` (and ``GH_TOKEN`` for ``gh``; ``HEAD_REF`` or ``GITHUB_HEAD_REF`` names the head
branch on a ``pull_request``). On a ``workflow_dispatch`` the PR is looked up with
``gh pr list --head $REF_NAME``; its ``headRefOid`` must equal ``HEAD_SHA``, and its title
and base replace the empty ``PR_TITLE`` and ``BASE_REF``. No PR passes. Then:

1. the base is allowed: the integration branch, a ``commits.pr-allowed-bases`` glob, or
   (with ``commits.allow-stacked``) the head branch of another open PR;
2. the title passes ``committed --commit-file`` and the subject rules above that apply;
3. every commit in ``git rev-list --no-merges base..head`` passes ``committed <sha>`` and the
   rules above, the ``Release-As`` major rule included;
4. on a ``release-please--*`` branch: ``frappe-nix compat``.

``--major-free`` (frappe-nix itself, §4.10, which has no ``[tool.frappe-nix]``) drops the
major rules and check 4 and takes the integration branch from ``--base-branch``.

Exit 0 or 1; 2 for a bad argument.
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from frappe_nix_tools.common import config, repo
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError, EnvError

_BANG = re.compile(r"^[a-z]+(\([^)]*\))?!:")
_BREAKING = re.compile(r"^BREAKING[ -]CHANGE:", re.M)
_RELEASE_AS = re.compile(r"^Release-As:\s*v?(\d+)\.\d+\.\d+\S*\s*$", re.M | re.I)


@dataclass
class Rules:
	"""What the resolved configuration says the policy checks."""

	major: int | None = None  # set under version-scheme = "frappe-major"
	integration: str = "main"
	allow_stacked: bool = True
	allowed_bases: list[str] = field(default_factory=list)
	compat: bool = False


def message_problems(text: str, major: int | None) -> list[str]:
	"""The major rules (S12) a commit message or PR title breaks; none without a major."""
	if major is None:
		return []
	lines = [line for line in text.splitlines() if not line.startswith("#")]
	subject = next((line for line in lines if line.strip()), "")
	body = "\n".join(lines)
	out = []
	if _BANG.match(subject):
		out.append(f"{subject!r}: `!` marks a major bump, and the major is the Frappe major ({major})")
	if _BREAKING.search(body):
		out.append(
			f"a BREAKING CHANGE footer marks a major bump, and the major is the Frappe major ({major})"
		)
	for m in _RELEASE_AS.finditer(body):
		if int(m[1]) != major:
			out.append(f"Release-As: {m[0].split(':', 1)[1].strip()} — its major must be {major}")
	return out


def rules_for(root: Path, *, major_free: bool, base_branch: str) -> Rules:
	"""The rules for the app at ``root``; with ``major_free``, frappe-nix's own (no table)."""
	if major_free:
		return Rules(integration=base_branch)
	resolved = config.resolve(root / "pyproject.toml")
	cfg, modules = resolved.cfg, resolved.modules
	major = None
	if modules.get("releases") and cfg["releases"]["version-scheme"] == "frappe-major":
		major = int(cfg["frappe-major"])
	commits = cfg.get("commits", {})
	return Rules(
		major=major,
		integration=cfg["integration-branch"],
		allow_stacked=bool(commits.get("allow-stacked", True)),
		allowed_bases=list(commits.get("pr-allowed-bases", [])),
		compat=True,
	)


def committed_cmd(root: Path) -> list[str]:
	"""``committed`` as the app pins it (``tools/uv.lock``), else the one on PATH."""
	if (root / "tools" / "pyproject.toml").is_file():
		return ["uv", "run", "--frozen", "--project", str(root / "tools"), "committed"]
	return ["committed"]


def run_committed(root: Path, *args: str) -> tuple[bool, str]:
	try:
		proc = subprocess.run(
			[*committed_cmd(root), *args], cwd=root, capture_output=True, text=True, check=False
		)
	except FileNotFoundError as e:
		raise EnvError(f"committed is not available: {e}") from e
	return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def gh_json(*args: str) -> list[dict]:
	try:
		proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
	except FileNotFoundError as e:
		raise EnvError("gh is not on PATH") from e
	if proc.returncode != 0:
		raise EnvError(f"gh {' '.join(args)}: {proc.stderr.strip() or proc.returncode}")
	data = json.loads(proc.stdout or "[]")
	return data if isinstance(data, list) else []


def open_pr_heads() -> set[str]:
	return {
		pr.get("headRefName", "")
		for pr in gh_json("pr", "list", "--state", "open", "--limit", "500", "--json", "headRefName")
	}


def pr_number() -> str:
	"""The PR's number on a ``pull_request`` run (``refs/pull/<n>/merge``), for the retarget hint."""
	m = re.match(r"refs/pull/(\d+)/", os.environ.get("GITHUB_REF", ""))
	return os.environ.get("PR_NUMBER") or (m[1] if m else "<n>")


def base_problem(base: str, rules: Rules, number: str) -> str | None:
	"""Check 1."""
	if base == rules.integration or any(fnmatch.fnmatchcase(base, g) for g in rules.allowed_bases):
		return None
	if rules.allow_stacked and base in open_pr_heads():
		return None
	return f"the base is {base!r}, not {rules.integration!r}: retarget: gh pr edit {number} --base {rules.integration}"


def _base_ref(root: Path, base: str) -> str:
	for ref in (f"origin/{base}", base):
		try:
			repo.git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
			return ref
		except EnvError:
			continue
	raise EnvError(f"the base branch {base!r} is not fetched (actions/checkout: fetch-depth: 0)")


def commit_problems(root: Path, base: str, head: str, rules: Rules) -> list[str]:
	"""Check 3."""
	out = []
	try:
		ref = _base_ref(root, base)
	except EnvError as e:
		return [str(e)]
	shas = repo.git(root, "rev-list", "--no-merges", head, f"^{ref}").split()
	for sha in shas:
		ok, msg = run_committed(root, sha)
		if not ok:
			out.append(f"commit {sha[:12]}: committed: {msg}")
		text = repo.git(root, "log", "-1", "--format=%B", sha)
		out += [f"commit {sha[:12]}: {p}" for p in message_problems(text, rules.major)]
	return out


def title_problems(root: Path, title: str, rules: Rules) -> list[str]:
	"""Check 2."""
	out = []
	with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
		f.write(title + "\n")
		name = f.name
	try:
		ok, msg = run_committed(root, "--commit-file", name)
	finally:
		os.unlink(name)
	if not ok:
		out.append(f"title: committed: {msg}")
	out += [f"title: {p}" for p in message_problems(title, rules.major)]
	return out


def check_pr(root: Path, rules: Rules, env: dict[str, str]) -> list[str]:
	event = env.get("EVENT_NAME", "")
	head_sha = env.get("HEAD_SHA", "")
	title = env.get("PR_TITLE", "")
	base = env.get("BASE_REF", "")
	head_ref = env.get("HEAD_REF") or env.get("GITHUB_HEAD_REF") or ""
	number = pr_number()
	if event == "workflow_dispatch":
		ref = env.get("REF_NAME", "")
		prs = gh_json(
			"pr", "list", "--head", ref, "--state", "open", "--json", "number,title,baseRefName,headRefOid"
		)
		if not prs:
			print(f"frappe-nix policy: no open PR for {ref!r}; nothing to check")
			return []
		pr = prs[0]
		if pr.get("headRefOid") != head_sha:
			return [
				f"stale dispatch: PR #{pr.get('number')} is at {pr.get('headRefOid')}, this run at {head_sha}"
			]
		title, base, number, head_ref = (
			pr.get("title") or "",
			pr.get("baseRefName") or "",
			str(pr.get("number")),
			ref,
		)
		if not title or not base:
			return [f"PR #{number}: the lookup returned no title or base; refusing to check an empty value"]
	if not base:
		raise ConfigError("BASE_REF is empty (set it from github.event.pull_request.base.ref)")
	if not head_sha:
		raise ConfigError("HEAD_SHA is empty")
	out = []
	problem = base_problem(base, rules, number)
	if problem:
		out.append(problem)
	out += title_problems(root, title, rules)
	out += commit_problems(root, base, head_sha, rules)
	if rules.compat and head_ref.startswith("release-please--"):
		from frappe_nix_tools.commands import compat

		old = os.environ.get("GITHUB_HEAD_REF")
		os.environ["GITHUB_HEAD_REF"] = head_ref
		try:
			out += [f"compat: {v}" for v in compat.violations(root)]
		finally:
			if old is None:
				os.environ.pop("GITHUB_HEAD_REF", None)
			else:
				os.environ["GITHUB_HEAD_REF"] = old
	return out


def run(args: argparse.Namespace) -> int:
	root = repo.toplevel(Path.cwd()) if args.major_free else repo.find_up("pyproject.toml").parent
	if args.commit_msg_file:
		try:
			rules = rules_for(root, major_free=args.major_free, base_branch=args.base_branch)
		except config.NotOptedIn:
			return CLEAN
		found = message_problems(Path(args.commit_msg_file).read_text(), rules.major)
	elif args.pr:
		rules = rules_for(root, major_free=args.major_free, base_branch=args.base_branch)
		found = check_pr(root, rules, dict(os.environ))
	else:
		raise ConfigError("give --commit-msg-file F or --pr")
	for p in found:
		print(f"::error::{p}" if os.environ.get("GITHUB_ACTIONS") == "true" else p, file=sys.stderr)
	if not found:
		print("frappe-nix policy: ok")
	return DRIFT if found else CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"policy",
		help="the commit-msg hook and the pr-policy check (§5.9)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	mode = p.add_mutually_exclusive_group()
	mode.add_argument("--commit-msg-file", metavar="F", help="check one commit message (the commit-msg hook)")
	mode.add_argument("--pr", action="store_true", help="check the pull request (CI)")
	p.add_argument(
		"--major-free", action="store_true", help="no major rules and no compat (frappe-nix itself)"
	)
	p.add_argument(
		"--base-branch", default="main", help="the integration branch with --major-free (default: main)"
	)
	p.set_defaults(func=run)
