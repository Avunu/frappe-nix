"""``sync --write`` around the plan (spec §3.3, S32): the lock, the re-exec and the dev-shell
re-entry, with a fake ``nix`` on PATH that records each call and writes canned locks.

Every CI run of the real thing sets ``IRONCLAD_FRAPPE_NIX_URL``, which takes the lock and
re-exec branches out of play, so these are where those branches run.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import ClassVar
from unittest import mock

import ironclad
from ironclad.common.report import EnvError
from ironclad.scaffold import bootstrap
from scaffold_helpers import AppCase, _github, flake_lock

FAKE_NIX = """#!{python}
import json, os, sys
argv = sys.argv[1:]
keep = ("IRONCLAD_SYNC_REEXEC", "IRONCLAD_SYNC_REENTERED", "IRONCLAD_SYNC_LOCK_CHANGED",
        "FRAPPE_NIX_CI", "IRONCLAD_LOCKED_FRAPPE_NIX")
with open(os.environ["FAKE_NIX_LOG"], "a") as log:
    log.write(json.dumps({{"argv": argv, "env": {{k: os.environ[k] for k in keep if k in os.environ}}}}) + "\\n")
locks = json.loads(os.environ.get("FAKE_NIX_LOCKS", "{{}}"))
key = " ".join(argv[:2])
if key in locks:
    with open("flake.lock", "w") as f:
        json.dump(locks[key], f)
if argv[:1] == ["eval"]:
    sys.stdout.write(os.environ.get("FAKE_NIX_VERSION", ""))
