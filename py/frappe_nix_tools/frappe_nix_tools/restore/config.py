"""The configuration Nix hands the bench command line, as typed dataclasses.

Nix writes one JSON file per dev shell and the ``bench-*`` shims point
``FRAPPE_NIX_CLI_CONFIG`` at it (PLAN.md, Track D: configuration as JSON, not
interpolation). The file is generated, so a key this code does not know, or one
it expects and does not find, is a mismatch between the Nix side and the Python
side of one frappe-nix revision: it fails loudly here rather than being guessed
at.

Keys are the Nix option names (camelCase); the fields are their snake_case.
"""

import os
from dataclasses import dataclass
from typing import Any, Literal

from frappe_nix_tools.cli_config import ENV, ConfigError, load_dataclass, read_json

__all__ = ["ENV", "CliConfig", "ConfigError", "Files", "RestoreConfig", "load", "parse"]

Files = Literal["none", "private", "all"]


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
	return load_dataclass(CliConfig, data, "config")


def load(path: str | os.PathLike[str] | None = None) -> CliConfig:
	"""Read the file ``FRAPPE_NIX_CLI_CONFIG`` names (or PATH)."""
	return parse(read_json(path, hint="run this through the dev shell's bench command"))
