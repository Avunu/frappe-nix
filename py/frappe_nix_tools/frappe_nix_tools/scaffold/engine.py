"""The plan: every managed file's current and wanted state, computed in memory.

``--check`` reports the plan; ``--write`` applies it. Both run the same code, so
``--check`` exits 0 right after a ``--sync`` that exited 0 (spec §3.4).

An ``Item`` is one file: what is on disk, what sync wants there (``None`` to delete it),
and the problem when the two differ or a rule is broken. A problem sync can fix is drift;
one it can't (a rule on an app-owned key) carries its own exit code and no new content.

An entry is live when one of its modules is on and its ``when`` holds (§2.4). A file whose
entry is not live is retracted as §3.3 step 6 says: a ``whole`` file that still opens with
its managed header and has empty local regions is deleted (with content in a local region
it is exit 2), a seed is left, the merged files drop the keys of the modules that turned
off, and the version block loses its markers. A module "turned off" when it is off now and
was on in a configuration committed since the app opted in: a ``[tool.frappe-nix]`` table
with the profile committed beside it (``history``).

Every path is checked once rendered (``inside``): relative, no ``..``, and no symlink on the
way, since ``--check`` runs on untrusted pull requests.
"""

import contextlib
import copy
import difflib
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import frappe_nix_tools
from frappe_nix_tools.common import config, data_path, flakelock, known_apps, pins, pyproject, repo
from frappe_nix_tools.common.config import NotOptedIn, Resolved
from frappe_nix_tools.common.report import CLEAN, DRIFT, ENVIRONMENT, INVALID, ConfigError, EnvError, Finding
from frappe_nix_tools.listing import baseline, readme
from frappe_nix_tools.scaffold import (
	blocks,
	context,
	discover,
	floors,
	globs,
	hooks,
	jsonfmt,
	manifest,
	package_json,
	regions,
	semantic,
	tomlmerge,
)
from frappe_nix_tools.scaffold import render as rendering

# The version of an app that names none anywhere (§2.8: "<__version__ or 0.1.0>").
DEFAULT_VERSION = "0.1.0"
BLAME_LINE = re.compile(r"^[0-9a-f]{40}  # \S.*$")
# An input's name: a Nix identifier, or a quoted one (render.nix_attr) such as "my.app".
_FLAKE_NAME = r"""(?:(?P<name>[A-Za-z_][A-Za-z0-9_'-]*)|"(?P<quoted>[^"\\$]*)")"""
_FLAKE_INPUT = re.compile(rf"^\s{{4}}{_FLAKE_NAME}\s*=\s*\{{")
_FLAKE_ATTR = re.compile(rf'^\s{{4}}{_FLAKE_NAME}\.(?P<attr>url|follows)\s*=\s*"(?P<value>[^"]*)";')
_FLAKE_INNER = re.compile(r'^\s{6}(?P<attr>url|follows)\s*=\s*"(?P<value>[^"]*)";')
# An input written on one line: `erpnext = { url = "github:…"; flake = false; };`.
_FLAKE_INLINE = re.compile(rf"^\s{{4}}{_FLAKE_NAME}\s*=\s*\{{(?P<body>.*)\}};\s*$")
_FLAKE_INLINE_ATTR = re.compile(r'(?:^|[;{\s])(?P<attr>url|follows)\s*=\s*"(?P<value>[^"]*)";')
# The inputs flake.nix manages; a local `inputs` region may not define one (§2.5).
MANAGED_INPUTS = ("frappe-nix", "nixpkgs", "frappe", flakelock.PROFILE_INPUT)
# The files frappe-init --app's templates/app gives an app that has not opted in (S35).
APP_TEMPLATE = {"flake.nix": "app-template/flake.nix.in", ".envrc": "app-template/envrc"}


@dataclass
class Item:
	path: str
	strategy: str
	current: str | None
	wanted: str | None
	problem: str = ""
	code: int = CLEAN
	command: str | None = None
	phase: str = "b"
	# The module that manages the file (several, comma-separated, for the shared files);
	# empty for a whole-repo check. ``--check --format json`` reports it per file (§3.3).
	module: str = ""

	@property
	def changes(self) -> bool:
		return self.code == DRIFT and (self.wanted != self.current or self.command is not None)

	def diff(self) -> str:
		if self.wanted is None and self.current is None:
			return ""
		a = (self.current or "").splitlines(keepends=True)
		b = (self.wanted or "").splitlines(keepends=True) if self.wanted is not None else []
		return "".join(difflib.unified_diff(a, b, "current", "rendered"))

	def finding(self) -> Finding:
		return Finding(
			self.path,
			self.strategy,
			self.problem,
			self.diff() if self.wanted != self.current else "",
			self.module,
		)


@dataclass
class Plan:
	root: Path
	app: context.App
	resolved: Resolved
	ctx: context.NS
	items: list[Item] = field(default_factory=list)
	created_config: dict | None = None
	# The one version the version block, the release-please manifest seed and package.json
	# are given when sync writes them (C5 holds from the first sync): see ``effective_version``.
	version: str = DEFAULT_VERSION
	# What sync leaves to the app and says so: keys of a module that turned off but were
	# edited, seeds of a module that is off, the resolver's notices.
	notices: list[str] = field(default_factory=list)

	@property
	def cfg(self) -> dict:
		return self.resolved.cfg

	def add(self, item: Item) -> None:
		self.items.append(item)

	@property
	def problems(self) -> list[Item]:
		return [i for i in self.items if i.code != CLEAN]

	@property
	def code(self) -> int:
		return max((i.code for i in self.items), default=CLEAN)


# Directories sync never manages, refused as any component of a managed path. A rendered path
# is data a pull request can set (an in-repo profile's [[extra-files]]): one into .git/ would
# print .git/config (a persisted checkout token) under --check, and under --write replace it
# with config the following ``git add`` obeys (core.fsmonitor, core.hooksPath). Compared
# case-folded and without trailing dots and spaces, as case-insensitive (macOS) and Windows
# file systems resolve them.
UNMANAGED_DIRS = frozenset({".git", ".direnv", ".frappe-nix", ".venv", "node_modules"})


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def unmanaged_dir(path: str) -> str | None:
	"""The component of ``path`` that names a directory sync never writes into, if any."""
	for part in PurePosixPath(path).parts:
		if part.rstrip(". ").casefold() in UNMANAGED_DIRS:
			return part
	return None


def inside(root: Path, path: str, *, link_ok: bool = False) -> Path:
	"""``root / path``, once ``path`` is known to stay inside the app with no link on the way.

	``path`` is a rendered managed path (an entry's, an ``[[extra-files]]`` one, a node-lock
	seed): it must be relative, with no ``..``, and no existing component of it may be a
	symlink (the last one may, with ``link_ok``, for a retired link sync deletes unread), and
	none may be one of ``UNMANAGED_DIRS`` (``.git`` above all).
	``--check`` runs on untrusted pull requests: a committed link (``tools`` pointing outside
	the checkout) would otherwise print the file it reaches in the diff, and ``--write`` would
	write through it. Exit 2."""
	parts = PurePosixPath(path).parts
	if not parts or PurePosixPath(path).is_absolute() or ".." in parts:
		raise ConfigError(
			f"{path!r} is not a path inside the app: a managed path must be relative, without .."
		)
	if _CONTROL.search(path):
		# A newline in a path would start a line of its own in every message that names it
		# (a workflow command in a CI log, a forged finding).
		raise ConfigError(f"{path!r} is not a path sync may manage: it holds a control character")
	if (part := unmanaged_dir(path)) is not None:
		raise ConfigError(
			f"{path!r} is not a path sync may manage: it is inside {part}/, which sync never reads or writes"
		)
	cur = root
	for i, part in enumerate(parts):
		cur = cur / part
		last = i == len(parts) - 1
		if cur.is_symlink() and not (last and link_ok):
			if last:
				raise ConfigError(
					f"{path} is a symlink: a managed file must be a regular file (remove the link)"
				)
			raise ConfigError(
				f"{path}: {PurePosixPath(*parts[: i + 1])} is a symlink: sync never reads or writes through"
				" a link (remove it)"
			)
	return root / path


def read(root: Path, path: str) -> str | None:
	"""The text of ``path`` under ``root``; ``None`` when it is missing or a directory.

	A symlink anywhere on the path is refused, never followed (``inside``)."""
	inside(root, path)
	try:
		return (root / path).read_text()
	except FileNotFoundError:
		return None
	except IsADirectoryError:
		return None


# --- configuration -------------------------------------------------------------


_NIX_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|#[^\n]*')


def _input_name(m: re.Match[str]) -> str:
	return m["name"] if m["name"] is not None else m["quoted"]


