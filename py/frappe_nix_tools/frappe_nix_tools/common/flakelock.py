"""Reading ``flake.lock``: nodes, ``follows`` paths and the pins they lock.

A pin is a ``github``, ``gitlab`` or ``git`` lock node (spec §5.2, S43). An app reaches frappe-nix's own pinned inputs (marketplace, pilot, frappe-semgrep-rules)
through its lock's ``frappe-nix`` node (spec S20), so a name is looked up first among the
root's inputs and then among frappe-nix's. Those three are the exception: CI runs the
registry checks and the semgrep rules from them, so they are read only through the
``frappe-nix`` node (or the root of frappe-nix's own lock) and must name the repository
frappe-nix pins (``FRAPPE_NIX_PINS``). A lock that retargets one, or shadows it with a root
input of the same name, is a ``ConfigError`` rather than a different set of rules.
``standards-profile`` is the other way round: only the app's own root input counts (§8.3).
"""

import json
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from frappe_nix_tools.common.report import ConfigError, EnvError

_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# A GitLab owner may be a group path, which a flake URL spells with %2F.
_GITLAB_OWNER = re.compile(r"[A-Za-z0-9_.-]+(?:(?:/|%2F)[A-Za-z0-9_.-]+)*")
_HOST = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]+)?")
_REV = re.compile(r"[0-9a-f]{40}")

# The inputs frappe-nix pins for every app (spec S20), and the repository each must lock.
FRAPPE_NIX_PINS: dict[str, tuple[str, str]] = {
	"frappe-semgrep-rules": ("frappe", "semgrep-rules"),
	"marketplace": ("frappe", "marketplace"),
	"pilot": ("frappe", "pilot"),
}

# The org profile input (S38): read only from the app's own root.
PROFILE_INPUT = "standards-profile"

PIN_TYPES = ("github", "gitlab", "git")


@dataclass(frozen=True)
class Pin:
	"""A locked ``github``, ``gitlab`` or ``git`` input.

	``owner``/``repo`` name it on GitHub or GitLab (``owner`` may be a GitLab group path);
	``host`` is GitLab's host (``gitlab.com`` by default); ``url`` is a ``git`` input's
	clone URL. ``name`` is the directory stem under ``.dev-dist/pins/``.
	"""

	input: str
	type: str
	rev: str
	nar_hash: str
	owner: str = ""
	repo: str = ""
	host: str = ""
	url: str = ""

	@property
	def name(self) -> str:
		if self.type == "git":
			return self.url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "source"
		return self.repo

	@property
	def label(self) -> str:
		"""How messages name the source: ``owner/repo`` or the clone URL."""
		if self.type == "git":
			return self.url
		return f"{self.owner.replace('%2F', '/')}/{self.repo}"

	@property
	def tarball_url(self) -> str:
		"""Where a ``github`` or ``gitlab`` pin's tarball is (§5.2)."""
		if self.type == "github":
			return f"https://codeload.github.com/{self.owner}/{self.repo}/tar.gz/{self.rev}"
		if self.type == "gitlab":
			project = urllib.parse.quote(f"{self.owner.replace('%2F', '/')}/{self.repo}", safe="")
			return f"https://{self.host}/api/v4/projects/{project}/repository/archive.tar.gz?sha={self.rev}"
		raise ValueError(f"a {self.type} pin has no tarball")


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
	"""The node for input ``name``: the root's own, else frappe-nix's.

	A ``FRAPPE_NIX_PINS`` input is frappe-nix's only: in an app's lock (one with a
	``frappe-nix`` input) the root's input of that name is never consulted. And
	``standards-profile`` is the app's only: frappe-nix's is never consulted.
	"""
	if name in FRAPPE_NIX_PINS and node_at(lock, ["frappe-nix"]) is not None:
		return node_at(lock, ["frappe-nix", name])
	if name == PROFILE_INPUT:
		return node_at(lock, [name])
	return node_at(lock, [name]) or node_at(lock, ["frappe-nix", name])


def frappe_nix_rev(lock: dict) -> str | None:
	"""The frappe-nix revision an app locks, or ``None`` (frappe-nix itself has no such input)."""
	found = node_at(lock, ["frappe-nix"])
	locked = found[1].get("locked") if found else None
	rev = locked.get("rev") if isinstance(locked, dict) else None
	return rev if isinstance(rev, str) else None


def _field(name: str, locked: dict, key: str) -> str:
	value = locked.get(key)
	if not isinstance(value, str):
		raise EnvError(f"input {name!r} has no locked {key}")
	return value


def locked_pin(lock: dict, name: str) -> Pin:
	"""The locked coordinates of input ``name``: a ``github``, ``gitlab`` or ``git`` node."""
	found = input_node(lock, name)
	if found is None:
		raise ConfigError(f"flake.lock has no input {name!r}, neither the app's nor frappe-nix's")
	locked = found[1].get("locked", {})
	kind = locked.get("type") if isinstance(locked, dict) else None
	if not isinstance(locked, dict) or kind not in PIN_TYPES:
		raise ConfigError(f"input {name!r} is locked as {kind!r}, not a github, gitlab or git input")
	rev = _field(name, locked, "rev")
	nar_hash = _field(name, locked, "narHash")
	if not _REV.fullmatch(rev):
		raise ConfigError(f"input {name!r} locks rev {rev!r}, which is not a commit SHA")
	if kind == "git":
		url = _field(name, locked, "url")
		parts = urllib.parse.urlsplit(url.removeprefix("git+"))
		if parts.scheme not in ("https", "http", "ssh", "file") or not parts.path.strip("/"):
			raise ConfigError(f"input {name!r} locks url {url!r}, which is not a git URL")
		pin = Pin(name, kind, rev, nar_hash, url=url.removeprefix("git+"))
		if not _NAME.fullmatch(pin.name) or pin.name.startswith("."):
			raise ConfigError(f"input {name!r} locks url {url!r}, whose last part is not a repository name")
		return pin
	owner = _field(name, locked, "owner")
	repo = _field(name, locked, "repo")
	host = locked.get("host") or ("gitlab.com" if kind == "gitlab" else "github.com")
	# These become a URL and a directory name under .dev-dist/pins/, so a crafted lock
	# must not be able to leave it.
	owner_ok = (_GITLAB_OWNER if kind == "gitlab" else _NAME).fullmatch(owner)
	if not owner_ok or owner.startswith("."):
		raise ConfigError(f"input {name!r} locks owner {owner!r}, which is not a {kind} name")
	if not _NAME.fullmatch(repo) or repo.startswith("."):
		raise ConfigError(f"input {name!r} locks repo {repo!r}, which is not a {kind} name")
	if not isinstance(host, str) or not _HOST.fullmatch(host):
		raise ConfigError(f"input {name!r} locks host {host!r}, which is not a host name")
	expected = FRAPPE_NIX_PINS.get(name)
	# GitHub names are case-insensitive, and a flake URL may spell them either way.
	if expected and (kind != "github" or (owner.lower(), repo.lower()) != expected):
		raise ConfigError(
			f"input {name!r} locks {kind}:{owner}/{repo}, but frappe-nix pins it to github:{'/'.join(expected)}"
		)
	return Pin(name, kind, rev, nar_hash, owner=owner, repo=repo, host=host)


def locked_rev(lock: dict, name: str) -> str | None:
	"""The locked revision of input ``name`` (the app's own, else frappe-nix's), or ``None``."""
	found = input_node(lock, name)
	locked = found[1].get("locked") if found else None
	rev = locked.get("rev") if isinstance(locked, dict) else None
	return rev if isinstance(rev, str) else None
