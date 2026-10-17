"""The workspace steps the apps commands share: lib/scripts.nix's
registerWorkspaceMember, syncRegistry and syncBuildDeps snippets, as calls to
frappe-nix-workspace (lib/frappe-workspace.py, the one implementation of the
apps/ ⇄ pyproject.toml ⇄ sites/apps.{txt,json} contract) and uv. Each runs
from the bench root.
"""

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.config import AppsConfig


def register(config: AppsConfig, app: str) -> None:
	"""apps/APP a uv workspace member, with a [tool.uv.sources] entry keyed on
	its own [project].name (what uv resolves the member to)."""
	proc.say(f"Registering {app} in pyproject.toml workspace...")
	proc.run([config.workspace, "add-app", "--pyproject", "pyproject.toml", "--app", app])


def sync_registry(config: AppsConfig) -> None:
	"""sites/apps.txt and sites/apps.json, regenerated from the workspace members
	and the checkouts."""
	proc.run(
		[
			config.workspace,
			"sync-registry",
			"--pyproject",
			"pyproject.toml",
			"--apps-dir",
			"apps",
			"--sites-dir",
			"sites",
		]
	)


def build_deps(config: AppsConfig) -> None:
	"""After a `uv lock` or `uv sync`, the managed build-dependency block in step
	with the lock (lib/build-deps.nix); nothing when the shell has none."""
	if config.build_deps_sync:
		proc.run([config.build_deps_sync])


def uv_then_build_deps(config: AppsConfig, verb: str) -> None:
	"""`uv VERB` (uv from PATH: the dev shell's), then build_deps."""
	proc.run(["uv", verb])
	build_deps(config)
