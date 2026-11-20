"""What a relock's lock files may say before a job that holds the App's key pushes them (§4.5).

``relock-plan`` and ``relock-upgrade-plan`` run the app's and third-party code, on the caller's
runner (a shared self-hosted one, possibly). Their patch is data: the push jobs' "Check and
apply" step bounds the *paths* it may touch (``nix/uv.lock``, ``nix/node-locks/**`` and, for a
Dependabot relock, ``types/doctypes.d.ts``), and ``frappe-nix ci verify-relock`` bounds what the
lock files *say*, before the token is minted and before auto-merge is enabled. It compares the
lock files of the tree to be pushed (``--head``) with a commit the integration branch holds
(``--base``: the PR's merge-base, or the commit the weekly relock ran on), read from git:

- ``nix/uv.lock`` may not be deleted. Every package entry that is not the base's (by name,
  version, source, files and dependency edges) comes from PyPI (``registry = "https://pypi.org/simple"``), from a
  source the base lock already has, or from a directory inside one the base installs editable
  (``inside_trusted_tree``: Frappe's flow harness under ``apps/frappe``), and each of its files is an https URL on
  files.pythonhosted.org that names that package and version, or a URL the base lock already
  has. A git source the base has at another commit (frappe's ``pypika`` or ``gunicorn`` fork after
  a frappe bump) is ``needs-review``; any other new source is ``reject``.
- ``nix/node-locks/**`` holds only ``yarn.lock`` and ``source.json`` files. A ``yarn.lock`` is
  classic (a berry lock is ``reject``) and exactly the shape yarn writes (``strict_yarn``: split on
  ``\\n`` only, one grammar per line, no field twice in an entry, no spec in two entries), and
  lib/node/yarn-lock.nix's own line regexes (``nix_yarn``) find the same entries and values in
  it; else ``reject``, since a line one reader skips and another takes is how a lock says one
  thing here and another to the build. Every ``resolved`` URL is https on registry.yarnpkg.com or
  registry.npmjs.org, or one the base's locks already have, and an entry that is not the base's
  resolves the npm tarball of the name and version it names (``/<name>/-/<base name>-<version>.tgz``).
  A ``source.json`` stamp is the base's, the seed's, or the same stamp with other manifest hashes:
  only ``forced`` steers the build (lib/bench.nix takes the fallback lock over the app's own when
  it is set), so it must stay what the base (or, for a new target, the seed) says. Deleting the
  ``yarn.lock`` or the ``source.json`` of a target the base forces, and keeping the other, turns
  the override off as well and is ``reject``; pruning the whole forced target is ``needs-review``.
- A package name no base lock of that file had (uv or yarn; for a yarn ``a@npm:b@^1`` alias,
  ``a`` and ``b`` both), a yarn ``npm:`` alias spec the base does not have, or a new or changed
  yarn entry (its dependency lines included) without a ``resolved`` URL, with a ``name`` or
  ``uid``, or whose ``version`` its spec's range does not allow (ci/npmrange.py, node-semver's
  rules; a range it does not read counts as not allowing it), is ``needs-review``: an honest
  relock adds one only when the bumped dependency does, and a person should see it.

The decision is ``ok``, ``needs-review`` (pushed, labelled, auto-merge off) or ``reject`` (the
step fails and nothing is pushed). It runs no nix, uv or yarn, and reads git only.

rolloutpatch.py (``frappe-nix ci verify-rollout-patch``, the release-driven rollout's patch
check) reads lock files with this module: the helpers from ``_uv`` to ``trusted_locks``,
``strict_yarn``/``nix_yarn`` and ``yarn_problems``, and the stamp rules (``stamp_problems``,
``_pruned_forced``), with its own rollout rules on top. ``yarn_entries`` reads only the base's
locks, which the integration branch holds, for the URLs they already trust.
"""

import json
import re
import subprocess
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from packaging.version import InvalidVersion, Version

from frappe_nix_tools.ci import npmrange
from frappe_nix_tools.common import data_path
from frappe_nix_tools.common.repo import NO_AUTO_MAINTENANCE
from frappe_nix_tools.common.report import ConfigError, EnvError

