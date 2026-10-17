"""``frappe-nix repo apply``: settings, labels and rulesets from ``repo-policy/`` (spec §5.6, §4.8, §4.9, S40).

For each target it resolves the configuration (``targets.load``), skips it with a notice
when ``repo-policy.enable`` is false (making no API call), and otherwise reads what the
repository has, compares it with ``common.repo_policy``'s rendering and writes only what
differs: ``PATCH /repos/{r}``, ``PUT …/actions/permissions/workflow``, the
vulnerability-alert and automated-security-fix switches, the labels, and the rulesets
matched by name (``PUT`` when one differs, ``POST`` when it is missing; unknown ones are
listed, never deleted). ``--dry-run`` reads but never writes, and prints a JSON diff per
repository.

Exit codes: 0 applied or already matching; 1 some repository failed (the rest continue);
2 a configuration error.
"""

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from frappe_nix_tools.commands.repo import targets
from frappe_nix_tools.common import github, repo_policy
from frappe_nix_tools.common.github import ACTIONS_APP_ID, GitHubError
from frappe_nix_tools.common.report import CLEAN, DRIFT, INVALID, ConfigError, FrappeNixError

# frappe-nix's own repository, the target of ``--self``.
SELF_REPO = "Avunu/frappe-nix"
PRIVATE_RULESETS = (
	"its plan does not support rulesets on private repositories (GitHub Free returns 403), so the"
	" required checks would be missing: make it public, or move it to a Team or Enterprise plan"
)
BYPASS_FALLBACKS = (
	"GitHub refused GitHub Actions (Integration 15368) as a bypass actor. The fallbacks, in order:\n"
	'  1. repo-policy.release-bypass = "user:<id>": a machine user bypasses version-*, and the'
	" fast-forward uses its token (VERSION_BRANCH_TOKEN);\n"
	"  2. a DeployKey bypass, through an org profile override of templates/repo-policy/rulesets/release-branch.json"
	" (needs deploy keys enabled for the organisation)."
)


@dataclass
class Change:
	call: str
	body: Any = None
	diff: dict = field(default_factory=dict)


@dataclass
class Outcome:
	repo: str
	status: str = "ok"
	changes: list[Change] = field(default_factory=list)
	notices: list[str] = field(default_factory=list)
	unknown_rulesets: list[str] = field(default_factory=list)
	error: str = ""
	code: int = CLEAN

	def as_json(self) -> dict:
		return {
			"repo": self.repo,
			"status": self.status,
			"changes": [{"call": c.call, "diff": c.diff} for c in self.changes],
			"unknown_rulesets": self.unknown_rulesets,
			"notices": self.notices,
			"error": self.error,
		}


def _diff(want: dict, have: dict, prefix: str = "") -> dict:
	"""``{dotted key: [have, want]}`` for every wanted key the repository doesn't match."""
	out: dict = {}
	for key, value in want.items():
		cur = (have or {}).get(key)
		name = f"{prefix}{key}"
		if isinstance(value, dict) and isinstance(cur, dict):
			out.update(_diff(value, cur, f"{name}."))
		elif cur != value:
			out[name] = [cur, value]
	return out


def _rule_key(rule: dict) -> str:
	return rule.get("type", "")


