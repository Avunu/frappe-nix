"""Reading the JSON configuration Nix generates for frappe-nix's command line.

Nix writes the options and store paths a command needs to one file and its
wrapper points ``FRAPPE_NIX_CLI_CONFIG`` at it (PLAN.md, Track D: configuration
as JSON, not interpolation). Each command reads the file into a frozen
dataclass of its own (frappe_nix_tools.restore.config,
frappe_nix_tools.frappe_init.config). The file is generated, so a key the
dataclass does not know, or one it expects and does not find, is a mismatch
between the Nix side and the Python side of one frappe-nix revision: it fails
loudly here rather than being guessed at.

Keys are the Nix option names (camelCase); the fields are their snake_case.
"""

import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

ENV = "FRAPPE_NIX_CLI_CONFIG"


class ConfigError(Exception):
	"""The generated configuration is missing, unreadable or of the wrong shape."""


def _camel(name: str) -> str:
	head, *rest = name.split("_")
	return head + "".join(part.title() for part in rest)


def _check(value: Any, kind: Any, where: str) -> Any:
	origin = get_origin(kind)
	if origin is Literal:
		if value not in get_args(kind):
			raise ConfigError(f"{where}: {value!r} is not one of {', '.join(map(repr, get_args(kind)))}")
		return value
	if origin is list:
		(item,) = get_args(kind)
		if not isinstance(value, list):
			raise ConfigError(f"{where}: expected a list, got {type(value).__name__}")
		return [_check(v, item, f"{where}[{i}]") for i, v in enumerate(value)]
	if kind == str | None:
		if value is not None and not isinstance(value, str):
			raise ConfigError(f"{where}: expected a string or null, got {type(value).__name__}")
		return value
	if kind is bool:
		if not isinstance(value, bool):
			raise ConfigError(f"{where}: expected true or false, got {type(value).__name__}")
		return value
	if kind is str:
		if not isinstance(value, str):
			raise ConfigError(f"{where}: expected a string, got {type(value).__name__}")
		return value
	if isinstance(kind, type) and hasattr(kind, "__dataclass_fields__"):
		return load_dataclass(kind, value, where)
	raise TypeError(f"cli_config.py: no checker for {kind!r}")  # a bug here, not in the file


def load_dataclass(cls: Any, data: Any, where: str) -> Any:
	"""DATA as an instance of the dataclass CLS: every field present, nothing else."""
	if not isinstance(data, dict):
		raise ConfigError(f"{where}: expected an object, got {type(data).__name__}")
	wanted = {_camel(f.name): f for f in fields(cls)}
	unknown = sorted(set(data) - set(wanted))
	missing = sorted(set(wanted) - set(data))
	if unknown:
		raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
	if missing:
		raise ConfigError(f"{where}: missing key(s): {', '.join(missing)}")
	return cls(**{f.name: _check(data[key], f.type, f"{where}.{key}") for key, f in wanted.items()})


def read_json(path: str | os.PathLike[str] | None, *, hint: str) -> Any:
	"""The JSON in PATH, or in the file ``FRAPPE_NIX_CLI_CONFIG`` names.

	HINT says how the command is meant to be run, for when the variable is unset.
	"""
	where = path if path is not None else os.environ.get(ENV)
	if not where:
		raise ConfigError(f"{ENV} is not set; {hint}")
	try:
		return json.loads(Path(where).read_text())
	except (OSError, ValueError) as e:
		raise ConfigError(f"cannot read {where}: {e}") from e