OK, REVIEW, REJECT = "ok", "needs-review", "reject"

UV_LOCK = "nix/uv.lock"
NODE_LOCKS = "nix/node-locks/"
UV_LOCKS = ("nix/uv.lock", "tools/uv.lock")
PYPI = "https://pypi.org/simple"
PYPI_FILES = frozenset({"files.pythonhosted.org"})
NPM_HOSTS = frozenset({"registry.yarnpkg.com", "registry.npmjs.org"})
_SHA = re.compile(r"[0-9a-f]{40}")
_HASH = re.compile(r"[0-9a-f]{64}")
_STAMP_KEYS = frozenset({"package.json", "yarn.lock", "forced"})


@dataclass
class Result:
	rejects: list[str] = field(default_factory=list)
	reviews: list[str] = field(default_factory=list)

	def decision(self) -> str:
		if self.rejects:
			return REJECT
		return REVIEW if self.reviews else OK

	def reasons(self) -> list[str]:
		lines = [f"reject: {r}" for r in self.rejects] + [f"needs-review: {r}" for r in self.reviews]
		return list(dict.fromkeys(lines))


# --- shared with rolloutpatch.py --------------------------------------------------------


def _uv(text: str | None) -> dict:
	if text is None:
		return {}
	doc = tomllib.loads(text)
	return doc if isinstance(doc, dict) else {}


def _packages(doc: dict) -> list[dict]:
	pkgs = doc.get("package") or []
	return [p for p in pkgs if isinstance(p, dict)] if isinstance(pkgs, list) else []


def _uv_urls(pkg: dict) -> list[str]:
	urls = []
	for dist in [pkg.get("sdist"), *(pkg.get("wheels") or [])]:
		if isinstance(dist, dict) and isinstance(dist.get("url"), str):
			urls.append(dist["url"])
	return urls


@dataclass
class BaseLocks:
	"""What the base's locks already trust: sources, registries, file URLs, resolved URLs, and
	each uv ``(name, version)``'s source and files as the base locks them."""

	sources: set[str] = field(default_factory=set)
	urls: set[str] = field(default_factory=set)
	resolved: set[str] = field(default_factory=set)
	artifacts: dict[tuple[str, str], list[Any]] = field(default_factory=dict)


def _norm(name: str) -> str:
	"""A distribution name normalised the way PEP 503 and wheel filenames compare it."""
	return re.sub(r"[-_.]+", "-", name).lower()


def _same_version(a: str, b: str) -> bool:
	try:
		return Version(a) == Version(b)
	except InvalidVersion:
		return a == b


def _source_key(source: dict) -> list[str]:
	return [f"{kind}={source[kind]}" for kind in sorted(source) if isinstance(source[kind], str)]


# A segment of a local source path: what a package or app directory is called. No ``.``/``..``,
# separator, drive, scheme, ``~``, whitespace or control character can be one.
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


def _local_segments(value: object) -> tuple[str, ...] | None:
	"""``value``'s segments if it is a plain relative path (``apps/frappe/flow/harness``) and
	nothing else: not absolute, no ``..``/``.``/empty segment, no backslash, ``:`` or ``~``; else
	``None``. Read as text only, so the same string means the same tree everywhere it is read."""
	if not isinstance(value, str) or not value:
		return None
	segments = tuple(value.split("/"))
	return segments if all(_PATH_SEGMENT.fullmatch(s) for s in segments) else None


def inside_trusted_tree(kind: str, value: object, trusted: BaseLocks) -> bool:
	"""Whether a uv ``editable`` or ``directory`` source is a package inside the directory of one
	the base already installs ``editable``: Frappe's flow harness (``apps/frappe/flow/harness``,
	from 16.60) under ``apps/frappe``. Its files are that app's own, pinned by the same
	``flake.lock`` entry (which a relock does not change), so the lock names nothing the base
	did not already build from. The match is by whole segments (``apps/frappe-x`` is not under
	``apps/frappe``), and the tree itself, ``.`` or a new top-level directory are not inside one."""
	if kind not in ("editable", "directory") or (path := _local_segments(value)) is None:
		return False
	for key in trusted.sources:
		root_kind, _, root_value = key.partition("=")
		root = _local_segments(root_value) if root_kind == "editable" else None
		if root is not None and len(path) > len(root) and path[: len(root)] == root:
			return True
	return False


