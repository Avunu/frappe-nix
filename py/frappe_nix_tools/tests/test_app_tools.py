"""frappe-test's, frappe-demo's and frappe-shots' pieces the shell tests reach only
through a whole run (tests/standards/runtime.nix standards-frappe-test,
tests/standards/demo-shots.sh): the command lines, the stage switches, the
demo's parameters, and the configuration Nix bakes in.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.app_tools import config, devshell, frappe_demo, frappe_shots, frappe_test
from frappe_nix_tools.cli_config import ENV, ConfigError


def quiet() -> contextlib.AbstractContextManager:
	"""The tools' messages, out of the test run's output."""
	return contextlib.redirect_stderr(io.StringIO())


class TestFrappeTestArgs(unittest.TestCase):
	def test_defaults(self) -> None:
		args = frappe_test.parse_args([])
		self.assertEqual(args, frappe_test.TestArgs())
		self.assertIsNone(args.coverage)
		self.assertIsNone(args.site)
		self.assertFalse(args.filtered)

	def test_every_flag(self) -> None:
		args = frappe_test.parse_args(
			[
				"--app",
				"a",
				"--site",
				"s",
				"--reuse-site",
				"--keep-up",
				"--ci",
				"--module",
				"m",
				"--doctype",
				"d",
				"--test",
				"t1",
				"--test",
				"t2",
				"--no-coverage",
				"--no-testmap",
				"--no-composition",
				"--ty",
				"--nix-lint",
				"--shell-checks",
				"--junit",
				"j.xml",
				"--out",
				"o",
			]
		)
		self.assertEqual((args.app, args.site, args.module, args.doctype), ("a", "s", "m", "d"))
		self.assertEqual(args.tests, ["t1", "t2"])
		self.assertTrue(args.reuse and args.keep_up and args.ci and args.filtered)
		self.assertEqual((args.coverage, args.testmap, args.composition), (False, False, False))
		self.assertEqual((args.ty, args.nix_lint, args.shell_checks), (True, True, True))
		self.assertEqual((args.junit, args.out), ("j.xml", "o"))
		self.assertTrue(frappe_test.parse_args(["--recreate-site"]).recreate)
		self.assertTrue(frappe_test.parse_args(["--down"]).down)

	def test_a_value_is_the_next_word_and_not_empty(self) -> None:
		for flag in frappe_test.VALUED:
			with self.subTest(flag=flag):
				with self.assertRaisesRegex(frappe_test.UsageError, f"{flag} needs a value"):
					frappe_test.parse_args([flag])
				with self.assertRaisesRegex(frappe_test.UsageError, f"{flag} needs a value"):
					frappe_test.parse_args([flag, ""])
		# The next word is the value, even when it looks like a flag.
		self.assertEqual(frappe_test.parse_args(["--app", "--ci"]).app, "--ci")
		self.assertFalse(frappe_test.parse_args(["--app", "--ci"]).ci)

	def test_unknown_and_help(self) -> None:
		with self.assertRaises(frappe_test.UsageError) as caught:
			frappe_test.parse_args(["--bogus"])
		self.assertEqual(str(caught.exception), "unknown argument: --bogus")
		self.assertTrue(caught.exception.show_usage)
		# Not the --flag=value form.
		with self.assertRaises(frappe_test.UsageError):
			frappe_test.parse_args(["--app=x"])
		for word in ("-h", "--help"):
			with self.assertRaises(frappe_test.Help):
				frappe_test.parse_args(["--ci", word, "--bogus"])
		# Left to right: an unknown word before -h is the error.
		with self.assertRaises(frappe_test.UsageError):
			frappe_test.parse_args(["--bogus", "-h"])

	def test_pick(self) -> None:
		# The command line's, else --ci's plan, else the interactive default.
		self.assertFalse(frappe_test.pick(False, True, True, True))
		self.assertTrue(frappe_test.pick(True, False, False, False))
		self.assertTrue(frappe_test.pick(None, True, True, False))
		self.assertFalse(frappe_test.pick(None, True, False, True))
		self.assertTrue(frappe_test.pick(None, False, False, True))

	def test_ty_diagnostics(self) -> None:
		self.assertEqual(frappe_test.ty_diagnostics("x\nFound 1 diagnostic\n"), 1)
		self.assertEqual(frappe_test.ty_diagnostics("Found 2 diagnostics\nFound 12 diagnostics\n"), 12)
		self.assertEqual(frappe_test.ty_diagnostics("All checks passed!\n"), 0)

	def test_stage_status(self) -> None:
		self.assertEqual(
			[frappe_test.stage_status(rc) for rc in (0, 1, 2, 127)], ["ok", "failed", "error", "error"]
		)


class TestFrappeTestBench(unittest.TestCase):
	def setUp(self) -> None:
		self.tmp = Path(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, self.tmp)
		self.bench = self.tmp / "bench"
		(self.bench / "apps").mkdir(parents=True)
		(self.bench / "sites").mkdir()
		self.repo = self.tmp / "repo"
		self.repo.mkdir()

	def test_find_app(self) -> None:
		self.assertEqual(frappe_test.find_app(self.bench, self.repo), "")
		(self.bench / "apps" / "frappe").mkdir()
		(self.bench / "apps" / "other").symlink_to(self.tmp)
		(self.bench / "apps" / "mine").symlink_to(self.repo)
		self.assertEqual(frappe_test.find_app(self.bench, self.repo), "mine")
		self.assertEqual(frappe_test.find_app(self.tmp / "nowhere", self.repo), "")

	def test_default_setup(self) -> None:
		wizard = ["execute:frappe.utils.install.complete_setup_wizard"]
		bootstrap = ["module:erpnext.tests.bootstrap_test_data"]
		self.assertEqual(frappe_test.default_setup([], self.bench), wizard)
		self.assertEqual(frappe_test.default_setup(["erpnext"], self.bench), bootstrap)
		(self.bench / "sites" / "apps.txt").write_text("frappe\nerpnext_extra\n")
		self.assertEqual(frappe_test.default_setup([], self.bench), wizard)
		(self.bench / "sites" / "apps.txt").write_text("frappe\nerpnext\n")
		self.assertEqual(frappe_test.default_setup([], self.bench), bootstrap)

	def test_verdict(self) -> None:
		run = frappe_test.Run(
			frappe_test.TestArgs(),
			bench=self.bench,
			repo=self.repo,
			out=self.tmp,
			junit="",
			app="a",
			site="s",
		)
		self.assertEqual(run.verdict(), 0)
		run.stages.update({"tests": "ok", "coverage": "skipped", "ty": "failed", "nix-lint": "error"})
		self.assertEqual(run.verdict(), 5)
		run.stages["coverage"] = "error"
		self.assertEqual(run.verdict(), 2)
		run.env_failed = True
		self.assertEqual(run.verdict(), 10)

	def test_guards_off_env(self) -> None:
		env = {"PATH": "/bin"}
		self.assertEqual(frappe_test.guards_off_env([], env), env)
		self.assertEqual(
			frappe_test.guards_off_env(["mail"], env), {"PATH": "/bin", "FRAPPE_DEVGUARD_DISABLE": "mail"}
		)
		# What the caller already turned off for the command stays off, once.
		env["FRAPPE_DEVGUARD_DISABLE"] = " backups, mail ,"
		self.assertEqual(
			frappe_test.guards_off_env(["mail", "webhooks"], env)["FRAPPE_DEVGUARD_DISABLE"],
			"backups,mail,webhooks",
		)
		self.assertEqual(env["FRAPPE_DEVGUARD_DISABLE"], " backups, mail ,")

	def test_relaxed_guards_reach_the_test_processes_only(self) -> None:
		calls: list[tuple[list[str], dict | None]] = []

		def fake_call(cmd, *, cwd=None, env=None, **_):
			calls.append((list(cmd), env))
			return 0

		with mock.patch.dict(os.environ, {"FRAPPE_DEVGUARD_DISABLE": ""}), quiet():
			run = frappe_test.Run(
				frappe_test.TestArgs(),
				bench=self.bench,
				repo=self.repo,
				out=self.tmp,
				junit="",
				app="a",
				site="s",
			)
			run.relax_guards([])
			self.assertEqual(run.test_env["FRAPPE_DEVGUARD_DISABLE"], "")
			with contextlib.redirect_stdout(io.StringIO()) as said:
				run.relax_guards(["mail"])
			self.assertIn("devguard-disable", said.getvalue())
			with mock.patch.object(frappe_test, "call", side_effect=fake_call):
				run.bench_cmd("--site", "s", "execute", "x.y")
				run.run_tests(coverage=False)
				run.testmap(True, have_coverage=True)
				run.composition(True)
			# The dev shell's own environment is untouched.
			self.assertEqual(os.environ["FRAPPE_DEVGUARD_DISABLE"], "")
		self.assertEqual(len(calls), 4)
		for cmd, env in calls:
			with self.subTest(cmd=cmd[:3]):
				self.assertIsNotNone(env)
				assert env is not None
				self.assertEqual(env["FRAPPE_DEVGUARD_DISABLE"], "mail")

	def test_outside_the_dev_shell_and_down(self) -> None:
		with mock.patch.dict(os.environ, {"FRAPPE_BENCH_ROOT": "", "DEVENV_ROOT": ""}), quiet():
			self.assertEqual(frappe_test.main([]), 10)
		env = {"FRAPPE_BENCH_ROOT": str(self.bench), "DEVENV_ROOT": str(self.repo), "PC_SOCKET_PATH": ""}
		with mock.patch.dict(os.environ, env), quiet():
			self.assertEqual(frappe_test.main(["--down"]), 10)
			with mock.patch.object(devshell.Compose, "stop", autospec=True, return_value=3) as stop:
				os.environ["PC_SOCKET_PATH"] = "/run/pc.sock"
				self.assertEqual(frappe_test.main(["--down"]), 3)
			stop.assert_called_once()
			self.assertEqual(stop.call_args.args[0].socket, "/run/pc.sock")

	def test_usage_errors_before_anything_starts(self) -> None:
		env = {"FRAPPE_BENCH_ROOT": str(self.bench), "DEVENV_ROOT": str(self.repo), "FRAPPE_SITE": "s"}
		with mock.patch.dict(os.environ, env), mock.patch.object(frappe_test, "Run") as run, quiet():
			self.assertEqual(frappe_test.main(["--out", str(self.tmp / "out")]), 64)  # no app
			self.assertEqual(frappe_test.main(["--app", "a", "--reuse-site", "--recreate-site"]), 64)
			os.environ["FRAPPE_SITE"] = ""
			self.assertEqual(frappe_test.main(["--app", "a"]), 64)
			(self.bench / "sites" / "s").mkdir()
			with mock.patch.object(devshell, "has_terminal", return_value=False):
				self.assertEqual(frappe_test.main(["--app", "a", "--site", "s"]), 64)
		run.assert_not_called()


class TestFrappeDemo(unittest.TestCase):
	def test_args(self) -> None:
		self.assertEqual(frappe_demo.parse_args([]), frappe_demo.DemoArgs())
		args = frappe_demo.parse_args(
			["--site", "s", "--fresh", "--erpnext-demo", "--date", "2026-01-02", "--seed", "3", "--no-up"]
		)
		self.assertEqual((args.site, args.date, args.seed), ("s", "2026-01-02", "3"))
		self.assertTrue(args.fresh and args.erpnext)
		self.assertFalse(args.up or args.recreate)
		recreate = frappe_demo.parse_args(["--recreate-site", "--no-erpnext-demo"])
		self.assertTrue(recreate.fresh and recreate.recreate)
		self.assertIs(recreate.erpnext, False)
		# An empty value is accepted, and means "not given".
		self.assertEqual(frappe_demo.parse_args(["--date", ""]).date, "")

	def test_arg_errors_are_exit_10(self) -> None:
		for argv, message in (
			(["--site"], "--site needs a value"),
			(["--seed"], "--seed needs a value"),
			(["--bogus"], "unknown argument --bogus (see --help)"),
		):
			with self.subTest(argv=argv):
				with self.assertRaises(frappe_demo.Fail) as caught:
					frappe_demo.parse_args(argv)
				self.assertEqual((str(caught.exception), caught.exception.code), (message, 10))
		with self.assertRaises(frappe_demo.Help):
			frappe_demo.parse_args(["--fresh", "-h"])

	def test_resolve(self) -> None:
		module = {"date": "2024-04-04", "seed": 7, "erpnext-demo": True}
		args = frappe_demo.DemoArgs()
		self.assertEqual(frappe_demo.resolve(args, {}, module), frappe_demo.Demo("2024-04-04", "7", True))
		spec = {"date": "2026-01-15", "seed": 1, "erpnextDemo": False}
		self.assertEqual(frappe_demo.resolve(args, spec, module), frappe_demo.Demo("2026-01-15", "1", False))
		# jq's `//`: a null or false in the spec falls through to the module.
		self.assertEqual(frappe_demo.resolve(args, {"date": None, "seed": False}, module).seed, "7")
		# jq's truth: 0 is true.
		self.assertTrue(
			frappe_demo.resolve(args, {"erpnextDemo": 0}, {**module, "erpnext-demo": False}).erpnext
		)
		given = frappe_demo.DemoArgs(date="2025-02-03", seed="9", erpnext=False)
		self.assertEqual(frappe_demo.resolve(given, spec, module), frappe_demo.Demo("2025-02-03", "9", False))

	def test_resolve_refuses(self) -> None:
		for args, module, message in (
			(frappe_demo.DemoArgs(date="2026-1-5"), {"seed": 1}, "--date must be YYYY-MM-DD, not 2026-1-5"),
			(frappe_demo.DemoArgs(), {"seed": 1}, "--date must be YYYY-MM-DD, not null"),
			(
				frappe_demo.DemoArgs(seed="x1"),
				{"date": "2026-01-01"},
				"--seed must be a whole number, not x1",
			),
			(
				frappe_demo.DemoArgs(seed="-1"),
				{"date": "2026-01-01"},
				"--seed must be a whole number, not -1",
			),
		):
			with self.subTest(message=message):
				with self.assertRaisesRegex(frappe_demo.Fail, f"^{message}$"):
					frappe_demo.resolve(args, {}, module)

	def test_project_name(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			py = Path(tmp) / "pyproject.toml"
			py.write_text('[project]\nname = "app_x"\n')
			self.assertEqual(frappe_demo.project_name(py), "app_x")
			for text in ('[project]\nversion = "1"\n', "not toml [", 'project = "x"\n'):
				py.write_text(text)
				with self.assertRaisesRegex(frappe_demo.Fail, "cannot read"):
					frappe_demo.project_name(py)
			with self.assertRaises(frappe_demo.Fail):
				frappe_demo.project_name(Path(tmp) / "missing.toml")


class TestFrappeShots(unittest.TestCase):
	def test_args(self) -> None:
		self.assertEqual(frappe_shots.parse_args([]), frappe_shots.ShotsArgs())
		args = frappe_shots.parse_args(
			[
				"--spec",
				"s.ts",
				"--only",
				"a,b",
				"--theme",
				"dark",
				"--out",
				"o",
				"--video",
				"v",
				"--check",
				"--reuse-site",
			]
		)
		self.assertEqual(
			(args.spec, args.only, args.theme, args.out, args.video), ("s.ts", "a,b", "dark", "o", "v")
		)
		self.assertEqual(args.mode, "check")
		self.assertTrue(args.reuse)
		self.assertEqual(frappe_shots.parse_args(["--check", "--update"]).mode, "update")
		self.assertTrue(frappe_shots.parse_args(["--recreate-site"]).recreate)

	def test_arg_errors_are_exit_2(self) -> None:
		for flag in frappe_shots.VALUED:
			with self.subTest(flag=flag), self.assertRaises(frappe_shots.Fail) as caught:
				frappe_shots.parse_args([flag])
			self.assertEqual((str(caught.exception), caught.exception.code), (f"{flag} needs a value", 2))
		with self.assertRaises(frappe_shots.Fail) as caught:
			frappe_shots.parse_args(["--bogus"])
		self.assertEqual(caught.exception.code, 2)
		with self.assertRaises(frappe_shots.Help):
			frappe_shots.parse_args(["--help"])

	def test_resolve(self) -> None:
		shots_cfg = {"timezone": "America/New_York", "locale": "en-US"}
		demo_cfg = {"date": "2024-04-04", "seed": 7, "erpnext-demo": True}
		demo = frappe_shots.resolve({"demo": {}, "timezone": None}, shots_cfg, demo_cfg)
		self.assertEqual(demo, frappe_shots.Demo("America/New_York", "2024-04-04", "7", True))
		spec = {"demo": {"date": "2026-01-15", "seed": 1, "erpnextDemo": False}, "timezone": "Asia/Tokyo"}
		self.assertEqual(
			frappe_shots.resolve(spec, shots_cfg, demo_cfg),
			frappe_shots.Demo("Asia/Tokyo", "2026-01-15", "1", False),
		)
		# Only a literal true turns the erpnext demo on (`[ "$ERPNEXT" = true ]`).
		self.assertFalse(frappe_shots.resolve({"demo": {"erpnextDemo": 1}}, shots_cfg, demo_cfg).erpnext)
		self.assertTrue(frappe_shots.resolve({"demo": {"erpnextDemo": "true"}}, shots_cfg, demo_cfg).erpnext)

	def test_demo_flags_and_runner_args(self) -> None:
		demo = frappe_shots.Demo("UTC", "2026-01-15", "1", False)
		self.assertEqual(
			frappe_shots.demo_flags("s", demo),
			[
				"--fresh",
				"--recreate-site",
				"--site",
				"s",
				"--date",
				"2026-01-15",
				"--seed",
				"1",
				"--no-erpnext-demo",
			],
		)
		self.assertEqual(
			frappe_shots.demo_flags("s", frappe_shots.Demo("UTC", "d", "1", True))[-1], "--erpnext-demo"
		)
		args = frappe_shots.ShotsArgs(mode="check", only="a", video="v")
		with mock.patch.dict(os.environ, {"FRAPPE_NIX_TEST_ADMIN_PASSWORD": ""}):
			cmd = frappe_shots.runner_args(
				args,
				spec="/r/s.ts",
				out="/r/o",
				repo="/r",
				site="s",
				port="8000",
				cfg={"locale": "de-DE"},
				date="d",
			)
		self.assertEqual(
			cmd,
			[
				"--spec",
				"/r/s.ts",
				"--base",
				"http://s:8000",
				"--mode",
				"check",
				"--out",
				"/r/o",
				"--masters",
				"/r/.dev-dist/shots",
				"--timezone",
				"null",
				"--locale",
				"de-DE",
				"--clock",
				"d",
				"--password",
				"admin",
				"--only",
				"a",
				"--video",
				"v",
			],
		)


class TestDevshell(unittest.TestCase):
	def test_jq(self) -> None:
		self.assertEqual(
			[devshell.jq_raw(v) for v in ("a", 1, 80.0, None, True)], ["a", "1", "80.0", "null", "true"]
		)
		self.assertEqual(devshell.alt(None, 1), 1)
		self.assertEqual(devshell.alt(False, 1), 1)
		self.assertEqual(devshell.alt(0, 1), 0)
		self.assertEqual(devshell.alt("", 1), "")
		self.assertFalse(devshell.truthy(None) or devshell.truthy(False))
		self.assertTrue(devshell.truthy(0) and devshell.truthy(""))

	@mock.patch.dict(os.environ, {"FRAPPE_NIX_PORTS_FILE": ""})
	def test_webserver_port(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			self.assertEqual(devshell.webserver_port(tmp), "")
			(Path(tmp) / "sites").mkdir()
			cfg = Path(tmp) / "sites" / "common_site_config.json"
			for data, port in (
				({"webserver_port": 8000}, "8000"),
				({"webserver_port": None}, ""),
				({}, ""),
				([], ""),
			):
				cfg.write_text(json.dumps(data))
				self.assertEqual(devshell.webserver_port(tmp), port)
			cfg.write_text("{")
			self.assertEqual(devshell.webserver_port(tmp), "")

	def test_leased_ports(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			ports = Path(tmp) / "ports.json"
			(Path(tmp) / "sites").mkdir()
			(Path(tmp) / "sites" / "common_site_config.json").write_text('{"webserver_port": 8000}')
			with mock.patch.dict(os.environ, {"FRAPPE_NIX_PORTS_FILE": str(ports)}):
				# Before `devenv up`: no port at all, not the config's stale one.
				self.assertEqual(devshell.webserver_port(tmp), "")
				self.assertIsNone(devshell.bench_id())
				ports.write_text(json.dumps({"bench_id": "b/devenv-1", "pid": os.getpid(), "web": 8235}))
				self.assertEqual(devshell.webserver_port(tmp), "8235")
				self.assertEqual(devshell.bench_id(), "b/devenv-1")
				# Sockets off: no nginx answers the identity, so none is expected.
				ports.write_text(
					json.dumps({"bench_id": "b/devenv-1", "pid": os.getpid(), "web": 8235, "identity": False})
				)
				self.assertEqual(devshell.webserver_port(tmp), "8235")
				self.assertIsNone(devshell.bench_id())
				# A holder that is gone: its file does not count.
				ports.write_text(json.dumps({"bench_id": "b/devenv-1", "pid": 2**22 + 1, "web": 8235}))
				self.assertEqual(devshell.webserver_port(tmp), "")
				self.assertIsNone(devshell.leased())

	def test_web_answers_identity(self) -> None:
		import http.server
		import threading

		class Bench(http.server.BaseHTTPRequestHandler):
			def do_GET(self) -> None:
				body = b"b/devenv-1" if self.path == "/__frappe_nix/bench" else b"hi"
				self.send_response(200)
				self.end_headers()
				self.wfile.write(body)

			def log_message(self, format: str, *args: object) -> None:
				pass

		server = http.server.HTTPServer(("127.0.0.1", 0), Bench)
		threading.Thread(target=server.serve_forever, daemon=True).start()
		port = str(server.server_address[1])
		try:
			self.assertTrue(devshell.web_answers(port))
			self.assertTrue(devshell.web_answers(port, "b/devenv-1"))
			# Another bench on the port: an answer, but not this bench's.
			self.assertFalse(devshell.web_answers(port, "b/devenv-2"))
		finally:
			server.shutdown()
			server.server_close()
		self.assertFalse(devshell.web_answers(port, "b/devenv-1"))

	def test_faked_env(self) -> None:
		self.assertEqual(devshell.faked_env({"A": "1"}), {"A": "1"})
		faked = devshell.faked_env(
			{
				"FRAPPE_NIX_SHOTS_FAKETIME": "@2026-01-15 14:00:00",
				"FRAPPE_NIX_SHOTS_PRELOAD": "/f.so",
				"LD_PRELOAD": "/x.so",
			}
		)
		self.assertEqual(faked["LD_PRELOAD"], "/f.so:/x.so")
		self.assertEqual(faked["FAKETIME"], "@2026-01-15 14:00:00")
		self.assertEqual(
			(faked["TZ"], faked["FAKETIME_DISABLE_SHM"], faked["FAKETIME_DONT_FAKE_MONOTONIC"]),
			("UTC", "1", "1"),
		)
		self.assertEqual(devshell.faked_env({"FRAPPE_NIX_SHOTS_FAKETIME": "@x"})["LD_PRELOAD"], "")

	def test_ask_recreate(self) -> None:
		for answer, yes in (
			("y\n", True),
			("  YES \n", True),
			("yes", True),
			("n\n", False),
			("", False),
			("Yes\n", False),
		):
			with self.subTest(answer=answer):
				with mock.patch("sys.stdin") as stdin, mock.patch("sys.stderr"):
					stdin.readline.return_value = answer
					self.assertIs(devshell.ask_recreate("q"), yes)

	def test_call(self) -> None:
		self.assertEqual(devshell.call(["sh", "-c", "exit 3"], quiet=True), 3)
		self.assertEqual(devshell.call(["/nonexistent/command"], quiet=True), 127)
		self.assertEqual(devshell.capture(["sh", "-c", "printf 'a\\n\\n'"]), (0, "a"))
		self.assertEqual(devshell.call(["sh", "-c", 'read l; [ -z "$l" ]'], stdin_text="\n", quiet=True), 0)
		self.assertEqual(
			devshell.capture_quiet(["sh", "-c", "echo out; echo err >&2; exit 2"]), (2, "out\nerr")
		)
		self.assertEqual(devshell.capture_quiet(["/nonexistent/command"]), (127, ""))
		self.assertEqual(devshell.capture_quiet(["sleep", "5"], timeout=0.1), (127, ""))

	def test_diagnose(self) -> None:
		"""A bench that did not come up is reported with its process list, the
		logs of the processes that exist, and its lease."""
		calls: list[list[str]] = []

		def fake(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
			calls.append(cmd)
			if cmd[4:] == ["process", "list", "-o", "wide"]:
				return 0, "nginx  Skipped  1"
			if cmd[4:6] == ["process", "logs"] and cmd[6] == "frappe-ports":
				return 0, "frappe-nix: nginx moved to 127.0.0.1:8235"
			return 1, "no such process"

		with tempfile.TemporaryDirectory() as tmp:
			ports = Path(tmp) / "ports.json"
			ports.write_text('{"web": 8235}\n')
			with (
				mock.patch.dict(os.environ, {"FRAPPE_NIX_PORTS_FILE": str(ports)}),
				mock.patch.object(devshell, "capture_quiet", fake),
			):
				text = devshell.Compose("/run/pc.sock").diagnose()
				self.assertIn("nginx  Skipped  1", text)
				self.assertIn("── frappe-ports (last 40 lines)\nfrappe-nix: nginx moved", text)
				self.assertNotIn("── nginx (last", text)
				self.assertIn('{"web": 8235}', text)
				self.assertEqual(calls[0][:4], ["process-compose", "-U", "-u", "/run/pc.sock"])
				# No manager to ask: the lease alone.
				calls.clear()
				self.assertEqual(devshell.Compose("").diagnose(), f'── {ports}\n{{"web": 8235}}')
				self.assertEqual(calls, [])
		with mock.patch.dict(os.environ, {"FRAPPE_NIX_PORTS_FILE": ""}):
			self.assertEqual(devshell.Compose("").diagnose(), "")

	def test_stop(self) -> None:
		"""A down whose server exits before it replies (the client's "EOF") is a
		stop; one whose server still answers, or never did, is not."""

		def run(down_rc: int, answers: list[bool]) -> tuple[int, list[str]]:
			"""stop() with `down` exiting DOWN_RC and `process list` answering
			ANSWERS in turn (the last one from then on); its status and the
			subcommands it ran."""
			ran: list[str] = []
			left = list(answers)

			def fake(cmd: list[str], **_: object) -> int:
				sub = " ".join(cmd[4:])
				ran.append(sub)
				if sub == "down":
					return down_rc
				answer = left.pop(0) if len(left) > 1 else left[0]
				return 0 if answer else 1

			with mock.patch.object(devshell, "call", fake), mock.patch("time.sleep"), quiet():
				return devshell.Compose("/run/pc.sock").stop(settle=5), ran

		self.assertEqual(run(0, [True]), (0, ["process list", "down"]))
		# The reply lost to the server's exit: it stops answering.
		self.assertEqual(
			run(1, [True, True, False]), (0, ["process list", "down", "process list", "process list"])
		)
		# Nothing was up to stop: down's own failure.
		self.assertEqual(run(1, [False]), (1, ["process list", "down"]))
		# Still answering after SETTLE: down failed.
		with mock.patch("time.monotonic", side_effect=[0, 1, 10]):
			self.assertEqual(run(1, [True])[0], 1)


class TestAdminPassword(unittest.TestCase):
	def test_new_old_and_default(self):
		env = {"FRAPPE_NIX_TEST_ADMIN_PASSWORD": "new", "FRAPPE_TEST_ADMIN_PASSWORD": "old"}
		with mock.patch.dict(os.environ, env, clear=True):
			self.assertEqual(devshell.admin_password(), "new")
		# The 1.x name is not read since frappe-nix 2.0.0.
		with mock.patch.dict(os.environ, {"FRAPPE_TEST_ADMIN_PASSWORD": "old"}, clear=True):
			self.assertEqual(devshell.admin_password(), "admin")
		with mock.patch.dict(os.environ, {}, clear=True):
			self.assertEqual(devshell.admin_password(), "admin")


class TestConfig(unittest.TestCase):
	def test_load(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "c.json"
			path.write_text(json.dumps({"demoPy": "/nix/store/x/frappe_demo.py"}))
			self.assertEqual(config.load_demo(path).demo_py, "/nix/store/x/frappe_demo.py")
			path.write_text(json.dumps({"shotsDir": "/s", "faketimeLib": "/f/$LIB/libfaketime.so.1"}))
			self.assertEqual(config.load_shots(path), config.ShotsConfig("/s", "/f/$LIB/libfaketime.so.1"))
			with self.assertRaisesRegex(ConfigError, "unknown key"):
				config.load_demo(path)
			path.write_text(json.dumps({"shotsDir": "/s"}))
			with self.assertRaisesRegex(ConfigError, "missing key"):
				config.load_shots(path)
		with mock.patch.dict(os.environ, {ENV: ""}), self.assertRaisesRegex(ConfigError, "frappe-demo"):
			config.load_demo()

	def test_a_bad_configuration_is_an_environment_error(self) -> None:
		with mock.patch.dict(os.environ, {ENV: "/nonexistent.json"}), quiet():
			self.assertEqual(frappe_demo.main([]), 10)
			self.assertEqual(frappe_shots.main([]), 3)


if __name__ == "__main__":
	unittest.main()
