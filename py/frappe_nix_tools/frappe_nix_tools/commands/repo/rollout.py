"""``frappe-nix repo rollout``: move apps to a frappe-nix release or an org profile release (spec §5.8, S5, S38).

The one path by which a frappe-nix (or org profile) release reaches an app with CI callers:
the bump rewrites the caller workflows' ``uses:`` SHAs, which a ``GITHUB_TOKEN`` can't push,
so it runs locally with the user's ``gh`` token (scope ``workflow``). For each repository,
in a temporary clone of its integration branch::

    nix flake update frappe-nix                 # with --to
    nix flake update standards-profile         # with --profile-to (the ref in `profile` set first)
    nix run --no-pure-eval .#relock && nix run --no-pure-eval .#frappe-init -- --sync

then a commit ``chore(deps): frappe-nix vX.Y.Z`` (or ``chore(deps): standards profile
<short rev>``, or both in one subject), a push of ``standards/frappe-nix-vX.Y.Z`` (or
``standards/profile-<short rev>``), a pull request against the integration branch, and,
unless ``--no-auto-merge``, ``gh pr merge --auto --<first of repo-policy.merge-methods>``.
A repository whose ``profile`` is a built-in is skipped by ``--profile-to``.
``--dry-run`` prints each repository's plan and runs nothing.

``--to`` names the release the bump must reach: after ``nix flake update frappe-nix`` the
locked revision must be that tag's commit (the release branch the app tracks is
fast-forwarded to each tag), else the repository fails and nothing is pushed. Exit 0 when every repository was rolled out or had
nothing to do, 1 when one failed, 2 for a usage error.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from frappe_nix_tools.commands.repo import targets
from frappe_nix_tools.common import appgates, flakelock, github
from frappe_nix_tools.common.config import ORG_PROFILE_PREFIXES
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError, EnvError, FrappeNixError

_VERSION = re.compile(r"^v\d+\.\d+\.\d+$")


def sh(cmd: list[str], cwd: Path) -> str:
	"""Run ``cmd`` in ``cwd``; a failure is an ``EnvError`` naming it. Tests replace it."""
	try:
		proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
	except FileNotFoundError as e:
		raise EnvError(f"{cmd[0]} is not on PATH") from e
	if proc.returncode != 0:
		raise EnvError(f"{' '.join(cmd)}: {proc.stderr.strip()[-2000:]}")
	return proc.stdout


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


def check_reached(root: Path, to: str) -> None:
	"""The lock's frappe-nix revision is ``to``'s commit."""
	lock = flakelock.load(root / "flake.lock")
	found = flakelock.node_at(lock, ["frappe-nix"])
	locked = (found[1].get("locked") or {}) if found else {}
	tag = github.get_or_none(f"repos/{locked.get('owner')}/{locked.get('repo')}/commits/{to}") or {}
	if not tag.get("sha") or tag["sha"] != locked.get("rev"):
		raise EnvError(
			f"flake.lock locks frappe-nix {str(locked.get('rev'))[:12]}, not {to} ({str(tag.get('sha'))[:12]}):"
			" its release branch has not reached that release"
		)


def plan(app: appgates.AppConfig, to: str | None, profile_to: str | None) -> tuple[list[list[str]], str, str]:
	"""The commands, the commit subject and the branch for one app."""
	cmds: list[list[str]] = []
	subjects, branches = [], []
	if to:
		cmds.append(["nix", "flake", "update", "frappe-nix"])
		subjects.append(f"frappe-nix {to}")
		branches.append(f"frappe-nix-{to}")
	source = app.resolved.profile.get("source", "builtin")
	if profile_to and source.startswith(ORG_PROFILE_PREFIXES):
		cmds.append(["nix", "flake", "update", "standards-profile"])
	cmds += [
		["nix", "run", "--no-pure-eval", ".#relock"],
		["nix", "run", "--no-pure-eval", ".#frappe-init", "--", "--sync"],
	]
	return cmds, "; ".join(subjects), "-".join(branches)


def rollout_one(r: str, args: argparse.Namespace) -> str:
	root = targets.clone(r, depth=None)
	if root is None:
		raise EnvError(f"{r}: could not clone")
	try:
		app = appgates.load(root)
	except ConfigError:
		return f"{r}: skipped, no [tool.frappe-nix]"
	integration = app.integration
	if targets.clone_branch(root) != integration:
		sh(["git", "fetch", "-q", "origin", integration], root)
		sh(["git", "checkout", "-q", "-B", integration, f"origin/{integration}"], root)
		app = appgates.load(root)
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
		lines.append(f"  gh pr create --base {integration}")
		if not args.no_auto_merge:
			lines.append(f"  gh pr merge --auto --{method}")
		return "\n".join(lines)
	if args.profile_to:
		set_profile_ref(root / "pyproject.toml", args.profile_to)
	for c in cmds:
		sh(c, root)
		if args.to and c[-1] == "frappe-nix":
			check_reached(root, args.to)
	if args.profile_to and source.startswith(ORG_PROFILE_PREFIXES):
		lock = flakelock.load(root / "flake.lock")
		rev = (flakelock.locked_rev(lock, flakelock.PROFILE_INPUT) or "")[:7]
		subject = "; ".join(s for s in (subject, f"standards profile {rev}") if s)
		branch = "-".join(b for b in (branch, f"profile-{rev}") if b)
	if not sh(["git", "status", "--porcelain"], root).strip():
		return f"{r}: already current"
	head = f"standards/{branch}"
	sh(["git", "checkout", "-q", "-B", head], root)
	sh(["git", "add", "-A"], root)
	sh(["git", "commit", "-q", "-m", f"chore(deps): {subject}"], root)
	sh(["git", "push", "-q", "--force", "origin", f"HEAD:refs/heads/{head}"], root)
	res = github.cli(
		"pr",
		"create",
		"-R",
		r,
		"--base",
		integration,
		"--head",
		head,
		"--title",
		f"chore(deps): {subject}",
		"--body",
		f"Rolled out with `frappe-nix repo rollout`.\n\n{subject}",
	)
	url = res.stdout.strip().splitlines()[-1] if res.code == 0 and res.stdout.strip() else ""
	if res.code != 0 and "already exists" not in res.stderr:
		raise EnvError(f"{r}: gh pr create: {res.stderr.strip()}")
	if not args.no_auto_merge:
		target = url or head
		merge = github.cli("pr", "merge", "-R", r, "--auto", f"--{method}", target)
		if merge.code != 0:
			print(
				f"notice: {r}: auto-merge not enabled ({merge.stderr.strip()}); run frappe-nix repo doctor",
				file=sys.stderr,
			)
	return f"{r}: {url or head}"


def run(args: argparse.Namespace) -> int:
	if not args.to and not args.profile_to:
		raise ConfigError("give --to vX.Y.Z, --profile-to <ref>|latest, or both")
	if args.to and not _VERSION.match(args.to):
		raise ConfigError(f"--to takes a release tag vX.Y.Z, not {args.to!r}")
	pairs, _fleet = targets.entries(args.repo or [], args.all, args.fleet)
	code = CLEAN
	try:
		for r, _entry in pairs:
			try:
				print(rollout_one(r, args))
			except FrappeNixError as e:
				code = DRIFT
				print(f"frappe-nix repo rollout: {r}: {e}", file=sys.stderr)
	finally:
		targets.cleanup()
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
	p.set_defaults(func=run)
