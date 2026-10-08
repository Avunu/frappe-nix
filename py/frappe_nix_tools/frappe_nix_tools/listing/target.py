"""The app a ``frappe-nix listing`` or ``frappe-nix icon`` command looks at, resolved once.

The same configuration sync renders with (§8.4): the profile layers, the org values and the
module switches, plus the template context (§2.3) for the facts the rules share with sync
(the listing, the siblings, the branches).
"""

from dataclasses import dataclass
from pathlib import Path

from frappe_nix_tools.common.config import Resolved
from frappe_nix_tools.scaffold import context, engine, manifest


@dataclass
class Target:
	root: Path
	app: context.App
	resolved: Resolved
	ctx: context.NS
	package: dict

	@property
	def name(self) -> str:
		return self.app.name

	@property
	def cfg(self) -> dict:
		return self.resolved.cfg

	@property
	def modules(self) -> dict[str, bool]:
		return self.resolved.modules

	@property
	def org(self) -> dict:
		return self.ctx.org

	@property
	def listing(self) -> dict:
		"""``marketplace/listing.toml`` parsed, ``{}`` when the app has none."""
		return dict(self.ctx.listing or {})


def load(root: Path, profile_dir: Path | None = None) -> Target:
	"""The app at ``root`` (a git work tree whose ``pyproject.toml`` names a package with
	``hooks.py``). Not opted in, or an invalid configuration, is exit 2 as for sync."""
	app = engine.load_app(root)
	resolved = engine.resolve(app, profile_dir=profile_dir)
	ctx = context.build(
		app, resolved, lock=engine.locked_frappe_nix(root), floors=manifest.load().floors, options={}
	)
	return Target(root, app, resolved, ctx, engine.load_package(root) or {})
