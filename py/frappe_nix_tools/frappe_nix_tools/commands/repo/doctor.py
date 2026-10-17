"""``frappe-nix repo doctor``: the repository settings the enabled modules rely on (spec §5.6).

Read-only, and needs no admin. For the app in the current directory (or a cloned
``<owner/repo>``) it lists each setting as ``ok``, ``missing`` or ``unknown`` (not readable
with this token), with where to change it:

- ``releases``: Actions may create pull requests (release-please); under
  ``develop+version``, GitHub Actions may move ``version-*`` (a ruleset bypass, or no
  ruleset restricting updates there);
- ``dependabot`` with ``auto-merge``: ``allow_auto_merge``, and every enabled gate's
  ``ci / <gate>`` context required on the integration branch (otherwise deps.yml labels
  instead of merging, §4.5);
- ``screenshots`` and ``dependabot``: their labels;
- ``listing.publish``: the ``REGISTRY_TOKEN`` secret and the registry fork;
- an org profile in a private repository: ``FRAPPE_NIX_FETCH_TOKEN`` (Actions, and
  Dependabot with ``dependabot`` on).

With ``ci.schedules.ci`` or ``release`` off while auto-merge is on, it also prints the
catch-up caveat of §4.7 as a notice. Exit 0 when nothing is ``missing``, else 1.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from frappe_nix_tools.commands.repo import targets
from frappe_nix_tools.common import appgates, github, repo
from frappe_nix_tools.common.github import ACTIONS_APP_ID, GitHubError
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError

OK, MISSING, UNKNOWN = "ok", "missing", "unknown"


@dataclass
class Row:
	module: str
	setting: str
	status: str
	where: str


def _status(fn) -> str:
	"""``fn()``'s verdict, or ``unknown`` when GitHub refuses to say."""
	try:
		return OK if fn() else MISSING
	except GitHubError:
		return UNKNOWN


def _secret(r: str, name: str, kind: str = "actions") -> str:
	"""Whether the repository (or its organisation, for this repository) has the secret."""
	try:
		github.get(f"repos/{r}/{kind}/secrets/{name}")
		return OK
	except GitHubError as e:
		if e.status not in (404,):
			return UNKNOWN
	if kind == "actions":
		try:
			org = github.get(f"repos/{r}/actions/organization-secrets") or {}
		except GitHubError:
			return UNKNOWN
		if any(s.get("name") == name for s in org.get("secrets", [])):
			return OK
	return MISSING


def _release_branch_movable(r: str, release: str) -> str:
	"""Whether GitHub Actions may move ``release``: no ruleset restricts updates there, or a
	ruleset that does lets integration 15368 bypass it."""
	try:
		rules = github.get(f"repos/{r}/rules/branches/{release}", paginate=True) or []
	except GitHubError:
		return UNKNOWN
	ids = {rule.get("ruleset_id") for rule in rules if rule.get("type") == "update"}
	ids.discard(None)
	if not ids:
		return OK
	for rid in ids:
		try:
			rs = github.get(f"repos/{r}/rulesets/{rid}") or {}
		except GitHubError:
			return UNKNOWN
		if "bypass_actors" not in rs:
			return UNKNOWN
		if not any(
			a.get("actor_type") == "Integration" and a.get("actor_id") == ACTIONS_APP_ID
			for a in rs["bypass_actors"]
		):
			return MISSING
	return OK


