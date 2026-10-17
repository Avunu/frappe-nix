"""An app checkout's resolved configuration and its gates (spec §2.3 ``gates``, §4).

The CI steps (``frappe-nix ci …``), ``frappe-nix gate`` and the ``repo`` group all need the
same two answers sync renders the callers from: the resolved configuration (§8.4) and the
list of enabled gates, in the order ``pr-policy``, ``lint``, ``typecheck``, ``test``,
``marketplace``. They are computed exactly as sync computes them, so a ruleset's required
contexts and a rendered ``ci.yml`` can never disagree.
"""

from dataclasses import dataclass
from pathlib import Path

from frappe_nix_tools.common import config, repo
from frappe_nix_tools.common.config import Resolved


@dataclass
class AppConfig:
	root: Path
	app: str
	resolved: Resolved
	gates: list[str]

	@property
	def cfg(self) -> dict:
		return self.resolved.cfg

	@property
	def modules(self) -> dict[str, bool]:
		return self.resolved.modules

	@property
	def integration(self) -> str:
		return self.cfg["integration-branch"]

	@property
	def release_branch(self) -> str:
		"""``branches.release``: ``version-<major>`` under ``develop+version`` with ``releases`` on, else ""."""
		if self.modules.get("releases") and self.cfg["releases"]["branching"] == "develop+version":
			return f"version-{self.cfg['frappe-major']}"
		return ""


def gates_for(root: Path, app: str, resolved: Resolved) -> list[str]:
	"""The enabled gates of the app at ``root``, as sync's context computes them."""
	from frappe_nix_tools.scaffold import context, discover

	tracked = repo.ls_files(root)
	facts = discover.facts(root, app, resolved.cfg, tracked, resolved.modules)
	return context.gates(resolved.modules, resolved.cfg, facts)


def load(root: Path, *, lock_path: Path | None = None, profile_dir: Path | None = None) -> AppConfig:
	"""The app at ``root``: raises ``config.NotOptedIn`` (exit 2) without ``[tool.frappe-nix]``."""
	root = root.resolve()
	resolved = config.resolve(root / "pyproject.toml", lock_path=lock_path, profile_dir=profile_dir)
	app = repo.app_name(root)
	return AppConfig(root, app, resolved, gates_for(root, app, resolved))