def normalise(ruleset: dict, like: dict | None = None) -> dict:
	"""A ruleset reduced to what ``like`` (the wanted one) sets, rules sorted by type, so a
	GitHub response compares equal to the JSON that created it."""
	like = like or ruleset
	out: dict = {}
	for key in ("name", "target", "enforcement", "conditions", "bypass_actors"):
		if key in like:
			out[key] = ruleset.get(key)
	if "conditions" in out and isinstance(out["conditions"], dict):
		ref = out["conditions"].get("ref_name") or {}
		out["conditions"] = {
			"ref_name": {"include": sorted(ref.get("include", [])), "exclude": sorted(ref.get("exclude", []))}
		}
	if "bypass_actors" in out:
		out["bypass_actors"] = sorted(
			(
				{k: a.get(k) for k in ("actor_type", "actor_id", "bypass_mode")}
				for a in out["bypass_actors"] or []
			),
			key=lambda a: json.dumps(a, sort_keys=True),
		)
	wanted_rules = {_rule_key(r): r for r in like.get("rules", [])}
	rules = []
	for rule in sorted(ruleset.get("rules", []), key=_rule_key):
		want = wanted_rules.get(_rule_key(rule), {})
		params = rule.get("parameters")
		item: dict = {"type": rule.get("type")}
		if params is not None or "parameters" in want:
			keys = want.get("parameters", params or {}).keys()
			item["parameters"] = {k: (params or {}).get(k) for k in keys}
			checks = item["parameters"].get("required_status_checks")
			if isinstance(checks, list):
				item["parameters"]["required_status_checks"] = sorted(
					(
						{"context": c.get("context"), "integration_id": c.get("integration_id")}
						for c in checks
					),
					key=lambda c: c["context"] or "",
				)
			methods = item["parameters"].get("allowed_merge_methods")
			if isinstance(methods, list):
				item["parameters"]["allowed_merge_methods"] = sorted(methods)
		rules.append(item)
	if "rules" in like:
		out["rules"] = rules
	return out


def plan_policy(t: targets.Target, phase: str, documentation: str) -> repo_policy.Policy:
	return repo_policy.for_app(
		t.cfg,
		t.modules,
		t.gates,
		phase=phase,
		release_branch=t.release_branch if t.opted_in else "",
		documentation=documentation,
		overrides=(t.profile_dir / "templates") if t.profile_dir else None,
	)


def documentation_url(t: targets.Target) -> str:
	"""``{documentation}`` (§4.9): the listing's, else the fleet entry's, else nothing."""
	if t.modules.get("listing") and t.root is not None:
		path = t.root / "marketplace" / "listing.toml"
		if path.is_file():
			try:
				doc = tomllib.loads(path.read_text())
			except (OSError, tomllib.TOMLDecodeError):
				doc = {}
			url = doc.get("documentation")
			if isinstance(url, str) and url:
				return url
	return t.entry.get("documentation", "")


def _write(
	out: Outcome, dry_run: bool, method: str, endpoint: str, body: Any = None, diff: Any = None
) -> Any:
	out.changes.append(Change(f"{method} {endpoint}", body, diff if diff is not None else {}))
	if dry_run:
		return None
	return github.request(method, endpoint, body)


def apply_settings(out: Outcome, r: str, policy: repo_policy.Policy, dry_run: bool) -> None:
	info = github.get(f"repos/{r}") or {}
	if policy.repository:
		diff = _diff(policy.repository, info)
		if diff:
			_write(out, dry_run, "PATCH", f"repos/{r}", policy.repository, diff)
	if policy.workflow_permissions:
		have = github.get(f"repos/{r}/actions/permissions/workflow") or {}
		diff = _diff(policy.workflow_permissions, have)
		if diff:
			_write(
				out,
				dry_run,
				"PUT",
				f"repos/{r}/actions/permissions/workflow",
				policy.workflow_permissions,
				diff,
			)
	if policy.vulnerability_alerts is not None:
		try:
			github.get(f"repos/{r}/vulnerability-alerts")
			enabled = True
		except GitHubError as e:
			if e.status != 404:
				raise
			enabled = False
		if enabled != policy.vulnerability_alerts:
			method = "PUT" if policy.vulnerability_alerts else "DELETE"
			_write(
				out,
				dry_run,
				method,
				f"repos/{r}/vulnerability-alerts",
				None,
				{"enabled": [enabled, not enabled]},
			)
	if policy.automated_security_fixes is not None:
		have = (github.get_or_none(f"repos/{r}/automated-security-fixes") or {}).get("enabled", False)
		if have != policy.automated_security_fixes:
			method = "PUT" if policy.automated_security_fixes else "DELETE"
			_write(
				out,
				dry_run,
				method,
				f"repos/{r}/automated-security-fixes",
				None,
				{"enabled": [have, policy.automated_security_fixes]},
			)
	if policy.labels:
		have_labels = {lb["name"].lower(): lb for lb in github.get(f"repos/{r}/labels", paginate=True) or []}
		for label in policy.labels:
			cur = have_labels.get(label["name"].lower())
			if cur is None:
				_write(out, dry_run, "POST", f"repos/{r}/labels", label, {"label": [None, label["name"]]})
			else:
				diff = _diff({"color": label["color"], "description": label["description"]}, cur)
				if diff:
					_write(out, dry_run, "PATCH", f"repos/{r}/labels/{cur['name']}", label, diff)


