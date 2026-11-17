"""The publish side of the release-driven rollout (``rollout.yml``, spec §5.8, S5).

``rollout.yml``'s plan job runs the consumer's own code (its flake, ``relock``, uv, yarn and
sync phase B) with no write token and no secret, and hands over a patch
(``frappe-nix repo rollout --emit-patch``). Its publish job holds the App token, so before
the token is minted it runs ``frappe-nix ci verify-rollout-patch`` from the tag's own
frappe-nix and acts only on the decision. The patch is never trusted; it is checked against
the base tree the job checked out itself:

a. **Hygiene.** Applied to the base in a temporary index (``git read-tree <base>``,
   ``git apply --cached``): every entry it adds or changes is a regular ``100644`` file (no
   symlink, executable bit, submodule or mode change); a deletion removes a regular file or a
   link; no rename or copy; no absolute path, ``..``, control character or ``.git``
   component. ``git write-tree`` on that index is the ``tree_id`` the job re-derives.
b. **Lock files** are the only content taken from the plan: ``flake.lock``,
   ``nix/uv.lock``, ``nix/node-locks/**``, ``tools/uv.lock`` and ``yarn.lock``.

   - ``flake.lock``: the root ``frappe-nix`` node locks ``Avunu/frappe-nix`` at exactly the
     tag's commit; every node reachable from the root other than through ``frappe-nix`` is
     the base's (``standards-profile`` included); the nodes under ``frappe-nix`` are
     frappe-nix's own ``flake.lock`` at the tag, except a ``follows`` the base already had.
   - ``uv.lock`` sources: pypi.org (files from files.pythonhosted.org), a git source, URL or
     registry the base's locks already have, a relative workspace or editable path, and one
     new git source: ``frappe-runtime`` at
     ``https://github.com/Avunu/frappe-nix?subdirectory=runtime#<tag sha>``.
   - ``yarn.lock`` and ``nix/node-locks/**/yarn.lock``: every ``resolved`` URL is on
     registry.yarnpkg.com or registry.npmjs.org, or one the base's locks already have.
   - A lock change the release does not explain (a package name no base lock of that file
     had, or a version change in ``nix/uv.lock`` outside ``frappe-runtime``'s dependency
     closure) makes the decision ``needs-review``, not ``reject``. A node-lock seed equal to
     the one this frappe-nix-tools ships is explained.
c. **Every other path** is computed here with this (the tag's) sync engine, the plan
   ``sync --check`` builds, from the base tree plus the verified lock files. The patch's
   content for it must be those bytes; a path the engine does not change is ``reject``. A
   ``.json`` file that differs only in layout (the same data: oxfmt settles a merged
   ``package.json`` after sync writes it) is ``needs-review``.
   As a second line, a ``frappe-nix:local`` region and the text outside a ``blocks`` file's
   managed block stay byte-identical to the base, and a seed is only ever created.
d. ``sync --check --expect-rev <tag sha>`` (the same checks, in process) passes on the
   result. A problem the base already has, word for word, is ``needs-review``.

The decision (``--out``, a JSON object) is ``publish`` | ``needs-review`` | ``reject`` |
``noop`` (an empty patch), with ``reasons``, ``patch_sha256``, ``tree_id``, ``branch``,
``title``, ``body``, ``merge_method`` (the base's first ``repo-policy.merge-methods``),
``automerge`` (whether every enabled gate's ``ci / <gate>`` check is required on the
integration branch, the auto-merge safety rule of §4.5, from the branch's rules) and
``legacy_prs`` (the open ``standards/frappe-nix-v*`` pull requests, which the single branch
``standards/frappe-nix`` replaces). The step exits 0 with any decision, and writes
``decision``, ``tree_id`` and ``patch_sha256`` to ``$GITHUB_OUTPUT``; 2 is a bad argument
and 3 an environment error. It runs no nix, uv or yarn, and makes no write call: its GitHub
reads (frappe-nix's ``flake.lock`` at the tag, the branch rules, the open pull requests) go
through ``gh`` with the job's read-only ``GH_TOKEN``.

``frappe-nix ci rollout-contract`` prints ``CONTRACT``: rollout.yml's resolve job skips a
tag whose frappe-nix-tools lacks it (or prints another number).
"""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import frappe_nix_tools
from frappe_nix_tools.ci import output
from frappe_nix_tools.common import appgates, data_path, flakelock, github
from frappe_nix_tools.common.repo import NO_AUTO_MAINTENANCE
from frappe_nix_tools.common.report import INVALID, ConfigError, EnvError, FrappeNixError

#: What rollout.yml's resolve job asks of a tag's frappe-nix-tools (``ci rollout-contract``).
CONTRACT = 1

BRANCH = "standards/frappe-nix"
LEGACY_PREFIX = "standards/frappe-nix-v"
FRAPPE_NIX = ("avunu", "frappe-nix")
RUNTIME = "frappe-runtime"
RUNTIME_GIT = "https://github.com/Avunu/frappe-nix?subdirectory=runtime#{rev}"

PUBLISH, REVIEW, REJECT, NOOP = "publish", "needs-review", "reject", "noop"

