"""``frappe-nix repo rollout``: move apps to a frappe-nix release or an org profile release (spec §5.8, S5, S38).

A frappe-nix release reaches the fleet through frappe-nix's ``rollout.yml``, which runs this
command's ``--emit-patch`` mode with no write token and publishes the patch only once
``frappe-nix ci verify-rollout-patch`` accepts it. Run by hand, with your own ``gh`` token
(scope ``workflow``: the bump rewrites the caller workflows' ``uses:`` SHAs), it is the
fallback, and the mover for an org profile. For each repository, in a temporary clone of its
integration branch::

    nix flake update frappe-nix                 # with --to
    nix flake update standards-profile         # with --profile-to (the ref in `profile` set first)
    nix run --no-pure-eval .#relock && nix run --no-pure-eval .#frappe-init -- --sync

The ``nix`` commands run the repository's own code, so they get an environment without
``GH_TOKEN``, ``GITHUB_TOKEN`` or any other ``*_TOKEN`` or ``*_KEY`` variable (nor
``SSH_AUTH_SOCK``), and ``GH_CONFIG_DIR`` points at an empty directory: ``gh``'s stored login
is not theirs to use.

By hand, the result is a commit ``chore(deps): frappe-nix vX.Y.Z`` (or ``chore(deps):
standards profile <short rev>``, or both in one subject) on the one branch
``standards/frappe-nix`` (``standards/profile-<short rev>`` for a profile alone), pushed
with ``--force-with-lease`` to the head it saw, a pull request against the integration
branch (edited when it is open already), the old per-release ``standards/frappe-nix-v*``
pull requests closed, and, unless ``--no-auto-merge``, auto-merge under the safety rule of
§4.5 (``frappe-nix ci enable-automerge``): only when the integration branch requires every
enabled gate, else the label ``needs-review``. A branch that has a commit by anyone but
``avunu-rollout[bot]`` (or an earlier ``chore(deps): frappe-nix`` commit of yours) is left
alone, with a comment on its pull request, while that pull request is open or no pull request
holds those commits. Once its latest pull request is merged or closed and the branch is still
that pull request's head, the branch is stale and the next rollout replaces it (with
``--force-with-lease``). With ``--no-auto-merge``, or when the safety rule fails, an
auto-merge an earlier run enabled is turned off. A repository whose ``profile`` is a
built-in is skipped by ``--profile-to``. ``--dry-run`` prints each repository's plan and
runs nothing.

``--emit-patch DIR`` (with one ``--repo``, ``--to`` and ``--base <sha>``) is rollout.yml's
plan job: it checks out exactly ``--base``, which must be on the integration branch, runs
the commands above, stages everything and writes ``DIR/patch`` (``git diff --cached
--binary --full-index <base>``) and ``DIR/meta.json`` (``repo``, ``integration``,
``base_sha``, ``tag``, ``tag_sha``, ``state``, ``tools_version``). ``state`` is ``patch``,
``noop`` (an empty patch) or ``superseded``: frappe-nix's release branch has moved past
``--to``, so a later release's run moves the app instead, and nothing else is written. It
commits, pushes and writes nothing on GitHub.

``--to`` names the release the bump must reach: after ``nix flake update frappe-nix`` the
locked revision must be that tag's commit (the release branch the app tracks is
fast-forwarded to each tag), else the repository fails and nothing is pushed. Exit 0 when
every repository was rolled out or had nothing to do, 1 when one failed, 2 for a usage
error.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import frappe_nix_tools
from frappe_nix_tools.ci import automerge
from frappe_nix_tools.commands.repo import targets
from frappe_nix_tools.common import appgates, flakelock, github
from frappe_nix_tools.common.config import ORG_PROFILE_PREFIXES
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError, EnvError, FrappeNixError

_VERSION = re.compile(r"^v\d+\.\d+\.\d+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")

#: The one branch a frappe-nix rollout uses, and the per-release ones it replaces.
BRANCH = "standards/frappe-nix"
LEGACY_PREFIX = "standards/frappe-nix-v"
#: The rollout's own commits: rollout.yml commits as the avunu-rollout App, whose key only the
#: frappe-nix-rollout environment holds (docs/app-standards/github.md, Rollout).
BOT = "avunu-rollout[bot]"

# The environment the repository's own code (its flake, relock, sync) never sees.
_SECRET_SUFFIXES = ("_TOKEN", "_KEY", "_SECRET", "_PASSWORD")
_SECRET_NAMES = frozenset({"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "SSH_AUTH_SOCK"})
# After that code ran in a clone, git there runs none of its hooks or fsmonitor.
_GIT_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
_empty_config: str | None = None


def scrubbed_env() -> dict[str, str]:
	"""``os.environ`` without tokens or keys, and with ``gh`` pointed at an empty config."""
	global _empty_config
	if _empty_config is None or not os.path.isdir(_empty_config):
		_empty_config = tempfile.mkdtemp(prefix="frappe-nix-gh-empty-")
	env = {
		k: v
		for k, v in os.environ.items()
		if k.upper() not in _SECRET_NAMES and not k.upper().endswith(_SECRET_SUFFIXES)
	}
	env["GH_CONFIG_DIR"] = _empty_config
	return env


def sh(cmd: list[str], cwd: Path, *, scrub: bool | None = None) -> str:
	"""Run ``cmd`` in ``cwd``; a failure is an ``EnvError`` naming it. Tests replace it.

	``scrub`` (default: for ``nix``, the commands that run the repository's code) runs it
	with ``scrubbed_env()``."""
	if scrub is None:
		scrub = cmd[:1] == ["nix"]
	env = scrubbed_env() if scrub else None
	try:
		proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False, env=env)
	except FileNotFoundError as e:
		raise EnvError(f"{cmd[0]} is not on PATH") from e
	if proc.returncode != 0:
		raise EnvError(f"{' '.join(cmd)}: {proc.stderr.strip()[-2000:]}")
	return proc.stdout


def git_safe(*args: str) -> list[str]:
	"""``git`` with no hook or fsmonitor of the clone: for a clone the repository's code has run in."""
	return ["git", *_GIT_SAFE, *args]


def set_profile_ref(pyproject: Path, ref: str) -> bool:
	"""Point ``[tool.frappe-nix] profile`` at ``ref`` (``latest`` keeps the current ref)."""
	if ref == "latest":
		return False
	text = pyproject.read_text()
	new, n = re.subn(r'(?m)^(profile\s*=\s*)"[^"]*"', lambda m: f'{m[1]}"{ref}"', text, count=1)
	if n and new != text:
		pyproject.write_text(new)
		return True
	return False


def locked_frappe_nix(root: Path) -> dict:
	lock = flakelock.load(root / "flake.lock")
	found = flakelock.node_at(lock, ["frappe-nix"])
	locked = found[1].get("locked") if found else None
	return locked if isinstance(locked, dict) else {}


def tag_sha(locked: dict, to: str) -> str | None:
	"""The commit of release ``to`` in the repository ``locked`` names."""
	tag = github.get_or_none(f"repos/{locked.get('owner')}/{locked.get('repo')}/commits/{to}") or {}
	return tag.get("sha")


def check_reached(root: Path, to: str) -> None:
	"""The lock's frappe-nix revision is ``to``'s commit."""
	locked = locked_frappe_nix(root)
	sha = tag_sha(locked, to)
	if not sha or sha != locked.get("rev"):
		raise EnvError(
			f"flake.lock locks frappe-nix {str(locked.get('rev'))[:12]}, not {to} ({str(sha)[:12]}):"
			" its release branch has not reached that release"
		)


def moved_past(locked: dict, sha: str) -> bool:
	"""Whether the locked revision descends from ``sha``: the release branch moved past it."""
	rev = locked.get("rev")
	if not rev or not sha:
		return False
	cmp = github.get_or_none(f"repos/{locked.get('owner')}/{locked.get('repo')}/compare/{sha}...{rev}") or {}
	return cmp.get("status") == "ahead"


def plan(app: appgates.AppConfig, to: str | None, profile_to: str | None) -> tuple[list[list[str]], str, str]:
	"""The commands, the commit subject and the branch (``standards/<branch>``) for one app."""
	cmds: list[list[str]] = []
	subjects = []
	branch = ""
	if to:
		cmds.append(["nix", "flake", "update", "frappe-nix"])
		subjects.append(f"frappe-nix {to}")
		branch = BRANCH.removeprefix("standards/")
	source = app.resolved.profile.get("source", "builtin")
	if profile_to and source.startswith(ORG_PROFILE_PREFIXES):
		cmds.append(["nix", "flake", "update", "standards-profile"])
	cmds += [
		["nix", "run", "--no-pure-eval", ".#relock"],
		["nix", "run", "--no-pure-eval", ".#frappe-init", "--", "--sync"],
	]
	return cmds, "; ".join(subjects), branch


def _integration_checkout(r: str, root: Path) -> appgates.AppConfig | str:
	"""The app on its integration branch, or why it is skipped."""
	try:
		app = appgates.load(root)
	except ConfigError:
		return f"{r}: skipped, no [tool.frappe-nix]"
	integration = app.integration
	if targets.clone_branch(root) != integration:
		sh(["git", "fetch", "-q", "origin", integration], root)
		sh(["git", "checkout", "-q", "-B", integration, f"origin/{integration}"], root)
		app = appgates.load(root)
	return app


# --- --emit-patch (rollout.yml's plan job) ------------------------------------------


def emit_patch(r: str, args: argparse.Namespace) -> str:
	out = Path(args.emit_patch)
	out.mkdir(parents=True, exist_ok=True)
	root = targets.clone(r, depth=None)
	if root is None:
		raise EnvError(f"{r}: could not clone")
	try:
		app = appgates.load(root)
	except ConfigError as e:
		raise ConfigError(f"{r}: no [tool.frappe-nix] on its default branch: {e}") from e
	integration = app.integration
	sh(["git", "fetch", "-q", "origin", f"+refs/heads/{integration}:refs/remotes/origin/{integration}"], root)
	on_branch = subprocess.run(
		["git", "merge-base", "--is-ancestor", args.base, f"refs/remotes/origin/{integration}"],
		cwd=root,
		capture_output=True,
		check=False,
	)
	if on_branch.returncode != 0:
		raise EnvError(f"{r}: --base {args.base[:12]} is not on {integration}")
	sh(["git", "checkout", "-q", "--detach", args.base], root)
	app = appgates.load(root)
	if app.integration != integration:
		raise EnvError(
			f"{r}: at {args.base[:12]} the integration branch is {app.integration}, not {integration}"
		)
	meta = {
		"repo": r,
		"integration": integration,
		"base_sha": args.base,
		"tag": args.to,
		"tag_sha": "",
		"state": "",
		"tools_version": frappe_nix_tools.__version__,
	}
	cmds, _subject, _branch = plan(app, args.to, None)
	for c in cmds:
		sh(c, root, scrub=True)
		if c[-1] == "frappe-nix":
			locked = locked_frappe_nix(root)
			meta["tag_sha"] = tag_sha(locked, args.to) or ""
			if meta["tag_sha"] != locked.get("rev"):
				if moved_past(locked, meta["tag_sha"]):
					meta["state"] = "superseded"
					(out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
					return f"{r}: superseded, frappe-nix's release branch is past {args.to}"
				check_reached(root, args.to)
	sh(git_safe("add", "-A"), root, scrub=True)
	try:
		proc = subprocess.run(
			git_safe("diff", "--cached", "--binary", "--full-index", args.base),
			cwd=root,
			capture_output=True,
			check=False,
			env=scrubbed_env(),
		)
	except FileNotFoundError as e:
		raise EnvError("git is not on PATH") from e
	if proc.returncode != 0:
		raise EnvError(f"git diff --cached: {proc.stderr.decode(errors='replace').strip()}")
	(out / "patch").write_bytes(proc.stdout)
	meta["state"] = "patch" if proc.stdout else "noop"
	(out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
	return f"{r}: {meta['state']} ({len(proc.stdout)} bytes) in {out}"


# --- by hand -------------------------------------------------------------------------


def remote_head(root: Path, head: str) -> str:
	"""The SHA ``origin``'s ``head`` branch points at, or "" when there is none."""
	out = sh(git_safe("ls-remote", "origin", f"refs/heads/{head}"), root)
	return out.split()[0] if out.strip() else ""


def foreign_commits(root: Path, head: str, integration: str, own_email: str) -> list[str]:
	"""The authors of the commits on ``origin``'s ``head`` that the integration branch lacks,
	other than the rollout's: ``avunu-rollout[bot]``, and an earlier ``chore(deps): frappe-nix``
	commit by ``own_email`` (this command run by hand)."""
	ref = f"refs/remotes/origin/{head}"
	sh(git_safe("fetch", "-q", "origin", f"+refs/heads/{head}:{ref}"), root)
	log = sh(git_safe("log", "--format=%an%x1f%ae%x1f%s", ref, "--not", f"origin/{integration}"), root)
	out = []
	for line in log.splitlines():
		name, email, subject = [*line.split("\x1f"), "", ""][:3]
		if name == BOT:
			continue
		if own_email and email == own_email and subject.startswith("chore(deps): frappe-nix"):
			continue
		if name not in out:
			out.append(name)
	return out


def open_pr(r: str, head: str) -> dict | None:
	owner = r.split("/", 1)[0]
	pulls = github.get(f"repos/{r}/pulls?state=open&head={owner}:{head}") or []
	return pulls[0] if pulls else None


def stale(r: str, head: str, observed: str) -> bool:
	"""Whether ``head`` (at ``observed``) is what its latest pull request, now merged or closed,
	left behind: no open pull request holds its commits, so a rollout may replace it."""
	owner = r.split("/", 1)[0]
	query = f"state=all&head={owner}:{head}&sort=created&direction=desc&per_page=1"
	pulls = github.get_or_none(f"repos/{r}/pulls?{query}") or []
	pr = pulls[0] if pulls else None
	return bool(pr) and pr.get("state") == "closed" and (pr.get("head") or {}).get("sha") == observed


def close_legacy(r: str, url: str) -> list[int]:
	"""Close the open ``standards/frappe-nix-v*`` pull requests ``url`` replaces."""
	closed = []
	for pr in github.get(f"repos/{r}/pulls?state=open&per_page=100", paginate=True) or []:
		ref = str((pr.get("head") or {}).get("ref", ""))
		if not ref.startswith(LEGACY_PREFIX):
			continue
		res = github.cli(
			"pr",
			"close",
			str(pr["number"]),
			"-R",
			r,
			"--comment",
			f"Superseded by {url}: frappe-nix releases now move on the one branch `{BRANCH}`.",
		)
		if res.code == 0:
			closed.append(int(pr["number"]))
	return closed


def _own_email(root: Path) -> str:
	"""The email this run commits as (``git var GIT_AUTHOR_IDENT``), or ""."""
	proc = subprocess.run(
		git_safe("var", "GIT_AUTHOR_IDENT"), cwd=root, capture_output=True, text=True, check=False
	)
	m = re.search(r"<([^<>]*)>", proc.stdout) if proc.returncode == 0 else None
	return m[1] if m else ""


def rollout_one(r: str, args: argparse.Namespace) -> str:
	root = targets.clone(r, depth=None)
	if root is None:
		raise EnvError(f"{r}: could not clone")
	app = _integration_checkout(r, root)
	if isinstance(app, str):
		return app
	integration = app.integration
	source = app.resolved.profile.get("source", "builtin")
	if args.profile_to and not args.to and not source.startswith(ORG_PROFILE_PREFIXES):
		return f"{r}: skipped, its profile is the built-in {source!r}"
	cmds, subject, branch = plan(app, args.to, args.profile_to)
	method = (app.cfg.get("repo-policy", {}).get("merge-methods") or ["squash"])[0]
	if args.dry_run:
		lines = [f"{r} ({integration}):"]
		if args.profile_to and args.profile_to != "latest" and source.startswith(ORG_PROFILE_PREFIXES):
			lines.append(f"  set profile = {args.profile_to!r}")
		lines += [f"  {' '.join(c)}" for c in cmds]
		lines.append(
			f"  commit 'chore(deps): {subject or 'standards profile <rev>'}' on standards/{branch or 'profile-<rev>'}"
		)
		lines.append("  git push --force-with-lease (not over another author's commits)")
		lines.append(f"  gh pr create --base {integration} (or edit the open one)")
		if args.to:
			lines.append(f"  close the open {LEGACY_PREFIX}* pull requests")
		if not args.no_auto_merge:
			lines.append(
				f"  auto-merge ({method}) when {integration} requires every gate, else label needs-review"
			)
		return "\n".join(lines)
	if args.profile_to:
		set_profile_ref(root / "pyproject.toml", args.profile_to)
	for c in cmds:
		sh(c, root, scrub=True)
		if args.to and c[-1] == "frappe-nix":
			check_reached(root, args.to)
	if args.profile_to and source.startswith(ORG_PROFILE_PREFIXES):
		lock = flakelock.load(root / "flake.lock")
		rev = (flakelock.locked_rev(lock, flakelock.PROFILE_INPUT) or "")[:7]
		subject = "; ".join(s for s in (subject, f"standards profile {rev}") if s)
		branch = branch or f"profile-{rev}"
	if not sh(git_safe("status", "--porcelain"), root).strip():
		return f"{r}: already current"
	head = f"standards/{branch}"
	observed = remote_head(root, head)
	if observed:
		others = foreign_commits(root, head, integration, _own_email(root))
		if others and stale(r, head, observed):
			print(
				f"{r}: {head} has commits by {', '.join(others)}, but its pull request is closed: replacing it",
				file=sys.stderr,
			)
			others = []
		if others:
			pr = open_pr(r, head)
			if pr:
				github.cli(
					"pr",
					"comment",
					str(pr["number"]),
					"-R",
					r,
					"--body",
					f"`frappe-nix repo rollout` did not update `{head}` to {subject}: it has commits by"
					f" {', '.join(others)}. Merge or close this pull request and the next rollout"
					" replaces the branch.",
				)
			return f"{r}: skipped, {head} has commits by {', '.join(others)}"
	sh(git_safe("checkout", "-q", "-B", head), root)
	sh(git_safe("add", "-A"), root)
	sh(git_safe("commit", "-q", "-m", f"chore(deps): {subject}"), root)
	sh(
		git_safe(
			"push",
			"-q",
			f"--force-with-lease=refs/heads/{head}:{observed}",
			"origin",
			f"HEAD:refs/heads/{head}",
		),
		root,
	)
	title = f"chore(deps): {subject}"
	body = f"Rolled out with `frappe-nix repo rollout`.\n\n{subject}"
	pr = open_pr(r, head)
	if pr:
		url = str(pr.get("html_url") or "")
		github.cli("pr", "edit", str(pr["number"]), "-R", r, "--title", title, "--body", body)
	else:
		res = github.cli(
			"pr", "create", "-R", r, "--base", integration, "--head", head, "--title", title, "--body", body
		)
		if res.code != 0:
			raise EnvError(f"{r}: gh pr create: {res.stderr.strip()}")
		url = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
	if args.to and url:
		closed = close_legacy(r, url)
		if closed:
			print(f"{r}: closed {', '.join(f'#{n}' for n in closed)} ({LEGACY_PREFIX}*)", file=sys.stderr)
	if not args.no_auto_merge and url:
		automerge.enable(url, r, integration, app.gates, method)
	elif url:
		automerge.disable(url)
	return f"{r}: {url or head}"


def run(args: argparse.Namespace) -> int:
	if not args.to and not args.profile_to:
		raise ConfigError("give --to vX.Y.Z, --profile-to <ref>|latest, or both")
	if args.to and not _VERSION.match(args.to):
		raise ConfigError(f"--to takes a release tag vX.Y.Z, not {args.to!r}")
	if args.emit_patch or args.base:
		if not args.emit_patch or not args.base or not args.to:
			raise ConfigError(
				"--emit-patch needs --to vX.Y.Z and --base <sha>, and --base needs --emit-patch"
			)
		if not _SHA.match(args.base):
			raise ConfigError(f"--base takes a full commit SHA, not {args.base!r}")
		if args.profile_to or args.all or len(args.repo or []) != 1 or args.dry_run:
			raise ConfigError("--emit-patch takes one --repo, and no --profile-to, --all or --dry-run")
	pairs, _fleet = targets.entries(args.repo or [], args.all, args.fleet)
	code = CLEAN
	try:
		for r, _entry in pairs:
			try:
				print(emit_patch(r, args) if args.emit_patch else rollout_one(r, args))
			except FrappeNixError as e:
				code = DRIFT
				print(f"frappe-nix repo rollout: {r}: {e}", file=sys.stderr)
	finally:
		targets.cleanup()
		if _empty_config:
			shutil.rmtree(_empty_config, ignore_errors=True)
	return code


def register(sub: argparse._SubParsersAction) -> None:
	p = sub.add_parser(
		"rollout",
		help="move apps to a frappe-nix or org profile release (needs nix and a gh token with `workflow`)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("--to", metavar="vX.Y.Z")
	p.add_argument("--profile-to", metavar="<ref>|latest")
	p.add_argument("--repo", action="append", metavar="owner/repo")
	p.add_argument("--all", action="store_true")
	p.add_argument("--fleet")
	p.add_argument("--no-auto-merge", action="store_true")
	p.add_argument("--dry-run", action="store_true")
	p.add_argument(
		"--emit-patch",
		metavar="DIR",
		help="rollout.yml's plan: write DIR/patch and DIR/meta.json, push nothing (needs --base, --to)",
	)
	p.add_argument("--base", metavar="SHA", help="with --emit-patch: the integration head to start from")
	p.set_defaults(func=run)