def strip_nix_comments(text: str) -> str:
	"""``text`` without its ``#`` comments (string literals kept): templates/app's flake.nix
	shows siblings in comments, which are not the app's."""
	return _NIX_COMMENT.sub(lambda m: m[0] if m[0].startswith('"') else "", text)


def flake_facts(text: str | None) -> tuple[int | None, list[str], str | None]:
	"""``frappeVersion``'s major, the sibling names and the ``siteName`` an existing ``flake.nix`` declares."""
	if not text:
		return None, [], None
	text = strip_nix_comments(text)
	m = re.search(r'frappeVersion\s*=\s*"version-(\d+)"', text)
	block = re.search(r"siblings\s*=\s*\[(?P<body>.*?)\];", text, re.S)
	names = re.findall(r'name\s*=\s*"([^"]+)"', block["body"]) if block else []
	site = re.search(r'siteName\s*=\s*"([^"$\\]+)"', text)
	return (int(m[1]) if m else None), names, (site[1] if site else None)


def _sibling_entry(name: str, url: str | None, major: int, apps: dict) -> str | dict:
	"""How ``[tool.frappe-nix] siblings`` writes a sibling an existing flake declares: its
	known name or ``<owner>/<repo>``, or the object form when the generic rule would give it
	another flake URL than the one it has, so its branch is kept (§3.3 step 1)."""
	forge = re.fullmatch(r"github:([^/]+)/([^/?]+)(?:/([^?]+))?", url or "")
	spelling = name
	if forge and name not in apps:
		spelling = f"{forge[1]}/{forge[2]}"
	try:
		rule = known_apps.resolve(spelling, major, apps)
	except ConfigError:
		rule = None
	if url is None or (rule is not None and rule.flake_url.lower() == url.lower()):
		return spelling
	if forge:
		entry: dict[str, Any] = {"repo": f"{forge[1]}/{forge[2]}"}
		if forge[3]:
			entry["branch"] = forge[3]
		return entry
	# Off GitHub: the repo is the URL's path, the flake URL is kept as written.
	path = re.sub(r"^(gitlab:|git\+https://[^/]+/)", "", url).split("?", 1)[0].removesuffix(".git").strip("/")
	parts = path.split("/")
	if url.startswith("gitlab:") and len(parts) > 2:
		parts = parts[:-1]  # gitlab:<group>/<repo>/<ref>
	return {"repo": "/".join(parts) if len(parts) > 1 else f"{name}/{name}", "flake-url": url}


def flake_input_urls(text: str) -> dict[str, str]:
	"""Each input's ``url`` as written (no normalisation), for creating ``[tool.frappe-nix]``."""
	m = re.search(r"\n  inputs = \{\n(?P<body>.*?)\n  \};\n", text, re.S)
	out: dict[str, str] = {}
	block = None
	for line in m["body"].splitlines() if m else []:
		if (one := _FLAKE_ATTR.match(line)) and not block:
			if one["attr"] == "url":
				out[_input_name(one)] = one["value"]
		elif (inline := _FLAKE_INLINE.match(line)) and not block:
			for attr in _FLAKE_INLINE_ATTR.finditer(inline["body"]):
				if attr["attr"] == "url":
					out[_input_name(inline)] = attr["value"]
		elif (opened := _FLAKE_INPUT.match(line)) and not block:
			block = _input_name(opened)
		elif block and re.match(r"^\s{4}\};", line):
			block = None
		elif block and (inner := _FLAKE_INNER.match(line)) and inner["attr"] == "url":
			out[block] = inner["value"]
	return out


def new_config(
	root: Path,
	app: str,
	app_hooks: dict,
	frappe_version: str | None,
	package: dict | None,
	tracked: list[str],
	*,
	profile: str,
	site: str | None = None,
) -> dict:
	"""``[tool.frappe-nix]`` as ``--standards <profile>`` creates it (§3.3 step 1). ``site`` is
	``--site`` (``frappe-init --app --site``), which wins over the site an existing flake
	names. ``integration-branch`` is added by ``with_integration_branch`` once the profile's
	branching model is known."""
	major = None
	if frappe_version:
		m = re.fullmatch(r"version-(\d+)", frappe_version)
		if not m:
			raise ConfigError(f"--frappe-version must be version-<major>, not {frappe_version!r}")
		major = int(m[1])
	flake_text = strip_nix_comments(read(root, "flake.nix") or "")
	flake_major, flake_siblings, flake_site = flake_facts(flake_text)
	major = major or flake_major
	if major is None:
		raise ConfigError(
			"there is no [tool.frappe-nix] yet and nothing names the Frappe major: pass --frappe-version version-<N>"
		)
	urls = flake_input_urls(flake_text or "")
	apps = known_apps.merged()
	siblings: list[str | dict] = []
	seen: set[str] = set()
	for spelling in hooks.required_apps(app_hooks):
		bare = hooks.bare(spelling)
		if bare == "frappe" or bare in seen:
			continue
		seen.add(bare)
		entry = _sibling_entry(bare, urls.get(bare), major, apps)
		if isinstance(entry, dict):
			# Pinned elsewhere than the generic rule says: keep the flake's branch.
			if "/" in spelling:
				entry["repo"] = spelling
			siblings.append(entry)
		else:
			siblings.append(spelling)
	for name in flake_siblings:
		if name == "frappe" or name in seen:
			continue
		seen.add(name)
		siblings.append(_sibling_entry(name, urls.get(name), major, apps))
	cfg: dict[str, Any] = {"schema": 1, "profile": profile, "frappe-major": major}
	if siblings:
		cfg["siblings"] = siblings
	if site is not None and not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", site):
		raise ConfigError(f"--site {site!r} is not a site name (lower-case letters, digits, '.' and '-')")
	flake_site = site or flake_site
	# The dev site an existing frappe-nix flake already uses: a new name would orphan every
	# developer's site state (an app's own flake may name it after the app's short name).
	if (
		flake_site
		and re.fullmatch(r"[a-z0-9][a-z0-9.-]*", flake_site)
		and flake_site != f"{app.replace('_', '-')}.localhost"
	):
		cfg["site"] = flake_site
	# An app whose build script is a real step with nothing sync can see building:
	# say so, rather than create a configuration that fails its own first check. The
	# same discovery as the rendered package.json and compat's C8 (no SPA is declared yet).
	scripts = (package or {}).get("scripts") or {}
	facts = discover.facts(root, app, {}, tracked)
	if isinstance(scripts, dict) and "build" in scripts and not (facts["vite"] or facts["nested_frontends"]):
		cfg["build"] = True
	return cfg


def origin_default_branch(root: Path) -> str | None:
	"""The branch ``origin/HEAD`` names, when the clone knows it."""
	try:
		ref = repo.git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
	except EnvError:
		return None
	return ref.split("/", 1)[1] if "/" in ref else None


def with_integration_branch(root: Path, created: dict, resolved: Resolved) -> dict:
	"""``created`` plus ``integration-branch`` when ``origin/HEAD`` names a branch other than
	the branching model's default (S41; read once, at creation, so later syncs stay
	deterministic)."""
	branch = origin_default_branch(root)
	default = "main" if resolved.cfg["releases"]["branching"] == "main+tags" else "develop"
	if branch and branch != default:
		out = dict(created)
		out["integration-branch"] = branch
		return out
	return created


def load_app(root: Path) -> context.App:
	name = repo.app_name(root)
	doc = pyproject.load(root / "pyproject.toml")
	tracked = repo.ls_files(root)
	return context.App(root, name, doc, hooks.read(root / name / "hooks.py"), tracked)


def load_package(root: Path) -> dict | None:
	text = read(root, "package.json")
	if text is None:
		return None
	try:
		doc = json.loads(text)
	except json.JSONDecodeError as e:
		raise ConfigError(f"package.json: {e}") from e
	if not isinstance(doc, dict):
		raise ConfigError("package.json is not an object")
	return doc


def with_table(doc: dict, table: dict) -> dict:
	"""``doc`` (a parsed ``pyproject.toml``) with ``[tool.frappe-nix]`` set to ``table``."""
	out = copy.deepcopy(doc)
	out.setdefault("tool", {})["frappe-nix"] = copy.deepcopy(table)
	return out


def input_spec(attr: str, value: str) -> str:
	"""A flake input reference in one comparable form: ``follows:<a>/<b>``, or the URL with a
	forge's owner and repository lower-cased (Nix matches them case-insensitively)."""
	if attr == "follows":
		return f"follows:{value}"
	forge = re.fullmatch(r"(github|gitlab|sourcehut):([^/?]+)/([^/?]+)(?:/([^?]+))?", value)
	if not forge:
		return value
	kind, owner, repo_name, ref = forge.groups()
	return f"{kind}:{owner.lower()}/{repo_name.lower()}" + (f"/{ref}" if ref else "")


