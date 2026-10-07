"""Reading ``flake.lock``: nodes, ``follows`` paths and the GitHub pins they lock.

An app reaches frappe-nix's own pinned inputs (marketplace, pilot, frappe-semgrep-rules)
through its lock's ``frappe-nix`` node (spec S20), so a name is looked up first among the
root's inputs and then among frappe-nix's.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from ironclad.common.report import ConfigError, EnvError


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
	if not isinstance(lock, dict) or "nodes" not in lock or "root" not in lock:
		raise EnvError(f"{path} is not a flake lock file")
	return lock


def node_at(lock: dict, path: list[str]) -> tuple[str, dict] | None:
	"""Follow input names from the root node; ``None`` when the path does not exist.

	Each step's reference is either a node name or, for ``follows``, a path from the root.
	"""
	nodes = lock["nodes"]
	name = lock["root"]
	for step in path:
		ref = nodes[name].get("inputs", {}).get(step)
		if ref is None:
			return None
		if isinstance(ref, list):
			found = node_at(lock, ref)
			if found is None:
				return None
			name = found[0]
		else:
			name = ref
	return name, nodes[name]


def input_node(lock: dict, name: str) -> tuple[str, dict] | None:
	"""The node for input ``name``: the root's own, else frappe-nix's."""
	return node_at(lock, [name]) or node_at(lock, ["frappe-nix", name])


def frappe_nix_rev(lock: dict) -> str | None:
	"""The frappe-nix revision an app locks, or ``None`` (frappe-nix itself has no such input)."""
	found = node_at(lock, ["frappe-nix"])
	return found[1].get("locked", {}).get("rev") if found else None


def github_pin(lock: dict, name: str) -> Pin:
	"""The locked GitHub coordinates of input ``name``."""
	found = input_node(lock, name)
	if found is None:
		raise ConfigError(f"flake.lock has no input {name!r}, neither the app's nor frappe-nix's")
	locked = found[1].get("locked", {})
	if locked.get("type") != "github":
		raise ConfigError(f"input {name!r} is locked as {locked.get('type')!r}, not a GitHub input")
	try:
		return Pin(name, locked["owner"], locked["repo"], locked["rev"], locked["narHash"])
	except KeyError as e:
		raise EnvError(f"input {name!r} has no locked {e.args[0]}") from e
