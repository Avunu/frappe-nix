"""``sync --write`` around the plan (spec §3.3, S32): the lock, the re-exec and the dev-shell
re-entry, with a fake ``nix`` on PATH that records each call and writes canned locks.

Every CI run of the real thing sets ``FRAPPE_NIX_URL_OVERRIDE``, which takes the lock and
re-exec branches out of play, so these are where those branches run.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import ClassVar
from unittest import mock

import frappe_nix_tools
from frappe_nix_tools.common.report import EnvError
from frappe_nix_tools.scaffold import bootstrap
from scaffold_helpers import AppCase, _github, flake_lock, git

FAKE_NIX = """#!{python}
import json, os, sys
argv = sys.argv[1:]
keep = ("FRAPPE_NIX_SYNC_REEXEC", "FRAPPE_NIX_SYNC_REENTERED", "FRAPPE_NIX_SYNC_LOCK_CHANGED",
        "FRAPPE_NIX_CI", "FRAPPE_NIX_LOCKED_REV")
with open(os.environ["FAKE_NIX_LOG"], "a") as log:
    log.write(json.dumps({{"argv": argv, "env": {{k: os.environ[k] for k in keep if k in os.environ}}}}) + "\\n")
locks = json.loads(os.environ.get("FAKE_NIX_LOCKS", "{{}}"))
key = " ".join(argv[:2])
if key in locks:
    with open("flake.lock", "w") as f:
        json.dump(locks[key], f)
if argv[:1] == ["eval"]:
    sys.stdout.write(os.environ.get("FAKE_NIX_VERSION", ""))
if "FAKE_CHILD_RESULT" in os.environ and argv[:1] in (["run"], ["develop"]) and "FRAPPE_NIX_SYNC_RESULT" in os.environ:
    with open(os.environ["FRAPPE_NIX_SYNC_RESULT"], "w") as f:
        f.write(os.environ["FAKE_CHILD_RESULT"])
