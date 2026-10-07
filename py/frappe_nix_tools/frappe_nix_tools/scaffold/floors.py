"""Floors: versions dependabot moves, which sync never lowers (spec S9, §3.2).

- a pre-commit ``rev:`` of a third-party hook (``rev(url, floor)`` in a template);
- a third-party ``uses: owner/repo/path@<sha> # <version>`` in a caller workflow
  (``pin(…)``), kept once present;
- a ``devDependencies`` range in ``package.json``: its minimum must be at least the floor;
- a package version in ``tools/uv.lock``.
"""

import re
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version

from frappe_nix_tools.common.report import ConfigError


def version(text: str) -> Version | None:
	"""``text`` as a version, a leading ``v`` stripped; ``None`` when it is not one."""
	try:
		return Version(text.strip().removeprefix("v"))
	except InvalidVersion:
		return None


def at_least(text: str, floor: str) -> bool:
	have, want = version(text), version(floor)
	return have is not None and want is not None and have >= want


def rev(current: str | None, url: str, floor: str) -> str:
	"""The ``rev:`` of the ``repo: <url>`` hook: the current one when it is at least ``floor``."""
	if current:
		m = re.search(rf"^\s*-\s*repo:\s*{re.escape(url)}\s*\n\s*rev:\s*['\"]?([^\s'\"#]+)", current, re.M)
		if m and at_least(m[1], floor):
			return m[1]
	return floor


def pin(current: str | None, action: str, sha: str, ver: str) -> str:
	"""``<action>@<sha> # <version>``: the pin already in the file for ``action`` wins."""
	if current:
		m = re.search(rf"{re.escape(action)}@([0-9a-f]{{40}})(?:[ \t]+#[ \t]*(\S+))?", current)
		if m:
			return f"{action}@{m[1]}" + (f" # {m[2]}" if m[2] else "")
	return f"{action}@{sha} # {ver}"


_RANGE = re.compile(r"^\s*(?P<op>\^|~|>=|=)?\s*v?(?P<v>\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?)\s*$")


def range_minimum(spec: str) -> Version | None:
	"""The minimum of a caret, tilde, ``>=`` or exact npm range; ``None`` for any other form."""
	m = _RANGE.match(spec)
	return version(m["v"]) if m else None


def range_ok(spec: str, floor: str) -> bool:
	"""Whether the app's range is a caret or exact range whose minimum is at least ``floor``."""
	m = _RANGE.match(spec)
	if not m or m["op"] not in (None, "^", "="):
		return False
	have, want = version(m["v"]), version(floor)
	return have is not None and want is not None and have >= want


def lock_versions(path: Path) -> dict[str, str]:
	"""``{package: version}`` from a ``uv.lock``; a missing lock is empty."""
	try:
		doc = tomllib.loads(path.read_text())
	except FileNotFoundError:
		return {}
	except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
		raise ConfigError(f"{path.name}: {e}") from e
	return {
		p["name"]: p.get("version", "") for p in doc.get("package", []) if isinstance(p, dict) and "name" in p
	}


def lock_shortfalls(path: Path, floors: dict[str, str]) -> list[str]:
	"""The packages whose locked version is below its floor, or that the lock lacks."""
	locked = lock_versions(path)
	return sorted(pkg for pkg, floor in floors.items() if not at_least(locked.get(pkg, ""), floor))