sys.exit(int(os.environ.get("FAKE_NIX_CODE", "0")))
"""


def main_locked(apps: list[str]) -> dict:
	lock = flake_lock(apps)
	lock["nodes"]["frappe-nix"] = _github("Avunu", "frappe-nix", "main")
	lock["nodes"]["frappe-nix"]["original"].pop("ref")
	return lock


class FakeNix(AppCase):
	"""An app with a fake ``nix`` (and ``git``, nothing else) on PATH, online."""

	def setUp(self) -> None:
		super().setUp()
		os.environ.pop("IRONCLAD_OFFLINE", None)
		for key in ("IRONCLAD_SYNC_REEXEC", "IRONCLAD_SYNC_REENTERED", bootstrap.LOCK_CHANGED):
			os.environ.pop(key, None)
		self.bin = Path(self._tmp.name + "-bin")
		self.bin.mkdir()
		self.addCleanup(shutil.rmtree, self.bin, True)
		nix = self.bin / "nix"
		nix.write_text(FAKE_NIX.format(python=sys.executable))
		nix.chmod(0o755)
		git = shutil.which("git")
		assert git
		(self.bin / "git").symlink_to(git)
		self.log = self.bin / "log.jsonl"
		os.environ["FAKE_NIX_LOG"] = str(self.log)
		os.environ["PATH"] = str(self.bin)
		os.environ["FAKE_NIX_VERSION"] = ironclad.__version__

	def calls(self) -> list[dict]:
		if not self.log.is_file():
			return []
		return [json.loads(line) for line in self.log.read_text().splitlines()]

	def argvs(self) -> list[str]:
		return [" ".join(c["argv"]) for c in self.calls()]

	def runner(self) -> bootstrap.Runner:
		return bootstrap.Runner(self.root)


class TestLockReasons(FakeNix):
	def test_missing_lock(self):
		self.assertEqual(bootstrap.lock_reasons(self.root, ["frappe", "frappe-nix"], 1), (True, False))

	def test_input_set_changed(self):
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.assertEqual(
			bootstrap.lock_reasons(self.root, ["frappe", "frappe-nix", "nixpkgs"], 1), (False, False)
		)
		self.assertEqual(
			bootstrap.lock_reasons(self.root, ["erpnext", "frappe", "frappe-nix", "nixpkgs"], 1),
			(True, False),
		)

	def test_main_locked(self):
		self.write("flake.lock", json.dumps(main_locked(["frappe"])))
		self.assertEqual(
			bootstrap.lock_reasons(self.root, ["frappe", "frappe-nix", "nixpkgs"], 1), (False, True)
		)


class TestPhaseALock(FakeNix):
	inputs: ClassVar[list[str]] = ["frappe", "frappe-nix", "nixpkgs"]

	def test_no_lock_locks_once(self):
		os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake lock": flake_lock(["frappe"])})
		self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.inputs, 1))
		self.assertEqual(self.argvs(), ["flake lock"])

	def test_main_locked_moves_to_release(self):
		self.write("flake.lock", json.dumps(main_locked(["frappe"])))
		os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake update": flake_lock(["frappe"])})
		self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.inputs, 1))
		self.assertEqual(self.argvs(), ["flake update frappe-nix"])

	def test_new_lock_on_main_is_moved_too(self):
		os.environ["FAKE_NIX_LOCKS"] = json.dumps(
			{"flake lock": main_locked(["frappe"]), "flake update": flake_lock(["frappe"])}
		)
		self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.inputs, 1))
		self.assertEqual(self.argvs(), ["flake lock", "flake update frappe-nix"])

	def test_current_lock_runs_nothing(self):
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.assertFalse(bootstrap.phase_a_lock(self.runner(), self.inputs, 1))
		self.assertEqual(self.argvs(), [])


class TestReexec(FakeNix):
	def setUp(self) -> None:
		super().setUp()
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))

	def test_same_version_stays(self):
		bootstrap.maybe_reexec(self.runner(), ["--skip-lock"])
		self.assertEqual([c["argv"][0] for c in self.calls()], ["eval"])

	def test_other_version_reexecs_once_from_the_lock(self):
		os.environ["FAKE_NIX_VERSION"] = "9.9.9"
		with self.assertRaises(SystemExit) as caught:
			bootstrap.maybe_reexec(self.runner(), ["--skip-lock"], lock_changed=True)
		self.assertEqual(caught.exception.code, 0)
		calls = self.calls()
		self.assertEqual(
			calls[-1]["argv"], ["run", "--no-pure-eval", ".#frappe-init", "--", "--sync", "--skip-lock"]
		)
		self.assertEqual(calls[-1]["env"].get("IRONCLAD_SYNC_REEXEC"), "1")
		self.assertEqual(calls[-1]["env"].get(bootstrap.LOCK_CHANGED), "1")
		os.environ["IRONCLAD_SYNC_REEXEC"] = "1"
		bootstrap.maybe_reexec(self.runner(), [])
		self.assertEqual(len(self.calls()), len(calls), "a second re-exec ran")

	def test_lock_reaches_nix_as_data(self):
		lock = flake_lock(["frappe"])
		lock["nodes"]["frappe-nix"]["locked"]["owner"] = "Avunu${builtins.currentSystem}"
		self.write("flake.lock", json.dumps(lock))
		bootstrap.locked_frappe_nix_version(self.runner())
		call = self.calls()[0]
		expr = call["argv"][call["argv"].index("--expr") + 1]
		self.assertNotIn("Avunu", expr)
		self.assertIn("builtins.getEnv", expr)
		self.assertEqual(
			json.loads(call["env"]["IRONCLAD_LOCKED_FRAPPE_NIX"])["owner"], "Avunu${builtins.currentSystem}"
		)


class TestReentry(FakeNix):
	def test_without_uv_and_yarn_relocks_then_reenters(self):
		with self.assertRaises(SystemExit):
			bootstrap.ensure_tools(self.runner(), ["--skip-lock"], lock_changed=True)
		calls = self.calls()
		self.assertEqual(calls[0]["argv"], ["run", "--no-pure-eval", ".#relock"])
		self.assertEqual(
			calls[1]["argv"],
			["develop", "--no-pure-eval", "-c", "ironclad", "sync", "--write", "--phase", "b", "--skip-lock"],
		)
		self.assertEqual(
			calls[1]["env"],
			{"FRAPPE_NIX_CI": "1", "IRONCLAD_SYNC_REENTERED": "1", bootstrap.LOCK_CHANGED: "1"},
		)

	def test_existing_bench_lock_reenters_directly(self):
		self.write("nix/uv.lock", "version = 1\n")
		with self.assertRaises(SystemExit):
			bootstrap.ensure_tools(self.runner(), [])
		self.assertEqual([c["argv"][0] for c in self.calls()], ["develop"])
		self.assertNotIn(bootstrap.LOCK_CHANGED, self.calls()[0]["env"])

	def test_second_entry_is_an_environment_error(self):
		os.environ["IRONCLAD_SYNC_REENTERED"] = "1"
		with self.assertRaises(EnvError):
			bootstrap.ensure_tools(self.runner(), [])
		self.assertEqual(self.calls(), [])

	def test_with_the_tools_nothing_runs(self):
		for tool in ("uv", "yarn"):
			(self.bin / tool).symlink_to(self.bin / "nix")
		bootstrap.ensure_tools(self.runner(), [])
		self.assertEqual(self.calls(), [])


class TestBareSyncBootstrap(FakeNix):
	"""A bare ``ironclad sync --write`` on a main-locked app with nix/uv.lock and no uv or
	yarn: phase A moves frappe-nix, and the re-entered phase B is told the lock changed."""

	def test_main_locked_app(self):
		self.synced_offline()
		self.write("flake.lock", json.dumps(main_locked(["frappe"])))
		self.write("nix/uv.lock", "version = 1\n")
		self.commit()
		os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake update": flake_lock(["frappe"])})
		code, _, err = self.ironclad("sync", "--write")
		self.assertEqual(code, 0, err)
		self.assertEqual(
			[" ".join(c["argv"][:2]) for c in self.calls()],
			["flake update", "eval --raw", "develop --no-pure-eval"],
		)
		self.assertEqual(self.calls()[-1]["env"].get(bootstrap.LOCK_CHANGED), "1")

	def test_reentered_phase_b_relocks(self):
		self.synced_offline()
		self.write("nix/uv.lock", "version = 1\n")
		self.commit()
		for tool in ("uv", "yarn"):
			(self.bin / tool).symlink_to(self.bin / "nix")
		with mock.patch.dict(os.environ, {bootstrap.LOCK_CHANGED: "1", "IRONCLAD_SYNC_REENTERED": "1"}):
			code, _, err = self.ironclad("sync", "--write", "--phase", "b")
		self.assertEqual(code, 0, err)
		self.assertIn(["run", "--no-pure-eval", ".#relock"], [c["argv"] for c in self.calls()])

	def synced_offline(self) -> None:
		with mock.patch.dict(os.environ, {"IRONCLAD_OFFLINE": "1"}):
			self.synced()