def locked_spec(lock: dict, ref: object) -> str | None:
	"""What a root input of ``flake.lock`` was locked from, in ``input_spec``'s form; ``None``
	when the lock records it in a form this can't compare."""
	if isinstance(ref, list):
		return "follows:" + "/".join(str(step) for step in ref)
	node = lock["nodes"].get(ref) if isinstance(ref, str) else None
	original = node.get("original") if isinstance(node, dict) else None
	if not isinstance(original, dict):
		return None
	kind = original.get("type")
	if kind == "git" and isinstance(original.get("url"), str):
		url = original["url"]
		if original.get("ref"):
			url += ("&" if "?" in url else "?") + f"ref={original['ref']}"
		return "git+" + url if not url.startswith("git+") else url
	if kind not in ("github", "gitlab", "sourcehut"):
		return None
	tail = original.get("ref") or original.get("rev")
	return input_spec(
		"url",
		f"{kind}:{original.get('owner')}/{original.get('repo')}" + (f"/{tail}" if tail else ""),
	)


def profile_locked(root: Path, table: dict) -> bool:
	"""Whether an org profile's ``standards-profile`` input is locked from the URL ``profile``
	names. Built-in and in-repo profiles need no lock."""
	profile = table.get("profile", "minimal")
	if not profile.startswith(config.ORG_PROFILE_PREFIXES):
		return True
	try:
		lock = flakelock.load(root / "flake.lock")
	except EnvError:
		return False
	ref = lock["nodes"].get(lock["root"], {}).get("inputs", {}).get(flakelock.PROFILE_INPUT)
	if ref is None:
		return False
	have = locked_spec(lock, ref)
	return have is None or have == input_spec("url", profile)


def resolve(
	app: context.App,
	*,
	created: dict | None = None,
	profile_dir: Path | None = None,
	bootstrap: bool = False,
) -> Resolved:
	"""The app's resolved configuration (§8.4); ``created`` is the table ``--standards`` makes.

	``bootstrap`` is phase A's: an org profile whose input is not locked yet (or is locked
	from another URL) cannot be read, so the flake is rendered from the app's own table on
	top of ``minimal``, locked, and then rendered again from the real resolution.
	"""
	table = created if created is not None else pyproject.tool_frappe_nix(app.pyproject)
	if table is None:
		raise NotOptedIn()
	doc = with_table(app.pyproject, table)
	if bootstrap and profile_dir is None and not profile_locked(app.root, table):
		doc = with_table(app.pyproject, {**table, "profile": "minimal"})
		resolved = config.resolve_doc(doc, root=app.root)
		resolved.cfg["profile"] = table.get("profile", "minimal")
		resolved.profile["source"] = table.get("profile", "minimal")
		return resolved
	return config.resolve_doc(doc, root=app.root, profile_dir=profile_dir)


def _in_repo_dir(name: str) -> str | None:
	"""The directory an in-repo profile ``./<dir>`` names, relative to the app; ``None`` for
	any other profile (or one that would leave the app, which resolving refuses)."""
	if not name.startswith("./"):
		return None
	rel = PurePosixPath(name)
	return None if not rel.parts or ".." in rel.parts or rel.is_absolute() else rel.as_posix()


