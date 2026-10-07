"""The resolved configuration of an app (spec §8.4): profiles, the merge, defaults and modules.

Every consumer resolves the same way: sync and ``--check``, ``frappe-nix config``,
frappe-test, the CI ``cfg`` step, ``repo apply`` and ``repo audit``.

1. Read ``[tool.frappe-nix]``. No table means not opted in (S35): ``resolve`` raises
   ``NotOptedIn`` and the caller decides what that means.
2. Load the layers, lowest first: the built-in profile (``profile`` itself when it names
   one, else the org profile's ``extends``; plain ``recommended`` is the newest snapshot,
   S42), the org profile (from the locked ``standards-profile`` input, or ``./<dir>`` in
   the app's own tree), and the app's table.
3. Validate each layer on its own: the profile schema for the first two (app-only keys
   refused), the app schema for the third (profile-only keys refused).
4. Merge: tables key by key, recursively; scalars and arrays replaced whole.
   ``[known-apps]`` merges by key; ``[[retire]]``, ``[[replace-apps]]`` and
   ``[[extra-files]]`` come only from the org profile.
5. Apply the schema defaults, then the module rules of §8.2 (needs, ``tool = "none"``,
   GitHub-only modules off on other hosts), computing ``modules``.
6. Check ``requires-frappe-nix`` against the running version (exit 3). Whether every org
   value a live manifest entry ``uses`` is set is sync's check (N3), since it needs the
   manifest.
"""

import copy
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from frappe_nix_tools import __version__
from frappe_nix_tools.common import data_path, flakelock, pyproject, repo, schema
from frappe_nix_tools.common.pins import pin_path
from frappe_nix_tools.common.report import ConfigError, EnvError

PROFILE_SCHEMA = "profile.schema.json"
APP_SCHEMA = "tool-frappe-nix.schema.json"

# The modules of §8.2, in the spec's order.
MODULES = (
	"dev-shell",
	"editorconfig",
	"metadata",
	"python-lint",
	"ssort",
	"python-types",
	"js",
	"typescript",
	"stylelint",
	"tests",
	"semgrep",
	"workflow-lint",
	"shell-lint",
	"nix-lint",
	"hygiene",
	"commits",
	"releases",
	"dependabot",
	"ci",
	"repo-policy",
	"vite-register",
	"pilot-assets",
	"listing",
	"readme",
	"icons",
	"screenshots",
	"demo",
	"test-utils",
	"docs-site",
)
# §8.2's "Needs" column.
NEEDS: dict[str, tuple[str, ...]] = {
	"releases": ("commits",),
	"pilot-assets": ("ci", "releases"),
	"listing": ("metadata",),
	"readme": ("listing",),
	"screenshots": ("demo",),
	"docs-site": ("dependabot",),
}
# Modules that act only on GitHub (S43, §8.2): off on a repository hosted elsewhere.
GITHUB_ONLY = ("ci", "releases", "dependabot", "repo-policy", "pilot-assets", "docs-site")
# The top-level keys only a profile may set (§8.1). `schema` is in both: the app table's is
# the [tool.frappe-nix] schema version (§2.1).
PROFILE_ONLY = (
	"name",
	"description",
	"extends",
	"requires-frappe-nix",
	"retire",
	"replace-apps",
	"extra-files",
)
# The top-level keys only an app may set (§2.1); the app-only module parameters are marked
# x-app-only in the profile schema.
APP_ONLY = (
	"profile",
	"frappe-major",
	"siblings",
	"repo",
	"integration-branch",
	"retire-keep",
	"site",
	"nightly-suites",
	"generated",
	"build",
	"shell-checks",
	"frappe-node-modules",
	"untested",
	"coverage-omit",
	"unchecked-js",
	"override-doctype-class",
)
# The resolved lists only the org profile sets (§8.4 step 4): in ``cfg``, never in the app's
# table or its schema.
RESOLVED_FROM_PROFILE = ("retire", "replace-apps", "extra-files")
ORG_PROFILE_PREFIXES = ("github:", "gitlab:", "git+https://")


