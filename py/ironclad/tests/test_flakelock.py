import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from ironclad.common import flakelock
from ironclad.common.report import ConfigError, EnvError


def github(owner, repo, rev, nar_hash="sha256-AAAA"):
	return {"locked": {"type": "github", "owner": owner, "repo": repo, "rev": rev, "narHash": nar_hash}}


# An app's lock: frappe is its own input, marketplace is frappe-nix's, and nixpkgs follows
# frappe-nix's.
LOCK: dict[str, Any] = {
	"version": 7,
	"root": "root",
	"nodes": {
		"root": {
			"inputs": {"frappe": "frappe", "frappe-nix": "frappe-nix", "nixpkgs": ["frappe-nix", "nixpkgs"]}
		},
		"frappe": github("frappe", "frappe", "f" * 40),
		"frappe-nix": {
			**github("Avunu", "frappe-nix", "a" * 40),
			"inputs": {"marketplace": "marketplace", "nixpkgs": "nixpkgs", "flake-parts": "flake-parts"},
		},
		"marketplace": github("frappe", "marketplace", "2" * 40),
		"nixpkgs": github("NixOS", "nixpkgs", "n" * 40),
		"flake-parts": {"locked": {"type": "path", "path": "/x"}},
	},
}


class TestFlakeLock(unittest.TestCase):
	def test_load(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "flake.lock"
			with self.assertRaises(EnvError):
				flakelock.load(path)
			path.write_text("{")
			with self.assertRaises(EnvError):
				flakelock.load(path)
			path.write_text(json.dumps(LOCK))
			self.assertEqual(flakelock.load(path), LOCK)

	def test_malformed_locks_are_env_errors(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "flake.lock"
			for doc in (
				[],
				{"root": "root", "nodes": []},
				{"root": 1, "nodes": {}},
				{"root": "r", "nodes": {"r": 1}},
			):
				path.write_text(json.dumps(doc))
				with self.assertRaises(EnvError, msg=doc):
					flakelock.load(path)
		dangling = {"root": "root", "nodes": {"root": {"inputs": {"pilot": "missing"}}}}
		with self.assertRaises(EnvError):
			flakelock.input_node(dangling, "pilot")
		bad_inputs = {"root": "root", "nodes": {"root": {"inputs": ["pilot"]}}}
		with self.assertRaises(EnvError):
			flakelock.input_node(bad_inputs, "pilot")
		not_a_table = {"root": "root", "nodes": {"root": {"inputs": {"p": "p"}}, "p": {"locked": "x"}}}
		with self.assertRaises(ConfigError):
			flakelock.github_pin(not_a_table, "p")
		missing = {"root": "root", "nodes": {"root": {"inputs": {"p": "p"}}, "p": github("o", "r", "a" * 40)}}
		del missing["nodes"]["p"]["locked"]["narHash"]
		with self.assertRaisesRegex(EnvError, "no locked narHash"):
			flakelock.github_pin(missing, "p")

	def test_follows(self):
		self.assertEqual(flakelock.node_at(LOCK, ["nixpkgs"]), ("nixpkgs", LOCK["nodes"]["nixpkgs"]))
		self.assertIsNone(flakelock.node_at(LOCK, ["nope"]))

	def test_input_node_falls_back_to_frappe_nix(self):
		self.assertEqual(flakelock.input_node(LOCK, "frappe"), ("frappe", LOCK["nodes"]["frappe"]))
		self.assertEqual(
			flakelock.input_node(LOCK, "marketplace"), ("marketplace", LOCK["nodes"]["marketplace"])
		)
		self.assertIsNone(flakelock.input_node(LOCK, "pilot"))

	def test_frappe_nix_rev(self):
		self.assertEqual(flakelock.frappe_nix_rev(LOCK), "a" * 40)
		self.assertIsNone(flakelock.frappe_nix_rev({"root": "root", "nodes": {"root": {}}}))

	def test_github_pin(self):
		pin = flakelock.github_pin(LOCK, "marketplace")
		self.assertEqual((pin.owner, pin.repo, pin.rev), ("frappe", "marketplace", "2" * 40))
		self.assertEqual(pin.tarball_url, f"https://codeload.github.com/frappe/marketplace/tar.gz/{'2' * 40}")
		with self.assertRaises(ConfigError):
			flakelock.github_pin(LOCK, "pilot")
		with self.assertRaises(ConfigError):
			flakelock.github_pin(LOCK, "flake-parts")


if __name__ == "__main__":
	unittest.main()