def _committed_profile(
	root: Path, sha: str, name: str, cache: dict[str, dict] | None = None
) -> tuple[dict | None, str]:
	"""``(profile.toml, key)`` of the org profile ``name`` as commit ``sha`` had it: an in-repo
	profile's committed file, a flake URL's tree as that commit's ``flake.lock`` locks it;
	``(None, "")`` for a built-in. ``key`` tells two of them apart, and ``cache`` holds what
	each key read, so a tree is fetched and hashed once however many commits lock it.

	A profile the commit doesn't hold (no such file, a link, no locked input) is a
	``ConfigError``: that state tells nothing. A locked tree that can't be fetched is an
	``EnvError``: what it held is unknown."""
	if config.is_builtin(name):
		return None, ""
	directory = _in_repo_dir(name)
	if directory is not None:
		path = f"{directory}/profile.toml"
		try:
			listing = repo.git(root, "ls-tree", sha, "--", path)
			fields = listing.split("\t", 1)[0].split()
			# A committed link is never followed, as resolving the working tree refuses one.
			if len(fields) != 3 or fields[1] != "blob" or fields[0] == "120000":
				raise ConfigError(f"{sha}: {path} is not a committed regular file")
			return tomllib.loads(repo.git(root, "cat-file", "blob", fields[2])), "blob:" + fields[2]
		except (EnvError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
			raise ConfigError(f"{sha}: {path}: {e}") from e
	if not name.startswith(config.ORG_PROFILE_PREFIXES):
		raise ConfigError(f"profile {name!r} is not a built-in, a flake URL or ./<dir>")
	try:
		lock = flakelock.parse(repo.git(root, "show", f"{sha}:./flake.lock"), f"{sha}:flake.lock")
		pin = flakelock.locked_pin(lock, flakelock.PROFILE_INPUT)
	except EnvError as e:
		raise ConfigError(f"{sha}: {e}") from e
	key = "nar:" + pin.nar_hash
	if cache is not None and key in cache:
		return cache[key], key
	tree = pins.pin_path(flakelock.PROFILE_INPUT, root / "flake.lock", lock=lock)
	try:
		doc = config.read_profile_dir(tree, name)
	except ConfigError as e:
		raise ConfigError(f"{sha}: {e}") from e
	if cache is not None:
		cache[key] = doc
	return doc, key


def _profile_at(
	root: Path, sha: str, doc: dict, profile_dir: Path | None, cache: dict[str, dict] | None = None
) -> tuple[dict | None, str]:
	"""The org profile commit ``sha``'s table (in ``doc``) names, as that commit had it
	(``_committed_profile``), not today's: a profile edit or bump that turns a module off
	changes nothing in ``pyproject.toml``, and the module was on all the same.

	With ``--profile-path``, a state whose committed profile can't be read is read from that
	checkout instead, as before a profile author's first lock."""
	name = (pyproject.tool_frappe_nix(doc) or {}).get("profile", "minimal")
	name = name if isinstance(name, str) else "minimal"
	try:
		return _committed_profile(root, sha, name, cache)
	except (ConfigError, EnvError):
		if profile_dir is None:
			raise
		return config.read_profile_dir(profile_dir, name), f"path:{profile_dir}"


# What only a repository set up for GitHub holds: its .github/ directory, or a file only a
# GitHub-only module renders (releases' release-please files; dependabot's file is in .github/).
GITHUB_MARKS = (".github", "release-please-config.json", ".release-please-manifest.json")


def _has_github_dir(root: Path, sha: str) -> bool:
	"""Whether commit ``sha`` was set up for GitHub: it has a ``.github/`` directory, or a file
	only a GitHub-only module renders (``GITHUB_MARKS``), so an app on a profile that renders
	no ``.github/`` (``releases`` on, ``ci`` off) still counts."""
	try:
		return bool(repo.git(root, "ls-tree", sha, "--", *GITHUB_MARKS).strip())
	except EnvError:
		return False


def _committed_doc(root: Path, sha: str) -> dict | None:
	"""Commit ``sha``'s ``pyproject.toml``, parsed; ``None`` when it has none it can parse."""
	try:
		return tomllib.loads(repo.git(root, "show", f"{sha}:./pyproject.toml"))
	except (EnvError, tomllib.TOMLDecodeError):
		return None


def previous(root: Path, profile_dir: Path | None = None) -> context.NS | None:
	"""The modules and configuration ``HEAD`` committed (its table and its profile): what a
	module that is off now was before. ``None`` when there is no ``HEAD``, no table there, or
	it doesn't resolve: then nothing counts as turned off, and only files with a managed
	header are retracted."""
	doc = _committed_doc(root, "HEAD")
	if doc is None or pyproject.tool_frappe_nix(doc) is None:
		return None
	try:
		profile_doc, _ = _profile_at(root, "HEAD", doc, profile_dir)
		resolved = config.resolve_doc(doc, root=root, profile_doc=profile_doc)
	except Exception:
		return None
	return context.ns({"modules": resolved.modules, "cfg": resolved.cfg})


# How far back ``history`` reads: commits that changed pyproject.toml, flake.lock or an
# in-repo profile, newest first.
HISTORY_DEPTH = 300


def history(root: Path, profile_dir: Path | None = None) -> context.History:
	"""The modules and configuration of every state the app has committed since it opted in,
	newest first (``HEAD``'s included, each distinct state once). A state is a
	``[tool.frappe-nix]`` table with the org profile committed beside it: an in-repo
	profile's ``profile.toml`` at that commit, a flake URL's tree as that commit's
	``flake.lock`` locks it. So the walk visits the commits that changed ``pyproject.toml``,
	``flake.lock`` or an in-repo profile one of those tables names.

	A module that is off now but on in any of them was on once, so sync may have written its
	files and keys: they are retracted (§3.3 step 6) whichever commit turned it off, in the
	app's table or in its profile. ``HEAD`` alone would miss a module turned off in a commit
	made before syncing, which is what ``--check`` sees on a pull request (and then the
	leftovers would stay for good). The walk stops at the newest commit without the table:
	the app's settings before it opted in are its own (opting in with ``minimal`` removes
	nothing).

	Where the repository is hosted is not committed: ``origin`` is today's. When it is off
	GitHub and no ``repo`` names the host, each state is also read as hosted on GitHub when
	``HEAD`` or its commit has a ``.github/`` directory (a repository set up for GitHub), so
	what a GitHub-only module left before a move is retracted.

	A shallow clone whose walk reaches its boundary with the table still there is
	``truncated``, and a state whose locked profile can't be fetched makes the history
	``unknown``: a module found on in what it has was on, and a question it can't answer
	fails (``context.ever``, exit 3), so the verdict never depends on the clone's depth or
	on what happens to be cached."""
	out = context.History()
	try:
		edits = repo.git(root, "log", f"-n{HISTORY_DEPTH}", "--format=%H", "--", "pyproject.toml").split()
	except EnvError:
		return out
	docs: dict[str, dict | None] = {}
	paths = ["pyproject.toml", "flake.lock"]
	for sha in edits:
		docs[sha] = doc = _committed_doc(root, sha)
		table = pyproject.tool_frappe_nix(doc) if doc is not None else None
		if doc is not None and table is None:
			break
		directory = _in_repo_dir(str((table or {}).get("profile", "")))
		if directory is not None and directory not in paths:
			paths.append(directory)
	try:
		shas = repo.git(root, "log", f"-n{HISTORY_DEPTH}", "--format=%H", "--", *paths).split()
	except EnvError:
		return out
	# Each distinct state once: whether origin puts it off GitHub, and those also read as on it.
	elsewhere: dict[tuple[str, str], bool] = {}
	hosted: set[tuple[str, str]] = set()
	profiles: dict[str, dict] = {}
	github_dir: bool | None = None
	stopped = len(shas) >= HISTORY_DEPTH
	for sha in shas:
		doc = docs[sha] if sha in docs else _committed_doc(root, sha)
		if doc is None:
			continue
		table = pyproject.tool_frappe_nix(doc)
		if table is None:
			stopped = True
			break
		try:
			profile_doc, key = _profile_at(root, sha, doc, profile_dir, profiles)
		except ConfigError:
			continue  # no profile there to read (an in-repo one not added yet, no lock yet)
		except EnvError as e:
			out.unknown = out.unknown or (
				"retracting what a module that is off now left behind needs the org profile"
				f" commit {sha[:12]} locked, and it could not be read: {e}"
			)
			continue
		state = (json.dumps(table, sort_keys=True, default=str), key)
		if state not in elsewhere:
			try:
				resolved = config.resolve_doc(doc, root=root, profile_doc=profile_doc)
			except Exception:
				continue  # a state that no longer resolves (an old schema) tells nothing
			out.append(context.ns({"modules": resolved.modules, "cfg": resolved.cfg}))
			elsewhere[state] = (
				resolved.repo_host not in (None, "github.com") and "repo" not in resolved.sources
			)
		if not elsewhere[state] or state in hosted:
			continue
		if github_dir is None:
			github_dir = _has_github_dir(root, "HEAD")
		if not (github_dir or _has_github_dir(root, sha)):
			continue
		hosted.add(state)
		try:
			on_github = config.resolve_doc(doc, root=root, profile_doc=profile_doc, on_github=True)
		except Exception:
			continue
		out.append(context.ns({"modules": on_github.modules, "cfg": on_github.cfg}))
	if shas and not stopped:
		try:
			out.truncated = repo.git(root, "rev-parse", "--is-shallow-repository").strip() == "true"
		except EnvError:
			out.truncated = True
	return out


def profile_templates(root: Path, cfg: dict, profile_dir: Path | None) -> Path | None:
	"""The org profile's ``templates/`` directory, when it has one (§8.1)."""
	name = cfg.get("profile", "minimal")
	if config.is_builtin(name):
		return None
	if profile_dir is not None:
		directory = profile_dir
	else:
		try:
			directory, _ = config.org_profile_dir(name, root)
		except (ConfigError, EnvError):
			return None
	templates = directory / "templates"
	if templates.is_symlink():
		raise ConfigError(
			"the profile's templates/ is a symlink: a profile's templates are read only as regular"
			" files, never through a link"
		)
	return templates if templates.is_dir() else None


def check_overrides(templates: Path | None, entries: list[manifest.Entry]) -> None:
	"""Each file in the profile's ``templates/`` replaces an overridable template or is an
	``[[extra-files]]`` template; anything else is exit 2 naming it (§8.1), and so is a symlink
	anywhere in it (``rendering.template_files``). An overridable entry whose template ends in
	``/`` (the README blocks, ``readme/``) renders several, and each one under it may be replaced."""
	if templates is None:
		return
	allowed = {e.template for e in entries if e.template and (e.overridable or e.profile_template)}
	folders = tuple(t for t in allowed if t.endswith("/"))
	for path in rendering.template_files(templates):
		rel = path.relative_to(templates).as_posix()
		if rel not in allowed and not (folders and rel.startswith(folders)):
			raise ConfigError(
				f"the profile's templates/{rel} overrides no overridable template and no [[extra-files]] entry"
				" names it (tool configs, merged keys and caller workflows change through parameters)"
			)


def check_uses(ctx: context.NS, entry: manifest.Entry, path: str) -> None:
	"""An org value a live entry needs must be set (S37); ``org.<key>?`` is optional."""
	for key in entry.uses:
		if not key.startswith("org.") or key.endswith("?"):
			continue
		value: Any = ctx.org
		for part in key.split(".")[1:]:
			value = value.get(part) if isinstance(value, dict) else None
		if not value:
			raise ConfigError(
				f"{path} needs {key}, which is empty: set it in the profile's [org] or [tool.frappe-nix.org]"
			)


_GITHUB_FLAKE_URL = re.compile(r"^(github:|(git\+)?https://github\.com/)", re.I)


def check_frappe_nix_url(cfg: dict, modules: dict) -> None:
	"""§2.5: a ``dev-shell.frappe-nix-url`` off GitHub (``git+https://git.example.org/…``) works
	for the dev shell and sync, but a GitHub ``uses:`` can't name it: exit 2 while ``ci`` is on."""
	url = (cfg.get("dev-shell") or {}).get("frappe-nix-url")
	if url and modules.get("ci") and not _GITHUB_FLAKE_URL.match(url):
		raise ConfigError(
			f"dev-shell.frappe-nix-url = {url!r} is not on GitHub, and ci is on: the caller workflows'"
			" `uses:` can name only a GitHub repository (use a github: URL, or turn ci off)"
		)


def check_preset(app: context.App, cfg: dict, modules: dict) -> None:
	"""``typescript.preset = "inline"`` has no Frappe declarations, which check-js,
	audit-consumer and the gen-doctypes freshness step need (§2.9)."""
	ts = cfg.get("typescript", {})
	if not modules.get("typescript") or ts.get("preset") != "inline":
		return
	for key in ("check-js", "audit-consumer"):
		if ts.get(key):
			raise ConfigError(
				f'typescript.{key} needs the Frappe declarations of the frappe-types preset: set typescript.preset = "frappe-types" or turn {key} off'
			)
	if "types/doctypes.d.ts" in app.tracked:
		raise ConfigError(
			'types/doctypes.d.ts is checked with frappe-types gen-doctypes, which needs typescript.preset = "frappe-types"'
		)


def _check_regions(path: str, text: str, entry: manifest.Entry) -> None:
	"""A local region may add, never redefine (§3.2)."""
	inside: list[str] = []
	outside: list[str] = []
	current_region = None
	for line in text.splitlines():
		m = re.match(r"^\s*# frappe-nix:local-(begin|end) ", line)
		if m:
			current_region = line if m[1] == "begin" else None
			continue
		(inside if current_region else outside).append(line)

	def keys(lines: list[str]) -> set[str]:
		found: set[str] = set()
		eco = None
		for line in lines:
			if m := re.match(r"^\s*-\s+id:\s*(\S+)", line):
				found.add(f"hook id {m[1]}")
			if m := re.match(r"^\s*-\s+package-ecosystem:\s*(\S+)", line):
				eco = m[1]
			if eco and (m := re.match(r"^\s+directory:\s*(\S+)", line)):
				found.add(f"updates entry {eco} {m[1]}")
				eco = None
			if entry.header == "ini" and (m := re.match(r"^\s*(\[[^\]]+\])\s*$", line)):
				found.add(f"section {m[1]}")
		return found

	clash = sorted(keys(inside) & keys(outside))
	if entry.header == "nix":
		defined = {
			m["name"]
			for line in inside
			if (m := re.match(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_'-]*)(?:\.[A-Za-z]+)?\s*=", line))
		}
		managed_inputs = {
			*MANAGED_INPUTS,
			*re.findall(r"^\s{4}([A-Za-z_][A-Za-z0-9_'-]*) = \{", "\n".join(outside), re.M),
		}
		clash += [f"flake input {name}" for name in sorted(defined & managed_inputs)]
	if clash:
		raise ConfigError(f"{path}: the local region redefines what sync manages: {', '.join(clash)}")


# --- the plan --------------------------------------------------------------------


def _template_dir(plan: Plan, entry: manifest.Entry, templates: Path | None) -> Path | None:
	"""Where ``entry``'s template comes from: the org profile's ``templates/`` for an
	``[[extra-files]]`` template or an override, else the built-in templates (``None``)."""
	if entry.profile_template:
		if templates is None or not (templates / (entry.template or "")).is_file():
			raise ConfigError(f"[[extra-files]] {entry.path}: the profile has no templates/{entry.template}")
		return templates
	if entry.overridable and templates is not None and (templates / (entry.template or "")).is_file():
		return templates
	return None


def _whole(plan: Plan, entry: manifest.Entry, path: str, current: str | None, templates: Path | None) -> str:
	text = rendering.render(
		entry.template or "", plan.ctx, current, directory=_template_dir(plan, entry, templates)
	)
	if entry.local_regions:
		text = regions.splice(text, current, path, entry.local_regions)
		_check_regions(path, text, entry)
	return rendering.header(entry.header, bool(entry.local_regions), entry.header_note) + text


def _json(text: str | None, path: str) -> Any:
	if text is None:
		return None
	try:
		return json.loads(text)
	except json.JSONDecodeError as e:
		raise ConfigError(f"{path}: {e}") from e


def effective_version(root: Path, app: str, package: dict | None) -> str:
	"""The version sync writes wherever it writes one: ``__version__``, else the release-please
	manifest's, else package.json's, else 0.1.0. Each later source only fills a gap, so a
	first sync leaves the three agreeing (C5) whichever of them the app already had."""
	have = context.app_version(root, app)
	if have:
		return have
	try:
		manifest_doc = json.loads(read(root, ".release-please-manifest.json") or "null")
	except json.JSONDecodeError:
		manifest_doc = None
	if isinstance(manifest_doc, dict) and isinstance(manifest_doc.get("."), str) and manifest_doc["."]:
		return manifest_doc["."]
	version = (package or {}).get("version")
	if isinstance(version, str) and version:
		return version
	return DEFAULT_VERSION


def _seed_text(plan: Plan, entry: manifest.Entry, templates: Path | None) -> str:
	if entry.handler == "release-please-manifest":
		return jsonfmt.dumps({".": plan.version})
	text = rendering.render(
		entry.template or "", plan.ctx, None, directory=_template_dir(plan, entry, templates)
	)
	if entry.profile_template and entry.header != "none":
		return rendering.header(entry.header, False) + text
	return text


def blame_problems(root: Path, text: str) -> list[str]:
	"""``.git-blame-ignore-revs`` rules (§2.20); reachability only when the history is all there."""
	out = []
	shas = []
	for number, line in enumerate(text.splitlines(), 1):
		if not line.strip() or line.startswith("#"):
			continue
		if not BLAME_LINE.match(line):
			out.append(f"line {number} is not `<40-hex sha>  # <subject>`: {line}")
		else:
			shas.append(line[:40])
	try:
		shallow = repo.git(root, "rev-parse", "--is-shallow-repository").strip() == "true"
	except EnvError:
		shallow = True
	if not shallow:
		for sha in shas:
			try:
				repo.git(root, "merge-base", "--is-ancestor", sha, "HEAD")
			except EnvError:
				out.append(f"{sha} is not reachable from HEAD")
	return out


def _validate_seed(plan: Plan, entry: manifest.Entry, path: str, current: str) -> list[Item]:
	out = []
	if entry.handler == "blame-ignore-revs":
		for problem in blame_problems(plan.root, current):
			out.append(Item(path, "seed", current, current, problem, DRIFT))
	elif entry.handler == "release-please-manifest":
		doc = _json(current, path)
		if not isinstance(doc, dict) or not isinstance(doc.get("."), str):
			out.append(Item(path, "seed", current, current, 'must be {".": "<version>"}', INVALID))
	elif entry.handler == "semgrep-baseline":
		# N5's marketplace/semgrep-baseline.json (§2.21): its shape here, its counts in L7.
		for problem in baseline.problems(current):
			out.append(Item(path, "seed", current, current, problem, INVALID))
	return out


def _spa_config(ctx: context.NS, path: str, current: str | None) -> bool:
	"""``path`` is a tsconfig sync manages, but what is there is a Vite app's own config."""
	if current is None or not ctx.discover.vite:
		return False
	if not re.fullmatch(r"tsconfig[^/]*\.json", path):
		return False
	return rendering.MARKER not in current.split("\n", 1)[0]


def _spa_problem(path: str) -> str:
	return (
		f"{path} is the app's own TypeScript config (it has no frappe-nix:managed header) and"
		" the app has a Vite config: declare it in [[tool.frappe-nix.typescript.spa]]"
		f' (tsconfig = "{path}"), or delete it to let sync manage {path}'
	)


def _yarn_lock_missing(plan: Plan, lock: str) -> list[str]:
	"""``package_json.yarn_lock_missing`` for the ``package.json`` on disk."""
	try:
		package = load_package(plan.root)
	except ConfigError:
		return []  # reported by the package.json entry
	local: set[str] = set()
	if isinstance(package, dict) and package.get("workspaces"):
		for path in plan.app.tracked:
			if path != "package.json" and path.endswith("/package.json"):
				with contextlib.suppress(ConfigError, json.JSONDecodeError):
					doc = json.loads(read(plan.root, path) or "{}")
					if isinstance(doc, dict) and isinstance(doc.get("name"), str):
						local.add(doc["name"])
	return package_json.yarn_lock_missing(package, lock, local)


def _js_oxc(ctx: context.NS) -> bool:
	return bool(ctx.modules.get("js")) and ctx.cfg.get("js", {}).get("tool", "oxc") == "oxc"


def legacy_app_file(path: str, current: str, app: str) -> str | None:
	"""What ``templates/app`` renders for ``path`` (``flake.nix``, ``.envrc``), with the site,
	Frappe version and branch the current file names: the file ``frappe-init --app`` wrote."""
	template = APP_TEMPLATE.get(path)
	if template is None:
		return None
	text = data_path(template).read_text()
	site = re.search(r'siteName\s*=\s*"([^"]*)"', current)
	version = re.search(r'frappeVersion\s*=\s*"([^"]*)"', current)
	branch = re.search(r'"github:frappe/frappe/([^"]*)"', current)
	return (
		text.replace("@APP_NAME@", app)
		.replace("@SITE_NAME@", site[1] if site else "")
		.replace("@FRAPPE_VERSION@", version[1] if version else "")
		.replace("@FRAPPE_BRANCH@", branch[1] if branch else "")
	)


def _managed_at_head(root: Path, path: str) -> bool:
	"""Whether ``HEAD``'s ``path`` opens with the managed header: sync wrote it before."""
	try:
		text = repo.git(root, "show", f"HEAD:./{path}")
	except EnvError:
		return False
	return rendering.MARKER in text.split("\n", 1)[0]


def _first_opt_in(
	plan: Plan, entry: manifest.Entry, path: str, current: str | None, wanted: str
) -> Item | None:
	"""§2.5: replacing an unmanaged ``flake.nix`` or ``.envrc`` that is not what
	``templates/app`` gave the app needs ``--force``; the app's own Nix would be lost. Once
	sync has written the file (it is managed at ``HEAD``), an edit is ordinary drift."""
	if path not in APP_TEMPLATE or current is None or current == wanted:
		return None
	if rendering.MARKER in current.split("\n", 1)[0] or plan.ctx.options.get("force"):
		return None
	if _managed_at_head(plan.root, path):
		return None
	if current == legacy_app_file(path, current, plan.app.name):
		return None
	diff = "".join(
		difflib.unified_diff(current.splitlines(True), wanted.splitlines(True), "current", "rendered")
	)
	return Item(
		path,
		entry.strategy,
		current,
		current,
		f"{path} is the app's own and differs from what frappe-init --app writes: sync would replace it with"
		" the managed file. Keep custom inputs in the flake.nix `inputs` local region, outputs in nix/local.nix,"
		" systems and caches in [tool.frappe-nix.dev-shell] (direnv lines in the .envrc `envrc` region), then"
		f" pass --force to replace it. The replacement:\n{diff}",
		INVALID,
		phase=entry.phase,
	)


def uv_floors(ctx: context.NS) -> dict[str, str]:
	"""The ``tools/uv.lock`` floors of the tools ``tools/pyproject.toml`` lists (§2.15)."""
	return {pkg: floor for pkg, floor in ctx.floors.get("uv", {}).items() if pkg in ctx.tools_deps}


def _entry_item(
	plan: Plan, entry: manifest.Entry, path: str, seed_manifest: bool, templates: Path | None
) -> list[Item]:
	"""The item(s) for one live entry."""
	root, ctx = plan.root, plan.ctx
	current = read(root, path)
	strategy = entry.strategy
	out: list[Item] = []

	def item(
		wanted: str | None, problem: str = "", code: int | None = None, command: str | None = None
	) -> Item:
		if code is None:
			code = DRIFT if (wanted != current or command) else CLEAN
		if code == DRIFT and not problem:
			problem = "missing" if current is None else "differs from the rendered file"
		return Item(path, strategy, current, wanted, problem, code, command, entry.phase)

	if strategy == "whole" and _spa_config(ctx, path, current):
		# §2.9: sync never writes over an SPA's config. A Vite app's own tsconfig that no
		# [[tool.frappe-nix.typescript.spa]] names yet is one sync would otherwise replace.
		out.append(item(current, _spa_problem(path), INVALID))
	elif strategy == "whole":
		wanted = _whole(plan, entry, path, current, templates)
		refused = _first_opt_in(plan, entry, path, current, wanted)
		if refused is not None:
			out.append(refused)
		elif not _js_oxc(ctx) and semantic.equal(path, current, wanted):
			# The app formats its YAML, JSON and TOML with its own tool (§2.10, §3.2).
			out.append(item(current))
		else:
			out.append(item(wanted))
	elif strategy == "blocks" and entry.handler == "gitignore":
		body = data_path("templates/gitignore.block").read_text()
		out.append(item(blocks.gitignore(current, body, path)))
	elif strategy == "blocks" and entry.handler == "readme":
		# N5's README blocks (§2.19): a wrong or missing marker is the app's to fix (exit 2).
		try:
			out.append(item(readme.render(root, ctx, current, templates, path)))
		except ConfigError as e:
			out.append(Item(path, strategy, current, current, str(e), INVALID))
	elif strategy == "blocks" and entry.handler == "init-py":
		wanted, offending = blocks.init_py(current, plan.version)
		out.append(item(wanted))
		if offending:
			out.append(
				Item(
					path,
					strategy,
					current,
					current,
					"only comments may sit outside the version block (marketplace rule); move this code to"
					f" {ctx.app}/api.py: {offending[0].strip()}",
					DRIFT,
				)
			)
	elif strategy == "toml-merge" and entry.handler == "pyproject":
		text = current or ""
		doc = tomllib.loads(text)
		if plan.created_config is not None:
			text = tomlmerge.add_tool_frappe_nix(text, plan.created_config)
		merged = tomlmerge.merge(text, ctx)
		plan.notices += merged.warnings
		wanted = merged.text
		same = tomlmerge.managed_view(doc, ctx) == tomlmerge.managed_view(tomllib.loads(wanted), ctx)
		if plan.created_config is not None:
			same = False
		out.append(item(wanted if not same else current, "managed keys differ" if not same else ""))
		for code, problem in tomlmerge.problems(doc, ctx):
			out.append(Item(path, strategy, current, current, problem, code))
	elif strategy == "json-merge" and entry.handler == "package-json":
		doc = _json(current, path)
		if doc is not None and not isinstance(doc, dict):
			raise ConfigError(f"{path} is not a JSON object")
		if doc is None and not ctx.package_json_live:
			return out
		merged = package_json.merge(doc, ctx, version=plan.version if seed_manifest else None)
		plan.notices += merged.warnings
		if merged.doc == doc:
			out.append(item(current))
		else:
			out.append(item(jsonfmt.stringify(merged.doc), "managed keys differ" if doc is not None else ""))
		for code, problem in package_json.problems(merged.doc, ctx):
			out.append(Item(path, strategy, current, current, problem, code))
	elif strategy == "json-merge" and entry.handler == "stylelint":
		doc = _json(current, path)
		merged_style = package_json.stylelint_merge(doc, ctx)
		out.append(item(current if merged_style == doc else jsonfmt.dumps(merged_style)))
	elif strategy == "seed":
		if entry.command:
			missing = current is None
			problem = ""
			if not missing and entry.handler == "uv-lock":
				short = floors.lock_shortfalls(root / path, uv_floors(ctx))
				if short:
					problem = f"below its floor or missing from the lock: {', '.join(short)}"
			if current is not None and entry.handler == "yarn-lock":
				absent = _yarn_lock_missing(plan, current)
				if absent:
					more = f" and {len(absent) - 5} more" if len(absent) > 5 else ""
					problem = f"does not lock package.json's {', '.join(absent[:5])}{more}"
			if missing or problem:
				out.append(item(current, problem or "missing", DRIFT, entry.command))
			else:
				out.append(item(current))
		elif current is None:
			out.append(item(_seed_text(plan, entry, templates)))
		else:
			out.append(item(current))
			out += _validate_seed(plan, entry, path, current)
	else:
		raise manifest.ManifestError(f"{entry.fragment}: {path}: no handler for {strategy}/{entry.handler}")
	return out


def _requirement_names(text: str) -> list[str]:
	"""The package names a requirements file lists (PEP 503-normalised), options skipped."""
	names = []
	for line in text.splitlines():
		line = line.split("#", 1)[0].strip()
		if not line or line.startswith("-"):
			continue
		m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
		if m:
			names.append(re.sub(r"[-_.]+", "-", m[0]).lower())
	return names


def _missing_deps(plan: Plan, text: str) -> list[str]:
	"""The packages ``text`` lists that ``[project].dependencies`` does not."""
	deps = plan.app.pyproject.get("project", {}).get("dependencies", [])
	have = set(_requirement_names("\n".join(d for d in deps if isinstance(d, str))))
	return sorted({n for n in _requirement_names(text) if n not in have})


def _rule_applies(plan: Plan, rule: manifest.Retire) -> bool:
	modules = plan.ctx.modules
	if not modules.get(rule.module) or not all(modules.get(m) for m in rule.also):
		return False
	return rendering.evaluate(rule.when, plan.ctx)


def _retired(plan: Plan, managed_paths: set[str], only: set[str] | None) -> list[Item]:
	"""Steps 7 and the ``legacy file`` check: every tracked match of an applying rule (§2.4.1)."""
	rules = [
		r for r in (*manifest.load().retire, *manifest.profile_retire(plan.cfg)) if _rule_applies(plan, r)
	]
	keep = list(plan.cfg.get("retire-keep", []))
	out = []
	for path in plan.app.tracked:
		if only is not None and path not in only:
			continue
		if path in keep or globs.match_any(keep, path):
			continue
		link = (plan.root / path).is_symlink()
		if not link and not (plan.root / path).is_file():
			continue
		for rule in rules:
			if not globs.match_any(list(rule.paths), path) or globs.match_any(list(rule.unless), path):
				continue
			if rule.unmanaged and path in managed_paths:
				continue
			if link:
				# Never read through a link: deleting the link is all retiring it takes, and a
				# rule that looks inside the file cannot apply to one.
				if not (rule.contains or rule.only_section or rule.deps_in_project):
					out.append(
						Item(
							path, "retire", "", None, f"legacy file ({rule.rule})", DRIFT, module=rule.module
						)
					)
					break
				continue
			text = read(plan.root, path) or ""
			if rule.contains and not any(re.search(pattern, text) for pattern in rule.contains):
				continue
			if rule.only_section:
				sections = set(re.findall(r"^\s*\[([^\]]+)\]\s*$", text, re.M))
				if sections != {rule.only_section}:
					continue
			missing = _missing_deps(plan, text) if rule.deps_in_project else []
			if missing:
				out.append(
					Item(
						path,
						"retire",
						text,
						text,
						f"legacy file ({rule.rule}), but [project].dependencies lacks"
						f" {', '.join(missing)}: add them there, then sync deletes it",
						INVALID,
						module=rule.module,
					)
				)
			else:
				out.append(
					Item(path, "retire", text, None, f"legacy file ({rule.rule})", DRIFT, module=rule.module)
				)
			break
	return out


def _was_on(plan: Plan, entry: manifest.Entry) -> bool:
	"""Whether one of the entry's modules was on in any table since the app opted in
	(``history``): only then is what sync would have written there sync's to retract."""
	return context.ever(plan.ctx.get("history"), lambda h: any(h.modules.get(m) for m in entry.modules))


def _on_at_head(plan: Plan, entry: manifest.Entry) -> bool:
	"""Whether one of the entry's modules was on at ``HEAD``: this run turns it off, so what
	sync leaves behind is reported now, once."""
	previous = plan.ctx.get("previous")
	return bool(previous) and any(previous.modules.get(m) for m in entry.modules)


def _retract(plan: Plan, entry: manifest.Entry, path: str, templates: Path | None) -> Item | None:
	"""A file whose entry's modules are all off (§3.3 step 6)."""
	current = read(plan.root, path)
	if current is None:
		return None
	if entry.strategy == "blocks" and entry.handler == "init-py":
		wanted = blocks.init_py_unblocked(current)
		if wanted == current or not _was_on(plan, entry):
			return None
		return Item(
			path, entry.strategy, current, wanted, "the version block's markers go with releases", DRIFT
		)
	if entry.strategy == "blocks" and entry.handler == "readme":
		if not readme.has_markers(current) or not _was_on(plan, entry):
			return None
		return Item(
			path, entry.strategy, current, readme.strip(current), "the README blocks go with readme", DRIFT
		)
	if entry.strategy == "seed":
		# A seed is the app's from the moment it exists: left, and said so on the run that
		# turns its module off (a file sync never seeded is the app's all along).
		if _on_at_head(plan, entry) and not entry.command:
			plan.notices.append(f"{path} stays: its module is off now, and the file is the app's")
		return None
	if entry.strategy != "whole":
		return None
	head = rendering.header(entry.header, bool(entry.local_regions), entry.header_note)
	if head:
		if not current.startswith(head):
			return None  # the app's own file of that name
		filled = regions.filled(current, path, entry.local_regions) if entry.local_regions else []
		if filled:
			return Item(
				path,
				entry.strategy,
				current,
				current,
				f"its module is off, but the local region {', '.join(filled)} holds content: move it out"
				f" (or delete {path}) before turning the module off",
				INVALID,
			)
		return Item(path, entry.strategy, current, None, "file should not exist (its module is off)", DRIFT)
	# A headerless JSON file: sync's when it is what the module renders, and the module was on.
	if not _was_on(plan, entry):
		return None
	try:
		would = _whole(plan, entry, path, current, templates)
	except Exception:
		would = None
	if would is not None and (would == current or semantic.equal(path, current, would)):
		return Item(path, entry.strategy, current, None, "file should not exist (its module is off)", DRIFT)
	if _on_at_head(plan, entry):
		plan.notices.append(
			f"{path} is the app's now that its module is off (it differs from what sync wrote)"
		)
	return None


def _absent(plan: Plan, entry: manifest.Entry, path: str, templates: Path | None) -> Item | None:
	"""An entry whose module is on but whose ``when`` is false: delete the file only when
	sync wrote it as it is."""
	current = read(plan.root, path)
	if current is None or entry.strategy == "blocks":
		# A blocks file is the app's own (README.md): only the blocks are sync's, and they wait
		# for the condition (a listing) to hold again.
		return None
	if entry.strategy == "whole" and _spa_config(plan.ctx, path, current):
		# A Vite app's own tsconfig where sync renders none (no browser, desk or scripts
		# project): the same §2.9 case as the one _entry_item reports, never sync's to delete.
		return Item(path, entry.strategy, current, current, _spa_problem(path), INVALID)
	would = None
	if entry.strategy == "whole":
		try:
			would = _whole(plan, entry, path, current, templates)
		except Exception:
			would = None
		# What a render under the previous context produced is not knowable here, but a file
		# that still opens with the managed header and holds nothing in its local regions is
		# one nobody edited (the header says not to): it is sync's to delete.
		head = rendering.header(entry.header, bool(entry.local_regions), entry.header_note)
		if head and current.startswith(head):
			try:
				untouched = not regions.bodies(current, path, entry.local_regions).strip()
			except ConfigError:
				untouched = False
			if untouched:
				would = current
	elif entry.handler == "stylelint":
		if _json(current, path) == package_json.stylelint_created(plan.ctx):
			would = current
	elif entry.strategy == "seed" or entry.strategy in ("toml-merge", "json-merge"):
		return None
	if would is not None and would == current:
		return Item(
			path,
			entry.strategy,
			current,
			None,
			"should not exist (sync wrote it; its condition no longer holds)",
			DRIFT,
		)
	return Item(
		path,
		entry.strategy,
		current,
		current,
		"file should not exist, and it is not what sync wrote (no managed header, or edited), so"
		f" sync leaves it: delete {path} if the app no longer needs it",
		DRIFT,
	)


# --- whole-repo checks -------------------------------------------------------------


def locked_frappe_nix(root: Path) -> dict:
	"""The ``flake.lock`` frappe-nix node's ``locked`` (``rev``, ``owner``, ``repo``), or ``{}``."""
	lock = root / "flake.lock"
	if not lock.is_file():
		return {}
	try:
		found = flakelock.node_at(flakelock.load(lock), ["frappe-nix"])
	except EnvError:
		return {}
	locked = found[1].get("locked") if found else None
	return dict(locked) if isinstance(locked, dict) else {}


def locked_rev(root: Path) -> str | None:
	return locked_frappe_nix(root).get("rev")


def check_excludes(app: context.App, cfg: dict) -> None:
	"""``typescript.exclude`` is for non-source paths only (§2.1): sources go in unchecked-js."""
	for glob in cfg.get("typescript", {}).get("exclude", []):
		hit = [
			p
			for p in app.tracked
			if p.startswith(f"{app.name}/") and re.search(r"\.(js|ts|vue)$", p) and globs.match(glob, p)
		]
		if hit:
			raise ConfigError(
				f"[tool.frappe-nix.typescript].exclude {glob!r} matches source file {hit[0]}: list it in"
				" [[tool.frappe-nix.unchecked-js]] or declare an SPA instead"
			)


def entries_for(cfg: dict) -> list[manifest.Entry]:
	"""The packaged entries and the org profile's ``[[extra-files]]``, which may not collide."""
	man = manifest.load()
	extra = manifest.extra_entries(cfg)
	taken = {e.path for e in man.entries}
	for e in extra:
		if _CONTROL.search(e.path):
			raise ConfigError(f"[[extra-files]] {e.path!r} holds a control character")
		if e.path in taken or e.path in {"pyproject.toml", "package.json"}:
			raise ConfigError(f"[[extra-files]] {e.path} is a path frappe-nix manages")
		if PurePosixPath(e.path).is_absolute() or ".." in PurePosixPath(e.path).parts:
			raise ConfigError(f"[[extra-files]] {e.path} must stay inside the app")
		if (part := unmanaged_dir(e.path)) is not None:
			raise ConfigError(f"[[extra-files]] {e.path} is inside {part}/, which sync never writes")
	return [*man.entries, *extra]


def build(
	root: Path,
	*,
	frappe_version: str | None = None,
	site: str | None = None,
	only: list[str] | None = None,
	options: dict | None = None,
	phases: tuple[str, ...] = ("a", "b"),
	profile_dir: Path | None = None,
	standards: str | None = None,
	frappe_nix_lock: dict | None = None,
) -> Plan:
	"""The plan for the app at ``root``.

	``standards`` is ``--standards <profile>``: it creates ``[tool.frappe-nix]`` when there is
	none, and must name the table's profile when there is one. Without either, the app has
	not opted in (exit 2, S35).

	``frappe_nix_lock`` stands in for ``flake.lock``'s frappe-nix node: a dry run on an app
	whose lock phase A would (re)lock renders against the rev that lock would take (§3.3).
	"""
	app = load_app(root)
	table = pyproject.tool_frappe_nix(app.pyproject)
	created = None
	if table is None:
		if not standards:
			raise NotOptedIn()
		created = new_config(
			root,
			app.name,
			app.hooks,
			frappe_version,
			load_package(root),
			app.tracked,
			profile=standards,
			site=site,
		)
	elif standards and table.get("profile", "minimal") != standards:
		raise ConfigError(
			f"[tool.frappe-nix] already names profile {table.get('profile', 'minimal')!r}: edit profile there"
			f" instead of passing --standards {standards}"
		)
	elif frappe_version and frappe_version != f"version-{table.get('frappe-major')}":
		# The table's frappe-major is what renders; a --frappe-version it contradicts would be
		# shown to the user (frappe-init's plan) and then silently ignored.
		raise ConfigError(
			f"[tool.frappe-nix] has frappe-major = {table.get('frappe-major')}: edit frappe-major there"
			f" instead of passing --frappe-version {frappe_version}"
		)
	resolved = resolve(app, created=created, profile_dir=profile_dir, bootstrap=phases == ("a",))
	if created is not None:
		created = with_integration_branch(root, created, resolved)
		resolved = resolve(app, created=created, profile_dir=profile_dir, bootstrap=phases == ("a",))
	cfg = resolved.cfg
	check_excludes(app, cfg)
	check_preset(app, cfg, resolved.modules)
	check_frappe_nix_url(cfg, resolved.modules)
	man = manifest.load()
	lock = frappe_nix_lock if frappe_nix_lock is not None else locked_frappe_nix(root)
	ctx = context.build(app, resolved, lock=lock, floors=man.floors, options=options)
	ctx["previous"] = previous(root, profile_dir)
	ctx["history"] = history(root, profile_dir)
	plan = Plan(root, app, resolved, ctx, created_config=created, notices=list(resolved.notices))
	try:
		package = load_package(root)
	except ConfigError:
		package = None  # reported by the package.json entry
	plan.version = effective_version(root, app.name, package)
	entries = entries_for(cfg)
	templates = profile_templates(root, cfg, profile_dir)
	check_overrides(templates, entries)
	only_set = set(only) if only else None
	seed_manifest = read(root, ".release-please-manifest.json") is None
	managed_paths: set[str] = set()
	for entry in entries:
		# The rendered path is what is read and written, so that is what must stay inside the
		# app: an [[extra-files]] path of "{{ '..' }}/x" passes any check of the template.
		path = rendering.render_string(entry.path, ctx)
		inside(root, path, link_ok=True)
		managed_paths.add(path)
		if entry.phase not in phases or (only_set is not None and path not in only_set):
			continue
		module_on = any(ctx.modules.get(m) for m in entry.modules)
		items: list[Item] = []
		if entry.strategy in ("toml-merge", "json-merge") and entry.handler in ("pyproject", "package-json"):
			# The merged files: every module's key group is set or retracted on each run.
			if module_on:
				check_uses(ctx, entry, path)
			items = _entry_item(plan, entry, path, seed_manifest, templates)
		elif module_on and rendering.evaluate(entry.when, ctx):
			check_uses(ctx, entry, path)
			items = _entry_item(plan, entry, path, seed_manifest, templates)
		elif module_on:
			items = [gone] if (gone := _absent(plan, entry, path, templates)) else []
		else:
			items = [gone] if (gone := _retract(plan, entry, path, templates)) else []
		for it in items:
			it.module = it.module or ",".join(entry.modules)
			plan.add(it)
	if "b" in phases:
		for it in _retired(plan, managed_paths, only_set):
			plan.add(it)
	return plan


def flake_input_specs(text: str) -> dict[str, str | None]:
	"""Each input a rendered ``flake.nix`` declares (top level of ``inputs = { … };``), with
	its ``url`` or ``follows:<path>`` normalised by ``input_spec``; ``None`` when it shows neither."""
	m = re.search(r"\n  inputs = \{\n(?P<body>.*?)\n  \};\n", text, re.S)
	specs: dict[str, str | None] = {}
	block = None
	for line in m["body"].splitlines() if m else []:
		if (one := _FLAKE_ATTR.match(line)) and not block:
			specs[_input_name(one)] = input_spec(one["attr"], one["value"])
		elif (inline := _FLAKE_INLINE.match(line)) and not block:
			specs.setdefault(_input_name(inline), None)
			for attr in _FLAKE_INLINE_ATTR.finditer(inline["body"]):
				specs[_input_name(inline)] = input_spec(attr["attr"], attr["value"])
		elif (opened := _FLAKE_INPUT.match(line)) and not block:
			block = _input_name(opened)
			specs.setdefault(block, None)
		elif block and re.match(r"^\s{4}\};", line):
			block = None
		elif block and (inner := _FLAKE_INNER.match(line)):
			specs[block] = input_spec(inner["attr"], inner["value"])
	return dict(sorted(specs.items()))


def flake_inputs(text: str) -> list[str]:
	"""The input names a rendered ``flake.nix`` declares."""
	return list(flake_input_specs(text))


def stale_inputs(lock: dict, wanted: dict[str, str | None], skip: tuple[str, ...] = ()) -> list[str]:
	"""The inputs ``flake.nix`` declares that ``flake.lock`` has no node for, or locked from
	another URL or ``follows`` (a frappe-major bump moves ``frappe`` to ``version-<N+1>``)."""
	root_inputs = lock["nodes"].get(lock["root"], {}).get("inputs", {})
	out = []
	for name, spec in wanted.items():
		if name in skip:
			continue
		if name not in root_inputs:
			out.append(f"{name} (no node)")
			continue
		have = locked_spec(lock, root_inputs[name])
		if spec is not None and have is not None and have != spec:
			out.append(
				f"{name} (locked from {have.removeprefix('follows:')}, flake.nix has {spec.removeprefix('follows:')})"
			)
	return out


def lock_problems(plan: Plan) -> list[Item]:
	"""``flake.lock`` must hold a node for every input ``flake.nix`` declares, locked from the
	URL (or ``follows``) ``flake.nix`` gives it (§3.3); ``standards-profile`` included."""
	text = read(plan.root, "flake.nix")
	if text is None:
		return []
	lock_path = plan.root / "flake.lock"
	if not lock_path.is_file():
		return [
			Item(
				"flake.lock",
				"lock",
				None,
				None,
				"missing: run `frappe-init --sync`",
				DRIFT,
				module="dev-shell",
			)
		]
	try:
		lock = flakelock.load(lock_path)
	except EnvError as e:
		return [Item("flake.lock", "lock", None, None, str(e), ENVIRONMENT, module="dev-shell")]
	# frappe-nix's self-tests lock the checkout under test in its place (FRAPPE_NIX_URL_OVERRIDE).
	skip = ("frappe-nix",) if os.environ.get("FRAPPE_NIX_ALLOW_SKEW") == "1" else ()
	stale = stale_inputs(lock, flake_input_specs(text), skip)
	if stale:
		return [
			Item(
				"flake.lock",
				"lock",
				None,
				None,
				f"does not follow flake.nix: {'; '.join(stale)}: run `frappe-init --sync`",
				DRIFT,
				module="dev-shell",
			)
		]
	return []


# The files the lock steps make (phase A's lock, steps 8 to 11). A run that fails after one
# of them is written leaves it untracked, and the step that made it does not run again.
LOCK_PATHS = ("flake.lock", "tools/uv.lock", "yarn.lock", "nix/uv.lock", "nix/node-locks")


def untracked_locks(root: Path) -> list[str]:
	"""The lock files and node-lock seeds that exist but are neither tracked nor staged
	(git-ignored ones aside): the flake and CI see only tracked files."""
	out = repo.git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", *LOCK_PATHS)
	return sorted(p for p in out.split("\0") if p)


def untracked_lock_problems(plan: Plan) -> list[Item]:
	return [
		Item(
			path,
			"lock",
			None,
			None,
			"exists but is not tracked (the flake and CI see only tracked files): run `frappe-init --sync`",
			DRIFT,
		)
		for path in untracked_locks(plan.root)
	]


def skew_problems(plan: Plan, expect_rev: str | None) -> list[Item]:
	"""§3.7: the caller workflows, the lock and the running frappe-nix-tools name one frappe-nix.

	The callers' ``uses:`` name the repository the app locks frappe-nix from
	(``frappe_nix.owner``/``frappe_nix.name``: Avunu/frappe-nix, or a fork)."""
	if os.environ.get("FRAPPE_NIX_ALLOW_SKEW") == "1":
		return []
	fn = plan.ctx.frappe_nix
	rev = fn.rev
	pattern = re.compile(
		re.escape(f"{fn.owner}/{fn.name}")
		+ r"/\.github/workflows/app-[A-Za-z0-9_-]+\.ya?ml@(?P<sha>[0-9a-f]{40})(?:[ \t]+#[ \t]*v?(?P<ver>\S+))?",
		re.I,
	)
	out = []
	if expect_rev and rev and expect_rev != rev:
		out.append(
			Item(
				"flake.lock",
				"skew",
				None,
				None,
				f"version skew: this frappe-nix-tools was installed from {expect_rev}, flake.lock pins frappe-nix {rev}",
				ENVIRONMENT,
			)
		)
	for path in plan.app.tracked:
		if not globs.match(".github/workflows/*.y*ml", path):
			continue
		for m in pattern.finditer(read(plan.root, path) or ""):
			if m["sha"] != rev or (m["ver"] or "") != frappe_nix_tools.__version__:
				out.append(
					Item(
						path,
						"skew",
						None,
						None,
						f"version skew: calls frappe-nix @{m['sha'][:12]} # v{m['ver']}, but flake.lock pins"
						f" {rev[:12] or '(nothing)'} and this frappe-nix-tools is v{frappe_nix_tools.__version__}",
						ENVIRONMENT,
					)
				)
				break
	return out
