"""The dependency commands (frappe_nix_tools.deps): what the
shell tests (tests/node-modules.sh, node-locks.sh, bench-patches.sh,
setup-requirements.sh, bench-update.sh, update-deps.sh, relock.sh) reach only
indirectly — the fingerprint's bytes against the bash pipeline it replaces,
argument parsing, the unbuildable-entry rules, the stamp's format, the patch
list's reading, remote matching, and the configuration schemas.
"""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from frappe_nix_tools import bench_cli
from frappe_nix_tools.bench_config import parse
from frappe_nix_tools.cli_config import ConfigError, load_dataclass
from frappe_nix_tools.deps import (
	bench_patches,
	node_locks,
	node_modules,
	relock,
	setup_requirements,
	update,
	update_deps,
)
from frappe_nix_tools.deps.config import RelockConfig

DEPS = {
	"appsWithNode": ["alpha"],
	"appMode": False,
	"lockDir": "nix",
	"nodeVerify": "/bin/frappe-nix-node-verify",
	"nodeModules": "/bin/frappe-nix-node-modules",
	"nodeLocks": "/bin/frappe-nix-node-locks",
	"nodeNestedFrontendExcludes": [],
	"requirementsCheck": "/lib/requirements-check.py",
}
APPS = {
	"bench": "bench",
	"python": "python3",
	"workspace": "/bin/frappe-nix-workspace",
	"sources": "/bin/frappe-nix-sources",
	"buildDepsSync": None,
	"renameTool": "/lib/frappe_rename_app.py",
	"renamedApps": [],
	"replacedApps": [],
}
RESTORE = {
	"enable": False,
	"fetch": "frappe-nix-backup-fetch",
	"secretsEnv": None,
	"prefix": "",
	"files": "none",
	"carryConfigKeys": [],
	"migrate": False,
	"neutralize": False,
	"requireDevguard": True,
	"devguard": True,
}


@contextlib.contextmanager
def chdir(path):
	old = os.getcwd()
	os.chdir(path)
	try:
		yield
	finally:
		os.chdir(old)


def write(root: Path, rel: str, text: str) -> None:
	path = root / rel
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(text)


