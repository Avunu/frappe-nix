import http.server
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.common import flakelock, nar, pins
from frappe_nix_tools.common.pins import TOKEN, pin_path
from frappe_nix_tools.common.report import ConfigError, EnvError
from helpers import TREE_NAR_HASH, make_tree

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
		self.patch = mock.patch.dict(os.environ, {"FRAPPE_NIX_PIN_URL": url})
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
		with mock.patch.dict(os.environ, {"FRAPPE_NIX_PIN_URL": "{bogus}"}):
			with self.assertRaises(ConfigError):
				pin_path("pilot", self.lock, store_dir=self.store)

	def test_not_a_url_is_exit_3(self):
		self.lock.write_text(json.dumps(lock_for(TREE_NAR_HASH)))
		with mock.patch.dict(os.environ, {"FRAPPE_NIX_PIN_URL": "notaurl"}):
			with self.assertRaisesRegex(EnvError, "cannot fetch"):
				pin_path("pilot", self.lock, store_dir=self.store)


def git(cwd, *args):
	subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class _Handler(http.server.BaseHTTPRequestHandler):
	"""Serves one tarball, only to ``Authorization: Bearer secret`` when ``token`` is set.

	With ``redirect`` set, answers every path but ``/final`` with a 302 to it.
	"""

	tarball: Path
	token = ""
	redirect = ""
	seen: list

	def do_GET(self):
		self.seen.append((self.path, self.headers.get("Authorization")))
		if self.redirect and self.path != "/final":
			self.send_response(302)
			self.send_header("Location", self.redirect)
			self.send_header("Content-Length", "0")
			self.end_headers()
			return
		if self.token and self.headers.get("Authorization") != f"Bearer {self.token}":
			self.send_response(404)
			self.end_headers()
			return
		data = self.tarball.read_bytes()
		self.send_response(200)
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)

	def log_message(self, format: str, *args: object) -> None:
		pass


