"""The sibling apps frappe-nix knows how to pin (``frappe_nix_tools/data/known-apps.json``, spec §2.1).

A sibling is written one of three ways in ``[tool.frappe-nix] siblings``:

- a bare app name that is a key of the known apps (``"erpnext"``);
- ``"<owner>/<repo>"`` for any app on GitHub, which uses the entry of that exact key when
  one exists and otherwise the generic ``"*/*"`` rule (the Frappe convention:
  ``version-{n}`` branches and major-tracking versions);
- an object with any of ``repo``, ``flake-url``, ``branch``, ``range`` and ``desk_global``,
  whose unset fields come from the matching rule.

The known apps are ``known-apps.json``, then a profile's ``[known-apps]``, then the app's
``[tool.frappe-nix.known-apps]``, merged by key (§8.4); ``resolve`` takes the merged table.
"""

import json
import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from frappe_nix_tools.common import data_path
from frappe_nix_tools.common.report import ConfigError

GENERIC = "*/*"
_NAME = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class Sibling:
	"""A sibling resolved for one Frappe major.

	``name`` is the app name, which is also the flake input name; ``spelling`` is how
	``required_apps`` writes it (``"<owner>/<repo>"`` for an app outside the known ones, D6).
	"""

	name: str
	spelling: str
	repo: str
	branch: str
	range: str
	desk_global: str | None
	flake_url: str

	@property
	def input(self) -> str:
		return self.name


@cache
def builtin() -> dict:
	"""The raw templates shipped with frappe-nix, keyed by app name (and ``"*/*"``)."""
	return json.loads(data_path("known-apps.json").read_text())


def merged(*layers: dict | None) -> dict:
	"""``known-apps.json`` with each layer's entries added or replacing by key."""
	out = dict(builtin())
	for layer in layers:
		for key, entry in (layer or {}).items():
			out[key] = {**out.get(key, {}), **entry}
	return out


def _fill(template: Any, major: int, owner: str, name: str) -> Any:
	if not isinstance(template, str):
		return template
	return (
		template.replace("{n1}", str(major + 1))
		.replace("{n}", str(major))
		.replace("{owner}", owner)
		.replace("{name}", name)
	)


def _rule(key: str, apps: dict) -> tuple[str, str, dict]:
	"""``(owner, name, rule)`` for a bare name or an ``<owner>/<repo>`` spelling."""
	if "/" in key:
		owner, _, name = key.partition("/")
		if not _NAME.fullmatch(owner) or not _NAME.fullmatch(name) or name.startswith("."):
			raise ConfigError(f"unknown sibling {key!r}: write it as <owner>/<repo>")
		rule = apps.get(key) or apps.get(GENERIC)
		if rule is None:
			raise ConfigError(f"unknown sibling {key!r}: no known-apps entry and no {GENERIC!r} rule")
		return owner, name, rule
	if key == GENERIC or key not in apps:
		raise ConfigError(f"unknown sibling {key!r}; write it as <owner>/<repo>")
	return "", key, apps[key]


def resolve(sibling: str | dict, major: int, apps: dict | None = None) -> Sibling:
	"""Resolve one ``siblings`` entry for Frappe ``major`` against the merged known apps."""
	apps = apps if apps is not None else builtin()
	if isinstance(sibling, str):
		spelling, override = sibling, {}
	elif isinstance(sibling, dict):
		override = sibling
		spelling = sibling.get("repo")
		if not isinstance(spelling, str) or not spelling:
			raise ConfigError(f"sibling {sibling!r} needs a repo")
	else:
		raise ConfigError(f"sibling {sibling!r} is neither a name nor a table")
	# An object's repo may be off GitHub (<host>/<group>/…/<repo>): the app is its last part.
	parts = spelling.split("/")
	key = spelling if len(parts) <= 2 else f"{parts[-2]}/{parts[-1]}"
	owner, name, rule = _rule(key, apps)
	template = {**rule, **{k: v for k, v in override.items() if k != "repo"}}
	repo = override.get("repo") or _fill(rule.get("repo"), major, owner, name) or ""
	branch = _fill(template.get("branch"), major, owner, name) or ""
	flake_url = template.get("flake-url")
	if not flake_url:
		if len(parts) > 2 and not override.get("flake-url"):
			raise ConfigError(f"sibling {spelling!r} is not on GitHub: give it a flake-url")
		flake_url = f"github:{repo}/{branch}"
	return Sibling(
		name=name,
		spelling=spelling,
		repo=repo,
		branch=branch,
		range=_fill(template.get("range"), major, owner, name) or "",
		desk_global=_fill(template.get("desk_global"), major, owner, name),
		flake_url=_fill(flake_url, major, owner, name),
	)
