import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import TREE_NAR_HASH, make_tree
from ironclad.common import nar
from ironclad.common.pins import pin_path
from ironclad.common.report import EnvError

REV = "e" * 40


def lock_for(nar_hash):
	return {
		"version": 7,
		"root": "root",
		"nodes": {
			"root": {"inputs": {"frappe-nix": "frappe-nix"}},
			"frappe-nix": {"inputs": {"pilot": "pilot"}, "locked": {"type": "github", "rev": "a" * 40}},
			"pilot": {
				"locked": {
					"type": "github",
					"owner": "frappe",
					"repo": "pilot",
					"rev": REV,
					"narHash": nar_hash,
				}
			},
		},
	}


class TestPins(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.root = Path(self.tmp.name)
		# A GitHub-shaped tarball: one top-level <repo>-<rev>/ directory.
		src = make_tree(self.root / "src" / f"pilot-{REV}")
		self.tarball = self.root / "pilot.tar.gz"
		with tarfile.open(self.tarball, "w:gz") as tar:
			tar.add(src, arcname=src.name)
		self.app = self.root / "app"
		self.app.mkdir()
		self.lock = self.app / "flake.lock"
		self.store = str(self.root / "store")
		url = f"file://{self.root}/{{repo}}.tar.gz"
		self.patch = mock.patch.dict(os.environ, {"IRONCLAD_PIN_URL": url})
		self.patch.start()

	def tearDown(self):
		self.patch.stop()
		self.tmp.cleanup()

	def test_fetch_verify_and_reuse(self):
		self.lock.write_text(json.dumps(lock_for(TREE_NAR_HASH)))
		path = pin_path("pilot", self.lock, store_dir=self.store)
		self.assertEqual(path, self.app / ".dev-dist" / "pins" / f"pilot-{REV}")
		self.assertEqual(nar.nar_hash(path), TREE_NAR_HASH)
		self.tarball.unlink()
		self.assertEqual(pin_path("pilot", self.lock, store_dir=self.store), path)

	def test_mismatch_is_exit_3_and_leaves_nothing(self):
		self.lock.write_text(json.dumps(lock_for("sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=")))
		with self.assertRaises(EnvError) as ctx:
			pin_path("pilot", self.lock, store_dir=self.store)
		self.assertEqual(ctx.exception.code, 3)
		self.assertIn("does not match flake.lock", str(ctx.exception))
		self.assertEqual([p.name for p in (self.app / ".dev-dist" / "pins").iterdir()], [])

	def test_store_path_wins(self):
		self.lock.write_text(json.dumps(lock_for(TREE_NAR_HASH)))
		in_store = Path(nar.store_path(TREE_NAR_HASH, store_dir=self.store))
		in_store.mkdir(parents=True)
		self.tarball.unlink()
		self.assertEqual(pin_path("pilot", self.lock, store_dir=self.store), in_store)

	def test_fetch_failure(self):
		self.lock.write_text(json.dumps(lock_for(TREE_NAR_HASH)))
		self.tarball.unlink()
		with self.assertRaisesRegex(EnvError, "cannot fetch"):
			pin_path("pilot", self.lock, store_dir=self.store)


if __name__ == "__main__":
	unittest.main()
