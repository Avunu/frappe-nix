"""``bench restore``'s section of the configuration Nix hands the bench command
line (frappe_nix_tools.bench_config), as a typed dataclass.

Nix writes one JSON file per dev shell and the ``bench-*`` shims point
``FRAPPE_NIX_CLI_CONFIG`` at it (configuration as JSON, not
interpolation). The file is generated, so a key this code does not know, or one
it expects and does not find, is a mismatch between the Nix side and the Python
side of one frappe-nix revision: it fails loudly here rather than being guessed
at.

Keys are the Nix option names (camelCase); the fields are their snake_case.
"""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from frappe_nix_tools.cli_config import ENV, ConfigError

if TYPE_CHECKING:
	from frappe_nix_tools.bench_config import CliConfig

__all__ = ["ENV", "ConfigError", "Files", "RestoreConfig", "load", "parse"]

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


def parse(data: Any) -> "CliConfig":
	"""The whole file (frappe_nix_tools.bench_config), whose `restore` is this."""
	from frappe_nix_tools.bench_config import parse as parse_all

	return parse_all(data)


def load(path: str | os.PathLike[str] | None = None) -> "CliConfig":
	"""Read the file ``FRAPPE_NIX_CLI_CONFIG`` names (or PATH)."""
	from frappe_nix_tools.bench_config import load as load_all

	return load_all(path)
