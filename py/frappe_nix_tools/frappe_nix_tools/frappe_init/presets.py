"""lib/frappe-presets.json, read and checked (python-devops-nix docs/conventions.md,
Scaffolder: presets file).

One object per Frappe series, keyed by the name ``--version`` takes::

    {
        "version-16": {
            "label": "…",
            "branch": "version-16",
            "python": "python314",
            "requiresPython": ">=3.14",
            "nodejs": "nodejs_24",
            "overrideDependencies": ["click~=8.3.1"],
        }
    }

``label``, ``branch``, ``python`` and ``requiresPython`` are required;
``nodejs`` and ``overrideDependencies`` are optional in the shared schema.
frappe-nix builds Node assets, so a frappe-nix preset without ``nodejs`` is
refused when it is chosen (Preset.node).
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED = ("label", "branch", "python", "requiresPython")
OPTIONAL = ("nodejs", "overrideDependencies")


class PresetError(Exception):
	"""The presets file is not of the documented shape."""


@dataclass(frozen=True)
class Preset:
	key: str
	label: str
	branch: str
	# A nixpkgs attribute: python314.
	python: str
	requires_python: str
	# A nixpkgs attribute: nodejs_24. None when the preset names none.
	nodejs: str | None = None
	override_dependencies: tuple[str, ...] = ()

	@property
	def pynum(self) -> str:
		return self.python.removeprefix("python")

	@property
	def pytag(self) -> str:
		"""ruff's target-version: py314."""
		return f"py{self.pynum}"

	@property
	def pyver(self) -> str:
		"""The version the attribute names: 3.14."""
		return f"{self.pynum[:1]}.{self.pynum[1:]}"

	@property
	def node(self) -> str:
		"""The nodejs attribute; frappe-nix cannot build a bench without one."""
		if not self.nodejs:
			raise PresetError(f"preset '{self.key}' names no nodejs, which a frappe-nix bench needs")
		return self.nodejs

	@property
	def node_version(self) -> str:
		"""What the plan prints: 24 for nodejs_24."""
		return self.node.removeprefix("nodejs_")

	@property
	def overrides_json(self) -> str:
		"""overrideDependencies as one line of JSON, which is also a TOML array."""
		return json.dumps(list(self.override_dependencies), separators=(",", ":"), ensure_ascii=False)


def _string(value: Any, where: str) -> str:
	if not isinstance(value, str) or not value:
		raise PresetError(f"{where}: expected a non-empty string, got {value!r}")
	return value


def parse(data: Any) -> dict[str, Preset]:
	"""DATA as the presets, in the file's order."""
	if not isinstance(data, dict) or not data:
		raise PresetError("the presets file must be a non-empty object of presets")
	presets: dict[str, Preset] = {}
	for key, entry in data.items():
		where = f"presets.{key}"
		if not isinstance(entry, dict):
			raise PresetError(f"{where}: expected an object, got {type(entry).__name__}")
		unknown = sorted(set(entry) - set(REQUIRED) - set(OPTIONAL))
		missing = [k for k in REQUIRED if k not in entry]
		if unknown:
			raise PresetError(f"{where}: unknown key(s): {', '.join(unknown)}")
		if missing:
			raise PresetError(f"{where}: missing key(s): {', '.join(missing)}")
		python = _string(entry["python"], f"{where}.python")
		if not re.fullmatch(r"python3\d+", python):
			raise PresetError(f"{where}.python: expected a nixpkgs python3xx attribute, got {python!r}")
		nodejs = entry.get("nodejs")
		if nodejs is not None:
			_string(nodejs, f"{where}.nodejs")
		overrides = entry.get("overrideDependencies", [])
		if not isinstance(overrides, list) or not all(isinstance(o, str) for o in overrides):
			raise PresetError(f"{where}.overrideDependencies: expected a list of strings")
		presets[key] = Preset(
			key=key,
			label=_string(entry["label"], f"{where}.label"),
			branch=_string(entry["branch"], f"{where}.branch"),
			python=python,
			requires_python=_string(entry["requiresPython"], f"{where}.requiresPython"),
			nodejs=nodejs,
			override_dependencies=tuple(overrides),
		)
	return presets


def load(path: str | Path) -> dict[str, Preset]:
	try:
		data = json.loads(Path(path).read_text())
	except (OSError, ValueError) as e:
		raise PresetError(f"cannot read {path}: {e}") from e
	return parse(data)
