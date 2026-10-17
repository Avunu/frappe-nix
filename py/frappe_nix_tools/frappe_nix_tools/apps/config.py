"""The apps commands' section of the bench configuration
(frappe_nix_tools.bench_config). The root sync reads its own file
(frappe_nix_tools.apps.root_sync).

Keys are the Nix names (camelCase); the fields are their snake_case.
"""

from dataclasses import dataclass

from frappe_nix_tools.cli_config import ConfigError

__all__ = ["AppPair", "AppsConfig", "ConfigError"]


@dataclass(frozen=True)
class AppPair:
	"""One entry of frappe-nix.renamedApps or replacedApps: OLD = NEW."""

	old: str
	new: str


@dataclass(frozen=True)
class AppsConfig:
	"""What get-app, remove-app, new-app and reconcile-apps need of the shell."""

	# The bench CLI itself (devPythonEnv/bin/bench), not the dev shell's wrapper.
	bench: str
	# The bench's interpreter, which runs frappe_rename_app.py.
	python: str
	# frappe-nix-workspace (lib/workspace-tool.nix): the apps/ ⇄ pyproject.toml ⇄
	# sites/apps.{txt,json} contract.
	workspace: str
	# frappe-nix-sources (python-devops-nix's lib.sources): an app's first clone.
	sources: str
	# lib/build-deps.nix's sync, after a `uv lock` or `uv sync`; null runs nothing.
	build_deps_sync: str | None
	# lib/rename/frappe_rename_app.py.
	rename_tool: str
	# frappe-nix.renamedApps and replacedApps, in name order.
	renamed_apps: list[AppPair]
	replaced_apps: list[AppPair]
