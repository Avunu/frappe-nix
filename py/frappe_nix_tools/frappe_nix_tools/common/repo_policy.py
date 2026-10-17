"""Repo policy: the settings and rulesets ``frappe-nix repo apply`` wants for an app (spec §4.8, §4.9, S40).

The defaults are ``frappe_nix_tools/data/repo-policy/``: ``repo-settings.json`` and
``rulesets/{integration,integration-provisional,release-branch,tags}.json``, JSON with
``{placeholders}`` filled from the app's resolved configuration. An org profile replaces any
of them with ``templates/repo-policy/<same path>`` (§8.1, ``overridable``). ``--policy-dir``
(an org profile repository's own policy) and ``--self`` (frappe-nix's ``repo-policy/self/``)
read another directory, whose rulesets are applied as they are.

A string value that is exactly ``"{name}"`` becomes the typed value (a list, a number, a
boolean); a placeholder inside a longer string is substituted as text. ``{<method> in
merge_methods}`` is whether ``repo-policy.merge-methods`` lists that method. A placeholder
nothing fills is exit 2.
"""

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from frappe_nix_tools.common import data_path
from frappe_nix_tools.common.github import ACTIONS_APP_ID
from frappe_nix_tools.common.report import ConfigError

# The order §4.8 lists the required contexts in.
GATE_ORDER = ("pr-policy", "lint", "typecheck", "test", "marketplace")
SETTINGS = "repo-settings.json"
_WHOLE = re.compile(r"^\{([a-z_]+(?: in [a-z_]+)?)\}$")
_PART = re.compile(r"\{([a-z_]+(?: in [a-z_]+)?)\}")
# Labels and the modules that use them (§4.9).
LABEL_MODULES = {"dependencies": "dependabot", "needs-review": "dependabot", "screenshots": "screenshots"}


@dataclass
class Policy:
	"""What a repository should look like: the settings parts and the rulesets, by name."""

	repository: dict = field(default_factory=dict)
	workflow_permissions: dict | None = None
	vulnerability_alerts: bool | None = None
	automated_security_fixes: bool | None = None
	labels: list[dict] = field(default_factory=list)
	rulesets: list[dict] = field(default_factory=list)


def default_dir() -> Path:
	return data_path("repo-policy")


def self_dir() -> Path:
	"""frappe-nix's own policy (``repo-policy/self``, N6)."""
	try:
		return data_path("repo-policy/self")
	except ConfigError as e:
		raise ConfigError("this frappe-nix has no repo-policy/self/ (frappe-nix's own policy)") from e


def _read(path: Path) -> Any:
	try:
		return json.loads(path.read_text())
	except FileNotFoundError as e:
		raise ConfigError(f"{path} does not exist") from e
	except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
		raise ConfigError(f"{path}: {e}") from e


def read_file(rel: str, *, directory: Path | None = None, overrides: Path | None = None) -> Any:
	"""``rel`` from ``overrides/repo-policy/`` when the org profile has it, else ``directory``
	(default: the built-in policy)."""
	if overrides is not None:
		over = overrides / "repo-policy" / rel
		if over.is_symlink():
			raise ConfigError(f"the profile's templates/repo-policy/{rel} is a symlink")
		if over.is_file():
			return _read(over)
	return _read((directory or default_dir()) / rel)


def ruleset_files(directory: Path) -> list[Path]:
	"""A policy directory's rulesets: ``rulesets/*.json``, else every ``*.json`` beside
	``repo-settings.json`` (an org profile repository's ``repo-policy/main.json``, …)."""
	sub = directory / "rulesets"
	if sub.is_dir():
		return sorted(sub.glob("*.json"))
	return sorted(p for p in directory.glob("*.json") if p.name != SETTINGS)


def fill(value: Any, values: dict[str, Any], where: str) -> Any:
	"""``value`` with every placeholder replaced (module docstring)."""
	if isinstance(value, dict):
		return {k: fill(v, values, where) for k, v in value.items()}
	if isinstance(value, list):
		return [fill(v, values, where) for v in value]
	if not isinstance(value, str):
		return value

	def lookup(name: str) -> Any:
		method, _, listed = name.partition(" in ")
		if listed:
			if listed not in values:
				raise ConfigError(f"{where}: no value for {{{name}}}")
			return method in values[listed]
		if name not in values:
			raise ConfigError(f"{where}: no value for {{{name}}}")
		return values[name]

	whole = _WHOLE.match(value)
	if whole:
		return copy.deepcopy(lookup(whole[1]))
	return _PART.sub(lambda m: str(lookup(m[1])), value)


def required_checks(cfg: dict, gates: list[str]) -> list[dict]:
	"""``{required_checks}`` (§4.8): one context per required gate, in §4.8's order."""
	want = cfg.get("repo-policy", {}).get("required-checks", "gates")
	if want == "gates":
		names = list(gates)
	else:
		extra = [g for g in want if g not in gates]
		if extra:
			raise ConfigError(
				f"repo-policy.required-checks names {extra}, which are not enabled gates ({', '.join(gates) or 'none'})"
			)
		names = list(want)
	return [{"context": f"ci / {g}", "integration_id": ACTIONS_APP_ID} for g in GATE_ORDER if g in names]