def _edges(pkg: dict) -> str:
	"""A uv package's dependency edges (``dependencies``, ``optional-dependencies``,
	``dev-dependencies``, markers and extras included), as one canonical string."""
	return json.dumps(
		{k: pkg.get(k) for k in ("dependencies", "optional-dependencies", "dev-dependencies")},
		sort_keys=True,
		default=str,
	)


def _artifacts(pkg: dict) -> Any:
	"""A uv package's source, files (URL and hash each) and dependency edges: what its name and
	version stand for. An entry the base has by name and version but with other edges is not
	the base's: it pulls in other packages."""
	dists = []
	for dist in [pkg.get("sdist"), *(pkg.get("wheels") or [])]:
		if isinstance(dist, dict):
			dists.append((str(dist.get("url", dist.get("path", ""))), str(dist.get("hash", ""))))
	source = pkg.get("source")
	return (sorted(_source_key(source)) if isinstance(source, dict) else [], sorted(dists), _edges(pkg))


_SDIST_EXT = (".tar.gz", ".zip", ".tar.bz2", ".tgz", ".tar.xz", ".tar")


def pypi_file_problem(name: str, version: str, url: str) -> str | None:
	"""Why a files.pythonhosted.org ``url`` is not a file of ``name`` ``version``, or ``None``."""
	filename = urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(url).path).name)
	if filename.endswith(".whl"):
		parts = filename[: -len(".whl")].split("-")
		dist, ver = (parts[0], parts[1]) if len(parts) >= 5 else ("", "")
	else:
		stem = next((filename[: -len(e)] for e in _SDIST_EXT if filename.endswith(e)), "")
		dist, _, ver = stem.rpartition("-")
	if not dist or _norm(dist) != _norm(name) or not _same_version(ver, version):
		return f"{name} {version} downloads {filename!r}, which is not a file of that package and version"
	return None


_YARN_KEY = re.compile(r"^(?![\s#])(?P<keys>.+):\s*$")
_YARN_RESOLVED = re.compile(r'^\s+resolved\s+"?(?P<url>[^"\s]+)"?\s*$')


def _yarn_name(spec: str) -> tuple[str, str]:
	"""A yarn key's ``(name, registry name)``: ``a@npm:b@^1`` is ``a``, installed from ``b``."""
	at = spec.find("@", 1)
	name, rng = (spec[:at], spec[at + 1 :]) if at > 0 else (spec, "")
	if rng.startswith("npm:"):
		target = rng[len("npm:") :]
		at = target.find("@", 1)
		return name, target[:at] if at > 0 else target
	return name, name


def _yarn_range(spec: str) -> str:
	"""A yarn key's range: ``a@^1`` is ``^1``, ``a@npm:b@^1`` is ``^1`` too."""
	at = spec.find("@", 1)
	rng = spec[at + 1 :] if at > 0 else ""
	if rng.startswith("npm:"):
		target = rng[len("npm:") :]
		at = target.find("@", 1)
		rng = target[at + 1 :] if at > 0 else ""
	return rng


def npm_url_problem(spec: str, fields: dict[str, str]) -> str | None:
	"""Why an entry's npm registry URL is not the tarball of the package and version it names."""
	url = fields.get("resolved", "")
	parts = urllib.parse.urlsplit(url)
	if (parts.hostname or "") not in NPM_HOSTS:
		return None
	_name, registry = _yarn_name(spec)
	base_name = registry.rsplit("/", 1)[-1]
	want = f"/{registry}/-/{base_name}-{fields.get('version', '')}.tgz"
	if urllib.parse.unquote(parts.path) != want:
		return f"{spec} resolves {url[:200]}, not {registry} {fields.get('version', '?')}'s tarball"
	return None