class TestOtherHostsAndTokens(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.root = Path(self.tmp.name)
		self.app = self.root / "app"
		self.app.mkdir()
		self.lock = self.app / "flake.lock"
		self.store = str(self.root / "store")
		self.env = mock.patch.dict(os.environ, {})
		self.env.start()
		os.environ.pop(TOKEN, None)
		os.environ.pop("FRAPPE_NIX_PIN_URL", None)

	def tearDown(self):
		self.env.stop()
		self.tmp.cleanup()

	def write_lock(self, name, locked):
		self.lock.write_text(
			json.dumps(
				{
					"version": 7,
					"root": "root",
					"nodes": {"root": {"inputs": {name: "n"}}, "n": {"locked": locked}},
				}
			)
		)

	def serve(self, token="", redirect=""):
		src = self.root / "src" / f"profile-{REV}-{REV}"
		if not src.exists():
			make_tree(src)
		tarball = self.root / "archive.tar.gz"
		with tarfile.open(tarball, "w:gz") as tar:
			tar.add(src, arcname=src.name)
		handler = type(
			"H", (_Handler,), {"tarball": tarball, "token": token, "redirect": redirect, "seen": []}
		)
		server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
		threading.Thread(target=server.serve_forever, daemon=True).start()
		self.addCleanup(server.server_close)
		self.addCleanup(server.shutdown)
		return server, handler

	def test_gitlab_archive_with_and_without_a_token(self):
		server, handler = self.serve(token="secret")
		port = server.server_address[1]
		self.write_lock(
			"standards-profile",
			{
				"type": "gitlab",
				"owner": "group",
				"repo": "profile",
				"rev": REV,
				"narHash": TREE_NAR_HASH,
				"host": "git.example.org",
			},
		)
		os.environ["FRAPPE_NIX_PIN_URL"] = (
			f"http://127.0.0.1:{port}/api/v4/projects/{{owner}}%2F{{repo}}/repository/archive.tar.gz?sha={{rev}}"
		)
		with self.assertRaises(EnvError) as ctx:
			pin_path("standards-profile", self.lock, store_dir=self.store)
		self.assertEqual(ctx.exception.code, 3)
		self.assertIn(f"set {TOKEN} (Contents read on group/profile)", str(ctx.exception))
		os.environ[TOKEN] = "secret"
		path = pin_path("standards-profile", self.lock, store_dir=self.store)
		self.assertEqual(path, self.app / ".dev-dist" / "pins" / f"profile-{REV}")
		self.assertEqual(nar.nar_hash(path), TREE_NAR_HASH)
		self.assertEqual(
			handler.seen[-1],
			(f"/api/v4/projects/group%2Fprofile/repository/archive.tar.gz?sha={REV}", "Bearer secret"),
		)

	def profile_lock(self, port, path="/archive"):
		self.write_lock(
			"standards-profile",
			{"type": "gitlab", "owner": "group", "repo": "profile", "rev": REV, "narHash": TREE_NAR_HASH},
		)
		os.environ["FRAPPE_NIX_PIN_URL"] = f"http://127.0.0.1:{port}{path}"

	def test_token_is_not_forwarded_to_another_host(self):
		# A GitLab that redirects archive downloads to object storage, or a mirror, must
		# not be able to hand the token on.
		other, other_seen = self.serve()
		first, first_seen = self.serve(redirect=f"http://127.0.0.1:{other.server_address[1]}/final")
		self.profile_lock(first.server_address[1])
		os.environ[TOKEN] = "secret"
		pin_path("standards-profile", self.lock, store_dir=self.store)
		self.assertEqual(first_seen.seen, [("/archive", "Bearer secret")])
		self.assertEqual(other_seen.seen, [("/final", None)])

	def test_token_follows_a_redirect_on_the_same_origin(self):
		server, handler = self.serve(token="secret")
		handler.redirect = f"http://127.0.0.1:{server.server_address[1]}/final"
		self.profile_lock(server.server_address[1])
		os.environ[TOKEN] = "secret"
		pin_path("standards-profile", self.lock, store_dir=self.store)
		self.assertEqual(handler.seen, [("/archive", "Bearer secret"), ("/final", "Bearer secret")])

	def test_https_to_http_redirect_is_refused(self):
		request = pins.urllib.request.Request("https://git.example.org/archive")
		request.add_header("Authorization", "Bearer secret")
		with self.assertRaisesRegex(EnvError, "not https"):
			pins._Redirects().redirect_request(
				request, None, 302, "Found", {}, "http://git.example.org/archive"
			)
		same = pins._Redirects().redirect_request(
			request, None, 302, "Found", {}, "https://git.example.org/final"
		)
		self.assertEqual(same.get_header("Authorization"), "Bearer secret")
		other = pins._Redirects().redirect_request(
			request, None, 302, "Found", {}, "https://storage.example.net/blob"
		)
		self.assertIsNone(other.get_header("Authorization"))

	def test_token_only_over_https_or_loopback(self):
		for url, carries in (
			("https://git.example.org/x", True),
			("http://127.0.0.1:8080/x", True),
			("http://[::1]/x", True),
			("http://localhost/x", True),
			("http://git.example.org/x", False),
			("http://10.0.0.1/x", False),
			("file:///tmp/x", False),
			("ssh://git@git.example.org/x", False),
		):
			self.assertEqual(pins._may_carry_token(url), carries, url)

	def test_git_token_is_in_the_environment_not_the_command_line(self):
		os.environ[TOKEN] = "secret"
		os.environ["GIT_CONFIG_COUNT"] = "1"
		calls = []

		def run(cmd, **kwargs):
			calls.append((cmd, kwargs["env"]))
			raise subprocess.CalledProcessError(128, cmd, stderr="fatal: nope")

		for url, sent in (
			("https://git.example.org/libs/shared_lib.git", True),
			("http://git.example.org/libs/shared_lib.git", False),
		):
			calls.clear()
			pin = flakelock.Pin("shared_lib", "git", REV, TREE_NAR_HASH, url=url)
			with mock.patch.object(pins.subprocess, "run", side_effect=run):
				with self.assertRaises(EnvError) as ctx:
					pins._clone(pin, self.root)
			cmd, env = calls[0]
			self.assertNotIn("secret", " ".join(cmd))
			if sent:
				self.assertEqual(env["GIT_CONFIG_COUNT"], "2")
				self.assertEqual(env["GIT_CONFIG_KEY_1"], "http.extraHeader")
				self.assertEqual(env["GIT_CONFIG_VALUE_1"], "Authorization: Bearer secret")
			else:
				self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
				self.assertNotIn("GIT_CONFIG_VALUE_1", env)
				self.assertIn("sent only over https", str(ctx.exception))

	def test_git_clone_and_checkout(self):
		# git keeps no empty directories, so the locked tree has none.
		src = make_tree(self.root / "tree")
		(src / "emptydir").rmdir()
		expected = nar.nar_hash(src)
		origin = self.root / "origin"
		shutil.copytree(src, origin, symlinks=True)
		git(origin, "init", "-q")
		git(origin, "add", "-A")
		git(origin, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "tree")
		rev = subprocess.run(
			["git", "-C", str(origin), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
		).stdout.strip()
		# A later commit: the pin must check out the locked one, not the tip.
		(origin / "later").write_text("x\n")
		git(origin, "add", "-A")
		git(origin, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "later")
		bare = self.root / "shared_lib.git"
		git(self.root, "clone", "-q", "--bare", str(origin), str(bare))
		self.write_lock(
			"shared_lib", {"type": "git", "url": f"file://{bare}", "rev": rev, "narHash": expected}
		)
		path = pin_path("shared_lib", self.lock, store_dir=self.store)
		self.assertEqual(path, self.app / ".dev-dist" / "pins" / f"shared_lib-{rev}")
		self.assertFalse((path / ".git").exists())
		self.assertFalse((path / "later").exists())
		self.assertEqual(nar.nar_hash(path), expected)
		shutil.rmtree(bare)
		self.assertEqual(pin_path("shared_lib", self.lock, store_dir=self.store), path)

	def test_git_clone_failure_hints_at_the_token(self):
		self.write_lock(
			"shared_lib",
			{"type": "git", "url": f"file://{self.root}/missing.git", "rev": REV, "narHash": TREE_NAR_HASH},
		)
		with self.assertRaisesRegex(EnvError, TOKEN):
			pin_path("shared_lib", self.lock, store_dir=self.store)


if __name__ == "__main__":
	unittest.main()
