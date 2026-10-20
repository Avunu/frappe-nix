"""The configuration Nix hands the bench command line: one file per dev shell,
one section per command family (PLAN.md, Track D: configuration as JSON, not
interpolation).

lib/scripts.nix writes it and the ``bench-*`` shims point
``FRAPPE_NIX_CLI_CONFIG`` at it. Each section is a frozen dataclass of the
command family that reads it (frappe_nix_tools.restore.config,
frappe_nix_tools.apps.config, frappe_nix_tools.deps.config); a key this code does not know, or one it expects
and does not find, fails loudly (frappe_nix_tools.cli_config).
"""

import os
from dataclasses import dataclass
from typing import Any

from frappe_nix_tools.apps.config import AppsConfig
from frappe_nix_tools.cli_config import ENV, ConfigError, load_dataclass, read_json
from frappe_nix_tools.deps.config import DepsConfig
from frappe_nix_tools.restore.config import RestoreConfig

__all__ = ["ENV", "CliConfig", "ConfigError", "load", "parse"]


@dataclass(frozen=True)
class CliConfig:
	"""The whole file: one section per command family that has configuration."""

	restore: RestoreConfig
	apps: AppsConfig
	deps: DepsConfig


def parse(data: Any) -> CliConfig:
	return load_dataclass(CliConfig, data, "config")


def load(path: str | os.PathLike[str] | None = None) -> CliConfig:
	"""Read the file ``FRAPPE_NIX_CLI_CONFIG`` names (or PATH)."""
	return parse(read_json(path, hint="run this through the dev shell's bench command"))
