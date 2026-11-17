"""What a relock's lock files may say before a job that holds the App's key pushes them (§4.5).

``relock-plan`` and ``relock-upgrade-plan`` run the app's and third-party code, on the caller's
runner (a shared self-hosted one, possibly). Their patch is data: the push jobs' "Check and
apply" step bounds the *paths* it may touch (``nix/uv.lock``, ``nix/node-locks/**`` and, for a
Dependabot relock, ``types/doctypes.d.ts``), and ``frappe-nix ci verify-relock`` bounds what the
lock files *say*, before the token is minted and before auto-merge is enabled. It compares the
lock files of the tree to be pushed (``--head``) with a commit the integration branch holds
(``--base``: the PR's merge-base, or the commit the weekly relock ran on), read from git:

- ``nix/uv.lock`` may not be deleted. Every package entry that is not the base's (by name,
  version, source and files) comes from PyPI (``registry = "https://pypi.org/simple"``) or from a
  source the base lock already has, and each of its files is an https URL on
  files.pythonhosted.org that names that package and version, or a URL the base lock already
  has. A git source the base has at another commit (frappe's ``pypika`` or ``gunicorn`` fork after
  a frappe bump) is ``needs-review``; any other new source is ``reject``.
- ``nix/node-locks/**`` holds only ``yarn.lock`` and ``source.json`` files. In a ``yarn.lock``
  (classic; a berry lock is ``reject``) every ``resolved`` URL is https on registry.yarnpkg.com or
  registry.npmjs.org, or one the base's locks already have, and an entry that is not the base's
  resolves the npm tarball of the name and version it names (``/<name>/-/<base name>-<version>.tgz``).
  A ``source.json`` stamp is the base's, the seed's, or the same stamp with other manifest hashes:
  only ``forced`` steers the build (lib/bench.nix takes the fallback lock over the app's own when
  it is set), so it must stay what the base (or, for a new target, the seed) says.
- A package name no base lock of that file had (uv or yarn), or a new yarn entry without a
  ``resolved`` URL, is ``needs-review``: an honest relock adds one only when the bumped
  dependency does, and a person should see it.

The decision is ``ok``, ``needs-review`` (pushed, labelled, auto-merge off) or ``reject`` (the
step fails and nothing is pushed). It runs no nix, uv or yarn, and reads git only.

The helpers from ``_uv`` to ``trusted_locks`` are rolloutpatch.py's (frappe-nix#281, the
release-driven rollout's patch check), copied as they are so that the two share this module
when #281 lands.
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


# --- shared with rolloutpatch.py (#281) ------------------------------------------------


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


def _artifacts(pkg: dict) -> Any:
	"""A uv package's source and files (URL and hash each): what its name and version stand for."""
	dists = []
	for dist in [pkg.get("sdist"), *(pkg.get("wheels") or [])]:
		if isinstance(dist, dict):
			dists.append((str(dist.get("url", dist.get("path", ""))), str(dist.get("hash", ""))))
	source = pkg.get("source")
	return (sorted(_source_key(source)) if isinstance(source, dict) else [], sorted(dists))


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
_YARN_FIELD = re.compile(r'^  (?P<field>version|resolved|integrity)\s+"?(?P<value>[^"\s]+)"?\s*$')


def _yarn_name(spec: str) -> tuple[str, str]:
	"""A yarn key's ``(name, registry name)``: ``a@npm:b@^1`` is ``a``, installed from ``b``."""
	at = spec.find("@", 1)
	name, rng = (spec[:at], spec[at + 1 :]) if at > 0 else (spec, "")
	if rng.startswith("npm:"):
		target = rng[len("npm:") :]
		at = target.find("@", 1)
		return name, target[:at] if at > 0 else target
	return name, name


def yarn_blocks(text: str) -> dict[str, dict[str, str]]:
	"""A yarn (classic) lock's entries: each key → its ``version``, ``resolved`` and ``integrity``."""
	out: dict[str, dict[str, str]] = {}
	fields: dict[str, str] | None = None
	for line in text.splitlines():
		if m := _YARN_KEY.match(line):
			fields = {}
			for spec in m["keys"].split(","):
				out[spec.strip().strip('"')] = fields
		elif fields is not None and (m := _YARN_FIELD.match(line)):
			fields[m["field"]] = m["value"]
	return out


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
	rejects: list[str] = []
	reviews: list[str] = []
	_names, urls = yarn_entries(head_text)
	for url in sorted(urls):
		parts = urllib.parse.urlsplit(url)
		if (
			not (parts.scheme == "https" and (parts.hostname or "") in NPM_HOSTS)
			and url not in trusted.resolved
		):
			rejects.append(f"{path}: resolves {url[:200]}, which is not on the npm registry")
	before = yarn_blocks(base_text) if base_text else {}
	unresolved = []
	for spec, fields in yarn_blocks(head_text).items():
		if before.get(spec) == fields:
			continue
		if "resolved" not in fields:
			unresolved.append(spec)
		elif problem := npm_url_problem(spec, fields):
			rejects.append(f"{path}: {problem}")
	if unresolved:
		reviews.append(f"{path}: {_more(unresolved)} resolve to no URL")
	added = sorted(yarn_entries(head_text)[0] - (yarn_entries(base_text)[0] if base_text else set()))
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
	"""Rejects for one ``source.json``: the base's or the seed's, or that stamp with other hashes."""
	if head in (base, seed):
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
