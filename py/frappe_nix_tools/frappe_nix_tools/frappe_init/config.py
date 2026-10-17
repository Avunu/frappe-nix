"""What lib/init.nix bakes into ``frappe-init``: the store paths of the presets
file and the two templates, as JSON (frappe_nix_tools.cli_config).

``frappe-init`` runs before any bench exists, so this is all it reads; it has
no dev shell and no bench configuration.
"""

import os
from dataclasses import dataclass
from typing import Any

from frappe_nix_tools.cli_config import ConfigError, load_dataclass, read_json

__all__ = ["ConfigError", "InitConfig", "load", "parse"]


@dataclass(frozen=True)
class InitConfig:
	# lib/frappe-presets.json.
	presets: str
	# templates/bench: the files a bench is given.
	template: str
	# templates/app: the files app mode writes into an app's repository.
	app_template: str


def parse(data: Any) -> InitConfig:
	return load_dataclass(InitConfig, data, "config")


def load(path: str | os.PathLike[str] | None = None) -> InitConfig:
	"""Read the file ``FRAPPE_NIX_CLI_CONFIG`` names (or PATH)."""
	return parse(read_json(path, hint="run frappe-init as `nix run github:Avunu/frappe-nix`"))