def yarn_entries(text: str) -> tuple[set[str], set[str]]:
	"""A yarn (classic) lock's package names and ``resolved`` URLs."""
	names: set[str] = set()
	urls: set[str] = set()
	for line in text.splitlines():
		if m := _YARN_KEY.match(line):
			for spec in m["keys"].split(","):
				spec = spec.strip().strip('"')
				at = spec.find("@", 1)
				names.add(spec[:at] if at > 0 else spec)
		elif m := _YARN_RESOLVED.match(line):
			urls.add(m["url"])
	return names, urls


def node_lock_seed(path: str, content: bytes, major: int | None) -> bool:
	"""Whether ``path`` (under ``nix/node-locks/``) is the seed this frappe-nix-tools ships for it."""
	if major is None:
		return False
	rel = path.removeprefix(NODE_LOCKS)
	try:
		seed = data_path(f"node-locks/version-{major}/{rel}")
	except ConfigError:
		return False
	return seed.is_file() and seed.read_bytes() == content


def trusted_locks(base: dict[str, bytes]) -> BaseLocks:
	"""The base's lock files (``path → bytes``) as what they already trust."""
	out = BaseLocks()
	for path, data in base.items():
		text = data.decode("utf-8", errors="replace")
		if path in UV_LOCKS:
			try:
				doc = _uv(text)
			except tomllib.TOMLDecodeError:
				continue
			for pkg in _packages(doc):
				if isinstance(pkg.get("source"), dict):
					out.sources.update(_source_key(pkg["source"]))
				out.urls.update(_uv_urls(pkg))
				key = (_norm(str(pkg.get("name", ""))), str(pkg.get("version", "")))
				out.artifacts.setdefault(key, []).append(_artifacts(pkg))
		elif path.endswith("yarn.lock"):
			out.resolved |= yarn_entries(text)[1]
	return out


# --- the relock's checks -----------------------------------------------------------------


def _git_repo(url: str) -> str | None:
	"""``https://github.com/<owner>/<repo>`` of a uv git source (query and commit dropped)."""
	parts = urllib.parse.urlsplit(url)
	segments = [s for s in parts.path.split("/") if s]
	if parts.scheme != "https" or (parts.hostname or "") != "github.com" or len(segments) != 2:
		return None
	return f"https://github.com/{segments[0].lower()}/{segments[1].lower().removesuffix('.git')}"


def _base_git_repos(trusted: BaseLocks) -> set[str]:
	out = set()
	for key in trusted.sources:
		kind, _, value = key.partition("=")
		if kind == "git" and (repo := _git_repo(value)):
			out.add(repo)
	return out


def _more(items: list[str]) -> str:
	return ", ".join(items[:10]) + (" …" if len(items) > 10 else "")


# A relock's yarn.lock, read strictly. Three programs read it after the push: the offline mirror
# (python-devops-nix lib/node/yarn-lock.nix: its own line regexes, last field wins), nixpkgs'
# fixup-yarn-lock and yarn (@yarnpkg/lockfile's tokenizer). A line one of them reads and the
# others skip is how a lock can say one thing to this check and another to the build, so a lock
# that is not exactly the shape yarn writes is refused rather than read the lenient way.
_LINE_BREAKS = re.compile("[\0\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]")
# What yarn writes unquoted (its lockfile stringify's shouldWrapKey is false): a letter first, and
# nothing its tokenizer stops at or reads as a boolean.
_BARE = r'(?!true|false)[a-zA-Z][^\s:"\\,\[\]#]*'
_QUOTED = r'"[^"\\\x00-\x1f\x7f]+"'  # a key or dependency range: spaces allowed, no escapes
_SRI = r"sha(?:1|256|384|512)-[A-Za-z0-9+/]+=*"
_STRICT_KEY = re.compile(rf"(?:{_QUOTED}|{_BARE})(?:, (?:{_QUOTED}|{_BARE}))*:")
_STRICT_FIELD = re.compile(
	rf'  (?P<field>version|resolved|integrity|name|uid) (?:"(?P<q>[^"\\\s]*)"|(?P<bare>{_BARE})'
	rf'|"(?P<sri>{_SRI}(?: {_SRI})+)")'
)
_STRICT_DEPS = re.compile(r"  (?:dependencies|optionalDependencies):")
_STRICT_DEP = re.compile(rf'    (?:{_QUOTED}|{_BARE}) (?:"[^"\\\x00-\x1f\x7f]*"|{_BARE})')
# lib/node/yarn-lock.nix's keyOf and fieldOf, as builtins.match applies them (whole line). Copied,
# so tests/standards/lockcheck.nix runs that file's `parse`, at the python-devops-nix revision
# flake.lock pins, over tests/yarn_corpus and the shipped seeds and compares it with nix_yarn.
_NIX_KEY = re.compile(r"([^ \t#].*):[ \t]*", re.S)
_NIX_FIELD = re.compile(r'  (resolved|integrity|version) "?([^"]*)"?[ \t]*', re.S)
_NIX_FIELDS = ("version", "resolved", "integrity")