def bypass_actors(release_bypass: str) -> list[dict]:
	"""``repo-policy.release-bypass`` as ``bypass_actors`` (§4.8)."""
	if release_bypass == "github-actions":
		return [{"actor_type": "Integration", "actor_id": ACTIONS_APP_ID, "bypass_mode": "always"}]
	m = re.fullmatch(r"user:(\d+)", release_bypass or "")
	if m:
		return [{"actor_type": "User", "actor_id": int(m[1]), "bypass_mode": "always"}]
	raise ConfigError(
		f'repo-policy.release-bypass is "github-actions" or "user:<id>", not {release_bypass!r}'
	)


def values(cfg: dict, gates: list[str], documentation: str = "") -> dict[str, Any]:
	"""The placeholder values of an app's resolved configuration."""
	rp = cfg.get("repo-policy", {})
	rel = cfg.get("releases", {})
	prefix = rel.get("tag-prefix", "v")
	return {
		"integration_branch": cfg["integration-branch"],
		"merge_methods": list(rp.get("merge-methods", ["squash"])),
		"required_approvals": rp.get("required-approvals", 1),
		"require_code_owner_review": rp.get("require-code-owner-review", False),
		"require_thread_resolution": rp.get("require-thread-resolution", True),
		"delete_branch_on_merge": rp.get("delete-branch-on-merge", True),
		"required_checks": required_checks(cfg, gates),
		"release_bypass": bypass_actors(rp.get("release-bypass", "github-actions")),
		"tag_prefix": prefix,
		"tag_pattern": f"refs/tags/{prefix}*" if prefix else "refs/tags/[0-9]*",
		"documentation": documentation,
	}


def _settings(doc: dict, vals: dict[str, Any], cfg: dict, modules: dict, where: str) -> Policy:
	rp = cfg.get("repo-policy", {})
	repository = dict(doc.get("repository", {}))
	if not rp.get("manage-default-branch", True):
		repository.pop("default_branch", None)
	if not rp.get("manage-features", True):
		repository.pop("has_wiki", None)
		repository.pop("has_projects", None)
	if not vals.get("documentation"):
		repository.pop("homepage", None)
	if not rp.get("secret-scanning", True):
		repository.pop("security_and_analysis", None)
	labels = [
		label
		for label in doc.get("labels", [])
		if modules.get(LABEL_MODULES.get(label.get("name", ""), ""), label.get("name") not in LABEL_MODULES)
	]
	return Policy(
		repository=fill(repository, vals, where),
		workflow_permissions=fill(doc.get("actions_workflow_permissions"), vals, where),
		vulnerability_alerts=doc.get("vulnerability_alerts"),
		automated_security_fixes=doc.get("automated_security_fixes"),
		labels=fill(labels, vals, where),
	)


def _strip_status_checks(ruleset: dict) -> dict:
	"""The ruleset without a ``required_status_checks`` rule that lists nothing (§4.8: no gates)."""
	rules = [
		r
		for r in ruleset.get("rules", [])
		if not (r.get("type") == "required_status_checks" and not r["parameters"]["required_status_checks"])
	]
	return {**ruleset, "rules": rules}


def for_app(
	cfg: dict,
	modules: dict,
	gates: list[str],
	*,
	phase: str,
	release_branch: str,
	documentation: str = "",
	overrides: Path | None = None,
) -> Policy:
	"""The built-in (or org-overridden) policy for an app (§4.8, §4.9, §5.6 step 6)."""
	vals = values(cfg, gates, documentation)
	policy = _settings(read_file(SETTINGS, overrides=overrides), vals, cfg, modules, SETTINGS)
	names = ["integration-provisional.json"] if phase == "provisional" else ["integration.json"]
	if phase == "full":
		if release_branch:
			names.append("release-branch.json")
		if modules.get("releases"):
			names.append("tags.json")
	for name in names:
		rel = f"rulesets/{name}"
		policy.rulesets.append(_strip_status_checks(fill(read_file(rel, overrides=overrides), vals, rel)))
	return policy


def from_dir(directory: Path, vals: dict[str, Any] | None = None) -> Policy:
	"""A whole policy directory (``--policy-dir``, ``--self``), every ruleset in it applied."""
	vals = vals or {}
	settings = _read(directory / SETTINGS) if (directory / SETTINGS).is_file() else {}
	policy = Policy(
		repository=fill(settings.get("repository", {}), vals, SETTINGS),
		workflow_permissions=fill(settings.get("actions_workflow_permissions"), vals, SETTINGS),
		vulnerability_alerts=settings.get("vulnerability_alerts"),
		automated_security_fixes=settings.get("automated_security_fixes"),
		labels=fill(settings.get("labels", []), vals, SETTINGS),
	)
	for path in ruleset_files(directory):
		policy.rulesets.append(_strip_status_checks(fill(_read(path), vals, path.name)))
	return policy