sys.exit(0 if argv[:1] == ["eval"] else int(os.environ.get("FAKE_NIX_CODE", "0")))
"""


# The URL a rendered flake.nix gives frappe-nix by default (release-<N> for v1).
URL = "github:Avunu/frappe-nix/release-1"
PINNED = "github:Avunu/frappe-nix/v1.0.0"


def main_locked(apps: list[str]) -> dict:
	lock = flake_lock(apps)
	lock["nodes"]["frappe-nix"] = _github("Avunu", "frappe-nix", "main")
	lock["nodes"]["frappe-nix"]["original"].pop("ref")
	return lock


class FakeNix(AppCase):
	"""An app with a fake ``nix`` (and ``git``, nothing else) on PATH, online."""

	def setUp(self) -> None:
		super().setUp()
		os.environ.pop("FRAPPE_NIX_OFFLINE", None)
		for key in (
			"FRAPPE_NIX_SYNC_REEXEC",
			"FRAPPE_NIX_SYNC_REENTERED",
			bootstrap.LOCK_CHANGED,
			bootstrap.RESULT,
			"FAKE_CHILD_RESULT",
			"FAKE_NIX_CODE",
		):
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
		os.environ["FAKE_NIX_VERSION"] = frappe_nix_tools.__version__
		# The release-<N> probe asks GitHub; these tests are offline and say it exists.
		probe = mock.patch.object(bootstrap, "release_branch_exists", return_value=True)
		probe.start()
		self.addCleanup(probe.stop)

	def calls(self) -> list[dict]:
		if not self.log.is_file():
			return []
		return [json.loads(line) for line in self.log.read_text().splitlines()]

	def argvs(self) -> list[str]:
		return [" ".join(c["argv"]) for c in self.calls()]

	def runner(self) -> bootstrap.Runner:
		return bootstrap.Runner(self.root)


def specs(*names: str, frappe: str = "version-16") -> dict[str, str | None]:
	"""``engine.flake_input_specs`` of a rendered flake.nix with these inputs."""
	known = {
		"frappe-nix": "github:avunu/frappe-nix/release-1",
		"nixpkgs": "follows:frappe-nix/nixpkgs",
		"frappe": f"github:frappe/frappe/{frappe}",
	}
	return {name: known.get(name, f"github:frappe/{name}/{frappe}") for name in sorted(names)}


class TestLockReasons(FakeNix):
	def test_missing_lock(self):
		self.assertEqual(bootstrap.lock_reasons(self.root, specs("frappe", "frappe-nix"), URL), (True, False))

	def test_input_set_changed(self):
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.assertEqual(
			bootstrap.lock_reasons(self.root, specs("frappe", "frappe-nix", "nixpkgs"), URL), (False, False)
		)
		self.assertEqual(
			bootstrap.lock_reasons(self.root, specs("erpnext", "frappe", "frappe-nix", "nixpkgs"), URL),
			(True, False),
		)

	def test_frappe_major_bump_relocks(self):
		"""Same input names, another branch: the lock is stale (review: --sync left it on version-16)."""
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.assertEqual(
			bootstrap.lock_reasons(
				self.root, specs("frappe", "frappe-nix", "nixpkgs", frappe="version-17"), URL
			),
			(True, False),
		)

	def test_follows_changed_relocks(self):
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		wanted = specs("frappe", "frappe-nix", "nixpkgs") | {"nixpkgs": "github:nixos/nixpkgs/nixos-unstable"}
		self.assertEqual(bootstrap.lock_reasons(self.root, wanted, URL), (True, False))

	def test_main_locked(self):
		self.write("flake.lock", json.dumps(main_locked(["frappe"])))
		self.assertEqual(
			bootstrap.lock_reasons(self.root, specs("frappe", "frappe-nix", "nixpkgs"), URL), (False, True)
		)


class TestPinnedFrappeNix(FakeNix):
	"""``dev-shell.frappe-nix-url`` pins frappe-nix (a tag, a rev, a fork): phase A locks it once
	and never moves it again, and only the default release branch is probed (§2.5, §3.3)."""

	def pinned_lock(self) -> dict:
		lock = flake_lock(["frappe"])
		lock["nodes"]["frappe-nix"]["original"]["ref"] = "v1.0.0"
		return lock

	def test_pinned_lock_is_kept(self):
		self.write("flake.lock", json.dumps(self.pinned_lock()))
		wanted = specs("frappe", "frappe-nix", "nixpkgs") | {"frappe-nix": "github:avunu/frappe-nix/v1.0.0"}
		self.assertEqual(bootstrap.lock_reasons(self.root, wanted, PINNED), (False, False))
		self.assertFalse(bootstrap.phase_a_lock(self.runner(), wanted, PINNED))
		self.assertEqual(self.argvs(), [])

	def test_a_new_pin_moves_once(self):
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		wanted = specs("frappe", "frappe-nix", "nixpkgs") | {"frappe-nix": "github:avunu/frappe-nix/v1.0.0"}
		self.assertEqual(bootstrap.lock_reasons(self.root, wanted, PINNED), (False, True))
		os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake update": self.pinned_lock()})
		self.assertTrue(bootstrap.phase_a_lock(self.runner(), wanted, PINNED))
		self.assertEqual(self.argvs(), ["flake update frappe-nix"])
		self.assertFalse(bootstrap.phase_a_lock(self.runner(), wanted, PINNED))
		self.assertEqual(self.argvs(), ["flake update frappe-nix"], "a second sync moved it again")

	def test_a_pin_is_never_probed(self):
		self.assertFalse(bootstrap.release_ref_needed(self.root, PINNED))
		self.assertTrue(bootstrap.release_ref_needed(self.root, URL))


class TestPhaseALock(FakeNix):
	inputs: ClassVar[dict[str, str | None]] = specs("frappe", "frappe-nix", "nixpkgs")

	def test_no_lock_locks_once(self):
		os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake lock": flake_lock(["frappe"])})
		self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.inputs, URL))
		self.assertEqual(self.argvs(), ["flake lock"])

	def test_main_locked_moves_to_release(self):
		self.write("flake.lock", json.dumps(main_locked(["frappe"])))
		os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake update": flake_lock(["frappe"])})
		self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.inputs, URL))
		self.assertEqual(self.argvs(), ["flake update frappe-nix"])

	def test_new_lock_on_main_is_moved_too(self):
		os.environ["FAKE_NIX_LOCKS"] = json.dumps(
			{"flake lock": main_locked(["frappe"]), "flake update": flake_lock(["frappe"])}
		)
		self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.inputs, URL))
		self.assertEqual(self.argvs(), ["flake lock", "flake update frappe-nix"])

	def test_frappe_major_bump_locks(self):
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		bumped = flake_lock(["frappe"])
		bumped["nodes"]["frappe"]["original"]["ref"] = "version-17"
		os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake lock": bumped})
		wanted = specs("frappe", "frappe-nix", "nixpkgs", frappe="version-17")
		self.assertTrue(bootstrap.phase_a_lock(self.runner(), wanted, URL))
		self.assertEqual(self.argvs(), ["flake lock"])

	def test_current_lock_runs_nothing(self):
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.assertFalse(bootstrap.phase_a_lock(self.runner(), self.inputs, URL))
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
		self.assertEqual(calls[-1]["env"].get("FRAPPE_NIX_SYNC_REEXEC"), "1")
		self.assertEqual(calls[-1]["env"].get(bootstrap.LOCK_CHANGED), "1")
		os.environ["FRAPPE_NIX_SYNC_REEXEC"] = "1"
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
			json.loads(call["env"]["FRAPPE_NIX_LOCKED_REV"])["owner"], "Avunu${builtins.currentSystem}"
		)


class TestReentry(FakeNix):
	def test_without_uv_and_yarn_relocks_then_reenters(self):
		with self.assertRaises(SystemExit):
			bootstrap.ensure_tools(self.runner(), ["--skip-lock"], lock_changed=True)
		calls = self.calls()
		self.assertEqual(calls[0]["argv"], ["run", "--no-pure-eval", ".#relock"])
		self.assertEqual(
			calls[1]["argv"],
			[
				"develop",
				"--no-pure-eval",
				"-c",
				"frappe-nix",
				"sync",
				"--write",
				"--phase",
				"b",
				"--skip-lock",
			],
		)
		self.assertEqual(
			calls[1]["env"],
			{"FRAPPE_NIX_CI": "1", "FRAPPE_NIX_SYNC_REENTERED": "1", bootstrap.LOCK_CHANGED: "1"},
		)

	def test_existing_bench_lock_reenters_directly(self):
		self.write("nix/uv.lock", "version = 1\n")
		with self.assertRaises(SystemExit):
			bootstrap.ensure_tools(self.runner(), [])
		self.assertEqual([c["argv"][0] for c in self.calls()], ["develop"])
		self.assertNotIn(bootstrap.LOCK_CHANGED, self.calls()[0]["env"])

	def test_second_entry_is_an_environment_error(self):
		os.environ["FRAPPE_NIX_SYNC_REENTERED"] = "1"
		with self.assertRaises(EnvError):
			bootstrap.ensure_tools(self.runner(), [])
		self.assertEqual(self.calls(), [])

	def test_with_the_tools_nothing_runs(self):
		for tool in ("uv", "yarn"):
			(self.bin / tool).symlink_to(self.bin / "nix")
		bootstrap.ensure_tools(self.runner(), [])
		self.assertEqual(self.calls(), [])


class TestHandoverResult(FakeNix):
	"""A failed ``nix develop``/``nix run`` is exit 3, never 1 (drift); the child sync's own
	code comes back through FRAPPE_NIX_SYNC_RESULT (review: nix's exit 1 read as drift)."""

	def reenter(self) -> tuple[int, str]:
		self.synced_offline()
		self.write("nix/uv.lock", "version = 1\n")
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.commit()
		code, _, err = self.fn("sync", "--write")
		return code, err

	def test_failed_nix_develop_is_an_environment_error(self):
		os.environ["FAKE_NIX_CODE"] = "1"
		code, err = self.reenter()
		self.assertEqual(code, 3, err)
		self.assertIn("nix develop", err)

	def test_child_code_is_passed_through(self):
		os.environ["FAKE_NIX_CODE"] = "1"
		os.environ["FAKE_CHILD_RESULT"] = "2"
		code, err = self.reenter()
		self.assertEqual(code, 2, err)

	def test_failed_reexec_is_an_environment_error(self):
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		os.environ["FAKE_NIX_VERSION"] = "9.9.9"
		os.environ["FAKE_NIX_CODE"] = "1"
		with self.assertRaises(EnvError):
			bootstrap.maybe_reexec(self.runner(), [])
		os.environ["FAKE_CHILD_RESULT"] = "0"
		with self.assertRaises(SystemExit) as caught:
			bootstrap.maybe_reexec(self.runner(), [])
		self.assertEqual(caught.exception.code, 0)

	def test_child_records_its_result(self):
		result = self.bin / "result"
		with mock.patch.dict(os.environ, {"FRAPPE_NIX_OFFLINE": "1", bootstrap.RESULT: str(result)}):
			code, _, err = self.fn("sync", "--write")
		self.assertEqual(result.read_text().strip(), str(code), err)

	def synced_offline(self) -> None:
		with mock.patch.dict(os.environ, {"FRAPPE_NIX_OFFLINE": "1"}):
			self.synced()


class TestBareSyncBootstrap(FakeNix):
	"""A bare ``frappe-nix sync --write`` on a main-locked app with nix/uv.lock and no uv or
	yarn: phase A moves frappe-nix, and the re-entered phase B is told the lock changed."""

	def test_main_locked_app(self):
		self.synced_offline()
		self.write("flake.lock", json.dumps(main_locked(["frappe"])))
		self.write("nix/uv.lock", "version = 1\n")
		self.commit()
		os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake update": flake_lock(["frappe"])})
		code, _, err = self.fn("sync", "--write")
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
		with mock.patch.dict(os.environ, {bootstrap.LOCK_CHANGED: "1", "FRAPPE_NIX_SYNC_REENTERED": "1"}):
			code, _, err = self.fn("sync", "--write", "--phase", "b")
		self.assertEqual(code, 0, err)
		self.assertIn(["run", "--no-pure-eval", ".#relock"], [c["argv"] for c in self.calls()])

	def synced_offline(self) -> None:
		with mock.patch.dict(os.environ, {"FRAPPE_NIX_OFFLINE": "1"}):
			self.synced()


class TestReleaseBranchPreflight(FakeNix):
	"""Before release-<N> exists (S32), a sync that would lock it refuses before writing
	anything (review: `frappe-init --app` failed in `nix flake lock` with a staged tree)."""

	def absent(self) -> None:
		probe = mock.patch.object(bootstrap, "release_branch_exists", return_value=False)
		probe.start()
		self.addCleanup(probe.stop)

	def test_unbootstrapped_app_is_refused_with_nothing_written(self):
		self.absent()
		self.write("pyproject.toml", self.read("pyproject.toml").split("[tool.frappe-nix]")[0])
		self.commit()
		code, _, err = self.fn("sync", "--write", "--standards", "minimal", "--frappe-version", "version-16")
		self.assertEqual(code, 3, err)
		self.assertIn("has no release-1 branch yet", err)
		self.assertEqual(git(self.root, "status", "--porcelain"), "")
		self.assertEqual(self.calls(), [])

	def test_preflight_phase(self):
		self.absent()
		code, _, err = self.fn("sync", "--write", "--phase", "preflight")
		self.assertEqual(code, 3, err)
		self.assertEqual(git(self.root, "status", "--porcelain"), "")

	def test_preflight_refuses_what_phase_a_would(self):
		"""frappe-init --app runs the preflight before it writes templates/app's files: an
		invalid configuration is exit 2 there, with nothing written or probed."""
		for argv, needle in (
			(("--standards", "minimal"), "already names profile 'recommended'"),
			(("--frappe-version", "version-15"), "has frappe-major = 16"),
		):
			with self.subTest(argv=argv):
				code, _, err = self.fn("sync", "--write", "--phase", "preflight", *argv)
				self.assertEqual(code, 2, err)
				self.assertIn(needle, err)
				self.assertEqual(git(self.root, "status", "--porcelain"), "")
		self.write("pyproject.toml", self.read("pyproject.toml").split("[tool.frappe-nix]")[0])
		self.commit()
		code, _, err = self.fn(
			"sync",
			"--write",
			"--phase",
			"preflight",
			"--standards",
			"./nope",
			"--frappe-version",
			"version-16",
		)
		self.assertEqual(code, 2, err)
		self.assertEqual(git(self.root, "status", "--porcelain"), "")
		self.assertEqual(self.calls(), [])

	def test_preflight_follows_the_apps_own_frappe_nix_url(self):
		"""dev-shell.frappe-nix-url is the app's choice: the preflight checks the URL phase A
		would lock (the pre-release escape hatch), not release-1."""
		self.absent()
		self.table('dev-shell.frappe-nix-url = "github:Avunu/frappe-nix"\n')
		self.commit()
		code, _, err = self.fn("sync", "--write", "--phase", "preflight")
		self.assertEqual(code, 0, err)

	def test_a_lock_already_on_the_branch_is_not_probed(self):
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.assertFalse(bootstrap.release_ref_needed(self.root, URL))
		self.write("flake.lock", json.dumps(main_locked(["frappe"])))
		self.assertTrue(bootstrap.release_ref_needed(self.root, URL))

	def test_offline_dry_run_and_unknown_pass(self):
		self.absent()
		bootstrap.require_release_branch(bootstrap.Runner(self.root, offline=True), URL)
		bootstrap.require_release_branch(bootstrap.Runner(self.root, dry_run=True), URL)
		with mock.patch.object(bootstrap, "release_branch_exists", return_value=None):
			bootstrap.require_release_branch(self.runner(), URL)
