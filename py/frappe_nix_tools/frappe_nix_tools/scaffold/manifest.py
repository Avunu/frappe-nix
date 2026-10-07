"""The managed-file manifest: the union of ``frappe_nix_tools/data/manifest.d/*.json`` (spec §2.4, §3.1).

A fragment is a JSON object:

``entries``
    managed files, each ``{path, template, strategy, module, when, uses, overridable,
    header, header_note, local_regions, handler, command, phase}`` (below);
``retire``
    files sync deletes and ``--check`` reports as ``legacy file`` (§2.4.1), each
    ``{paths, rule, module, with?, when?, contains?, except?, unmanaged?, only_section?,
    deps_in_project?}``. A rule applies only while ``module`` and every module in ``with``
    are on and ``when`` holds; ``contains`` holds regular expressions, any of which the
    file's text must match; ``unmanaged`` skips the paths a manifest entry manages;
    ``deps_in_project`` (a requirements file) retires it only once
    ``[project].dependencies`` names every package it lists, and is exit 2 until then;
``floors``
    version floors by kind (``npm-js``, ``npm-ts``, ``npm-frappe-types``, ``npm-scss``,
    ``uv``), merged into the template context as ``floors``.

An entry is live when one of its ``module`` (a name, or a list meaning any of them) is on
and its ``when`` holds. ``path`` is itself a Jinja template (``{{ app }}/__init__.py``,
``{{ discover.solution }}``), and ``when`` a Python expression over the template context.
``uses`` names the profile values the entry reads (``org.license``, ``js.format-width``);
an ``org.*`` value suffixed ``?`` is optional, any other ``org.*`` value must be set while
the entry is live (S37). ``overridable`` lets an org profile replace the template (§8.1).
``strategy`` is one of ``whole``, ``toml-merge``, ``json-merge``, ``seed`` and ``blocks``;
the merge, block and seed strategies name the Python ``handler`` that knows the file's
keys or markers, or for a lock the ``command`` that produces it. ``phase`` is ``a`` for
the files sync writes before locking the flake (``flake.nix``, ``.envrc``), else ``b``.

A path listed by two fragments, or twice in one, is an error: the engine refuses to load
(and ``tests/standards/sync.nix`` fails the frappe-nix build).
"""

import json
from dataclasses import dataclass
from functools import cache
from typing import Any

from frappe_nix_tools.common import data_path
from frappe_nix_tools.common.config import MODULES

STRATEGIES = ("whole", "toml-merge", "json-merge", "seed", "blocks")
HEADERS = ("yaml", "toml", "shell", "ini", "nix", "jsonc", "ts", "none")
HASH_HEADERS = ("yaml", "toml", "shell", "ini", "nix")
ENTRY_KEYS = {
	"path",
	"template",
	"strategy",
	"module",
	"when",
	"uses",
	"overridable",
	"header",
	"header_note",
	"local_regions",
	"handler",
	"command",
	"phase",
}
RETIRE_KEYS = {
	"paths",
	"rule",
	"module",
	"with",
	"when",
	"contains",
	"except",
	"unmanaged",
	"only_section",
	"deps_in_project",
}


class ManifestError(RuntimeError):
	"""The packaged manifest is inconsistent: a frappe-nix bug, never the app's."""


@dataclass(frozen=True)
class Entry:
	path: str
	strategy: str
	fragment: str
	modules: tuple[str, ...]
	template: str | None = None
	when: str = "True"
	uses: tuple[str, ...] = ()
	overridable: bool = False
	header: str = "none"
	header_note: str = ""
	local_regions: tuple[str, ...] = ()
	handler: str | None = None
	command: str | None = None
	phase: str = "b"
	# Set for an org profile's [[extra-files]] entry: the template is the profile's.
	profile_template: bool = False

	@property
	def module(self) -> str:
		"""The first (for a single-module entry, the only) module."""
		return self.modules[0]


@dataclass(frozen=True)
class Retire:
	paths: tuple[str, ...]
	rule: str
	fragment: str
	module: str
	also: tuple[str, ...] = ()
	when: str = "True"
	contains: tuple[str, ...] = ()
	unless: tuple[str, ...] = ()
	unmanaged: bool = False
	only_section: str | None = None
	deps_in_project: bool = False


@dataclass(frozen=True)
class Manifest:
	entries: tuple[Entry, ...]
	retire: tuple[Retire, ...]
	floors: dict[str, dict[str, str]]

	def templates(self) -> dict[str, Entry]:
		"""Every entry with a template, by template path."""
		return {e.template: e for e in self.entries if e.template}


def _modules(raw: Any, where: str) -> tuple[str, ...]:
	names = raw if isinstance(raw, list) else [raw]
	if not names or not all(isinstance(n, str) for n in names):
		raise ManifestError(f"{where}: module must be a module name or a list of them")
	unknown = [n for n in names if n not in MODULES]
	if unknown:
		raise ManifestError(f"{where}: unknown module(s) {unknown}")
	return tuple(names)