class TestFingerprint(unittest.TestCase):
	"""The sentinel a bench already has was written by the bash: the fingerprint
	must be the same bytes, or every app reinstalls once after the upgrade."""

	BASH = (
		'find -H "apps/$1" \\( -name node_modules -o -name .git -o -name .frappe-nix'
		" -o -name .devenv -o -name .direnv \\) -prune -o"
		" \\( -name package.json -o -name yarn.lock \\) -print0"
		" | LC_ALL=C sort -z | xargs -0 -r sha256sum | sha256sum | cut -d' ' -f1"
	)

	def bash(self, app: str) -> str:
		return subprocess.run(
			["bash", "-c", self.BASH, "fp", app], check=True, capture_output=True, text=True
		).stdout.strip()

	@unittest.skipUnless(shutil.which("bash") and shutil.which("sha256sum"), "needs bash and coreutils")
	def test_matches_the_bash_pipeline(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			write(root, "apps/alpha/package.json", '{"name":"alpha"}\n')
			write(root, "apps/alpha/yarn.lock", "# lock\n")
			write(root, "apps/alpha/desk/package.json", "{}\n")
			write(root, "apps/alpha/Desk/package.json", "{}\n")  # C order: upper case first
			write(root, "apps/alpha/node_modules/x/package.json", "{}\n")
			write(root, "apps/alpha/.frappe-nix/bench/apps/frappe/package.json", "{}\n")
			write(root, "apps/alpha/weird\\name/package.json", "{}\n")  # sha256sum escapes it
			write(root, "apps/alpha/README.md", "not a manifest\n")
			repo = root / "repo"
			write(repo, "package.json", "{}\n")
			write(repo, "ui/yarn.lock", "# ui\n")
			(root / "apps/gamma").symlink_to(repo)
			(root / "apps/empty").mkdir()
			with chdir(root):
				for app in ("alpha", "gamma", "empty"):
					with self.subTest(app=app):
						self.assertEqual(node_modules.fingerprint(app), self.bash(app))

	def test_prunes_and_follows_only_the_start(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			write(root, "apps/a/package.json", "{}\n")
			with chdir(root):
				before = node_modules.fingerprint("a")
				write(root, "apps/a/node_modules/p/package.json", "{}\n")
				write(root, "apps/a/.git/package.json", "{}\n")
				self.assertEqual(node_modules.fingerprint("a"), before)
				write(root, "apps/a/sub/yarn.lock", "x\n")
				self.assertNotEqual(node_modules.fingerprint("a"), before)

	def test_usage(self):
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			self.assertEqual(node_modules.main(["/nonexistent"]), 2)
		self.assertIn("usage: frappe-nix-node-modules", err.getvalue())
		with contextlib.redirect_stderr(io.StringIO()):
			self.assertEqual(node_modules.main(["/nonexistent/bench", "a"]), 1)


class TestNodeLocksArgs(unittest.TestCase):
	def test_flags_then_positionals(self):
		a = node_locks.parse(
			["--exclude=a/x", "--exclude", "b/y", "--command=c d", "bench", "locks", "t1", "--t2"]
		)
		self.assertEqual((a.bench, a.locks, a.targets), ("bench", "locks", ["t1", "--t2"]))
		self.assertEqual((a.excludes, a.command), (["a/x", "b/y"], "c d"))
		self.assertEqual(node_locks.parse(["--", "-bench", "locks"]).bench, "-bench")
		self.assertEqual(node_locks.parse(["b", "l"]).command, "bench-update --node-locks")

	def test_usage_errors(self):
		for argv in ([], ["b"], ["-h"], ["--help", "b", "l"], ["--exclude"], ["--bogus", "b", "l"]):
			with self.subTest(argv=argv), self.assertRaises(node_locks.Usage):
				node_locks.parse(argv)
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			self.assertEqual(node_locks.main(["--bogus", "b", "l"]), 2)
		self.assertIn("unknown flag --bogus", err.getvalue())
		self.assertIn("usage: frappe-nix-node-locks", err.getvalue())


class TestUnbuildable(unittest.TestCase):
	def test_rules(self):
		lock = "\n".join(
			[
				"# yarn lockfile v1",
				"",
				"ok@1.0.0:",
				'  version "1.0.0"',
				'  resolved "https://registry.yarnpkg.com/ok/-/ok-1.0.0.tgz"',
				"  integrity sha512-xyz",
				"",
				"sha1@1.0.0:",
				'  resolved "https://registry.yarnpkg.com/s/-/s-1.0.0.tgz#0123abcd"',
				"",
				"bare@1.0.0:",
				'  resolved "https://registry.yarnpkg.com/b/-/b-1.0.0.tgz"',
				"",
				'"g@git+https://x/g":',
				'  resolved "git+https://x/g#0123abcdef"',
				"",
				'"u@git+https://x/u":',
				'  resolved "git+https://x/u"',
				"",
				"c@1.0.0:",
				'  resolved "https://codeload.github.com/o/r/tar.gz/0123abc"',
				"",
				"cu@1.0.0:",
				'  resolved "https://codeload.github.com/o/r/tar.gz/main"',
				"",
				'"l@link:../l":',
				'  version "0.0.0"',
				"",
				"nores@1.0.0:",
				'  version "1.0.0"',
			]
		)
		self.assertEqual(
			node_locks.unbuildable(lock),
			[
				"bare@1.0.0: no integrity and no #hash on https://registry.yarnpkg.com/b/-/b-1.0.0.tgz",
				'"u@git+https://x/u": a git dependency without a commit: git+https://x/u',
				"cu@1.0.0: a git dependency without a commit: https://codeload.github.com/o/r/tar.gz/main",
				'"l@link:../l": a file:/link: dependency, which the Nix build cannot fetch',
				"nores@1.0.0: no resolved URL",
			],
		)

	def test_stamp_is_jq_s(self):
		self.assertEqual(
			node_locks.stamp_text({"yarn.lock": "b", "package.json": "a", "forced": True}),
			'{\n  "forced": true,\n  "package.json": "a",\n  "yarn.lock": "b"\n}\n',
		)

	def test_discovery(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			write(root, "apps/a/package.json", "{}")
			write(root, "apps/a/desk/package.json", "{}")
			write(root, "apps/a/sub/package.json", "{}")
			write(root, "apps/a/skip/package.json", "{}")
			write(root, "apps/a/node_modules/package.json", "{}")
			write(root, "apps/a/.hidden/package.json", "{}")
			write(root, "apps/a/.gitmodules", '[submodule "sub"]\n\tpath = sub\n')
			write(root, "apps/b/hooks.py", "")
			with chdir(root):
				gen = node_locks.Generator(node_locks.parse(["--exclude=a/skip", ".", "locks"]))
				self.assertEqual(list(gen.discover()), ["a", "a/desk"])
				self.assertEqual(gen.label, "locks")


class TestBenchPatches(unittest.TestCase):
	def test_entries(self):
		self.assertEqual(
			bench_patches.entries("# c\n  a  \n\n\t# indented\nb #2\nlast"),
			["a", "b #2", "last"],
		)

	def test_reconcile(self):
		with tempfile.TemporaryDirectory() as tmp:
			shipped = Path(tmp, "shipped")
			shipped.write_text("a\nb\nc\n")
			self.assertIsNone(bench_patches.reconcile(str(Path(tmp, "missing")), tmp))
			Path(tmp, "patches.txt").write_text("\nb\n  \n")
			self.assertEqual(bench_patches.reconcile(str(shipped), tmp), 2)
			self.assertEqual(Path(tmp, "patches.txt").read_text(), "b\na\nc\n")
			self.assertEqual(bench_patches.reconcile(str(shipped), tmp), 0)
			self.assertEqual(sorted(os.listdir(tmp)), ["patches.txt", "shipped"])

	def test_usage(self):
		with contextlib.redirect_stderr(io.StringIO()):
			self.assertEqual(bench_patches.main([]), 2)


class TestSetupRequirements(unittest.TestCase):
	def test_parse(self):
		self.assertEqual(setup_requirements.parse([]), (True, True, False))
		self.assertEqual(setup_requirements.parse(["--node", "--check", "--dev"]), (True, False, True))
		self.assertEqual(setup_requirements.parse(["--python"]), (False, True, False))
		out = io.StringIO()
		with contextlib.redirect_stdout(out), self.assertRaises(setup_requirements._Exit) as e:
			setup_requirements.parse(["--node", "--help", "--bogus"])
		self.assertEqual(e.exception.status, 0)
		self.assertTrue(out.getvalue().startswith("Usage: bench setup requirements"))
		with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(setup_requirements._Exit) as e:
			setup_requirements.parse(["--bogus", "--help"])
		self.assertEqual(e.exception.status, 2)

	def test_help_needs_no_configuration(self):
		out = io.StringIO()
		with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
			bench_cli.main(["setup-requirements", "--help"])
		self.assertEqual(e.exception.code, 0)
		self.assertIn("--check  report only", out.getvalue())


class TestConfig(unittest.TestCase):
	def test_deps_section(self):
		cfg = parse({"restore": RESTORE, "apps": APPS, "deps": DEPS}).deps
		self.assertEqual(cfg.apps_with_node, ["alpha"])
		self.assertEqual(cfg.requirements_check, "/lib/requirements-check.py")

	def test_unknown_and_missing_keys_fail(self):
		with self.assertRaisesRegex(ConfigError, r"config\.deps: unknown key\(s\): nodeLockz"):
			parse({"restore": RESTORE, "apps": APPS, "deps": {**DEPS, "nodeLockz": "x"}})
		partial = dict(DEPS)
		del partial["appMode"]
		with self.assertRaisesRegex(ConfigError, r"config\.deps: missing key\(s\): appMode"):
			parse({"restore": RESTORE, "apps": APPS, "deps": partial})
		with self.assertRaisesRegex(ConfigError, r"appsWithNode\[0\]: expected a string"):
			parse({"restore": RESTORE, "apps": APPS, "deps": {**DEPS, "appsWithNode": [1]}})


class TestUpdateArgs(unittest.TestCase):
	def steps(self, *argv, app_mode=False):
		p = update.parse(list(argv), app_mode=app_mode)
		return (p.pull, p.migrate, p.build, p.node_locks, p.targets)

	def test_flags_switch_steps_off_in_turn(self):
		self.assertEqual(self.steps(), (True, True, True, False, []))
		self.assertEqual(self.steps("--pull"), (True, False, False, False, []))
		self.assertEqual(self.steps("--migrate"), (False, True, False, False, []))
		self.assertEqual(self.steps("--build"), (False, False, True, False, []))
		self.assertEqual(self.steps("--node-locks", "a", "b/c"), (False, False, False, True, ["a", "b/c"]))
		# As the bash read them: each flag turns others off, so two modes cancel.
		self.assertEqual(self.steps("--migrate", "--build"), (False, False, False, False, []))
		self.assertEqual(self.steps("--node-locks", "--pull"), (False, False, False, True, []))
		self.assertEqual(self.steps(app_mode=True), (False, True, True, False, []))

	def test_refusals(self):
		for argv, status in (
			(["--bogus"], 1),
			(["-"], 1),
			(["frappe"], 1),
			(["--help"], 0),
			(["-h", "--bogus"], 0),
		):
			with (
				self.subTest(argv=argv),
				contextlib.redirect_stdout(io.StringIO()),
				contextlib.redirect_stderr(io.StringIO()),
				self.assertRaises(update._Exit) as e,
			):
				update.parse(argv, app_mode=False)
			self.assertEqual(e.exception.status, status)
		err = io.StringIO()
		with contextlib.redirect_stderr(err), self.assertRaises(update._Exit):
			update.parse(["--node-locks"], app_mode=True, lock_dir="locks")
		self.assertIn("locks/node-locks/ is generated", err.getvalue())

	def test_remote_for_url(self):
		with tempfile.TemporaryDirectory() as tmp:
			subprocess.run(["git", "init", "-q", tmp], check=True)
			subprocess.run(["git", "-C", tmp, "remote", "add", "origin", "https://x/fork.git"], check=True)
			subprocess.run(["git", "-C", tmp, "remote", "add", "upstream", "https://x/app/"], check=True)
			self.assertEqual(update.remote_for_url("https://x/app.git", tmp), "upstream")
			self.assertEqual(update.remote_for_url("https://x/fork/", tmp), "origin")
			self.assertIsNone(update.remote_for_url("https://x/other", tmp))
			self.assertIsNone(update.remote_for_url("", tmp))


class TestRelock(unittest.TestCase):
	def test_parse(self):
		a = relock.parse(["--upgrade-package", "x", "--uv-only"])
		self.assertEqual((a.uv, a.locks, a.uv_args, a.targets), (True, False, ["--upgrade-package", "x"], []))
		a = relock.parse(["--node-locks", "beta", "alpha/desk", "--x"])
		self.assertEqual(
			(a.uv, a.locks, a.uv_args, a.targets), (False, True, ["--x"], ["beta", "alpha/desk"])
		)
		with contextlib.redirect_stderr(io.StringIO()) as err:
			self.assertFalse(relock.parse(["--node-hashes"]).uv)
		self.assertIn("now --node-locks", err.getvalue())
		with self.assertRaises(relock._Help):
			relock.parse(["--uv-only", "-h"])

	def test_help_names_each_modes_files(self):
		self.assertIn("locks/uv.lock", relock.help_text("locks"))
		self.assertIn("Run it from the bench root", relock.help_text(None))

	def test_config(self):
		data = {
			"appMode": True,
			"nodeLocks": "/bin/frappe-nix-node-locks",
			"nodeNestedFrontendExcludes": ["a/b"],
			"buildDepsSync": None,
			"workspace": "/nix/store/w",
			"lockDir": "nix",
		}
		cfg = load_dataclass(RelockConfig, data, "relock")
		self.assertEqual((cfg.workspace, cfg.node_nested_frontend_excludes), ("/nix/store/w", ["a/b"]))
		with self.assertRaisesRegex(ConfigError, r"relock: missing key\(s\): lockDir"):
			load_dataclass(RelockConfig, {k: v for k, v in data.items() if k != "lockDir"}, "relock")


class TestUpdateDeps(unittest.TestCase):
	def test_arguments(self):
		with contextlib.redirect_stdout(io.StringIO()) as out:
			self.assertEqual(update_deps.main(["--help"]), 0)
		self.assertIn("Usage: update-deps", out.getvalue())
		with contextlib.redirect_stderr(io.StringIO()) as err:
			self.assertEqual(update_deps.main(["--bogus"]), 2)
		self.assertIn("unexpected argument '--bogus'", err.getvalue())

	def test_dashes_reach_the_command(self):
		self.assertTrue({"update", "update-deps", "restore", "provision"} <= bench_cli.KEEP_DASHES)


if __name__ == "__main__":
	unittest.main()
