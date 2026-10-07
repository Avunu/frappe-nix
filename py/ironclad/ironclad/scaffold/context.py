"""The template context (spec §2.3) and the app it describes.

Every template, every manifest ``when`` and every entry ``path`` sees the same names:
``app``, ``app_hyphen``, ``dist``, ``repo``, ``title``, ``tagline``, ``frappe``,
``siblings``, ``site``, ``cfg``, ``discover``, ``frappe_nix``, ``floors``, ``app_type``,
plus ``version`` (the app's ``__version__``), ``required_apps``, ``first_commit_year``
and ``options`` (the sync flags a ``when`` may read, such as ``init_listing``).

Nothing here reads the clock: the only date is the year of the first commit (§3.4).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ironclad
from ironclad.common import known_apps, repo
from ironclad.common.report import ConfigError, EnvError
from ironclad.scaffold import discover, hooks

# A string literal on the __version__ line, in any of the three forms sync reads (§2.13).
VERSION_LINE = re.compile(r"^__version__\s*=\s*(['\"])(?P<v>[^'\"]*)\1", re.M)

# Python per Frappe major: the frappe-nix preset's requiresPython (lib/frappe-presets.json).
PYTHON = {15: "3.12", 16: "3.14", 17: "3.14"}


class NS(dict):
	"""A dict whose keys also read as attributes, for ``when`` expressions and templates."""

	def __getattr__(self, name: str) -> Any:
		try:
			return self[name]
		except KeyError as e:
			raise AttributeError(name) from e


def ns(value: Any) -> Any:
	"""``value`` with every dict turned into an ``NS``, recursively."""
	if isinstance(value, dict):
		return NS({k: ns(v) for k, v in value.items()})
	if isinstance(value, list):
		return [ns(v) for v in value]
	return value


def frappe_nix_major(ver: str) -> int:
	"""The frappe-nix major the flake tracks (``release-<major>``): 1 for the pre-1.0 builds."""
	head = ver.split(".", 1)[0]
	return max(1, int(head)) if head.isdigit() else 1


def python_for(major: int) -> str:
	if major not in PYTHON:
		raise ConfigError(f"frappe-major {major} is not one this frappe-nix knows (one of {sorted(PYTHON)})")
	return PYTHON[major]


def app_version(root: Path, app: str) -> str | None:
	"""``__version__`` from ``<app>/__init__.py``, whatever form it is written in."""
	try:
		text = (root / app / "__init__.py").read_text()
	except FileNotFoundError:
		return None
	m = VERSION_LINE.search(text)
	return m["v"] if m else None


class ShallowHistory:
	"""``first_commit_year`` in a shallow clone, whose oldest commit is the shallow boundary
	rather than the first one: any use fails (exit 3) instead of rendering a wrong year that
	a full clone would then report as drift. Nothing reads it until a template does."""

	MESSAGE = (
		"first_commit_year needs the full git history, and this clone is shallow:"
		" fetch it with `git fetch --unshallow` (actions/checkout: fetch-depth: 0)"
	)

	def _fail(self, *_args: object) -> Any:
		raise EnvError(self.MESSAGE)

	__eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = _fail
	__hash__ = object.__hash__

	__str__ = __int__ = __index__ = __bool__ = __format__ = _fail

	def __repr__(self) -> str:
		return "ShallowHistory()"


def first_commit_year(root: Path) -> int | ShallowHistory | None:
	"""The year of the first commit, which never changes (§2.19); ``None`` before one exists."""
	try:
		if repo.git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
			return ShallowHistory()
		out = repo.git(root, "log", "--reverse", "--format=%cs")
	except Exception:
		return None
	first = out.split("\n", 1)[0].strip()
	return int(first[:4]) if first[:4].isdigit() else None


@dataclass
class App:
	"""An app checkout: its root, package name, parsed files and tracked paths."""

	root: Path
	name: str
	pyproject: dict
	hooks: dict
	tracked: list[str]

	@property
	def hyphen(self) -> str:
		return self.name.replace("_", "-")


def build(app: App, cfg: dict, *, rev: str | None, floors: dict, options: dict | None = None) -> NS:
	"""The context for ``app`` with the validated ``cfg`` and the frappe-nix ``rev``."""
	major = int(cfg["frappe-major"])
	python_for(major)
	facts = discover.facts(app.root, app.name, cfg, app.tracked)
	listing = facts.pop("listing")
	required = hooks.required_apps(app.hooks)
	required_bare = {hooks.bare(r) for r in required}
	frappe_sib = known_apps.resolve("frappe", major)
	siblings = []
	for spelling in cfg.get("siblings", []):
		s = known_apps.resolve(spelling, major)
		siblings.append(
			{
				"name": s.name,
				"spelling": s.spelling,
				"input": s.input,
				"flake_url": s.flake_url,
				"repo": s.repo,
				"branch": s.branch,
				"range": s.range,
				"desk_global": s.desk_global,
				"required": s.name in required_bare,
			}
		)
	title = listing.get("title") or app.hooks.get("app_title") or app.name
	tagline = listing.get("tagline") or app.hooks.get("app_description") or ""
	ver = ironclad.__version__
	return ns(
		{
			"app": app.name,
			"app_hyphen": app.hyphen,
			"dist": app.pyproject.get("project", {}).get("name", app.name),
			"repo": repo.github_repo(app.root, app.name),
			"title": str(title),
			"tagline": str(tagline),
			"frappe": {
				"major": major,
				"next": major + 1,
				"branch": frappe_sib.branch,
				"preset": f"version-{major}",
				"range": frappe_sib.range,
				"python": python_for(major),
			},
			"siblings": siblings,
			"required_apps": required,
			"site": cfg.get("site") or f"{app.hyphen}.localhost",
			"cfg": cfg,
			"discover": facts,
			"listing": listing,
			"frappe_nix": {"rev": rev or "", "version": ver, "major": frappe_nix_major(ver)},
			"floors": floors,
			"app_type": facts["app_type"],
			"version": app_version(app.root, app.name),
			"first_commit_year": first_commit_year(app.root),
			"options": options or {},
		}
	)