#: The files the plan's nix, relock, uv and yarn write: the only content taken from it.
LOCK_FILES = frozenset({"flake.lock", "nix/uv.lock", "tools/uv.lock", "yarn.lock"})
NODE_LOCKS = "nix/node-locks/"
UV_LOCKS = ("nix/uv.lock", "tools/uv.lock")
PYPI = "https://pypi.org/simple"
PYPI_FILES = frozenset({"files.pythonhosted.org"})
NPM_HOSTS = frozenset({"registry.yarnpkg.com", "registry.npmjs.org"})

# What sync's --check reads from the environment that would weaken it here.
_UNSAFE_ENV = ("FRAPPE_NIX_ALLOW_SKEW", "FRAPPE_NIX_URL_OVERRIDE")
_SHA = re.compile(r"[0-9a-f]{40}")
_TAG = re.compile(r"v\d+\.\d+\.\d+")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_REGION = re.compile(r"^[ \t]*# frappe-nix:local-(begin|end) ([A-Za-z0-9_-]+)[ \t]*$")
_FORBIDDEN_HEADERS = (
	(b"rename from ", "renames a file"),
	(b"rename to ", "renames a file"),
	(b"copy from ", "copies a file"),
	(b"copy to ", "copies a file"),
	(b"similarity index ", "renames or copies a file"),
	(b"dissimilarity index ", "rewrites a file as a rename"),
	(b"old mode ", "changes a file mode"),
	(b"new mode ", "changes a file mode"),
	(b"Subproject commit ", "touches a submodule"),
)
_NODE = frozenset({"inputs", "locked", "original", "flake"})
_FN_LOCKED = frozenset({"type", "owner", "repo", "rev", "narHash", "lastModified"})


def is_lock(path: str) -> bool:
	return path in LOCK_FILES or path.startswith(NODE_LOCKS)


@dataclass
class Result:
	"""What ``verify`` found; ``decision()`` folds it into one word."""

	rejects: list[str] = field(default_factory=list)
	reviews: list[str] = field(default_factory=list)
	empty: bool = False

	def decision(self) -> str:
		if self.rejects:
			return REJECT
		if self.empty:
			return NOOP
		return REVIEW if self.reviews else PUBLISH

	def reasons(self) -> list[str]:
		lines = [f"reject: {r}" for r in self.rejects] + [f"needs-review: {r}" for r in self.reviews]
		return list(dict.fromkeys(lines))


# --- git -----------------------------------------------------------------------------