@dataclass
class YarnLock:
	"""A yarn (classic) lock as the strict reading has it: entries in order (key line, fields and
	dependency blocks), each spec's, and what is wrong with the file (nothing, for a lock yarn
	wrote)."""

	entries: list[tuple[str, dict[str, str]]] = field(default_factory=list)
	specs: dict[str, dict[str, str]] = field(default_factory=dict)
	errors: list[str] = field(default_factory=list)


def _specs(keys: str) -> list[str]:
	"""A strict key line's specs, quotes off (a quoted one may hold a comma)."""
	return [s.strip('"') for s in re.findall(rf"{_QUOTED}|{_BARE}", keys)]


def strict_yarn(text: str) -> YarnLock:
	"""``text`` read line by line (``\\n`` only), accepting only what yarn writes: blank lines,
	comments, an unindented key line, two-space ``version``/``resolved``/``integrity``/``name``/
	``uid`` lines with one value each (quoted with no whitespace or escape, or bare), two-space
	``dependencies:``/``optionalDependencies:`` headers and the four-space lines under them. A field
	twice in one entry, a spec in two entries, or any other line is an error. An entry keeps its
	dependency lines, under their block's header line (``"  dependencies:"``), so an entry whose
	dependency edges change is a changed entry."""
	out = YarnLock()
	if m := _LINE_BREAKS.search(text):
		out.errors.append(f"has a {m[0]!r}, a line break only some of its readers see")
	fields: dict[str, str] | None = None
	in_deps = ""  # the header of the dependency block the next lines belong to, if any
	for n, line in enumerate(text.split("\n"), 1):
		bad = f"line {n} {line[:120]!r} is not a line yarn writes"
		if not line or line.startswith("#"):
			continue
		if _STRICT_KEY.fullmatch(line):
			fields, in_deps = {}, ""
			out.entries.append((line[:-1], fields))
			for spec in _specs(line[:-1]):
				if spec in out.specs:
					out.errors.append(f"line {n}: {spec} is in two entries")
				out.specs[spec] = fields
		elif fields is None:
			out.errors.append(bad)
		elif m := _STRICT_FIELD.fullmatch(line):
			in_deps = ""
			if m["field"] in fields:
				out.errors.append(f"line {n}: {m['field']} twice in one entry")
			if m["sri"] is not None and m["field"] != "integrity":
				out.errors.append(bad)
			fields[m["field"]] = next(v for v in (m["q"], m["bare"], m["sri"]) if v is not None)
		elif _STRICT_DEPS.fullmatch(line):
			if line in fields:
				out.errors.append(f"line {n}: {line.strip()} twice in one entry")
			fields[line] = ""
			in_deps = line
		elif in_deps and _STRICT_DEP.fullmatch(line):
			fields[in_deps] += line.strip() + "\n"
		else:
			out.errors.append(bad)
	return out


def nix_yarn(text: str) -> list[tuple[str, dict[str, str]]]:
	"""The entries lib/node/yarn-lock.nix's ``parse`` finds in ``text``: what the offline mirror
	fetches, by its own reading."""
	out: list[tuple[str, dict[str, str]]] = []
	for line in text.split("\n"):
		if k := _NIX_KEY.fullmatch(line):
			out.append((k[1], {}))
		elif (f := _NIX_FIELD.fullmatch(line)) and out:
			out[-1][1][f[1]] = f[2]
	return out