class NotOptedIn(ConfigError):
	"""Exit 2: the app has no ``[tool.frappe-nix]`` table (S35)."""

	def __init__(self) -> None:
		super().__init__(pyproject.OPT_IN_HINT)


@dataclass
class Resolved:
	"""The ``cfg``, ``modules`` and ``profile`` of the template context (§2.3).

	``sources`` maps each dotted key that a layer set to that layer's label
	(``builtin:recommended@1.0``, ``org:<source>@<rev>``, ``app``); every other key came
	from the schema's ``default``. ``repo_host`` is the host of ``cfg["repo"]``
	(``github.com`` unless it names another), or ``None`` when the repository is unknown.
	"""

	cfg: dict
	modules: dict[str, bool]
	profile: dict
	sources: dict[str, str] = field(default_factory=dict)
	repo_host: str | None = None
	notices: list[str] = field(default_factory=list)

	def source(self, key: str) -> str:
		"""The layer that set ``key`` (or one of its parents), else ``default``."""
		parts = key.split(".")
		for n in range(len(parts), 0, -1):
			found = self.sources.get(".".join(parts[:n]))
			if found:
				return found
		return "default"

	def get(self, key: str) -> Any:
		"""The resolved value of a dotted key: a ``cfg`` key, ``modules[.<m>]`` or ``profile[.<k>]``.

		A ``cfg`` key is one of the app schema's, or ``retire``, ``replace-apps`` or
		``extra-files``, which only the org profile sets.
		"""
		head, _, rest = key.partition(".")
		roots = {"modules": self.modules, "profile": self.profile}
		if head in roots:
			value: Any = roots[head]
			if rest and (not isinstance(value, dict) or rest not in value):
				raise ConfigError(f"no key {key!r}")
			return value[rest] if rest else value
		if head in RESOLVED_FROM_PROFILE:
			# Lists the org profile alone sets (§8.4 step 4); not in the app's schema.
			if rest:
				raise ConfigError(f"no key {key!r}: {head} is a list")
			return self.cfg.get(head, [])
		if schema.node_at(APP_SCHEMA, key) is None:
			raise ConfigError(f"[tool.frappe-nix] has no key {key!r}")
		value = self.cfg
		for part in key.split("."):
			if not isinstance(value, dict) or part not in value:
				return None
			value = value[part]
		return value


# --- built-in profiles ---------------------------------------------------------------------


@cache
def _snapshots() -> dict[Version, str]:
	"""``recommended@<minor>`` files, by their version."""
	out = {}
	for path in data_path("profiles").iterdir():
		stem = path.name.removesuffix(".toml")
		if path.suffix == ".toml" and stem.startswith("recommended@"):
			out[Version(stem.removeprefix("recommended@"))] = stem
	return out


def builtin_names() -> list[str]:
	"""``minimal`` and every ``recommended@<minor>`` snapshot, oldest first."""
	return ["minimal", *(_snapshots()[v] for v in sorted(_snapshots()))]


def builtin_name(name: str) -> str:
	"""The file stem a built-in profile name means: plain ``recommended`` is the newest snapshot."""
	if name == "recommended":
		if not _snapshots():
			raise ConfigError("frappe-nix ships no recommended snapshot")
		return _snapshots()[max(_snapshots())]
	if name in builtin_names():
		return name
	raise ConfigError(
		f"no built-in profile {name!r} (built-ins: {', '.join(['recommended', *builtin_names()])})"
	)


@cache
def _builtin_doc(stem: str) -> dict:
	return tomllib.loads(data_path(f"profiles/{stem}.toml").read_text())


def builtin_profile(name: str) -> tuple[str, dict]:
	"""``(stem, document)`` of a built-in profile; an unknown name is a ``ConfigError``."""
	stem = builtin_name(name)
	return stem, copy.deepcopy(_builtin_doc(stem))