def rows_for(r: str, app: appgates.AppConfig) -> tuple[list[Row], list[str]]:
	cfg, modules = app.cfg, app.modules
	rows: list[Row] = []
	notices: list[str] = []
	info: dict = {}
	try:
		info = github.get(f"repos/{r}") or {}
	except GitHubError:
		info = {}
	settings = f"https://github.com/{r}/settings"
	if modules.get("releases"):
		try:
			perms = github.get(f"repos/{r}/actions/permissions/workflow") or {}
			status = OK if perms.get("can_approve_pull_request_reviews") else MISSING
		except GitHubError:
			status = UNKNOWN
		rows.append(
			Row(
				"releases",
				"Actions may create pull requests (release-please)",
				status,
				f"{settings}/actions → Workflow permissions",
			)
		)
		if app.release_branch:
			rows.append(
				Row(
					"releases",
					f"GitHub Actions may fast-forward {app.release_branch}",
					_release_branch_movable(r, app.release_branch),
					f"{settings}/rules (a bypass for GitHub Actions on version-*)",
				)
			)
	auto_merge = modules.get("dependabot") and cfg.get("dependabot", {}).get("auto-merge", True)
	if auto_merge:
		allowed = info.get("allow_auto_merge")
		rows.append(
			Row(
				"dependabot",
				"auto-merge is allowed",
				UNKNOWN if allowed is None else (OK if allowed else MISSING),
				f"{settings} → Pull Requests → Allow auto-merge",
			)
		)
		contexts, known = github.required_contexts(r, app.integration)
		wanted = set(github.gate_contexts(app.gates))
		status = UNKNOWN if not known else (OK if wanted and wanted <= contexts else MISSING)
		rows.append(
			Row(
				"dependabot",
				f"{app.integration} requires {', '.join(sorted(wanted)) or 'the gate checks'}",
				status,
				f"{settings}/rules (or `frappe-nix repo apply` with repo-policy on)",
			)
		)
		schedules = cfg.get("ci", {}).get("schedules", {})
		if modules.get("ci") and (not schedules.get("ci") or not schedules.get("release")):
			notices.append(
				"ci.schedules.ci or ci.schedules.release is off while auto-merge is on: a GITHUB_TOKEN"
				" auto-merge raises no push, so the integration branch's CI status and the release PR"
				" catch up only on the next human push (§4.7)"
			)
	labels = []
	if modules.get("dependabot"):
		labels += ["dependencies", "needs-review"]
	if modules.get("screenshots"):
		labels.append("screenshots")
	if labels:
		try:
			have = {lb["name"].lower() for lb in github.get(f"repos/{r}/labels", paginate=True) or []}
			for name in labels:
				rows.append(
					Row(
						"labels",
						f"label {name}",
						OK if name in have else MISSING,
						"deps.yml and nightly create it on first use, or `frappe-nix repo apply`",
					)
				)
		except GitHubError:
			rows += [Row("labels", f"label {n}", UNKNOWN, f"{settings}/labels") for n in labels]
	listing = cfg.get("listing", {})
	if modules.get("listing") and listing.get("publish"):
		rows.append(
			Row(
				"listing",
				"secret REGISTRY_TOKEN",
				_secret(r, "REGISTRY_TOKEN"),
				f"{settings}/secrets/actions",
			)
		)
		fork = listing.get("registry-fork", "")
		rows.append(
			Row(
				"listing",
				f"registry fork {fork or '(listing.registry-fork is empty)'}",
				_status(lambda: bool(fork) and github.get_or_none(f"repos/{fork}") is not None),
				"listing.registry-fork in the profile",
			)
		)
	source = app.resolved.profile.get("source", "builtin")
	if source.startswith("github:"):
		owner_repo = "/".join(source.removeprefix("github:").split("/")[:2])
		try:
			prof = github.get_or_none(f"repos/{owner_repo}")
			private = prof is None or bool(prof.get("private"))
		except GitHubError:
			private = True
		if private:
			rows.append(
				Row(
					"ci",
					"secret FRAPPE_NIX_FETCH_TOKEN (private profile)",
					_secret(r, "FRAPPE_NIX_FETCH_TOKEN"),
					f"{settings}/secrets/actions",
				)
			)
			if modules.get("dependabot"):
				rows.append(
					Row(
						"dependabot",
						"Dependabot secret FRAPPE_NIX_FETCH_TOKEN",
						_secret(r, "FRAPPE_NIX_FETCH_TOKEN", "dependabot"),
						f"{settings}/secrets/dependabot",
					)
				)
	return rows, notices


def _repo_slug(app: appgates.AppConfig) -> str:
	slug = app.cfg.get("repo") or ""
	if slug:
		return slug
	origin = repo.origin_repo(app.root)
	if origin and origin[0] == "github.com":
		return origin[1]
	raise ConfigError(
		"cannot tell which GitHub repository this is: set [tool.frappe-nix] repo, or give <owner/repo>"
	)


def run(args: argparse.Namespace) -> int:
	try:
		if args.repo:
			t = targets.load(args.repo, {}, {})
			if not t.opted_in or t.app is None:
				raise ConfigError(f"{args.repo} has no [tool.frappe-nix]: nothing to check")
			app, r = t.app, args.repo
		else:
			app = appgates.load(Path(args.root or "."))
			r = _repo_slug(app)
		rows, notices = rows_for(r, app)
	finally:
		targets.cleanup()
	if args.format == "json":
		print(
			json.dumps(
				{"repo": r, "rows": [row.__dict__ for row in rows], "notices": notices},
				indent=2,
			)
		)
	else:
		if not rows:
			print(f"{r}: no enabled module relies on a repository setting")
		for row in rows:
			print(f"{row.status:8} {row.module:11} {row.setting}  ({row.where})")
		for n in notices:
			print(f"notice: {n}")
	return DRIFT if any(row.status == MISSING for row in rows) else CLEAN


def register(sub: argparse._SubParsersAction) -> None:
	p = sub.add_parser(
		"doctor",
		help="which repository settings the enabled modules rely on (read-only)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("repo", nargs="?", metavar="owner/repo")
	p.add_argument("--format", choices=("text", "json"), default="text")
	p.add_argument("--root", help=argparse.SUPPRESS)
	p.set_defaults(func=run)
