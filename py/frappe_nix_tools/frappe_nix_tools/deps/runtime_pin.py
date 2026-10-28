"""Lock frappe-runtime at the frappe-nix revision flake.lock pins.

Every re-lock runs ``uv lock --upgrade-package frappe-runtime``
(``frappe_nix_tools.deps.update.UV_LOCK``), and the bench template's source for
it is frappe-nix's git repository with no revision. Upgrading that resolves the
head of frappe-nix's default branch, whatever it is that minute, so the same
flake.lock locked twice could disagree, and a relock shell check failed as soon
as frappe-nix's main moved past the revision the app pins.

``runtime_at_pin`` gives the source the ``rev`` flake.lock holds for the length
of one lock, then puts pyproject.toml back as it was and takes the ``&rev=``
uv recorded out of uv.lock again. What is left is what an unpinned lock reads
at that commit, so ``uv lock --locked`` accepts it against the unpinned
pyproject.toml and a plain ``uv lock`` keeps it. It changes nothing when the
source is not that git URL with no ``rev``, ``tag`` or ``branch`` of its own,
or when flake.lock does not lock that same repository at a revision (a ``path``
input, a fork, no flake.lock).

Standard library only: the shell-entry root sync imports it.
"""

import re
import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from frappe_nix_tools.common import flakelock
from frappe_nix_tools.common.report import EnvError

PACKAGE = "frappe-runtime"
_GITHUB = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")


def _source(pyproject: str) -> str | None:
	"""frappe-runtime's git URL, when its source is one with no revision of its own."""
	try:
		doc = tomllib.loads(pyproject)
	except tomllib.TOMLDecodeError:
		return None
	source = doc.get("tool", {}).get("uv", {}).get("sources", {}).get(PACKAGE)
	if not isinstance(source, dict) or not isinstance(source.get("git"), str):
		return None
	if any(key in source for key in ("rev", "tag", "branch")):
		return None
	return source["git"]


def pinned_rev(flake_lock: Path, url: str) -> str | None:
	"""The revision flake.lock's ``frappe-nix`` input locks, if it is ``url``'s repository."""
	match = _GITHUB.fullmatch(url)
	if match is None or not flake_lock.is_file():
		return None
	try:
		lock = flakelock.load(flake_lock)
		found = flakelock.node_at(lock, ["frappe-nix"])
	except EnvError:
		return None
	locked = found[1].get("locked") if found else None
	if not isinstance(locked, dict) or locked.get("type") != "github":
		return None
	owner, repo = match.groups()
	if (
		str(locked.get("owner", "")).lower() != owner.lower()
		or str(locked.get("repo", "")).lower() != repo.lower()
	):
		return None
	rev = locked.get("rev")
	return rev if isinstance(rev, str) and re.fullmatch(r"[0-9a-f]{40}", rev) else None


def _with_rev(pyproject: str, rev: str) -> str | None:
	"""pyproject.toml with ``rev`` added to frappe-runtime's inline source table; ``None``
	when that table is not one line this can edit and read back."""
	line = re.compile(
		rf"^({re.escape(PACKAGE)}|\"{re.escape(PACKAGE)}\")(\s*=\s*\{{[^{{}}\n]*?)(\s*\}})", re.M
	)
	if len(line.findall(pyproject)) != 1:
		return None
	edited = line.sub(lambda m: f'{m.group(1)}{m.group(2)}, rev = "{rev}"{m.group(3)}', pyproject)
	try:
		source = tomllib.loads(edited)["tool"]["uv"]["sources"][PACKAGE]
	except (tomllib.TOMLDecodeError, KeyError, TypeError):
		return None
	return edited if isinstance(source, dict) and source.get("rev") == rev else None


def unpin_lock(lock: Path, url: str, rev: str) -> None:
	"""Take the ``&rev=`` (or ``?rev=``) a pinned source leaves in uv.lock out of ``url``'s entries."""
	if not lock.is_file():
		return
	text = lock.read_text()
	base = re.escape(url.rstrip("/"))
	text2 = re.sub(rf"({base}\?[^\"#]*?)&rev={rev}", r"\1", text)
	text2 = re.sub(rf"({base})\?rev={rev}&", r"\1?", text2)
	text2 = re.sub(rf"({base})\?rev={rev}(?=[\"#])", r"\1", text2)
	if text2 != text:
		lock.write_text(text2)


@contextmanager
def runtime_at_pin(workspace: str | Path = ".", flake_dir: str | Path | None = None) -> Generator[str | None]:
	"""For one ``uv lock`` in ``workspace``: frappe-runtime's source at the revision
	``flake_dir``'s flake.lock (default: ``workspace``'s) pins. Yields that revision,
	or ``None`` when nothing was pinned."""
	pyproject = Path(workspace) / "pyproject.toml"
	try:
		original = pyproject.read_text()
	except OSError:
		yield None
		return
	url = _source(original)
	rev = (
		pinned_rev(Path(flake_dir if flake_dir is not None else workspace) / "flake.lock", url)
		if url
		else None
	)
	edited = _with_rev(original, rev) if url and rev else None
	if url is None or rev is None or edited is None:
		yield None
		return
	pyproject.write_text(edited)
	try:
		yield rev
	finally:
		pyproject.write_text(original)
		unpin_lock(Path(workspace) / "uv.lock", url, rev)
