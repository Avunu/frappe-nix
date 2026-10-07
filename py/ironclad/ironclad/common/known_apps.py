"""The sibling apps frappe-nix knows how to pin (``ironclad/data/known-apps.json``, spec §2.1)."""

import json
import re
from dataclasses import dataclass
from functools import cache

from ironclad.common import data_path
from ironclad.common.report import ConfigError

AVUNU = "Avunu/*"


@dataclass(frozen=True)
class Sibling:
	"""A sibling resolved for one Frappe major.

	``name`` is the app name, which is also the flake input name; ``spelling`` is how
	``required_apps`` writes it (``"Avunu/<repo>"`` for an Avunu app, D6).
	"""

	name: str
	spelling: str
	repo: str
	branch: str
	range: str
	desk_global: str | None

	@property
	def input(self) -> str:
		return self.name

	@property
	def flake_url(self) -> str:
		return f"github:{self.repo}/{self.branch}"


@cache
def known() -> dict:
	"""The raw templates, keyed by app name (and ``"Avunu/*"``)."""
	return json.loads(data_path("known-apps.json").read_text())


def _fill(template: str | None, major: int, name: str) -> str | None:
	if template is None:
		return None
	return template.replace("{n1}", str(major + 1)).replace("{n}", str(major)).replace("{name}", name)


def resolve(spelling: str, major: int) -> Sibling:
	"""Resolve ``frappe``, a known app or ``Avunu/<repo>`` for Frappe ``major``."""
	apps = known()
	if spelling.startswith("Avunu/"):
		name = spelling.removeprefix("Avunu/")
		template = apps[AVUNU]
	elif spelling in apps and spelling != AVUNU:
		name = spelling
		template = apps[spelling]
	else:
		raise ConfigError(f'unknown sibling {spelling!r}: not in known-apps.json and not "Avunu/<repo>"')
	if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
		raise ConfigError(f"unknown sibling {spelling!r}")
	return Sibling(
		name=name,
		spelling=spelling,
		repo=_fill(template["repo"], major, name) or "",
		branch=_fill(template["branch"], major, name) or "",
		range=_fill(template["range"], major, name) or "",
		desk_global=_fill(template["desk_global"], major, name),
	)