def is_builtin(profile: str) -> bool:
	return profile in ("minimal", "recommended") or profile.startswith("recommended@")


# --- layers -------------------------------------------------------------------------------


def validate_profile(doc: dict, label: str, *, org: bool) -> None:
	"""A profile document against the profile schema, with §8.1's key rules."""
	errors = []
	for key in doc:
		if key in APP_ONLY:
			errors.append(f"{label}.{key}: an app-only key; set it in the app's [tool.frappe-nix]")
	if org and "extends" not in doc:
		errors.append(f"{label}: extends is required in an org profile")
	if not org and "extends" in doc:
		errors.append(f"{label}: a built-in profile has no extends")
	errors += schema.Validator(PROFILE_SCHEMA, label, app_only_allowed=False).errors(
		{k: v for k, v in doc.items() if k not in APP_ONLY}
	)
	if errors:
		raise ConfigError("invalid profile:\n  " + "\n  ".join(errors))


def validate_app(table: dict) -> None:
	"""``[tool.frappe-nix]`` against the app schema, with profile-only keys named as such."""
	where = "[tool.frappe-nix]"
	errors = [
		f'{where}.{key}: a profile-only key; a single app that needs it uses an in-repo profile (profile = "./<dir>")'
		for key in table
		if key in PROFILE_ONLY
	]
	errors += schema.Validator(APP_SCHEMA, where).errors(
		{k: v for k, v in table.items() if k not in PROFILE_ONLY}
	)
	if errors:
		raise ConfigError("invalid configuration:\n  " + "\n  ".join(errors))


def _read_profile_dir(directory: Path, label: str) -> dict:
	path = directory / "profile.toml"
	try:
		return tomllib.loads(path.read_text())
	except FileNotFoundError as e:
		raise ConfigError(f"profile {label}: {path} does not exist") from e
	except (OSError, UnicodeDecodeError) as e:
		raise EnvError(f"profile {label}: {path} is unreadable: {e}") from e
	except tomllib.TOMLDecodeError as e:
		raise ConfigError(f"profile {label}: {path}: {e}") from e


def org_profile_dir(profile: str, root: Path, lock_path: Path | None = None) -> tuple[Path, str]:
	"""``(directory, rev)`` of the org profile ``profile`` names.

	``./<dir>`` is the app's own tree (no rev). A flake URL is read from the locked
	``standards-profile`` input of the app's ``flake.lock``: the Nix store path when
	present, else fetched and narHash-verified (``pin-path``).
	"""
	if profile.startswith("./"):
		rel = PurePosixPath(profile)
		if ".." in rel.parts or rel.is_absolute():
			raise ConfigError(f"profile {profile!r}: an in-repo profile must stay inside the app")
		return root / rel, ""
	if not profile.startswith(ORG_PROFILE_PREFIXES):
		raise ConfigError(
			f"profile {profile!r} is not a built-in, a github:/gitlab:/git+https:// URL or ./<dir>"
		)
	lock_path = lock_path or root / "flake.lock"
	lock = flakelock.load(lock_path)
	if flakelock.input_node(lock, flakelock.PROFILE_INPUT) is None:
		raise ConfigError(
			f"profile {profile!r} is not locked: {lock_path} has no {flakelock.PROFILE_INPUT} input; run frappe-init --sync"
		)
	return pin_path(flakelock.PROFILE_INPUT, lock_path), flakelock.locked_rev(
		lock, flakelock.PROFILE_INPUT
	) or ""


def _record(table: dict, label: str, sources: dict[str, str], prefix: str) -> None:
	for key, value in table.items():
		path = f"{prefix}.{key}" if prefix else key
		if isinstance(value, dict) and value:
			_record(value, label, sources, path)
		else:
			sources[path] = label


# --- merge --------------------------------------------------------------------------------


