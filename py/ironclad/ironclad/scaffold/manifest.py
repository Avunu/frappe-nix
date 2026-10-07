"""The managed-file manifest: the union of ``ironclad/data/manifest.d/*.json`` (spec §3.1).

A fragment is a JSON object:

``entries``
    managed files, each ``{path, template, strategy, when, header, local_regions, floors,
    handler, command, phase, header_note}`` (below);
``retire``
    files sync deletes and ``--check`` reports as ``legacy file`` (§2.4.1), each
    ``{glob, rule, contains?, except?, unmanaged?, only_section?}``;
``floors``
    version floors by kind (``npm``, ``npm-ts``, ``npm-scss``, ``uv``), merged into the
    template context as ``floors``.

An entry's ``path`` is itself a Jinja template (``{{ app }}/__init__.py``,
``{{ discover.solution }}``), and ``when`` a Python expression over the template context.
``strategy`` is one of ``whole``, ``toml-merge``, ``json-merge``, ``seed`` and ``blocks``;
the merge, block and seed strategies name the Python ``handler`` that knows the file's keys
or markers, or for a lock the ``command`` that produces it. ``phase`` is ``a`` for the
files sync writes before locking the flake (``flake.nix``, ``.envrc``), else ``b``.

A path listed by two fragments, or twice in one, is an error: the engine refuses to load
(and ``tests/ironclad/sync.nix`` fails the frappe-nix build).
"""

import json
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from ironclad.common import data_path

STRATEGIES = ("whole", "toml-merge", "json-merge", "seed", "blocks")
HEADERS = ("yaml", "toml", "shell", "ini", "nix", "jsonc", "ts", "none")
ENTRY_KEYS = {
	"path",
	"template",
	"strategy",
	"when",
	"header",
	"header_note",
	"local_regions",
	"floors",
	"handler",
	"command",
	"phase",
}
RETIRE_KEYS = {"glob", "rule", "contains", "except", "unmanaged", "only_section"}


class ManifestError(RuntimeError):
	"""The packaged manifest is inconsistent: a frappe-nix bug, never the app's."""


@dataclass(frozen=True)
class Entry:
	path: str
	strategy: str
	fragment: str
	template: str | None = None
	when: str = "True"
	header: str = "none"
	header_note: str = ""
	local_regions: tuple[str, ...] = ()
	floors: dict = field(default_factory=dict)
	handler: str | None = None
	command: str | None = None
	phase: str = "b"


@dataclass(frozen=True)
class Retire:
	glob: str
	rule: str
	fragment: str
	contains: tuple[str, ...] = ()
	unless: tuple[str, ...] = ()
	unmanaged: bool = False
	only_section: str | None = None


@dataclass(frozen=True)
class Manifest:
	entries: tuple[Entry, ...]
	retire: tuple[Retire, ...]
	floors: dict[str, dict[str, str]]


def _entry(raw: Any, fragment: str) -> Entry:
	if not isinstance(raw, dict) or "path" not in raw or "strategy" not in raw:
		raise ManifestError(f"manifest.d/{fragment}: an entry needs path and strategy: {raw!r}")
	unknown = set(raw) - ENTRY_KEYS
	if unknown:
		raise ManifestError(f"manifest.d/{fragment}: {raw['path']}: unknown key(s) {sorted(unknown)}")
	if raw["strategy"] not in STRATEGIES:
		raise ManifestError(f"manifest.d/{fragment}: {raw['path']}: unknown strategy {raw['strategy']!r}")
	header = raw.get("header", "none")
	if header not in HEADERS:
		raise ManifestError(f"manifest.d/{fragment}: {raw['path']}: unknown header {header!r}")
	if raw["strategy"] == "whole" and not raw.get("template"):
		raise ManifestError(f"manifest.d/{fragment}: {raw['path']}: a whole file needs a template")
	if raw.get("local_regions") and header not in ("yaml", "toml", "shell", "ini", "nix"):
		raise ManifestError(f"manifest.d/{fragment}: {raw['path']}: local regions need a # comment header")
	return Entry(
		path=raw["path"],
		strategy=raw["strategy"],
		fragment=fragment,
		template=raw.get("template"),
		when=raw.get("when", "True"),
		header=header,
		header_note=raw.get("header_note", ""),
		local_regions=tuple(raw.get("local_regions", [])),
		floors=dict(raw.get("floors", {})),
		handler=raw.get("handler"),
		command=raw.get("command"),
		phase=raw.get("phase", "b"),
	)


def _retire(raw: Any, fragment: str) -> Retire:
	if not isinstance(raw, dict) or "glob" not in raw or "rule" not in raw:
		raise ManifestError(f"manifest.d/{fragment}: a retire rule needs glob and rule: {raw!r}")
	unknown = set(raw) - RETIRE_KEYS
	if unknown:
		raise ManifestError(f"manifest.d/{fragment}: retire {raw['glob']}: unknown key(s) {sorted(unknown)}")
	return Retire(
		glob=raw["glob"],
		rule=raw["rule"],
		fragment=fragment,
		contains=tuple(raw.get("contains", [])),
		unless=tuple(raw.get("except", [])),
		unmanaged=bool(raw.get("unmanaged", False)),
		only_section=raw.get("only_section"),
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