def apply_rulesets(out: Outcome, r: str, policy: repo_policy.Policy, dry_run: bool, private: bool) -> None:
	try:
		existing = github.get(f"repos/{r}/rulesets", paginate=True) or []
	except GitHubError as e:
		if private and e.status == 403:
			raise FrappeNixError(f"{r}: {PRIVATE_RULESETS}") from e
		raise
	by_name = {rs.get("name"): rs for rs in existing}
	wanted = {rs["name"] for rs in policy.rulesets}
	out.unknown_rulesets = sorted(n for n in by_name if n not in wanted)
	for ruleset in policy.rulesets:
		cur = by_name.get(ruleset["name"])
		try:
			if cur is None:
				_write(
					out, dry_run, "POST", f"repos/{r}/rulesets", ruleset, {"ruleset": [None, ruleset["name"]]}
				)
				continue
			full = github.get(f"repos/{r}/rulesets/{cur['id']}") or {}
			have, want = normalise(full, ruleset), normalise(ruleset)
			if have != want:
				diff = {k: [have.get(k), want.get(k)] for k in want if have.get(k) != want.get(k)}
				_write(out, dry_run, "PUT", f"repos/{r}/rulesets/{cur['id']}", ruleset, diff)
		except GitHubError as e:
			if private and e.status == 403:
				raise FrappeNixError(f"{r}: {PRIVATE_RULESETS}") from e
			bypass = any(
				a.get("actor_type") == "Integration" and a.get("actor_id") == ACTIONS_APP_ID
				for a in ruleset.get("bypass_actors", [])
			)
			if e.status == 422 and bypass:
				raise FrappeNixError(f"{r}: {BYPASS_FALLBACKS}") from e
			raise


def ensure_integration_branch(out: Outcome, r: str, integration: str, rename: bool, dry_run: bool) -> None:
	"""§5.6: the settings point ``default_branch`` at the integration branch, which must exist."""
	if not integration:
		return
	if github.get_or_none(f"repos/{r}/branches/{integration}") is not None:
		return
	info = github.get(f"repos/{r}") or {}
	default = info.get("default_branch", "")
	if not rename:
		raise FrappeNixError(
			f"{r}: the integration branch {integration!r} does not exist; rename {default!r} with"
			f" `frappe-nix repo apply {r} --rename-default-branch` (or gh api -X POST repos/{r}/branches/{default}/rename -f new_name={integration})"
		)
	_write(
		out,
		dry_run,
		"POST",
		f"repos/{r}/branches/{default}/rename",
		{"new_name": integration},
		{"default_branch": [default, integration]},
	)