def uv_problems(base_text: str | None, head_text: str, trusted: BaseLocks) -> tuple[list[str], list[str]]:
	"""``(rejects, reviews)`` for ``nix/uv.lock``: only what the base locks, or PyPI's files for the
	package and version they name."""
	path = UV_LOCK
	try:
		head, base = _uv(head_text), _uv(base_text)
	except tomllib.TOMLDecodeError as e:
		return [f"{path}: not TOML: {e}"], []
	rejects: list[str] = []
	reviews: list[str] = []
	git_repos = _base_git_repos(trusted)
	for pkg in _packages(head):
		name, version = str(pkg.get("name", "")), str(pkg.get("version", ""))
		known = trusted.artifacts.get((_norm(name), version))
		if known is not None and _artifacts(pkg) in known:
			continue  # this name and version, from the same source with the same files, as the base
		source = pkg.get("source")
		if not isinstance(source, dict) or not source:
			rejects.append(f"{path}: {name} {version} has no source")
			continue
		for kind, value in source.items():
			if f"{kind}={value}" in trusted.sources or (kind == "registry" and value == PYPI):
				continue
			if inside_trusted_tree(kind, value, trusted):
				continue
			if kind == "git" and isinstance(value, str) and _git_repo(value) in git_repos:
				reviews.append(f"{path}: {name} {version} moves to {value}, another commit of a git source")
				continue
			rejects.append(
				f"{path}: {name} {version} comes from {kind} {str(value)[:200]!r}, which is not allowed"
			)
		for dist in [pkg.get("sdist"), *(pkg.get("wheels") or [])]:
			if dist is None:
				continue
			url = dist.get("url") if isinstance(dist, dict) else None
			if not isinstance(url, str):
				rejects.append(f"{path}: {name} {version} has a file with no URL")
				continue
			if url in trusted.urls:
				continue
			parts = urllib.parse.urlsplit(url)
			if parts.scheme != "https" or (parts.hostname or "") not in PYPI_FILES:
				rejects.append(f"{path}: {name} {version} downloads {url[:200]}, which is not on PyPI")
			elif problem := pypi_file_problem(name, version, url):
				rejects.append(f"{path}: {problem}")
	added = sorted(
		{_norm(str(p.get("name", ""))) for p in _packages(head)}
		- {_norm(str(p.get("name", ""))) for p in _packages(base)}
	)
	if added:
		reviews.append(f"{path} adds {_more(added)}")
	return rejects, list(dict.fromkeys(reviews))