def _git(
	cwd: Path, *args: str, env: dict | None = None, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
	"""``git`` in ``cwd``, bytes out. No hook, fsmonitor or maintenance of the checkout runs."""
	cmd = [
		"git",
		"-c",
		"core.hooksPath=/dev/null",
		"-c",
		"core.fsmonitor=false",
		*NO_AUTO_MAINTENANCE,
		"-C",
		str(cwd),
		*args,
	]
	try:
		proc = subprocess.run(cmd, capture_output=True, env=env, check=False)
	except FileNotFoundError as e:
		raise EnvError("git is not on PATH") from e
	if check and proc.returncode != 0:
		raise EnvError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
	return proc


def _show(cwd: Path, rev: str, path: str) -> bytes | None:
	proc = _git(cwd, "cat-file", "blob", f"{rev}:{path}", check=False)
	return proc.stdout if proc.returncode == 0 else None


@contextlib.contextmanager
def _worktree(repo_dir: Path, rev: str, parent: Path, name: str) -> Iterator[Path]:
	"""A detached work tree of ``repo_dir`` at ``rev``, removed afterwards."""
	path = parent / name
	_git(repo_dir, "worktree", "add", "-q", "--force", "--detach", str(path), rev)
	try:
		yield path
	finally:
		_git(repo_dir, "worktree", "remove", "--force", str(path), check=False)
		shutil.rmtree(path, ignore_errors=True)
		_git(repo_dir, "worktree", "prune", check=False)


@contextlib.contextmanager
def _safe_env() -> Iterator[None]:
	saved = {k: os.environ.pop(k) for k in _UNSAFE_ENV if k in os.environ}
	try:
		yield
	finally:
		os.environ.update(saved)


# --- a: hygiene ------------------------------------------------------------------------


def header_problems(patch: bytes) -> list[str]:
	"""What the patch's extended headers do that a rollout never does."""
	out: list[str] = []
	for line in patch.split(b"\n"):
		for prefix, what in _FORBIDDEN_HEADERS:
			if line.startswith(prefix) and what not in out:
				out.append(f"the patch {what}")
		if line.startswith(b"new file mode ") and line[len(b"new file mode ") :].strip() != b"100644":
			out.append(f"the patch creates a file with mode {line[14:].strip().decode(errors='replace')}")
		if line.startswith(b"deleted file mode ") and line[18:].strip() not in (b"100644", b"120000"):
			out.append(f"the patch deletes an entry of mode {line[18:].strip().decode(errors='replace')}")
	return out


def path_problem(path: str) -> str | None:
	pure = PurePosixPath(path)
	if not path or pure.is_absolute() or path.startswith("/"):
		return f"{path!r} is an absolute path"
	if ".." in pure.parts or "." in path.split("/"):
		return f"{path!r} leaves the repository"
	if _CONTROL.search(path) or "\ufffd" in path:
		return f"{path!r} holds a control character or is not UTF-8"
	if any(part.rstrip(". ").casefold() == ".git" for part in pure.parts):
		return f"{path!r} is inside .git"
	return None


def parse_raw(raw: bytes) -> list[tuple[str, str, str, str]]:
	"""``git diff-index --raw -z``: ``[(old mode, new mode, status, path)]``."""
	out = []
	fields = raw.split(b"\0")
	i = 0
	while i < len(fields) - 1:
		meta = fields[i].decode()
		if not meta.startswith(":"):
			raise EnvError(f"git diff-index printed {meta!r}")
		old_mode, new_mode, _old, _new, status = meta[1:].split(" ")
		path = fields[i + 1].decode("utf-8", errors="replace")
		out.append((old_mode, new_mode, status[:1], path))
		i += 2
	return out


def entry_problem(old_mode: str, new_mode: str, status: str, path: str) -> str | None:
	if status == "A" and new_mode != "100644":
		return f"{path}: created with mode {new_mode}, not a regular 100644 file"
	if status == "M" and (old_mode, new_mode) != ("100644", "100644"):
		return f"{path}: mode {old_mode} -> {new_mode}, not a regular 100644 file"
	if status == "D" and old_mode not in ("100644", "120000"):
		return f"{path}: deletes an entry of mode {old_mode}"
	if status not in ("A", "M", "D"):
		return f"{path}: change of type {status}"
	return None


# --- b: lock files -----------------------------------------------------------------------


def _canonical(lock: dict, ref: Any, stack: tuple[str, ...] = (), memo: dict | None = None) -> Any:
	"""A lock node's subtree with node names taken out, so a renumbered node compares equal."""
	if isinstance(ref, list):
		return ["follows", *ref]
	if not isinstance(ref, str) or ref in stack or len(stack) > 64:
		raise ValueError(f"node reference {ref!r} is not a node, or loops")
	memo = {} if memo is None else memo
	if ref in memo:
		return memo[ref]
	node = lock["nodes"].get(ref)
	if not isinstance(node, dict):
		raise ValueError(f"no node {ref!r}")
	inputs = node.get("inputs") or {}
	if not isinstance(inputs, dict):
		raise ValueError(f"node {ref!r} has inputs that are not a table")
	memo[ref] = {
		**{k: v for k, v in node.items() if k != "inputs"},
		"inputs": {k: _canonical(lock, v, (*stack, ref), memo) for k, v in sorted(inputs.items())},
	}
	return memo[ref]


def _reachable(lock: dict) -> set[str]:
	seen: set[str] = set()
	todo = [lock["root"]]
	while todo:
		name = todo.pop()
		if name in seen or not isinstance(name, str):
			continue
		seen.add(name)
		node = lock["nodes"].get(name) or {}
		for ref in (node.get("inputs") or {}).values():
			if isinstance(ref, str):
				todo.append(ref)
	return seen


def _ref_at(lock: dict, path: list[str]) -> Any:
	"""The raw reference at input ``path`` (the last step not resolved), or ``None``."""
	try:
		parent = flakelock.node_at(lock, path[:-1])
	except EnvError:
		return None
	if parent is None:
		return None
	return (parent[1].get("inputs") or {}).get(path[-1])


def _fn_subtree(
	new: dict, node: dict, fn: dict, fn_node: dict, base: dict, path: list[str], budget: list[int]
) -> list[str]:
	"""The nodes under the app's ``frappe-nix`` against frappe-nix's own lock at the tag.
	``budget`` bounds the walk: a lock of shared nodes is a tree exponentially larger."""
	where = "/".join(path)
	budget[0] -= 1
	if len(path) > 32 or budget[0] < 0:
		return [f"flake.lock: {where} is nested too deep, or the lock too large"]
	n_inputs, f_inputs = node.get("inputs") or {}, fn_node.get("inputs") or {}
	if not isinstance(n_inputs, dict) or set(n_inputs) != set(f_inputs):
		return [f"flake.lock: {where} has other inputs than frappe-nix's own lock at the tag"]
	out: list[str] = []
	for name in sorted(n_inputs):
		nr, fr = n_inputs[name], f_inputs[name]
		here = [*path, name]
		if isinstance(nr, list):
			if isinstance(fr, list) and nr == ["frappe-nix", *fr]:
				continue
			if _ref_at(base, here) == nr:
				continue  # a follows the app's flake.nix sets, as it did at the base
			out.append(f"flake.lock: {'/'.join(here)} follows {nr}, which neither the tag nor the base does")
			continue
		if isinstance(fr, list) or not isinstance(nr, str):
			out.append(f"flake.lock: {'/'.join(here)} is a node where frappe-nix's lock follows {fr}")
			continue
		nn, fnode = new["nodes"].get(nr), fn["nodes"].get(fr)
		if not isinstance(nn, dict) or not isinstance(fnode, dict):
			out.append(f"flake.lock: {'/'.join(here)} names a node the lock does not have")
			continue
		if set(nn) - _NODE or any(nn.get(k) != fnode.get(k) for k in ("locked", "original", "flake")):
			out.append(f"flake.lock: {'/'.join(here)} is not what frappe-nix's own lock at the tag locks")
			continue
		out += _fn_subtree(new, nn, fn, fnode, base, here, budget)
	return out


def flake_lock_problems(
	base_text: str | None, new_text: str | None, tag_sha: str, fn_lock: dict
) -> list[str]:
	"""Why ``new_text`` is not ``base_text`` with frappe-nix moved to ``tag_sha`` (reject)."""
	if base_text is None or new_text is None:
		return ["flake.lock: the base and the result must both have one"]
	try:
		base = flakelock.parse(base_text, "the base's flake.lock")
		new = flakelock.parse(new_text, "flake.lock")
	except EnvError as e:
		return [str(e)]
	out: list[str] = []
	if new.get("version") != base.get("version") or set(new) != set(base):
		out.append("flake.lock: its version or top-level keys differ from the base's")
	nroot, broot = new["nodes"].get(new["root"]), base["nodes"].get(base["root"])
	if not isinstance(nroot, dict) or not isinstance(broot, dict) or set(nroot) - {"inputs"}:
		return [*out, "flake.lock: the root node is not a table of inputs"]
	nin, bin_ = nroot.get("inputs") or {}, broot.get("inputs") or {}
	if set(nin) != set(bin_):
		out.append(f"flake.lock: the root inputs differ from the base's ({sorted(set(nin) ^ set(bin_))})")
	ref, bref = nin.get("frappe-nix"), bin_.get("frappe-nix")
	node = new["nodes"].get(ref) if isinstance(ref, str) else None
	bnode = base["nodes"].get(bref) if isinstance(bref, str) else None
	if not isinstance(node, dict) or not isinstance(bnode, dict):
		return [*out, "flake.lock: frappe-nix is not a node of the root, here or at the base"]
	locked = node.get("locked") if isinstance(node.get("locked"), dict) else {}
	if (
		locked.get("type") != "github"
		or (str(locked.get("owner", "")).lower(), str(locked.get("repo", "")).lower()) != FRAPPE_NIX
		or locked.get("rev") != tag_sha
		or set(locked) - _FN_LOCKED
	):
		out.append(
			f"flake.lock: frappe-nix is locked as {json.dumps(locked, sort_keys=True)[:200]}, not"
			f" github:Avunu/frappe-nix at {tag_sha}"
		)
	if (
		set(node) - _NODE
		or node.get("original") != bnode.get("original")
		or node.get("flake") != bnode.get("flake")
	):
		out.append("flake.lock: frappe-nix's original or flake attribute differs from the base's")
	fn_root = fn_lock["nodes"].get(fn_lock["root"]) or {}
	out += _fn_subtree(new, node, fn_lock, fn_root, base, ["frappe-nix"], [4096])
	for name in sorted(set(nin) & set(bin_)):
		if name == "frappe-nix":
			continue
		try:
			same = _canonical(new, nin[name]) == _canonical(base, bin_[name])
		except ValueError as e:
			out.append(f"flake.lock: {name}: {e}")
			continue
		if not same:
			out.append(f"flake.lock: the root input {name} (or a node under it) differs from the base's")
	extra = set(new["nodes"]) - _reachable(new)
	if extra:
		out.append(f"flake.lock: nodes no input reaches: {', '.join(sorted(extra)[:5])}")
	return out


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
	"""What the base's locks already trust: sources, registries, file URLs, resolved URLs."""

	sources: set[str] = field(default_factory=set)
	urls: set[str] = field(default_factory=set)
	resolved: set[str] = field(default_factory=set)


def _source_key(source: dict) -> list[str]:
	return [f"{kind}={source[kind]}" for kind in sorted(source) if isinstance(source[kind], str)]


def _versions(doc: dict) -> dict[str, list[str]]:
	out: dict[str, list[str]] = {}
	for pkg in _packages(doc):
		out.setdefault(str(pkg.get("name", "")), []).append(str(pkg.get("version", "")))
	return {k: sorted(v) for k, v in out.items()}


def _closure(doc: dict, name: str) -> set[str]:
	deps: dict[str, set[str]] = {}
	for pkg in _packages(doc):
		names = deps.setdefault(str(pkg.get("name", "")), set())
		groups = [pkg.get("dependencies") or []]
		for table in ("optional-dependencies", "dev-dependencies"):
			groups += list((pkg.get(table) or {}).values()) if isinstance(pkg.get(table), dict) else []
		for group in groups:
			for dep in group if isinstance(group, list) else []:
				if isinstance(dep, dict) and isinstance(dep.get("name"), str):
					names.add(dep["name"])
	seen: set[str] = set()
	todo = [name]
	while todo:
		n = todo.pop()
		if n not in seen:
			seen.add(n)
			todo += deps.get(n, ())
	return seen


def uv_problems(
	path: str, base_text: str | None, new_text: str, tag_sha: str, trusted: BaseLocks
) -> tuple[list[str], list[str]]:
	"""``(rejects, reviews)`` for one ``uv.lock``."""
	try:
		new, base = _uv(new_text), _uv(base_text)
	except tomllib.TOMLDecodeError as e:
		return [f"{path}: not TOML: {e}"], []
	rejects: list[str] = []
	runtime = RUNTIME_GIT.format(rev=tag_sha)
	for pkg in _packages(new):
		name = str(pkg.get("name", ""))
		source = pkg.get("source")
		if not isinstance(source, dict) or not source:
			rejects.append(f"{path}: {name} has no source")
			continue
		for kind, value in source.items():
			key = f"{kind}={value}"
			if key in trusted.sources:
				continue
			if kind == "registry" and value == PYPI:
				continue
			if kind == "git" and name == RUNTIME and value == runtime:
				continue
			if kind in ("editable", "virtual", "directory", "path") and isinstance(value, str):
				p = PurePosixPath(value)
				if not p.is_absolute() and "://" not in value and ".." not in p.parts:
					continue
			rejects.append(f"{path}: {name} comes from {kind} {value!r}, which is not allowed")
		for url in _uv_urls(pkg):
			host = urllib.parse.urlsplit(url).hostname or ""
			if not (url.startswith("https://") and host in PYPI_FILES) and url not in trusted.urls:
				rejects.append(f"{path}: {name} downloads {url}, which is not on PyPI")
	reviews: list[str] = []
	new_versions = _versions(new)
	base_versions = _versions(base)
	added = sorted(set(new_versions) - set(base_versions) - {RUNTIME})
	if added:
		reviews.append(f"{path} adds {', '.join(added[:10])}{' …' if len(added) > 10 else ''}")
	if path == "nix/uv.lock":
		closure = _closure(new, RUNTIME)
		moved = sorted(
			n
			for n in set(new_versions) & set(base_versions)
			if new_versions[n] != base_versions[n] and n not in closure
		)
		if moved:
			reviews.append(f"{path} moves {', '.join(moved[:10])}, outside {RUNTIME}'s dependencies")
	return rejects, reviews


_YARN_KEY = re.compile(r"^(?![\s#])(?P<keys>.+):\s*$")
_YARN_RESOLVED = re.compile(r'^\s+resolved\s+"?(?P<url>[^"\s]+)"?\s*$')


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


def yarn_problems(
	path: str, base_text: str | None, new_text: str, trusted: BaseLocks, seed: bool
) -> tuple[list[str], list[str]]:
	if "__metadata:" in new_text:
		return [], [f"{path} is a yarn berry lock, which this check does not read"]
	names, urls = yarn_entries(new_text)
	rejects = []
	for url in sorted(urls):
		parts = urllib.parse.urlsplit(url)
		if (
			not (parts.scheme == "https" and (parts.hostname or "") in NPM_HOSTS)
			and url not in trusted.resolved
		):
			rejects.append(f"{path}: resolves {url[:200]}, which is not on the npm registry")
	reviews = []
	added = sorted(names - (yarn_entries(base_text)[0] if base_text else set()))
	if added and not seed:
		reviews.append(f"{path} adds {', '.join(added[:10])}{' …' if len(added) > 10 else ''}")
	return rejects, reviews


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
		elif path.endswith("yarn.lock"):
			out.resolved |= yarn_entries(text)[1]
	return out


# --- c: the render ---------------------------------------------------------------------


def _regions(text: str) -> dict[str, list[str]]:
	out: dict[str, list[str]] = {}
	current = None
	for line in text.splitlines():
		m = _REGION.match(line)
		if m and m[1] == "begin":
			current = m[2]
			out.setdefault(current, [])
		elif m:
			current = None
		elif current is not None:
			out[current].append(line)
	return out


def _outside(text: str, begin: str, end: str) -> str | None:
	lines = text.split("\n")
	if begin not in lines or end not in lines[lines.index(begin) :]:
		return None
	start = lines.index(begin)
	stop = lines.index(end, start)
	return "\n".join([*lines[:start], *lines[stop + 1 :]])


def second_line(path: str, strategy: str, before: str | None, after: str | None) -> list[str]:
	"""Rules the render already keeps, checked again on the patch itself."""
	from frappe_nix_tools.scaffold import blocks

	out = []
	if before is not None and "frappe-nix:local-begin" in before:
		after_regions = _regions(after or "")
		for name, body in _regions(before).items():
			if after_regions.get(name, []) != body and (after is not None or any(x.strip() for x in body)):
				out.append(f"{path}: changes the frappe-nix:local region {name!r}")
	if strategy == "seed" and before is not None and after != before:
		out.append(f"{path}: changes a seed file, which sync only ever creates")
	if strategy == "blocks" and before is not None and after is not None:
		for begin, end in (
			(blocks.GITIGNORE_BEGIN, blocks.GITIGNORE_END),
			(blocks.INIT_BEGIN, blocks.INIT_END),
		):
			b, a = _outside(before, begin, end), _outside(after, begin, end)
			if b is not None and a is not None and a != b:
				out.append(f"{path}: changes text outside its managed block")
	return out


def _decode(data: bytes | None, path: str) -> str | None:
	if data is None:
		return None
	try:
		return data.decode("utf-8")
	except UnicodeDecodeError as e:
		raise ValueError(f"{path} is not UTF-8 text") from e


def rendered_changes(root: Path) -> tuple[dict[str, tuple[str | None, str]], list[str]]:
	"""``({path: (wanted, strategy)}, invalid)``: what sync's plan changes in ``root``, lock files
	aside, and what it cannot render (exit 2 items)."""
	from frappe_nix_tools.scaffold import engine

	plan = engine.build(root, options={"init_listing": False, "force": False})
	out: dict[str, tuple[str | None, str]] = {}
	invalid = []
	for item in plan.items:
		if item.code >= INVALID:
			invalid.append(f"{item.path}: {item.problem}")
		elif item.command is None and item.wanted != item.current and not is_lock(item.path):
			out.setdefault(item.path, (item.wanted, item.strategy))
	return out, invalid


def check_problems(root: Path, expect_rev: str) -> list[str]:
	"""What ``sync --check --expect-rev`` reports in ``root``, one line each."""
	from frappe_nix_tools.scaffold import engine

	plan = engine.build(root, options={"init_listing": False, "force": False})
	items = [
		*plan.problems,
		*engine.lock_problems(plan),
		*engine.untracked_lock_problems(plan),
		*engine.skew_problems(plan, expect_rev),
	]
	return [f"{i.path}: {i.problem or 'drift'}" for i in items]


# --- GitHub reads ----------------------------------------------------------------------


def fetch_frappe_nix_lock(tag_sha: str) -> dict:
	"""frappe-nix's own ``flake.lock`` at ``tag_sha``."""
	doc = github.get(f"repos/Avunu/frappe-nix/contents/flake.lock?ref={tag_sha}") or {}
	try:
		text = base64.b64decode(doc.get("content", "")).decode()
	except (ValueError, UnicodeDecodeError) as e:
		raise EnvError(f"frappe-nix's flake.lock at {tag_sha}: {e}") from e
	return flakelock.parse(text, f"frappe-nix's flake.lock at {tag_sha}")


def legacy_prs(repo: str) -> list[int]:
	"""The open pull requests of the per-release branches ``standards/frappe-nix-v*``."""
	try:
		pulls = github.get(f"repos/{repo}/pulls?state=open&per_page=100", paginate=True) or []
	except github.GitHubError:
		return []
	out = []
	for pr in pulls:
		head = pr.get("head") or {}
		same_repo = str((head.get("repo") or {}).get("full_name", "")).lower() == repo.lower()
		if same_repo and str(head.get("ref", "")).startswith(LEGACY_PREFIX):
			out.append(int(pr["number"]))
	return sorted(out)


# --- the step ---------------------------------------------------------------------------


@dataclass
class Request:
	repo: str
	base_sha: str
	tag: str
	tag_sha: str
	patch: Path
	worktree: Path

	def validate(self) -> None:
		if not _REPO.fullmatch(self.repo):
			raise ConfigError(f"--repo {self.repo!r} is not <owner>/<repo>")
		for name, value in (("--base-sha", self.base_sha), ("--tag-sha", self.tag_sha)):
			if not _SHA.fullmatch(value):
				raise ConfigError(f"{name} {value!r} is not a full commit SHA")
		if not _TAG.fullmatch(self.tag):
			raise ConfigError(f"--tag {self.tag!r} is not a release tag vX.Y.Z")


def _materialize(root: Path, tree: str) -> None:
	"""Index and files of ``root`` (a work tree at the base) set to ``tree``; HEAD stays."""
	_git(root, "read-tree", "-m", "-u", "HEAD", tree)


def _same_json(path: str, a: str, b: str) -> bool:
	from frappe_nix_tools.scaffold import semantic

	if semantic.kind(path) != "json":
		return False
	try:
		return semantic.parse(a, "json") == semantic.parse(b, "json")
	except ValueError:
		return False


def _verify_trees(
	req: Request,
	res: Result,
	facts: dict,
	changes: dict[str, str],
	repo_dir: Path,
	result_root: Path,
	expected_root: Path,
	fn_lock: dict | None,
) -> None:
	def after(path: str) -> bytes | None:
		return None if changes.get(path) == "D" else (result_root / path).read_bytes()

	try:
		app = appgates.load(repo_dir)
	except FrappeNixError as e:
		res.rejects.append(f"the base's [tool.frappe-nix] does not load: {e}")
		return
	facts["merge_method"] = (app.cfg.get("repo-policy", {}).get("merge-methods") or ["squash"])[0]
	major = app.cfg.get("frappe-major")
	tracked = _git(repo_dir, "ls-files", "-z", "--", *sorted(LOCK_FILES), NODE_LOCKS).stdout.decode()
	base_locks = {p: _show(repo_dir, req.base_sha, p) or b"" for p in tracked.split("\0") if p}
	trusted = trusted_locks(base_locks)

	# b: the lock files, and the base plus them is what the engine renders from.
	if fn_lock is None:
		fn_lock = fetch_frappe_nix_lock(req.tag_sha)
	locks = sorted(p for p in changes if is_lock(p))
	base_flake = _show(repo_dir, req.base_sha, "flake.lock")
	new_flake = after("flake.lock") if "flake.lock" in changes else base_flake
	res.rejects += flake_lock_problems(
		base_flake.decode(errors="replace") if base_flake is not None else None,
		new_flake.decode(errors="replace") if new_flake is not None else None,
		req.tag_sha,
		fn_lock,
	)
	for path in locks:
		data = after(path)
		if data is None or path == "flake.lock":
			continue
		before = base_locks.get(path)
		try:
			text = _decode(data, path) or ""
		except ValueError as e:
			res.rejects.append(str(e))
			continue
		before_text = before.decode(errors="replace") if before is not None else None
		if path in UV_LOCKS:
			rejects, reviews = uv_problems(path, before_text, text, req.tag_sha, trusted)
		elif path == "yarn.lock" or (path.startswith(NODE_LOCKS) and path.endswith("/yarn.lock")):
			seed = before is None and node_lock_seed(path, data, major)
			rejects, reviews = yarn_problems(path, before_text, text, trusted, seed)
		elif path.startswith(NODE_LOCKS) and path.endswith("/source.json"):
			try:
				json.loads(text)
				rejects, reviews = [], []
			except json.JSONDecodeError as e:
				rejects, reviews = [f"{path}: not JSON: {e}"], []
		else:
			rejects, reviews = [f"{path}: not a file relock writes under {NODE_LOCKS}"], []
		res.rejects += rejects
		res.reviews += reviews
	if res.rejects:
		return
	for path in locks:
		data = after(path)
		if data is None:
			_git(expected_root, "rm", "-q", "--cached", "--ignore-unmatch", "--", path)
			(expected_root / path).unlink(missing_ok=True)
		else:
			(expected_root / path).parent.mkdir(parents=True, exist_ok=True)
			(expected_root / path).write_bytes(data)
			_git(expected_root, "add", "--", path)

	# c: every other path is what the tag's sync renders.
	try:
		wanted, invalid = rendered_changes(expected_root)
	except FrappeNixError as e:
		res.rejects.append(f"sync cannot render the base with the new locks: {e}")
		return
	res.rejects += [f"sync cannot render {p}" for p in invalid]
	for path in sorted(p for p in changes if not is_lock(p)):
		if path not in wanted:
			res.rejects.append(f"{path}: changed, but {req.tag}'s sync does not change it")
			continue
		want, strategy = wanted[path]
		try:
			got = _decode(after(path), path)
			before = _decode(_show(repo_dir, req.base_sha, path), path)
		except ValueError as e:
			res.rejects.append(str(e))
			continue
		if want is None or got is None:
			if want != got:
				res.rejects.append(
					f"{path}: {'deleted' if got is None else 'kept'}, but sync {'writes' if got is None else 'deletes'} it"
				)
			continue
		if got != want:
			# A merged JSON file (package.json) is settled by oxfmt after sync writes it; the
			# same data in another layout is for a person to look at. Anything else (YAML and
			# TOML, whose comments could carry anything) must be the render's bytes.
			if _same_json(path, got, want):
				res.reviews.append(f"{path} differs from {req.tag}'s render only in formatting")
			else:
				res.rejects.append(f"{path}: differs from what {req.tag}'s sync renders")
				continue
		res.rejects += second_line(path, strategy, before, got)
	for path in sorted(set(wanted) - set(changes)):
		res.rejects.append(f"{path}: {req.tag}'s sync changes it, the patch does not")
	if res.rejects:
		return

	# d: sync --check on the result.
	try:
		left = check_problems(result_root, req.tag_sha)
	except FrappeNixError as e:
		res.rejects.append(f"sync --check fails on the result: {e}")
		return
	if left:
		try:
			before = set(check_problems(repo_dir, req.tag_sha))
		except FrappeNixError:
			before = set()
		for line in left:
			if line in before:
				res.reviews.append(f"sync --check on the base reports this too: {line}")
			else:
				res.rejects.append(f"sync --check on the result: {line}")

	# The auto-merge safety rule (§4.5), read from the integration branch's rules.
	try:
		facts["automerge"] = github.gates_protected(req.repo, app.integration, app.gates)
	except EnvError:
		facts["automerge"] = False


def verify(req: Request, fn_lock: dict | None = None) -> tuple[Result, dict]:
	"""Check ``req``'s patch; returns the result and the facts the decision carries."""
	req.validate()
	res = Result()
	facts: dict[str, Any] = {"patch_sha256": "", "tree_id": "", "merge_method": "squash", "automerge": False}
	patch = req.patch.read_bytes()
	facts["patch_sha256"] = hashlib.sha256(patch).hexdigest()
	repo_dir = req.worktree.resolve()
	head = _git(repo_dir, "rev-parse", "HEAD").stdout.decode().strip()
	if head != req.base_sha:
		res.rejects.append(f"the checkout is at {head[:12]}, not the base {req.base_sha[:12]}")
		return res, facts
	if not patch.strip():
		res.empty = True
		return res, facts
	if not frappe_nix_tools.__version__ or req.tag != f"v{frappe_nix_tools.__version__}":
		res.rejects.append(f"this frappe-nix-tools is v{frappe_nix_tools.__version__}, not {req.tag}'s")
	res.rejects += header_problems(patch)
	with tempfile.TemporaryDirectory(prefix="frappe-nix-verify-") as tmp_name, _safe_env():
		tmp = Path(tmp_name)
		(tmp / "patch").write_bytes(patch)
		env = {**os.environ, "GIT_INDEX_FILE": str(tmp / "index")}
		_git(repo_dir, "read-tree", req.base_sha, env=env)
		applied = _git(
			repo_dir, "apply", "--cached", "--whitespace=nowarn", str(tmp / "patch"), env=env, check=False
		)
		if applied.returncode != 0:
			res.rejects.append(
				f"the patch does not apply to the base: {applied.stderr.decode(errors='replace').strip()[:300]}"
			)
			return res, facts
		raw = _git(
			repo_dir,
			"diff-index",
			"--cached",
			"--raw",
			"-z",
			"--no-renames",
			"--full-index",
			req.base_sha,
			env=env,
		).stdout
		entries = parse_raw(raw)
		facts["tree_id"] = _git(repo_dir, "write-tree", env=env).stdout.decode().strip()
		if not entries:
			res.empty = True
			return res, facts
		for old_mode, new_mode, status, path in entries:
			if problem := path_problem(path) or entry_problem(old_mode, new_mode, status, path):
				res.rejects.append(problem)
		if res.rejects:
			return res, facts
		changes = {path: status for _o, _n, status, path in entries}
		with (
			_worktree(repo_dir, req.base_sha, tmp, "result") as result_root,
			_worktree(repo_dir, req.base_sha, tmp, "expected") as expected_root,
		):
			_materialize(result_root, facts["tree_id"])
			_verify_trees(req, res, facts, changes, repo_dir, result_root, expected_root, fn_lock)
	return res, facts


def body(req: Request, res: Result, integration: str) -> str:
	lines = [
		f"Moves frappe-nix to [{req.tag}](https://github.com/Avunu/frappe-nix/releases/tag/{req.tag})"
		f" (`{req.tag_sha[:12]}`): the `flake.lock` bump, the relock and `frappe-init --sync`, in one commit.",
		"",
		f"Planned from `{req.base_sha[:12]}`{f' on `{integration}`' if integration else ''} by frappe-nix's"
		" `rollout.yml` with no write token, and verified before it was pushed: the lock files come from"
		f" the plan, and every other file is what {req.tag}'s `frappe-nix sync` renders.",
	]
	if res.reviews:
		lines += ["", "**Needs review:**", *[f"- {r}" for r in res.reviews]]
	lines += [
		"",
		f"`{BRANCH}` belongs to the rollout: a commit by anyone else pauses it on this repository.",
	]
	return "\n".join(lines)


def decision_doc(req: Request, res: Result, facts: dict) -> dict:
	integration = ""
	with contextlib.suppress(FrappeNixError, KeyError):
		integration = appgates.load(req.worktree.resolve()).integration
	decision = res.decision()
	return {
		"decision": decision,
		"reasons": res.reasons(),
		"patch_sha256": facts.get("patch_sha256", ""),
		"tree_id": facts.get("tree_id", ""),
		"branch": BRANCH,
		"title": f"chore(deps): frappe-nix {req.tag}",
		"body": body(req, res, integration),
		"merge_method": facts.get("merge_method", "squash"),
		"automerge": bool(facts.get("automerge")) and decision == PUBLISH,
		"legacy_prs": legacy_prs(req.repo) if decision in (PUBLISH, REVIEW) else [],
	}


def run_verify(args: argparse.Namespace) -> int:
	req = Request(args.repo, args.base_sha, args.tag, args.tag_sha, Path(args.patch), Path(args.worktree))
	fn_lock = None
	if args.frappe_nix_lock:
		fn_lock = flakelock.load(Path(args.frappe_nix_lock))
	res, facts = verify(req, fn_lock)
	doc = decision_doc(req, res, facts)
	Path(args.out).write_text(json.dumps(doc, indent=2) + "\n")
	print(f"{req.repo}: {doc['decision']}")
	for reason in doc["reasons"]:
		print(f"  {reason}")
	output(decision=doc["decision"], tree_id=doc["tree_id"], patch_sha256=doc["patch_sha256"])
	return 0


def run_contract(_args: argparse.Namespace) -> int:
	print(CONTRACT)
	return 0


def register(sub: argparse._SubParsersAction) -> None:
	q = sub.add_parser(
		"verify-rollout-patch",
		help="check a rollout plan's patch against the tag's render before it is pushed (§5.8)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	q.add_argument("--repo", required=True, help="the consumer, <owner>/<repo>")
	q.add_argument("--base-sha", required=True, help="the integration head the plan started from")
	q.add_argument("--tag", required=True, help="the frappe-nix release, vX.Y.Z")
	q.add_argument("--tag-sha", required=True, help="the release's commit")
	q.add_argument("--patch", required=True, help="the plan's patch")
	q.add_argument("--worktree", required=True, help="the consumer checked out at --base-sha")
	q.add_argument("--out", required=True, help="where to write the decision (JSON)")
	q.add_argument(
		"--frappe-nix-lock",
		default="",
		help="frappe-nix's own flake.lock at the tag (default: read from GitHub)",
	)
	q.set_defaults(func=run_verify)

	q = sub.add_parser("rollout-contract", help="the rollout.yml contract this frappe-nix-tools implements")
	q.set_defaults(func=run_contract)
