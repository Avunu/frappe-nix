"""The template context (spec §2.3) and the app it describes.

Every template, every manifest ``when`` and every entry ``path`` sees the same names:
``app``, ``app_hyphen``, ``dist``, ``repo``, ``title``, ``tagline``, ``frappe``,
``siblings``, ``site``, ``cfg``, ``modules``, ``org``, ``profile``, ``branches``,
``gates``, ``discover``, ``frappe_nix``, ``floors``, ``app_type``, plus ``version`` (the
app's ``__version__``), ``required_apps``, ``first_commit_year``, ``options`` (the sync
flags a ``when`` may read, such as ``init_listing``) and the facts the shared files are
gated on (``precommit_live``, ``tools_deps``, ``package_json_live``, ``profile_input``).

Nothing here reads the clock: the only date is the year of the first commit (§3.4).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frappe_nix_tools
from frappe_nix_tools.common import known_apps, repo
from frappe_nix_tools.common.config import ORG_PROFILE_PREFIXES, Resolved
from frappe_nix_tools.common.report import ConfigError, EnvError
from frappe_nix_tools.scaffold import discover, hooks, manifest

# A string literal on the __version__ line, in any of the three forms sync reads (§2.13).
VERSION_LINE = re.compile(r"^__version__\s*=\s*(['\"])(?P<v>[^'\"]*)\1", re.M)

# Python per Frappe major: the frappe-nix preset's requiresPython (lib/frappe-presets.json).
PYTHON = {15: "3.12", 16: "3.14", 17: "3.14"}

# The default frappe-nix source when the locked node names none (§2.2 `frappe_nix`).
FRAPPE_NIX_REPO = "Avunu/frappe-nix"
FRAPPE_NIX_OWNER, FRAPPE_NIX_NAME = FRAPPE_NIX_REPO.split("/")

# The gates of §4, in the order the context lists them (§2.3).
GATES = ("pr-policy", "lint", "typecheck", "test", "marketplace")

# Modules the compat hook runs rules for (§2.14): any of them puts it in the pre-commit config.
COMPAT_MODULES = ("metadata", "releases", "vite-register", "tests", "typescript", "hygiene")


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


def default_frappe_nix_url() -> str:
	return f"github:{FRAPPE_NIX_REPO}/release-{frappe_nix_major(frappe_nix_tools.__version__)}"


def frappe_nix_url(cfg: dict) -> str:
	"""The URL ``flake.nix`` gives the frappe-nix input: ``dev-shell.frappe-nix-url`` or the release branch."""
	return cfg.get("dev-shell", {}).get("frappe-nix-url") or default_frappe_nix_url()


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


def siblings(cfg: dict, major: int, required_bare: set[str] | None = None) -> list[dict]:
	"""``cfg["siblings"]`` resolved against the merged known apps (§2.1)."""
	apps = known_apps.merged(cfg.get("known-apps"))
	out = []
	for spelling in cfg.get("siblings", []):
		s = known_apps.resolve(spelling, major, apps)
		out.append(
			{
				"name": s.name,
				"spelling": s.spelling,
				"input": s.input,
				"flake_url": s.flake_url,
				"repo": s.repo,
				"branch": s.branch,
				"range": s.range,
				"desk_global": s.desk_global,
				"required": s.name in (required_bare or set()),
			}
		)
	return out


def gates(modules: dict, cfg: dict, facts: dict) -> list[str]:
	"""The gates of §4 the app has: the caller jobs and the required check contexts."""
	ci = modules.get("ci", False)
	ts = modules.get("typescript", False) and bool(
		facts["ts_projects"] or cfg.get("typescript", {}).get("spa")
	)
	unit = modules.get("tests", False) and cfg.get("tests", {}).get("js-unit") and facts["unit_tests"]
	on = {
		"pr-policy": ci and modules.get("commits", False),
		"lint": ci,
		"typecheck": ci and bool(ts or unit),
		"test": ci
		and (
			modules.get("tests", False)
			or modules.get("python-types", False)
			or modules.get("nix-lint", False)
			or bool(cfg.get("shell-checks"))
		),
		"marketplace": ci and modules.get("listing", False),
	}
	return [g for g in GATES if on[g]]


def tools_deps(modules: dict, precommit_live: bool) -> list[str]:
	"""``tools/pyproject.toml``'s dependencies: the tools of the enabled modules (§2.15)."""
	deps = {
		"prek": precommit_live,
		"ruff": modules.get("python-lint", False),
		"ty": modules.get("python-types", False),
		"semgrep": modules.get("semgrep", False),
		"zizmor": modules.get("workflow-lint", False),
		"actionlint-py": modules.get("workflow-lint", False),
		"shellcheck-py": modules.get("shell-lint", False),
		"committed": modules.get("commits", False),
	}
	return sorted(name for name, on in deps.items() if on)