def yarn_problems(
	path: str, base_text: str | None, head_text: str, trusted: BaseLocks
) -> tuple[list[str], list[str]]:
	"""``(rejects, reviews)`` for one ``nix/node-locks/**/yarn.lock``."""
	if "__metadata:" in head_text:
		return [f"{path} is a yarn berry lock, which relock never writes"], []
	head = strict_yarn(head_text)
	if head.errors:
		return [f"{path}: {e}" for e in head.errors[:10]], []
	nix = nix_yarn(head_text)
	ours = [(key, {f: v for f, v in fields.items() if f in _NIX_FIELDS}) for key, fields in head.entries]
	if nix != ours:
		return [f"{path}: lib/node/yarn-lock.nix would read its entries differently from yarn"], []
	rejects: list[str] = []
	reviews: list[str] = []
	urls = {f["resolved"] for _k, f in head.entries if "resolved" in f}
	urls |= {f["resolved"] for _k, f in nix if "resolved" in f}
	for url in sorted(urls):
		parts = urllib.parse.urlsplit(url)
		if (
			not (parts.scheme == "https" and (parts.hostname or "") in NPM_HOSTS)
			and url not in trusted.resolved
		):
			rejects.append(f"{path}: resolves {url[:200]}, which is not on the npm registry")
	# The base is the integration branch's: read the same way, best effort, so an entry it holds
	# in a shape the strict reading skips counts as changed and is checked, never the reverse.
	before = strict_yarn(base_text).specs if base_text else {}
	unresolved, renamed, aliases, outside = [], [], [], []
	for spec, fields in head.specs.items():
		if before.get(spec) == fields:
			continue
		if npmrange.satisfies(fields.get("version", ""), _yarn_range(spec)) is not True:
			outside.append(f"{spec} ({fields.get('version', 'no version')})")
		name, registry = _yarn_name(spec)
		if name != registry and spec not in before:
			aliases.append(f"{spec} ({registry})")
		if "name" in fields or fields.get("uid"):
			renamed.append(f"{spec} ({fields.get('name') or fields.get('uid')})")
		if "resolved" not in fields:
			unresolved.append(spec)
		elif problem := npm_url_problem(spec, fields):
			rejects.append(f"{path}: {problem}")
	if unresolved:
		reviews.append(f"{path}: {_more(unresolved)} resolve to no URL")
	if renamed:
		reviews.append(f"{path}: {_more(renamed)} set a name or uid")
	if aliases:
		reviews.append(f"{path}: {_more(aliases)} install a package under another name (npm: alias)")
	if outside:
		reviews.append(
			f"{path}: {_more(outside)} lock a version their range does not allow, or a range"
			" this check does not read"
		)
	# Both names of each spec: an npm: alias under a name the base has still installs its target.
	names = {n for s in head.specs for n in _yarn_name(s)} - {n for s in before for n in _yarn_name(s)}
	added = sorted(names)
	if added:
		reviews.append(f"{path} adds {_more(added)}")
	return rejects, reviews


def _stamp(data: bytes | None) -> dict | None:
	if data is None:
		return None
	try:
		doc = json.loads(data)
	except (json.JSONDecodeError, UnicodeDecodeError):
		return None
	return doc if isinstance(doc, dict) else None


def stamp_problems(path: str, base: bytes | None, head: bytes, seed: bytes | None) -> list[str]:
	"""Rejects for one ``source.json``: the base's (or, for a target the base does not have, the
	seed's), or that stamp with other hashes."""
	if head == (seed if base is None else base):
		return []
	doc = _stamp(head)
	if doc is None:
		return [f"{path}: not a JSON object"]
	out = []
	if set(doc) - _STAMP_KEYS or "package.json" not in doc:
		out.append(f"{path}: keys {sorted(doc)}, not package.json, yarn.lock and forced")
	for key in ("package.json", "yarn.lock"):
		if key in doc and not (isinstance(doc[key], str) and _HASH.fullmatch(doc[key])):
			out.append(f"{path}: {key} is not a sha256")
	if doc.get("forced", True) is not True:
		out.append(f"{path}: forced is {doc['forced']!r}, not true or absent")
	was = _stamp(base) if base is not None else _stamp(seed)
	if bool(doc.get("forced")) != bool((was or {}).get("forced")):
		out.append(f"{path}: changes forced, which decides which lock builds the target")
	return out


def _seed(path: str, major: int | None) -> bytes | None:
	if major is None:
		return None
	try:
		seed = data_path(f"node-locks/version-{major}/{path.removeprefix(NODE_LOCKS)}")
	except ConfigError:
		return None
	return seed.read_bytes() if seed.is_file() else None


def _pruned_forced(path: str, base: dict[str, bytes], head: dict[str, bytes]) -> tuple[str, str] | None:
	"""A deleted node-lock file of a target the base forces: lib/bench.nix forces a target only
	while both its ``yarn.lock`` and a ``forced`` stamp are there, so deleting either turns the
	override off and the app's own lock builds it again. One of the two is ``reject``; the whole
	target is ``needs-review`` (an honest relock prunes a target only when it is gone)."""
	target = str(PurePosixPath(path).parent)
	pair = (f"{target}/yarn.lock", f"{target}/source.json")
	if path not in pair or not (_stamp(base.get(pair[1])) or {}).get("forced"):
		return None
	if kept := [p for p in pair if p in head]:
		return REJECT, f"{path}: deleted while {kept[0]} stays, which turns the forced lock off"
	return REVIEW, f"{target}: a forced lock is pruned, so the app's own yarn.lock builds it again"


