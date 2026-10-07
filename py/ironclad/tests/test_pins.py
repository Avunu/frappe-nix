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
from ironclad.common.report import ConfigError, EnvError

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

	def test_tampered_cache_is_refetched(self):
		# A tree committed under the expected name (with the old stamp beside it) must be
		# hashed, not trusted: a PR could otherwise swap in its own semgrep rules.
		self.lock.write_text(json.dumps(lock_for(TREE_NAR_HASH)))
		pins = self.app / ".dev-dist" / "pins"
		dest = pins / f"pilot-{REV}"
		dest.mkdir(parents=True)
		(dest / "empty.yml").write_text("rules: []\n")
		(pins / f"pilot-{REV}.narHash").write_text(TREE_NAR_HASH + "\n")
		self.assertEqual(pin_path("pilot", self.lock, store_dir=self.store), dest)
		self.assertEqual(nar.nar_hash(dest), TREE_NAR_HASH)
		self.assertFalse((dest / "empty.yml").exists())

	def test_a_retargeted_pin_is_refused_before_fetching(self):
		# A PR's flake.lock pointing frappe-semgrep-rules at another repository (with that
		# repository's narHash) must not get its tree run as the rules.
		lock = lock_for(TREE_NAR_HASH)
		lock["nodes"]["frappe-nix"]["inputs"]["frappe-semgrep-rules"] = "pilot"
		self.lock.write_text(json.dumps(lock))
		with self.assertRaisesRegex(ConfigError, "frappe/semgrep-rules"):
			pin_path("frappe-semgrep-rules", self.lock, store_dir=self.store)
		# Nor can a root-level input of that name stand in for frappe-nix's.
		del lock["nodes"]["frappe-nix"]["inputs"]["frappe-semgrep-rules"]
		lock["nodes"]["root"]["inputs"]["frappe-semgrep-rules"] = "rules"
		lock["nodes"]["rules"] = json.loads(json.dumps(lock["nodes"]["pilot"]))
		lock["nodes"]["rules"]["locked"]["repo"] = "semgrep-rules"
		self.lock.write_text(json.dumps(lock))
		with self.assertRaisesRegex(ConfigError, "no input 'frappe-semgrep-rules'"):
			pin_path("frappe-semgrep-rules", self.lock, store_dir=self.store)
		self.assertFalse((self.app / ".dev-dist").exists())

	def test_tampered_cache_without_network_is_exit_3(self):
		self.lock.write_text(json.dumps(lock_for(TREE_NAR_HASH)))
		path = pin_path("pilot", self.lock, store_dir=self.store)
		self.tarball.unlink()
		(path / "planted").write_text("x\n")
		with self.assertRaisesRegex(EnvError, "cannot fetch"):
			pin_path("pilot", self.lock, store_dir=self.store)

	def test_symlinked_cache_is_replaced(self):
		self.lock.write_text(json.dumps(lock_for(TREE_NAR_HASH)))
		elsewhere = make_tree(self.root / "elsewhere")
		pins = self.app / ".dev-dist" / "pins"
		pins.mkdir(parents=True)
		dest = pins / f"pilot-{REV}"
		dest.symlink_to(elsewhere)
		self.assertEqual(pin_path("pilot", self.lock, store_dir=self.store), dest)
		self.assertFalse(dest.is_symlink())
		self.assertEqual(nar.nar_hash(dest), TREE_NAR_HASH)
		self.assertEqual(nar.nar_hash(elsewhere), TREE_NAR_HASH)

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

	def test_lock_names_cannot_leave_the_pins_directory(self):
		victim = self.root / "victim-r"
		victim.mkdir()
		(victim / "precious").write_text("keep\n")
		for key, value in (("repo", "../../victim"), ("repo", ".."), ("owner", "a/b"), ("rev", "r")):
			lock = lock_for(TREE_NAR_HASH)
			lock["nodes"]["pilot"]["locked"][key] = value
			self.lock.write_text(json.dumps(lock))
			with self.assertRaises(ConfigError, msg=f"{key}={value!r}"):
				pin_path("pilot", self.lock, store_dir=self.store)
		self.assertEqual((victim / "precious").read_text(), "keep\n")
		self.assertFalse((self.app / ".dev-dist").exists())

	def test_bad_url_template_is_exit_2(self):
		self.lock.write_text(json.dumps(lock_for(TREE_NAR_HASH)))
		with mock.patch.dict(os.environ, {"IRONCLAD_PIN_URL": "{bogus}"}):
			with self.assertRaises(ConfigError):
				pin_path("pilot", self.lock, store_dir=self.store)

	def test_not_a_url_is_exit_3(self):
		self.lock.write_text(json.dumps(lock_for(TREE_NAR_HASH)))
		with mock.patch.dict(os.environ, {"IRONCLAD_PIN_URL": "notaurl"}):
			with self.assertRaisesRegex(EnvError, "cannot fetch"):
				pin_path("pilot", self.lock, store_dir=self.store)


if __name__ == "__main__":
	unittest.main()