def _entry(raw: Any, fragment: str) -> Entry:
	if not isinstance(raw, dict) or "path" not in raw or "strategy" not in raw:
		raise ManifestError(f"manifest.d/{fragment}: an entry needs path and strategy: {raw!r}")
	where = f"manifest.d/{fragment}: {raw['path']}"
	unknown = set(raw) - ENTRY_KEYS
	if unknown:
		raise ManifestError(f"{where}: unknown key(s) {sorted(unknown)}")
	if "module" not in raw:
		raise ManifestError(f"{where}: an entry needs its module")
	if raw["strategy"] not in STRATEGIES:
		raise ManifestError(f"{where}: unknown strategy {raw['strategy']!r}")
	header = raw.get("header", "none")
	if header not in HEADERS:
		raise ManifestError(f"{where}: unknown header {header!r}")
	if raw["strategy"] == "whole" and not raw.get("template"):
		raise ManifestError(f"{where}: a whole file needs a template")
	if raw.get("local_regions") and header not in HASH_HEADERS:
		raise ManifestError(f"{where}: local regions need a # comment header")
	uses = raw.get("uses", [])
	if not isinstance(uses, list) or not all(isinstance(u, str) for u in uses):
		raise ManifestError(f"{where}: uses must be a list of dotted keys")
	return Entry(
		path=raw["path"],
		strategy=raw["strategy"],
		fragment=fragment,
		modules=_modules(raw["module"], where),
		template=raw.get("template"),
		when=raw.get("when", "True"),
		uses=tuple(uses),
		overridable=bool(raw.get("overridable", False)),
		header=header,
		header_note=raw.get("header_note", ""),
		local_regions=tuple(raw.get("local_regions", [])),
		handler=raw.get("handler"),
		command=raw.get("command"),
		phase=raw.get("phase", "b"),
	)


def _retire(raw: Any, fragment: str) -> Retire:
	if not isinstance(raw, dict) or "paths" not in raw or "rule" not in raw or "module" not in raw:
		raise ManifestError(f"manifest.d/{fragment}: a retire rule needs paths, rule and module: {raw!r}")
	where = f"manifest.d/{fragment}: retire {raw['paths']}"
	unknown = set(raw) - RETIRE_KEYS
	if unknown:
		raise ManifestError(f"{where}: unknown key(s) {sorted(unknown)}")
	(module,) = _modules(raw["module"], where)
	return Retire(
		paths=tuple(raw["paths"]),
		rule=raw["rule"],
		fragment=fragment,
		module=module,
		also=_modules(raw["with"], where) if raw.get("with") else (),
		when=raw.get("when", "True"),
		contains=tuple(raw.get("contains", [])),
		unless=tuple(raw.get("except", [])),
		unmanaged=bool(raw.get("unmanaged", False)),
		only_section=raw.get("only_section"),
		deps_in_project=bool(raw.get("deps_in_project", False)),
	)


def parse(fragments: dict[str, Any]) -> Manifest:
	"""The union of ``fragments`` (file name → parsed JSON), in file name order."""
	entries: list[Entry] = []
	retire: list[Retire] = []
	floors: dict[str, dict[str, str]] = {}
	seen: dict[str, str] = {}
	for name in sorted(fragments):
		doc = fragments[name]
		if not isinstance(doc, dict) or set(doc) - {"entries", "retire", "floors", "$comment"}:
			raise ManifestError(f"manifest.d/{name}: expected an object with entries, retire and floors")
		for raw in doc.get("entries", []):
			entry = _entry(raw, name)
			if entry.path in seen:
				raise ManifestError(
					f"{entry.path} is listed in manifest.d/{seen[entry.path]} and manifest.d/{name}"
				)
			seen[entry.path] = name
			entries.append(entry)
		retire += [_retire(raw, name) for raw in doc.get("retire", [])]
		for kind, table in doc.get("floors", {}).items():
			for pkg, floor in table.items():
				if pkg in floors.get(kind, {}):
					raise ManifestError(
						f"floor {kind}/{pkg} is set by two fragments (one is manifest.d/{name})"
					)
				floors.setdefault(kind, {})[pkg] = floor
	return Manifest(tuple(entries), tuple(retire), floors)


@cache
def load() -> Manifest:
	"""The packaged manifest."""
	directory = data_path("manifest.d")
	return parse({p.name: json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))})


# N2's managed copy of the registration module (S30, §5.11), in its manifest.d/assets.json.
VITE_REGISTER = "scripts/vite-register.mjs"


def ships(path: str) -> bool:
	"""Whether the packaged manifest renders ``path``.

	C8 and its build append, and retiring ``update-assets.mjs`` from a Vite app, need
	``scripts/vite-register.mjs``, which N2's fragment brings: until it is packaged, a Vite app
	keeps its own registration (§1.3)."""
	return any(e.path == path for e in load().entries)


def extra_entries(cfg: dict) -> list[Entry]:
	"""The org profile's ``[[extra-files]]`` as entries (§8.1): rendered from the profile's
	``templates/``, live while their module is on and their ``when`` holds."""
	out = []
	for i, raw in enumerate(cfg.get("extra-files", [])):
		out.append(
			Entry(
				path=raw["path"],
				strategy=raw["strategy"],
				fragment=f"profile [[extra-files]] {i}",
				modules=(raw["module"],),
				template=raw["template"],
				when=raw.get("when", "True"),
				header=raw.get("header", "none") if raw["strategy"] == "whole" else "none",
				profile_template=True,
			)
		)
	return out


def profile_retire(cfg: dict) -> list[Retire]:
	"""The org profile's ``[[retire]]`` rules (§8.1), applied like the built-in ones."""
	return [
		Retire(
			paths=tuple(raw["paths"]),
			rule="the profile's [[retire]] rule",
			fragment="profile",
			module=raw["module"],
			contains=tuple(raw.get("contains", [])),
		)
		for raw in cfg.get("retire", [])
	]