def check(base: dict[str, bytes], head: dict[str, bytes], major: int | None) -> Result:
	"""Check the lock files ``head`` (``path → bytes``) against ``base``'s."""
	res = Result()
	trusted = trusted_locks(base)
	for path in sorted(set(base) | set(head)):
		before, after = base.get(path), head.get(path)
		if before == after:
			continue
		if after is None:
			if path == UV_LOCK:
				res.rejects.append(f"{path}: deleted")
			elif path.startswith(NODE_LOCKS) and (problem := _pruned_forced(path, base, head)):
				(res.rejects if problem[0] == REJECT else res.reviews).append(problem[1])
			continue  # a node-lock target pruned: its app ships its own lock again, or is gone
		try:
			text = after.decode("utf-8")
		except UnicodeDecodeError:
			res.rejects.append(f"{path}: not UTF-8 text")
			continue
		before_text = before.decode("utf-8", errors="replace") if before is not None else None
		name = PurePosixPath(path).name
		if path == UV_LOCK:
			rejects, reviews = uv_problems(before_text, text, trusted)
		elif path.startswith(NODE_LOCKS) and name == "yarn.lock":
			if before is None and node_lock_seed(path, after, major):
				continue
			rejects, reviews = yarn_problems(path, before_text, text, trusted)
		elif path.startswith(NODE_LOCKS) and name == "source.json":
			rejects, reviews = stamp_problems(path, before, after, _seed(path, major)), []
		else:
			rejects, reviews = [f"{path}: not a file relock writes"], []
		res.rejects += rejects
		res.reviews += reviews
	return res


# --- reading the trees ---------------------------------------------------------------------


def _git(root: Path, *args: str) -> bytes:
	"""``git`` in ``root``, bytes out; no hook or fsmonitor of the checkout runs."""
	cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *NO_AUTO_MAINTENANCE]
	try:
		proc = subprocess.run([*cmd, "-C", str(root), *args], capture_output=True, check=False)
	except FileNotFoundError as e:
		raise EnvError("git is not on PATH") from e
	if proc.returncode != 0:
		raise EnvError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
	return proc.stdout


def lock_files(root: Path, rev: str) -> tuple[dict[str, bytes], list[str]]:
	"""``nix/uv.lock`` and ``nix/node-locks/**`` at ``rev`` (a commit or a tree), and the entries
	that are not regular files."""
	listing = _git(root, "ls-tree", "-r", "-z", "--full-tree", rev, "--", UV_LOCK, NODE_LOCKS.rstrip("/"))
	files: dict[str, bytes] = {}
	odd: list[str] = []
	for entry in listing.split(b"\0"):
		if not entry:
			continue
		meta, _, raw = entry.partition(b"\t")
		mode, kind, oid = meta.decode().split()
		path = raw.decode("utf-8", errors="replace")
		if kind != "blob" or mode != "100644":
			odd.append(f"{path}: mode {mode} ({kind}), not a regular file")
			continue
		files[path] = _git(root, "cat-file", "blob", oid)
	return files, odd


def frappe_major(root: Path, rev: str) -> int | None:
	"""``[tool.frappe-nix].frappe-major`` in ``pyproject.toml`` at ``rev``: which seeds apply."""
	try:
		doc = tomllib.loads(_git(root, "cat-file", "blob", f"{rev}:pyproject.toml").decode())
	except (EnvError, UnicodeDecodeError, tomllib.TOMLDecodeError):
		return None
	major = doc.get("tool", {}).get("frappe-nix", {}).get("frappe-major")
	return major if isinstance(major, int) and not isinstance(major, bool) else None


def verify(root: Path, base: str, head: str) -> Result:
	"""Check the lock files of ``head`` (a commit or tree id) against those of the commit ``base``."""
	for flag, value in (("--base", base), ("--head", head)):
		if not _SHA.fullmatch(value):
			raise ConfigError(f"{flag} {value!r} is not a full object id")
	base_files, _odd = lock_files(root, base)
	head_files, odd = lock_files(root, head)
	res = check(base_files, head_files, frappe_major(root, base))
	res.rejects[:0] = odd
	return res
