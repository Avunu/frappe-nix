"""The dependency commands' configuration: their section of the bench
configuration (frappe_nix_tools.bench_config), and the relock app's own file
(lib/relock.nix), which runs outside the dev shell.

Keys are the Nix names (camelCase); the fields are their snake_case.
"""

from dataclasses import dataclass

__all__ = ["DepsConfig", "RelockConfig"]


@dataclass(frozen=True)
class DepsConfig:
	"""What bench-update, update-deps and bench setup requirements need of the
	shell."""

	# The apps with a package.json, as Nix saw them (lib/bench.nix).
	apps_with_node: list[str]
	# This project is one Frappe app and the bench under it is generated.
	app_mode: bool
	# App mode: where the repository keeps its locks (frappe-nix.app.lockDir),
	# for messages.
	lock_dir: str
	# frappe-nix-node-verify (lib/node-verify.nix).
	node_verify: str
	# frappe-nix-node-modules (lib/node-modules.nix).
	node_modules: str
	# frappe-nix-node-locks (lib/node-locks.nix).
	node_locks: str
	# perSystem.frappe-nix.nodeNestedFrontendExcludes: what node-locks leaves alone.
	node_nested_frontend_excludes: list[str]
	# lib/requirements-check.py, run with the apps section's python.
	requirements_check: str


@dataclass(frozen=True)
class RelockConfig:
	"""`nix run .#relock` (lib/relock.nix)."""

	app_mode: bool
	# frappe-nix-node-locks.
	node_locks: str
	node_nested_frontend_excludes: list[str]
	# lib/build-deps.nix's sync, after a `uv lock` in a bench; null runs nothing.
	build_deps_sync: str | None
	# App mode: the assembled workspace without its lock; null in a bench.
	workspace: str | None
	# App mode: where the repository keeps its locks (frappe-nix.app.lockDir).
	lock_dir: str