def _merge(base: Any, over: Any, label: str, sources: dict[str, str], prefix: str = "") -> Any:
	"""``over`` merged onto ``base``: tables key by key, everything else replaced whole."""
	if isinstance(base, dict) and isinstance(over, dict):
		out = dict(base)
		for key, value in over.items():
			path = f"{prefix}.{key}" if prefix else key
			out[key] = _merge(base.get(key), value, label, sources, path)
		return out
	if isinstance(over, dict):
		_record(over, label, sources, prefix)
	else:
		sources[prefix] = label
	return copy.deepcopy(over)


def _fill_defaults(cfg: Any, defaults: Any) -> Any:
	if isinstance(cfg, dict) and isinstance(defaults, dict):
		out = dict(cfg)
		for key, value in defaults.items():
			out[key] = _fill_defaults(out[key], value) if key in out else copy.deepcopy(value)
		return out
	return cfg


# --- modules ------------------------------------------------------------------------------


def _switch(cfg: dict, module: str) -> bool:
	table = cfg.get(module, {})
	return bool(table.get("enable")) and table.get("tool") != "none"


def _modules(cfg: dict, app: dict, on_github: bool, notices: list[str]) -> dict[str, bool]:
	"""``modules``: each module's effective switch under §8.2's rules."""
	if not _switch(cfg, "dev-shell"):
		raise ConfigError(
			"module dev-shell is required: every opted-in app has it (enable = false is invalid)"
		)
	on = {m: _switch(cfg, m) for m in MODULES}
	host_off: set[str] = set()
	if not on_github:
		for m in GITHUB_ONLY:
			if app.get(m, {}).get("enable") is True:
				raise ConfigError(
					f"module {m} is GitHub-only and this repository is hosted elsewhere; remove its enable = true"
				)
			if on[m]:
				host_off.add(m)
				on[m] = False
		if app.get("listing", {}).get("publish") is True:
			raise ConfigError("listing.publish is GitHub-only and this repository is hosted elsewhere")
		if cfg.get("listing", {}).get("publish"):
			cfg["listing"]["publish"] = False
		if host_off:
			notices.append(
				f"repository is not on GitHub: GitHub-only modules off: {', '.join(sorted(host_off))}"
			)
	changed = True
	while changed:
		changed = False
		for m in MODULES:
			if not on[m]:
				continue
			for need in NEEDS.get(m, ()):
				if on[need]:
					continue
				if need in host_off and app.get(m, {}).get("enable") is not True:
					on[m] = False
					host_off.add(m)
					changed = True
					break
				raise ConfigError(f"module {m} needs module {need}, which is off: turn {need} on or {m} off")
	return on


# --- resolve ------------------------------------------------------------------------------


def _repo(cfg: dict, root: Path | None, app: str | None) -> tuple[str | None, str | None]:
	"""``(repo, host)`` by §2.2: the table's, else origin's, else ``<org.github-owner>/<app>``."""
	explicit = cfg.get("repo")
	if explicit:
		parts = explicit.split("/")
		host = parts[0].lower() if len(parts) > 2 and "." in parts[0] else "github.com"
		return (explicit if host == "github.com" else "/".join(parts[1:])), host
	origin = repo.origin_repo(root) if root is not None else None
	if origin:
		host, path = origin
		return path, host
	owner = cfg.get("org", {}).get("github-owner")
	if owner and app:
		return f"{owner}/{app}", "github.com"
	return None, None


def check_requires(spec: str | None, label: str) -> None:
	"""``requires-frappe-nix`` must admit the running frappe-nix-tools (exit 3 otherwise, §6.4)."""
	if not spec:
		return
	try:
		admits = SpecifierSet(spec).contains(Version(__version__), prereleases=True)
	except (InvalidSpecifier, InvalidVersion) as e:
		raise ConfigError(f"{label}: requires-frappe-nix {spec!r} is not a version specifier: {e}") from e
	if not admits:
		raise EnvError(f"{label} requires frappe-nix {spec}, but this is frappe-nix-tools {__version__}")


