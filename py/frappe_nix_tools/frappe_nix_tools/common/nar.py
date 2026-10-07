"""Nix archive (NAR) hashing in pure Python, so a no-Nix job can verify a ``flake.lock`` pin.

``nar_hash(path)`` equals ``nix hash path --type sha256 --sri path``: the SHA-256 of the
NAR serialisation of the tree, where a file is executable when its owner may execute it,
and directory entries are sorted by name. ``store_path`` computes where Nix keeps that
tree as a flake input (``/nix/store/<hash>-source``), so a dev shell can skip the download.
"""

import base64
import hashlib
import os
import stat
from pathlib import Path

_NIX32 = "0123456789abcdfghijklmnpqrsvwxyz"


def _str(h, data: bytes) -> None:
	h.update(len(data).to_bytes(8, "little"))
	h.update(data)
	h.update(b"\0" * (-len(data) % 8))


def _dump(h, path: str) -> None:
	st = os.lstat(path)
	_str(h, b"(")
	if stat.S_ISLNK(st.st_mode):
		_str(h, b"type")
		_str(h, b"symlink")
		_str(h, b"target")
		_str(h, os.fsencode(os.readlink(path)))
	elif stat.S_ISDIR(st.st_mode):
		_str(h, b"type")
		_str(h, b"directory")
		for name in sorted(os.listdir(os.fsencode(path))):
			_str(h, b"entry")
			_str(h, b"(")
			_str(h, b"name")
			_str(h, name)
			_str(h, b"node")
			_dump(h, os.path.join(path, os.fsdecode(name)))
			_str(h, b")")
	elif stat.S_ISREG(st.st_mode):
		_str(h, b"type")
		_str(h, b"regular")
		if st.st_mode & stat.S_IXUSR:
			_str(h, b"executable")
			_str(h, b"")
		_str(h, b"contents")
		h.update(st.st_size.to_bytes(8, "little"))
		with open(path, "rb") as f:
			while chunk := f.read(1 << 20):
				h.update(chunk)
		h.update(b"\0" * (-st.st_size % 8))
	else:
		raise ValueError(f"{path}: not a file, directory or symlink")
	_str(h, b")")


def nar_hash(path: Path) -> str:
	"""The SRI SHA-256 NAR hash of ``path`` (``sha256-<base64>``)."""
	h = hashlib.sha256()
	_str(h, b"nix-archive-1")
	_dump(h, os.fspath(path))
	return "sha256-" + base64.b64encode(h.digest()).decode()


def nix32(data: bytes) -> str:
	"""Nix's base-32 encoding (its own alphabet, least significant bits first)."""
	length = (len(data) * 8 - 1) // 5 + 1
	out = []
	for n in range(length - 1, -1, -1):
		b = n * 5
		i, j = divmod(b, 8)
		c = data[i] >> j
		if i + 1 < len(data):
			c |= data[i + 1] << (8 - j)
		out.append(_NIX32[c & 0x1F])
	return "".join(out)


def store_path(sri: str, name: str = "source", store_dir: str = "/nix/store") -> str:
	"""The store path of a tree with NAR hash ``sri`` added recursively under ``name``."""
	algo, _, b64 = sri.partition("-")
	if algo != "sha256":
		raise ValueError(f"only sha256 NAR hashes are supported, not {algo!r}")
	digest = base64.b64decode(b64)
	fingerprint = f"source:sha256:{digest.hex()}:{store_dir}:{name}"
	full = hashlib.sha256(fingerprint.encode()).digest()
	compressed = bytearray(20)
	for i, byte in enumerate(full):
		compressed[i % 20] ^= byte
	return f"{store_dir}/{nix32(bytes(compressed))}-{name}"