def apply_one(r: str, entry: dict, fleet: dict, args: argparse.Namespace, policy_dir: Path | None) -> Outcome:
	out = Outcome(r)
	if policy_dir is not None:
		policy = repo_policy.from_dir(policy_dir)
		if not args.rulesets_only:
			apply_settings(out, r, policy, args.dry_run)
		if not args.settings_only:
			apply_rulesets(out, r, policy, args.dry_run, bool(entry.get("private")))
		return out
	if args.phase == "full" and entry.get("private"):
		out.status, out.code = "failed", DRIFT
		out.error = f"{r} is private in the fleet list: make it public before --phase full"
		return out
	t = targets.load(r, entry, fleet, args.profile)
	if t.resolved is None:
		out.status = "skipped"
		out.notices.append(f"{r} has no [tool.frappe-nix] and no profile (--profile or the fleet's): skipped")
		return out
	if not t.modules.get("repo-policy"):
		out.status = "skipped"
		out.notices.append(f"{r}: repo-policy is off in its resolved configuration; nothing applied (S40)")
		return out
	if args.phase == "full" and not t.opted_in:
		out.status, out.code = "invalid", INVALID
		out.error = f"{r} has no [tool.frappe-nix] yet: only --phase provisional applies until it opts in"
		return out
	policy = plan_policy(t, args.phase, documentation_url(t))
	if not args.rulesets_only:
		if "default_branch" in policy.repository:
			ensure_integration_branch(out, r, t.integration, args.rename_default_branch, args.dry_run)
		apply_settings(out, r, policy, args.dry_run)
	if not args.settings_only:
		info = github.get(f"repos/{r}") or {}
		apply_rulesets(out, r, policy, args.dry_run, bool(info.get("private") or entry.get("private")))
	return out


def run(args: argparse.Namespace) -> int:
	if args.settings_only and args.rulesets_only:
		raise ConfigError("--settings-only and --rulesets-only exclude each other")
	if args.self and args.policy_dir:
		raise ConfigError("--self and --policy-dir exclude each other")
	policy_dir: Path | None = None
	if args.self:
		policy_dir = repo_policy.self_dir()
		if not args.repos and not args.all:
			args.repos = [SELF_REPO]
	elif args.policy_dir:
		policy_dir = Path(args.policy_dir)
		if not policy_dir.is_dir():
			raise ConfigError(f"--policy-dir {policy_dir}: not a directory")
	pairs, fleet = targets.entries(args.repos, args.all, args.fleet)
	code = CLEAN
	results = []
	try:
		for r, entry in pairs:
			try:
				out = apply_one(r, entry, fleet, args, policy_dir)
			except ConfigError as e:
				out = Outcome(r, "invalid", error=str(e), code=INVALID)
			except FrappeNixError as e:
				out = Outcome(r, "failed", error=str(e), code=DRIFT)
			code = max(code, out.code)
			results.append(out)
			for notice in out.notices:
				print(f"notice: {notice}", file=sys.stderr)
			if out.error:
				print(f"frappe-nix repo apply: {out.error}", file=sys.stderr)
			if args.dry_run:
				print(json.dumps(out.as_json(), indent=2))
			elif out.status == "ok":
				done = len(out.changes)
				print(f"{r}: {'applied ' + str(done) + ' change(s)' if done else 'already matching'}")
				if out.unknown_rulesets:
					print(
						f"{r}: rulesets frappe-nix does not manage (left alone): {', '.join(out.unknown_rulesets)}"
					)
	finally:
		targets.cleanup()
	return code


def register(sub: argparse._SubParsersAction) -> None:
	p = sub.add_parser(
		"apply",
		help="apply repo-policy/ settings and rulesets (admin; repo-policy module)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("repos", nargs="*", metavar="owner/repo")
	p.add_argument("--all", action="store_true", help="every repository in --fleet")
	p.add_argument("--fleet", help="the fleet list (an org profile repository's fleet.json)")
	p.add_argument("--phase", choices=("provisional", "full"), default="full")
	p.add_argument("--profile", help="the profile for a repository without [tool.frappe-nix]")
	p.add_argument("--rename-default-branch", action="store_true")
	p.add_argument("--self", action="store_true", help="frappe-nix's own repo-policy/self/")
	p.add_argument("--policy-dir", help="read the settings and rulesets from this directory")
	p.add_argument("--dry-run", action="store_true", help="print a JSON diff per repository; change nothing")
	p.add_argument("--settings-only", action="store_true")
	p.add_argument("--rulesets-only", action="store_true")
	p.set_defaults(func=run)
