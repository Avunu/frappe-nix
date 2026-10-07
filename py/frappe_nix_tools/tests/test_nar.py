import hashlib
import tempfile
import unittest
from pathlib import Path

from frappe_nix_tools.common import nar
from helpers import TREE_NAR_HASH, make_tree


class TestNar(unittest.TestCase):
	def test_tree_matches_nix(self):
		with tempfile.TemporaryDirectory() as tmp:
			self.assertEqual(nar.nar_hash(make_tree(Path(tmp) / "t")), TREE_NAR_HASH)

	def test_single_file(self):
		with tempfile.TemporaryDirectory() as tmp:
			f = Path(tmp) / "f"
			f.write_text("hello\n")
			f.chmod(0o644)
			# nix hash path --type sha256 --sri f
			self.assertEqual(nar.nar_hash(f), "sha256-HDfQGvQL4ugGkd48w99EN3ppmvuxfGjwgJZLL9Bx/BM=")

	def test_store_path(self):
		# flake-parts as frappe-nix's flake.lock pinned it, and where Nix put it.
		self.assertEqual(
			nar.store_path("sha256-zTwuwezD0w3hpga6a6P8emidzAZFyWqlNeRBlpWOOl8="),
			"/nix/store/6xr58mfvs42ixw6428x3vm9l5h88l2z7-source",
		)

	def test_store_path_rejects_other_algorithms(self):
		with self.assertRaises(ValueError):
			nar.store_path("sha512-AAAA")

	def test_nix32(self):
		self.assertEqual(nar.nix32(bytes(20)), "0" * 32)
		# nix hash convert --hash-algo sha256 --to nix32 <sha256 of "">
		self.assertEqual(
			nar.nix32(hashlib.sha256(b"").digest()), "0mdqa9w1p6cmli6976v4wi0sw9r4p5prkj7lzfd1877wk11c9c73"
		)


if __name__ == "__main__":
	unittest.main()