def resolve_doc(
	doc: dict,
	*,
	root: Path | None = None,
	lock_path: Path | None = None,
	profile_dir: Path | None = None,
) -> Resolved:
	"""Resolve a parsed ``pyproject.toml`` (§8.4).

	``root`` is the app's directory (in-repo profiles, the lock, origin); ``profile_dir``
	replaces the org profile's locked tree with a local checkout (``--profile-path``).
	"""
	app = pyproject.tool_frappe_nix(doc)
	if app is None:
		raise NotOptedIn()
	validate_app(app)
	app = copy.deepcopy(app)
	name = app.get("profile", "minimal")

	layers: list[tuple[str, dict]] = []
	org_doc: dict | None = None
	profile_info = {"name": "", "source": "builtin", "rev": ""}
	if is_builtin(name):
		stem, base = builtin_profile(name)
		profile_info["name"] = stem
	else:
		if profile_dir is not None:
			directory, rev = profile_dir, ""
		elif root is None:
			raise ConfigError(f"profile {name!r} needs the app's directory to be read")
		else:
			directory, rev = org_profile_dir(name, root, lock_path)
		label = f"org:{name}@{rev}" if rev else f"org:{name}"
		org_doc = _read_profile_dir(directory, name)
		validate_profile(org_doc, f"profile {name}", org=True)
		check_requires(org_doc.get("requires-frappe-nix"), f"profile {name}")
		stem, base = builtin_profile(org_doc["extends"])
		profile_info = {"name": org_doc["name"], "source": name, "rev": rev}
		layers.append((label, org_doc))
	validate_profile(base, f"built-in profile {stem}", org=False)
	layers.insert(0, (f"builtin:{stem}", base))
	layers.append(("app", app))

	sources: dict[str, str] = {}
	cfg: dict = {}
	for label, layer in layers:
		body = {
			k: v
			for k, v in layer.items()
			if k not in ("schema", "name", "description", "extends", "requires-frappe-nix")
		}
		cfg = _merge(cfg, body, label, sources)
	cfg["schema"] = app["schema"]
	for key in RESOLVED_FROM_PROFILE:
		cfg[key] = copy.deepcopy(org_doc.get(key, [])) if org_doc else []
	cfg = _fill_defaults(cfg, schema.defaults(PROFILE_SCHEMA))
	cfg = _fill_defaults(cfg, schema.defaults(APP_SCHEMA))
	cfg["profile"] = name

	org = cfg["org"]
	if not org.get("copyright-holder"):
		org["copyright-holder"] = org.get("publisher", "")
	if "integration-branch" not in cfg:
		cfg["integration-branch"] = "main" if cfg["releases"]["branching"] == "main+tags" else "develop"
	app_name = pyproject.project_name(doc)
	if "site" not in cfg and app_name:
		cfg["site"] = f"{app_name.replace('_', '-')}.localhost"
	repo_path, host = _repo(cfg, root, app_name)
	if repo_path:
		cfg["repo"] = repo_path

	notices: list[str] = []
	modules = _modules(cfg, app, host in (None, "github.com"), notices)
	return Resolved(
		cfg=cfg, modules=modules, profile=profile_info, sources=sources, repo_host=host, notices=notices
	)


def resolve(
	pyproject_path: Path, *, lock_path: Path | None = None, profile_dir: Path | None = None
) -> Resolved:
	"""Resolve the app whose ``pyproject.toml`` is ``pyproject_path``.

	Its text must spell the table the way the dev shell's line match finds it
	(``pyproject.check_opt_in_spelling``), so the shell and the tools never disagree on
	whether the app opted in.
	"""
	root = pyproject_path.resolve().parent
	text = pyproject.read(pyproject_path)
	doc = pyproject.parse(text, pyproject_path)
	pyproject.check_opt_in_spelling(text, doc)
	return resolve_doc(doc, root=root, lock_path=lock_path, profile_dir=profile_dir)