def build(
	app: App,
	resolved: Resolved,
	*,
	lock: dict | None = None,
	floors: dict,
	options: dict | None = None,
) -> NS:
	"""The context for ``app`` with its resolved configuration and the frappe-nix ``lock`` node."""
	cfg = resolved.cfg
	modules = resolved.modules
	major = int(cfg["frappe-major"])
	python_for(major)
	facts = discover.facts(app.root, app.name, cfg, app.tracked, modules)
	listing = facts.pop("listing")
	required = hooks.required_apps(app.hooks)
	required_bare = {hooks.bare(r) for r in required}
	frappe_sib = known_apps.resolve("frappe", major, known_apps.merged(cfg.get("known-apps")))
	title = listing.get("title") or app.hooks.get("app_title") or app.name
	tagline = listing.get("tagline") or app.hooks.get("app_description") or ""
	ver = frappe_nix_tools.__version__
	lock = lock or {}
	release = None
	if modules.get("releases") and cfg["releases"]["branching"] == "develop+version":
		release = f"version-{major}"
	precommit_live = any(
		modules.get(m, False)
		for m in (
			"python-lint",
			"ssort",
			"js",
			"workflow-lint",
			"shell-lint",
			"hygiene",
			"commits",
			"test-utils",
			*COMPAT_MODULES,
		)
	) or (modules.get("stylelint", False) and bool(facts["scss"]))
	profile = dict(resolved.profile)
	source = cfg.get("profile", "minimal")
	org = dict(cfg.get("org", {}))
	return ns(
		{
			"app": app.name,
			"app_hyphen": app.hyphen,
			"dist": app.pyproject.get("project", {}).get("name", app.name),
			"repo": cfg.get("repo") or "",
			"repo_host": resolved.repo_host or "",
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
			"siblings": siblings(cfg, major, required_bare),
			"required_apps": required,
			"site": cfg.get("site") or f"{app.hyphen}.localhost",
			"cfg": cfg,
			"modules": dict(modules),
			"org": org,
			"profile": profile,
			# The org profile's flake URL when it is one (§8.3): the standards-profile input.
			"profile_input": source if source.startswith(ORG_PROFILE_PREFIXES) else "",
			"branches": {"integration": cfg["integration-branch"], "release": release},
			"gates": gates(modules, cfg, facts),
			"discover": facts,
			"listing": listing,
			"frappe_nix": {
				"rev": lock.get("rev") or "",
				"version": ver,
				"major": frappe_nix_major(ver),
				"owner": lock.get("owner") or FRAPPE_NIX_OWNER,
				"name": lock.get("repo") or FRAPPE_NIX_NAME,
				"url": frappe_nix_url(cfg),
			},
			"floors": floors,
			"app_type": facts["app_type"],
			"version": app_version(app.root, app.name),
			"first_commit_year": first_commit_year(app.root),
			"options": options or {},
			"precommit_live": precommit_live,
			# Whether this package renders scripts/vite-register.mjs (N2): C8, the build append
			# and retiring a Vite app's update-assets.mjs wait for it.
			"vite_register_shipped": manifest.ships(manifest.VITE_REGISTER),
			"tools_deps": tools_deps(modules, precommit_live),
			"package_json_live": any(
				modules.get(m, False)
				for m in (
					"metadata",
					"js",
					"stylelint",
					"typescript",
					"tests",
					"python-lint",
					"python-types",
					"vite-register",
				)
			),
		}
	)
