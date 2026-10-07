"""Reading ``flake.lock``: nodes, ``follows`` paths and the GitHub pins they lock.

An app reaches frappe-nix's own pinned inputs (marketplace, pilot, frappe-semgrep-rules)
through its lock's ``frappe-nix`` node (spec S20), so a name is looked up first among the
root's inputs and then among frappe-nix's.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ironclad.common.report import ConfigError, EnvError

_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_REV = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Pin:
	"""A locked GitHub input."""

	input: str
	owner: str
	repo: str
	rev: str
	nar_hash: str

	@property
	def tarball_url(self) -> str:
		return f"https://codeload.github.com/{self.owner}/{self.repo}/tar.gz/{self.rev}"


def load(path: Path) -> dict:
	"""The parsed lock file; unreadable or not a version-7 lock is an ``EnvError``."""
	try:
		lock = json.loads(path.read_text())
	except FileNotFoundError as e:
		raise EnvError(f"{path} does not exist") from e
	except (OSError, json.JSONDecodeError) as e:
		raise EnvError(f"{path} is unreadable: {e}") from e
	if (
		not isinstance(lock, dict)
		or not isinstance(lock.get("nodes"), dict)
		or not isinstance(lock.get("root"), str)
		or not all(isinstance(node, dict) for node in lock["nodes"].values())
	):
		raise EnvError(f"{path} is not a flake lock file")
	return lock


def _node(lock: dict, name: object) -> dict:
	node = lock["nodes"].get(name) if isinstance(name, str) else None
	if not isinstance(node, dict):
		raise EnvError(f"not a flake lock file: it refers to a node {name!r} it does not have")
	return node


def node_at(lock: dict, path: list[str]) -> tuple[str, dict] | None:
	"""Follow input names from the root node; ``None`` when the path does not exist.

	Each step's reference is either a node name or, for ``follows``, a path from the root.
	"""
	name = lock["root"]
	for step in path:
		inputs = _node(lock, name).get("inputs", {})
		if not isinstance(inputs, dict):
			raise EnvError(f"not a flake lock file: node {name!r} has inputs that are not a table")
		ref = inputs.get(step)
		if ref is None:
			return None
		if isinstance(ref, list):
			found = node_at(lock, ref)
			if found is None:
				return None
			name = found[0]
		else:
			name = ref
	return name, _node(lock, name)


def input_node(lock: dict, name: str) -> tuple[str, dict] | None:
	"""The node for input ``name``: the root's own, else frappe-nix's."""
	return node_at(lock, [name]) or node_at(lock, ["frappe-nix", name])


def frappe_nix_rev(lock: dict) -> str | None:
	"""The frappe-nix revision an app locks, or ``None`` (frappe-nix itself has no such input)."""
	found = node_at(lock, ["frappe-nix"])
	locked = found[1].get("locked") if found else None
	rev = locked.get("rev") if isinstance(locked, dict) else None
	return rev if isinstance(rev, str) else None


def github_pin(lock: dict, name: str) -> Pin:
	"""The locked GitHub coordinates of input ``name``."""
	found = input_node(lock, name)
	if found is None:
		raise ConfigError(f"flake.lock has no input {name!r}, neither the app's nor frappe-nix's")
	locked = found[1].get("locked", {})
	if not isinstance(locked, dict) or locked.get("type") != "github":
		kind = locked.get("type") if isinstance(locked, dict) else None
		raise ConfigError(f"input {name!r} is locked as {kind!r}, not a GitHub input")
	fields = {}
	for key in ("owner", "repo", "rev", "narHash"):
		value = locked.get(key)
		if not isinstance(value, str):
			raise EnvError(f"input {name!r} has no locked {key}")
		fields[key] = value
	# These become a URL and a directory name under .dev-dist/pins/, so a crafted lock
	# must not be able to leave it.
	for key in ("owner", "repo"):
		if not _NAME.fullmatch(fields[key]) or fields[key].startswith("."):
			raise ConfigError(f"input {name!r} locks {key} {fields[key]!r}, which is not a GitHub name")
	if not _REV.fullmatch(fields["rev"]):
		raise ConfigError(f"input {name!r} locks rev {fields['rev']!r}, which is not a commit SHA")
	return Pin(name, fields["owner"], fields["repo"], fields["rev"], fields["narHash"])
