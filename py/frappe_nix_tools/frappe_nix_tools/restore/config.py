"""The configuration Nix hands the bench command line, as typed dataclasses.

Nix writes one JSON file per dev shell and the ``bench-*`` shims point
``FRAPPE_NIX_CLI_CONFIG`` at it (PLAN.md, Track D: configuration as JSON, not
interpolation). The file is generated, so a key this code does not know, or one
it expects and does not find, is a mismatch between the Nix side and the Python
side of one frappe-nix revision: it fails loudly here rather than being guessed
at.

Keys are the Nix option names (camelCase); the fields are their snake_case.
"""

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

ENV = "FRAPPE_NIX_CLI_CONFIG"

Files = Literal["none", "private", "all"]


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
		return _load(kind, value, where)
	raise TypeError(f"config.py: no checker for {kind!r}")  # a bug here, not in the file


def _load(cls: Any, data: Any, where: str) -> Any:
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


@dataclass(frozen=True)
class RestoreConfig:
	"""``perSystem.frappe-nix.restore``, plus what the dev shell resolved for it."""

	# Fetching from the object store is on (restore.enable).
	enable: bool
	# The frappe-nix-backup-fetch command (lib/backup-fetch.nix).
	fetch: str
	# A command that decrypts the bench's secrets and prints the environment
	# they make, NUL-separated (`env -0`); null when the bench has none.
	secrets_env: str | None
	# restore.prefix: BACKUPS_PREFIX when the backup-access secret sets none.
	prefix: str
	# restore.files: which file archives come down by default.
	files: Files
	# restore.carryConfigKeys: what crosses over from production's site config.
	carry_config_keys: list[str]
	# restore.migrate: run `bench migrate` afterwards.
	migrate: bool
	# restore.neutralize: pause the scheduler and switch mail off in the
	# restored database (a fetched restore; an explicit file needs --neutralize).
	neutralize: bool
	# restore.requireDevguard: refuse to restore into an unguarded bench.
	require_devguard: bool
	# devguard.enable, at Nix time: a persistent property of the bench.
	devguard: bool


@dataclass(frozen=True)
class CliConfig:
	"""The whole file: one section per command that has configuration."""

	restore: RestoreConfig


def parse(data: Any) -> CliConfig:
	return _load(CliConfig, data, "config")


def load(path: str | os.PathLike[str] | None = None) -> CliConfig:
	"""Read the file ``FRAPPE_NIX_CLI_CONFIG`` names (or PATH)."""
	where = path if path is not None else os.environ.get(ENV)
	if not where:
		raise ConfigError(f"{ENV} is not set; run this through the dev shell's bench command")
	try:
		data = json.loads(Path(where).read_text())
	except (OSError, ValueError) as e:
		raise ConfigError(f"cannot read {where}: {e}") from e
	return parse(data)
