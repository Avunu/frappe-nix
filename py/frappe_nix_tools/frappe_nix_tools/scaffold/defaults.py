"""The changed-defaults warning for an app on the floating ``recommended`` (spec S42, §5.13).

Plain ``recommended`` means the newest ``recommended@<minor>`` snapshot of the running
frappe-nix-tools. When a frappe-nix bump moves it to a newer snapshot, sync (and so
``frappe-nix repo rollout``) prints each value that changed and suggests pinning the old
snapshot, so the bump PR says in its own log which defaults moved. Exit codes don't change.

The snapshot the app resolved to at ``HEAD`` is the newest one at or below the frappe-nix
version ``HEAD`` ran: the ``# vX.Y.Z`` comment of ``HEAD``'s caller workflows. Without one
(no ``ci`` module yet) there is nothing to compare, and nothing is printed.
"""

import re
import sys
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from frappe_nix_tools.common import config, repo
from frappe_nix_tools.common.report import EnvError

_CALLER = re.compile(r"/\.github/workflows/app-[A-Za-z0-9_-]+\.ya?ml@[0-9a-f]{40}[ \t]+#[ \t]*v(?P<ver>\S+)")


def head_version(root: Path) -> Version | None:
	"""The frappe-nix version ``HEAD``'s caller workflows name, when they name one."""
	try:
		names = repo.git(root, "ls-tree", "--name-only", "HEAD", ".github/workflows/").split()
	except EnvError:
		return None
	for name in sorted(names):
		try:
			text = repo.git(root, "show", f"HEAD:./{name}")
		except EnvError:
			continue
		m = _CALLER.search(text)
		if m:
			try:
				return Version(m["ver"])
			except InvalidVersion:
				continue
	return None


def snapshot_at(version: Version) -> str | None:
	"""The ``recommended@<minor>`` that plain ``recommended`` meant in frappe-nix ``version``."""
	names = [n for n in config.builtin_names() if n.startswith("recommended@")]
	older = [
		n
		for n in names
		if Version(n.removeprefix("recommended@")) <= Version(f"{version.major}.{version.minor}")
	]
	return max(older, key=lambda n: Version(n.removeprefix("recommended@"))) if older else None


def _flat(doc: Any, prefix: str = "") -> dict[str, Any]:
	if isinstance(doc, dict):
		out: dict[str, Any] = {}
		for key, value in doc.items():
			if prefix == "" and key in ("name", "description", "schema"):
				continue
			out.update(_flat(value, f"{prefix}.{key}" if prefix else key))
		return out
	return {prefix: doc}


def changes(old: str, new: str) -> list[str]:
	"""Each value that differs between two built-in snapshots, as ``key: old → new``."""
	a = _flat(config.builtin_profile(old)[1])
	b = _flat(config.builtin_profile(new)[1])
	return [f"{k}: {a.get(k)!r} → {b.get(k)!r}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def warn_changed(root: Path, plan: Any) -> list[str]:
	"""Print (and return) the warning when ``recommended`` moved since ``HEAD``."""
	if plan.cfg.get("profile") != "recommended":
		return []
	at_head = head_version(root)
	if at_head is None:
		return []
	old = snapshot_at(at_head)
	new = config.builtin_name("recommended")
	if old is None or old == new:
		return []
	lines = [
		f'profile = "recommended" now resolves to {new} (it was {old} at HEAD); changed defaults:',
		*(f"  {c}" for c in changes(old, new)),
		f'pin profile = "{old}" in [tool.frappe-nix] to keep the old defaults',
	]
	for line in lines:
		print(f"frappe-nix sync: warning: {line}", file=sys.stderr)
	return lines
