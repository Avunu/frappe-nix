"""Locked GitHub trees on disk: ``ironclad pin-path <input>`` (spec §4.2, §5.2).

Given an input of the app's ``flake.lock`` (its own, such as ``frappe``, or one frappe-nix
pins for it, such as ``frappe-semgrep-rules``), produce a directory holding exactly the
locked tree:

1. In a dev shell the tree is usually in the Nix store already: when the store path its
   ``narHash`` implies exists, that is the answer.
2. Otherwise ``.dev-dist/pins/<repo>-<rev>/`` under the app, fetched once from GitHub's
   tarball endpoint and verified against the ``narHash`` with ``ironclad.common.nar``.
   A mismatch is an ``EnvError`` (exit 3) and leaves nothing behind.

``IRONCLAD_PIN_URL`` replaces the download URL (``{owner}``, ``{repo}`` and ``{rev}`` are
filled in), for mirrors and for the tests, which serve a tarball from ``file://``.
"""

import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from ironclad.common import flakelock, nar
from ironclad.common.report import EnvError

PINS_DIR = ".dev-dist/pins"


def _url(pin: flakelock.Pin) -> str:
	template = os.environ.get("IRONCLAD_PIN_URL")
	if not template:
		return pin.tarball_url
	return template.format(owner=pin.owner, repo=pin.repo, rev=pin.rev)


def _fetch(url: str, dest: Path) -> None:
	try:
		with urllib.request.urlopen(url, timeout=120) as response, dest.open("wb") as out:
			shutil.copyfileobj(response, out)
	except OSError as e:
		raise EnvError(f"cannot fetch {url}: {e}") from e


def _unpack(tarball: Path, into: Path) -> Path:
	"""Unpack and return the tarball's single top-level directory (GitHub's ``<repo>-<rev>/``)."""
	try:
		with tarfile.open(tarball) as tar:
			tar.extractall(into, filter="tar")
	except (tarfile.TarError, OSError) as e:
		raise EnvError(f"cannot unpack {tarball}: {e}") from e
	entries = list(into.iterdir())
	if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
		raise EnvError(f"{tarball} does not hold exactly one top-level directory")
	return entries[0]


def pin_path(name: str, lock_path: Path, store_dir: str = "/nix/store") -> Path:
	"""The directory holding input ``name`` as ``lock_path`` locks it."""
	pin = flakelock.github_pin(flakelock.load(lock_path), name)

	in_store = Path(nar.store_path(pin.nar_hash, store_dir=store_dir))
	if in_store.is_dir():
		return in_store

	pins = lock_path.parent / PINS_DIR
	dest = pins / f"{pin.repo}-{pin.rev}"
	stamp = pins / f"{pin.repo}-{pin.rev}.narHash"
	if dest.is_dir() and stamp.is_file() and stamp.read_text().strip() == pin.nar_hash:
		return dest

	pins.mkdir(parents=True, exist_ok=True)
	with tempfile.TemporaryDirectory(dir=pins, prefix=".fetch-") as tmp:
		work = Path(tmp)
		tarball = work / "src.tar.gz"
		_fetch(_url(pin), tarball)
		tree = _unpack(tarball, work / "unpacked")
		got = nar.nar_hash(tree)
		if got != pin.nar_hash:
			raise EnvError(
				f"{pin.owner}/{pin.repo}@{pin.rev} does not match flake.lock: narHash {got}, locked {pin.nar_hash}"
			)
		if dest.exists():
			shutil.rmtree(dest)
		tree.rename(dest)
	stamp.write_text(pin.nar_hash + "\n")
	return dest
