"""The apps commands (frappe_nix_tools.apps, PLAN.md Track D, D3): what the
shell tests (tests/bench-get-app.sh, bench-remove-app.sh, bench-new-app.sh,
reconcile-apps.sh, root-sync.sh) reach only indirectly — argument parsing, URL
and app-name resolution, the installed-apps query, the reconcile plan, and the
configuration schema.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools import bench_cli
from frappe_nix_tools.apps import get_app, proc, reconcile, remove_app, rename_app, root_sync
from frappe_nix_tools.apps.config import AppPair, AppsConfig, ConfigError
from frappe_nix_tools.bench_config import parse

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


def apps_config(**overrides) -> AppsConfig:
	return parse({"restore": RESTORE, "apps": {**APPS, **overrides}}).apps


class TestConfig(unittest.TestCase):
	def test_pairs_are_read_in_order(self):
		cfg = apps_config(
			replacedApps=[{"old": "a", "new": "b"}, {"old": "c", "new": "d"}], buildDepsSync="/bin/sync"
		)
		self.assertEqual(cfg.replaced_apps, [AppPair("a", "b"), AppPair("c", "d")])
		self.assertEqual(cfg.build_deps_sync, "/bin/sync")

	def test_unknown_and_missing_keys_fail(self):
		with self.assertRaisesRegex(ConfigError, r"config\.apps: unknown key\(s\): benchBin"):
			apps_config(benchBin="bench")
		partial = dict(APPS)
		del partial["sources"]
		with self.assertRaisesRegex(ConfigError, r"config\.apps: missing key\(s\): sources"):
			parse({"restore": RESTORE, "apps": partial})
		with self.assertRaisesRegex(ConfigError, r"renamedApps\[0\]: missing key\(s\): new"):
			apps_config(renamedApps=[{"old": "a"}])
		with self.assertRaisesRegex(ConfigError, "missing key"):
			parse({"restore": RESTORE})

	def test_root_sync_config(self):
		cfg = root_sync.parse_config({"workspace": "/w", "template": "/t", "buildDepsSync": None})
		self.assertEqual(cfg["workspace"], "/w")
		for data, message in (
			(
				{"workspace": "/w", "template": "/t", "buildDepsSync": None, "uv": "/uv"},
				r"unknown key\(s\): uv",
			),
			({"workspace": "/w", "template": "/t"}, r"missing key\(s\): buildDepsSync"),
			(
				{"workspace": None, "template": "/t", "buildDepsSync": None},
				r"config\.workspace: expected a string$",
			),
			([], "expected an object"),
		):
			with self.subTest(data=data), self.assertRaisesRegex(root_sync.ConfigError, message):
				root_sync.parse_config(data)


class TestGetAppArgs(unittest.TestCase):
	def test_branch_forms(self):
		for argv in (
			["-b", "v16", "hrms"],
			["--branch", "v16", "hrms"],
			["--branch=v16", "hrms"],
			["hrms", "--branch", "v16"],
		):
			with self.subTest(argv=argv):
				self.assertEqual(get_app.parse_args(argv), ("hrms", "v16"))
		self.assertEqual(get_app.parse_args(["hrms"]), ("hrms", ""))
		self.assertEqual(get_app.parse_args([]), ("", ""))
		self.assertIsNone(get_app.parse_args(["-h"]))

	def test_usage_errors(self):
		for argv, message in (
			(["--branch"], "--branch needs a branch name"),
			(["-b", ""], "-b needs a branch name"),
			(["--depth", "1", "x"], "unknown flag: --depth"),
			(["a", "b"], "one app at a time"),
		):
			with self.subTest(argv=argv), self.assertRaisesRegex(get_app.UsageError, message):
				get_app.parse_args(argv)

	def test_usage_error_exits_1_with_usage_on_stderr(self):
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			self.assertEqual(get_app.get_app(["a", "b"], apps_config()), 1)
		self.assertIn("Usage: bench-get-app", err.getvalue())
		self.assertTrue(err.getvalue().rstrip().endswith("error: one app at a time"))

	def test_no_app_prints_usage_and_exits_1(self):
		out = io.StringIO()
		with contextlib.redirect_stdout(out):
			self.assertEqual(get_app.get_app([], apps_config()), 1)
		self.assertIn("--branch version-16", out.getvalue())


class TestGetAppNames(unittest.TestCase):
	def test_resolve_url(self):
		self.assertEqual(get_app.resolve_url("helpdesk"), "https://github.com/frappe/helpdesk.git")
		self.assertEqual(get_app.resolve_url("acme/gadgets"), "https://github.com/acme/gadgets.git")
		for url in ("https://example.com/x/y.git", "file:///srv/y.git", "git@github.com:acme/y.git"):
			self.assertEqual(get_app.resolve_url(url), url)

	def test_repo_name_is_basename_without_dot_git(self):
		self.assertEqual(get_app.repo_name("https://github.com/frappe/hrms.git"), "hrms")
		self.assertEqual(get_app.repo_name("git@github.com:acme/flow_client.git"), "flow_client")
		self.assertEqual(get_app.repo_name("file:///srv/remotes/plain/"), "plain")
		self.assertEqual(get_app.repo_name("https://example.com/x/y"), "y")

	def test_app_name(self):
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			self.assertEqual(get_app.app_name("flow_client", ["flow"]), "flow")
			self.assertEqual(get_app.app_name("multi", ["multi", "multi_extra"]), "multi")
			self.assertEqual(err.getvalue(), "")
			self.assertEqual(get_app.app_name("plainlib", []), "plainlib")
			self.assertIn("does not look like a Frappe app; keeping the name 'plainlib'", err.getvalue())
			self.assertEqual(get_app.app_name("twin_client", ["alpha", "beta"]), "twin_client")
			self.assertIn("(alpha beta) and none is named for it", err.getvalue())

	def test_hooks_pattern(self):
		lines = ["flow/hooks.py", "flow/sub/hooks.py", "hooks.py", "README.md", "other/hooks.pyc"]
		self.assertEqual([m.group(1) for line in lines if (m := get_app.HOOKS.match(line))], ["flow"])


class TestRemoveAppArgs(unittest.TestCase):
	def test_flags(self):
		self.assertEqual(remove_app.parse_args(["crm"]), ("crm", False, True))
		self.assertEqual(remove_app.parse_args(["--force", "--no-backup", "crm"]), ("crm", True, False))
		self.assertIsNone(remove_app.parse_args(["--help"]))
		with self.assertRaisesRegex(remove_app.UsageError, "unknown flag: --depth"):
			remove_app.parse_args(["--depth", "crm"])
		with self.assertRaisesRegex(remove_app.UsageError, "one app at a time"):
			remove_app.parse_args(["a", "b"])

	def test_names_refused_before_anything_runs(self):
		for app, message in (
			("frappe", "cannot be removed"),
			("..", "is not an app name"),
			("a/b", "is not an app name"),
		):
			err = io.StringIO()
			with self.subTest(app=app), contextlib.redirect_stderr(err):
				self.assertEqual(
					proc.main_guard(lambda app=app: remove_app.remove_app([app], apps_config())), 1
				)
				self.assertIn(message, err.getvalue())

	def test_installed_on(self):
		def answer(status: int, out: str):
			return mock.patch.object(proc, "probe", return_value=(status, out))

		cfg = apps_config()
		with answer(0, json.dumps({"s": ["frappe", "crm"]})):
			self.assertTrue(remove_app.installed_on(cfg, "s", "crm"))
		with answer(0, json.dumps({"s": ["frappe"]})):
			self.assertFalse(remove_app.installed_on(cfg, "s", "crm"))
		with answer(0, json.dumps({"other": ["crm"]})):
			self.assertFalse(remove_app.installed_on(cfg, "s", "crm"))  # jq's `.[$s] // []`
		for status, out in ((1, ""), (0, "not json"), (0, "[]"), (0, '{"s": "crm"}')):
			with self.subTest(out=out), answer(status, out):
				self.assertIsNone(remove_app.installed_on(cfg, "s", "crm"))


class TestRenameAppArgs(unittest.TestCase):
	def test_flags(self):
		self.assertEqual(rename_app.parse_args(["a", "b"]), ("a", "b", "", "", True))
		self.assertEqual(
			rename_app.parse_args(["--no-pull", "--url", "u", "--branch", "x", "a", "b"]),
			("a", "b", "u", "x", False),
		)
		self.assertEqual(rename_app.parse_args(["a", "--url=u", "-b", "x", "b"]), ("a", "b", "u", "x", True))
		self.assertEqual(rename_app.parse_args(["--branch=x", "a", "b"]), ("a", "b", "", "x", True))
		self.assertIsNone(rename_app.parse_args(["--help"]))
		for argv, message in (
			(["--depth", "a", "b"], "unknown flag: --depth"),
			(["a"], "give the old name and the new one"),
			([], "give the old name and the new one"),
			(["a", "b", "c"], "give the old name and the new one"),
			(["--url"], "--url needs a value"),
			(["a", "b", "--branch", ""], "--branch needs a value"),
		):
			with self.subTest(argv=argv), self.assertRaisesRegex(rename_app.UsageError, message):
				rename_app.parse_args(argv)

	def test_the_url_follows_the_repository_name(self):
		for url, expected in (
			("https://github.com/Avunu/esign.git", "https://github.com/Avunu/esign_webforms.git"),
			("https://github.com/Avunu/frappe_editor", "https://github.com/Avunu/ui_text_editor"),
			("git@github.com:Avunu/esign.git", "git@github.com:Avunu/esign_webforms.git"),
			("https://github.com/Avunu/esign/", "https://github.com/Avunu/esign_webforms/"),
			("file:///srv/repos/esign.git", "file:///srv/repos/esign_webforms.git"),
		):
			old = "frappe_editor" if "frappe_editor" in url else "esign"
			new = "ui_text_editor" if old == "frappe_editor" else "esign_webforms"
			with self.subTest(url=url):
				self.assertEqual(rename_app.renamed_url(url, old, new), expected)

	def test_a_url_not_named_for_the_app_is_kept(self):
		for url in ("https://github.com/Avunu/avunu_erp.git", "https://github.com/Avunu/esign_old.git", ""):
			with self.subTest(url=url):
				self.assertEqual(rename_app.renamed_url(url, "esign", "esign_webforms"), url)

	def test_names_refused_before_anything_runs(self):
		for argv, message in (
			(["frappe", "x"], "cannot be renamed"),
			(["x", "frappe"], "cannot be renamed"),
			(["a", "a"], "is already called that"),
			(["a", ".."], "is not an app name"),
			(["a/b", "c"], "is not an app name"),
			(["a", "1b"], "is not a valid app name"),
			(["a", "b-c"], "is not a valid app name"),
		):
			err = io.StringIO()
			with self.subTest(argv=argv), contextlib.redirect_stderr(err):
				self.assertEqual(
					proc.main_guard(lambda argv=argv: rename_app.rename_app(argv, apps_config())), 1
				)
				self.assertIn(message, err.getvalue())

	def test_a_rename_the_flake_names_differently_is_refused(self):
		cfg = apps_config(renamedApps=[{"old": "crm", "new": "crm_other"}])
		err = io.StringIO()
		cwd = os.getcwd()
		with tempfile.TemporaryDirectory() as tmp:
			Path(tmp, "pyproject.toml").write_text('members = ["apps/crm"]\n')
			try:
				os.chdir(tmp)
				with (
					mock.patch.dict(os.environ, {"FRAPPE_BENCH_ROOT": tmp}),
					mock.patch.object(
						rename_app, "kind_of", lambda _cfg, app: "submodule" if app == "crm" else ""
					),
					contextlib.redirect_stderr(err),
				):
					status = proc.main_guard(lambda: rename_app.rename_app(["crm", "crm_pro"], cfg))
			finally:
				os.chdir(cwd)
		self.assertEqual(status, 1)
		self.assertIn("renamedApps says crm is renamed to crm_other, not crm_pro", err.getvalue())


class TestReconcile(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.root = Path(self.tmp.name)
		(self.root / "sites/site.local").mkdir(parents=True)
		(self.root / "sites/apps.txt").write_text("frappe\nerpnext\n\nhrms\nold_app\n")
		self.calls: list[list[str]] = []
		self.installed = ["frappe", "old_app"]
		self.cwd = os.getcwd()

	def tearDown(self):
		os.chdir(self.cwd)
		self.tmp.cleanup()

	def probe(self, cmd, cwd=None):
		self.calls.append(list(cmd))
		return 0, json.dumps({cmd[2]: self.installed})

	def attempt(self, cmd, cwd=None, env=None, quiet=False):
		self.calls.append(list(cmd))
		if cmd[3] == "install-app":
			self.installed.append(cmd[4])
		elif cmd[3] == "uninstall-app":
			self.installed.remove(cmd[4])
		return 0

	def run_it(self, cfg, *argv, env=None):
		out = io.StringIO()
		with (
			mock.patch.object(proc, "probe", self.probe),
			mock.patch.object(proc, "attempt", self.attempt),
			mock.patch.dict(os.environ, {"FRAPPE_BENCH_ROOT": str(self.root), **(env or {})}),
			contextlib.redirect_stdout(out),
		):
			code = proc.main_guard(lambda: reconcile.reconcile_apps(list(argv), cfg))
		return code, out.getvalue()

	def test_installs_what_apps_txt_names_and_the_site_lacks(self):
		code, out = self.run_it(apps_config(), "site.local")
		self.assertEqual(code, 0)
		installs = [c[4] for c in self.calls if c[3] == "install-app"]
		self.assertEqual(installs, ["erpnext", "hrms"])
		self.assertIn("now has every app in sites/apps.txt installed", out)
		self.assertTrue((self.root / "sites/site.local/locks/reconcile-apps.lock").exists())

	def test_replaced_apps_are_skipped_then_replaced(self):
		cfg = apps_config(replacedApps=[{"old": "old_app", "new": "hrms"}])
		code, _ = self.run_it(cfg, env={"FRAPPE_SITE": "site.local", reconcile.SKIP: "erpnext"})
		self.assertEqual(code, 0)
		steps = [" ".join(c[3:5]) for c in self.calls if c[3] != "list-apps"]
		# hrms by reconcile (old_app and erpnext skipped), so the replacement
		# only uninstalls.
		self.assertEqual(steps, ["install-app hrms", "uninstall-app old_app"])
		self.assertEqual(self.calls[-1][-2:], ["--yes", "--no-backup"])

	def test_renames_run_first_from_sites(self):
		cfg = apps_config(renamedApps=[{"old": "esign", "new": "esign_webforms"}])
		with mock.patch.object(proc, "run") as run:
			self.run_it(cfg, "site.local")
		cmd = run.call_args.args[0]
		self.assertEqual(cmd[:2], ["python3", "/lib/frappe_rename_app.py"])
		self.assertEqual(cmd[2:], ["--site", "site.local", "--yes", "esign=esign_webforms"])
		self.assertEqual(run.call_args.kwargs["cwd"], self.root / "sites")

	def test_no_site(self):
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			code, _ = self.run_it(apps_config(), env={"FRAPPE_SITE": ""})
		self.assertEqual(code, 1)
		self.assertIn("Usage: reconcile-apps <site>", err.getvalue())
		code, out = self.run_it(apps_config(), "nowhere.local")
		self.assertEqual((code, self.calls), (0, []))
		self.assertIn("sites/nowhere.local does not exist yet", out)


class TestRootSync(unittest.TestCase):
	def test_usage(self):
		err = io.StringIO()
		with contextlib.redirect_stderr(err):
			self.assertEqual(root_sync.main([]), 2)
			self.assertEqual(root_sync.main(["a", "b"]), 2)
		self.assertIn("usage: frappe-nix-root-sync <bench-root>", err.getvalue())

	def test_unconfigured_is_an_error(self):
		err = io.StringIO()
		with (
			tempfile.TemporaryDirectory() as tmp,
			mock.patch.dict(os.environ, {}, clear=True),
			contextlib.redirect_stderr(err),
		):
			self.assertEqual(root_sync.main([tmp]), 2)
		self.assertIn("FRAPPE_NIX_CLI_CONFIG is not set", err.getvalue())

	def test_a_failed_reconcile_restores_the_file(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			# A workspace tool that rewrites the file, then fails.
			tool = root / "workspace.py"
			tool.write_text("import sys\nopen('pyproject.toml', 'w').write('broken')\nsys.exit(3)\n")
			(root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
			os.utime(root / "pyproject.toml", ns=(10**18, 10**18))
			err = io.StringIO()
			cwd = os.getcwd()
			try:
				os.chdir(root)
				with contextlib.redirect_stderr(err):
					code = root_sync.sync({"workspace": str(tool), "template": "/t", "buildDepsSync": None})
			finally:
				os.chdir(cwd)
			self.assertEqual(code, 1)
			self.assertIn("could not reconcile pyproject.toml", err.getvalue())
			self.assertEqual((root / "pyproject.toml").read_text(), "[project]\nname = 'x'\n")
			self.assertEqual((root / "pyproject.toml").stat().st_mtime_ns, 10**18)
			self.assertFalse((root / "uv.lock").exists())

	def test_an_unchanged_root_runs_nothing_else(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			tool = root / "workspace.py"
			tool.write_text(
				"import sys\nassert sys.argv[1:4] == ['ensure-root', '--pyproject', 'pyproject.toml']\n"
			)
			(root / "pyproject.toml").write_text("[project]\n")
			out = io.StringIO()
			cwd = os.getcwd()
			try:
				os.chdir(root)
				with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {"PATH": ""}):
					code = root_sync.sync({"workspace": str(tool), "template": "/t", "buildDepsSync": None})
			finally:
				os.chdir(cwd)
			self.assertEqual((code, out.getvalue()), (0, ""))


class TestBenchCli(unittest.TestCase):
	def test_help_reaches_the_command(self):
		for command, text in (
			("get-app", "Usage: bench-get-app"),
			("remove-app", "Usage: bench-remove-app"),
			("rename-app", "Usage: bench-rename-app"),
		):
			out = io.StringIO()
			with (
				self.subTest(command=command),
				mock.patch.dict(os.environ, {"FRAPPE_NIX_CLI_CONFIG": "/nonexistent"}),
				contextlib.redirect_stdout(out),
				self.assertRaises(SystemExit) as exit_,
			):
				bench_cli.main([command, "--help"])
			self.assertEqual(exit_.exception.code, 0)
			self.assertIn(text, out.getvalue())

	def test_a_bad_configuration_is_exit_1(self):
		err = io.StringIO()
		with (
			mock.patch.dict(os.environ, {"FRAPPE_NIX_CLI_CONFIG": "/nonexistent"}),
			contextlib.redirect_stderr(err),
			self.assertRaises(SystemExit) as exit_,
		):
			bench_cli.main(["new-app", "x"])
		self.assertEqual(exit_.exception.code, 1)
		self.assertIn("cannot read /nonexistent", err.getvalue())


if __name__ == "__main__":
	unittest.main()
