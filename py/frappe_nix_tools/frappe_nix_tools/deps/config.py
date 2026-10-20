"""The dependency commands' section of the bench configuration
(frappe_nix_tools.bench_config): ``bench setup requirements`` today.

Keys are the Nix names (camelCase); the fields are their snake_case.
"""

from dataclasses import dataclass

__all__ = ["DepsConfig"]


@dataclass(frozen=True)
class DepsConfig:
	"""What the dependency commands need of the shell."""

	# The apps with a package.json, as Nix saw them (lib/bench.nix).
	apps_with_node: list[str]
	# This project is one Frappe app and the bench under it is generated.
	app_mode: bool
	# frappe-nix-node-verify (lib/node-verify.nix).
	node_verify: str
	# frappe-nix-node-modules (lib/node-modules.nix).
	node_modules: str
	# lib/requirements-check.py, run with the apps section's python.
	requirements_check: str
