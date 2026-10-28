"""frappe-init's pieces the shell tests reach only through a whole run
(tests/scaffold-modes.sh, migrate-classic.sh, migrate-versions.sh,
tests/standards/sync.nix): the command line, the configuration and presets,
mode detection, preset resolution and template rendering.
"""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.cli_config import ENV
from frappe_nix_tools.common.repo import NO_AUTO_MAINTENANCE
from frappe_nix_tools.frappe_init import args as fargs
from frappe_nix_tools.frappe_init import command, config, detect, presets, template, ui
from frappe_nix_tools.frappe_init.args import UsageError, parse_args
from frappe_nix_tools.frappe_init.state import (
	Run,
	normalize_dist,
	normalize_name,
	resolve_app_url,
	url_basename,
)

PRESETS = {
	"develop": {
		"label": "develop",
		"branch": "develop",
		"python": "python314",
		"nodejs": "nodejs_24",
		"requiresPython": ">=3.14",
		"overrideDependencies": ["click~=8.3.1"],
	},
	"version-16": {
		"label": "version-16",
		"branch": "version-16",
		"python": "python314",
		"nodejs": "nodejs_24",
		"requiresPython": ">=3.14",
		"overrideDependencies": ["pypika-stubs; python_full_version < '3.14'"],
	},
	"version-15": {
		"label": "version-15",
		"branch": "version-15",
		"python": "python312",
		"nodejs": "nodejs_20",
		"requiresPython": ">=3.12",
	},
}


def git(*argv: str, cwd: Path) -> None:
	subprocess.run(["git", *NO_AUTO_MAINTENANCE, *argv], cwd=cwd, check=True, capture_output=True)


@contextlib.contextmanager
def chdir(path: Path):
	old = os.getcwd()
	os.chdir(path)
	try:
		yield
	finally:
		os.chdir(old)


def run_state(**argv) -> Run:
	cfg = config.InitConfig(presets="", template="", app_template="", sources=None)
	return Run(args=fargs.InitArgs(**argv), config=cfg, presets=presets.parse(PRESETS))


class TestArgs(unittest.TestCase):
	def test_flags_and_values(self):
		a = parse_args(
			["--migrate", "-y", "--version", "version-15", "--name=acme", "--default-site", "a.local", "dir"]
		)
		self.assertEqual(
			(a.mode, a.assume_yes, a.version, a.name, a.site, a.target),
			("migrate", True, "version-15", "acme", "a.local", "dir"),
		)

	def test_the_1x_flags_are_usage_errors_naming_the_new_ones(self):
		for argv, new, code in (
			(["--frappe-version=develop"], "--version", 1),
			(["--site", "s.local"], "--default-site", 1),
			(["--check", "--frappe-version", "version-16"], "--version", 2),
		):
			with self.subTest(argv=argv):
				with self.assertRaises(fargs.UsageError) as caught:
					parse_args(argv)
				self.assertEqual(caught.exception.code, code)
				self.assertIn(f"removed in frappe-nix 2.0.0: use {new}", str(caught.exception))

	def test_commit_takes_its_message_only_after_an_equals_sign(self):
		a = parse_args(["--commit", "dir"])
		self.assertEqual((a.commit, a.commit_msg, a.target), (True, fargs.DEFAULT_COMMIT_MSG, "dir"))
		a = parse_args(["--commit=chore: adopt"])
		self.assertEqual((a.commit, a.commit_msg), (True, "chore: adopt"))

	def test_help_stops_parsing(self):
		self.assertTrue(parse_args(["--help", "--bogus"]).help)
		self.assertTrue(parse_args(["-h"]).help)

	def test_unknown_flag_is_1_and_stops_at_once(self):
		with self.assertRaises(UsageError) as e:
			parse_args(["--bogus", "--help"])
		self.assertEqual((e.exception.code, str(e.exception)), (1, "unknown flag: --bogus"))
		self.assertTrue(e.exception.show_usage)
		with self.assertRaises(UsageError) as e:
			parse_args(["-"])
		self.assertEqual(e.exception.code, 1)

	def test_under_sync_or_check_usage_errors_are_2_after_the_whole_line(self):
		with self.assertRaises(UsageError) as e:
			parse_args(["--bogus", "--other", "--check"])
		self.assertEqual((e.exception.code, str(e.exception)), (2, "unknown flag: --bogus"))
		# --help still wins over an unknown flag seen before it.
		self.assertTrue(parse_args(["--check", "--bogus", "--help"]).help)

	def test_a_value_flag_without_its_value(self):
		for argv, code in ((["--name"], 1), (["--version"], 1), (["--check", "--format"], 2)):
			with self.subTest(argv=argv), self.assertRaises(UsageError) as e:
				parse_args(argv)
			self.assertEqual((e.exception.code, e.exception.show_usage), (code, False))
			self.assertEqual(str(e.exception), f"{argv[-1]} needs a value")

	def test_check_flags_are_unknown_without_sync_or_check(self):
		for word in ("--format", "--format=json", "--expect-rev=x"):
			with self.subTest(word=word), self.assertRaises(UsageError) as e:
				parse_args(["--app", word, "x"])
			self.assertEqual(e.exception.code, 1)

	def test_sync_flags_pass_through_and_app_flags_are_remembered(self):
		a = parse_args(["--sync", "--only", "a,b", "--format=json", "--init-listing", "--profile-path", "p"])
		self.assertEqual((a.mode, a.app_action), ("app", "sync"))
		self.assertEqual(
			a.sync_args, ["--only", "a,b", "--format=json", "--init-listing", "--profile-path", "p"]
		)
		self.assertEqual(a.app_flag, "--only")
		self.assertEqual(parse_args(["--standards=minimal"]).app_flag, "--standards")

	def test_a_later_mode_flag_wins_but_the_app_action_stays(self):
		a = parse_args(["--sync", "--init"])
		self.assertEqual((a.mode, a.app_action), ("init", "sync"))

	def test_usage_names_the_new_flags_only(self):
		self.assertIn("--version <v>", fargs.USAGE)
		self.assertIn("--default-site <site>", fargs.USAGE)
		self.assertNotIn("--frappe-version", fargs.USAGE)
		self.assertNotIn("--site <", fargs.USAGE)
		self.assertNotIn("gum", fargs.USAGE)


class TestConfigAndPresets(unittest.TestCase):
	def test_config_keys(self):
		cfg = config.parse({"presets": "/p", "template": "/t", "appTemplate": "/a", "sources": "/s"})
		self.assertEqual(cfg, config.InitConfig("/p", "/t", "/a", "/s"))
		self.assertIsNone(
			config.parse({"presets": "/p", "template": "/t", "appTemplate": "/a", "sources": None}).sources
		)
		with self.assertRaisesRegex(config.ConfigError, r"missing key\(s\): appTemplate, sources"):
			config.parse({"presets": "/p", "template": "/t"})
		with self.assertRaisesRegex(config.ConfigError, r"unknown key\(s\): extra"):
			config.parse(
				{"presets": "/p", "template": "/t", "appTemplate": "/a", "sources": "/s", "extra": 1}
			)
		with (
			mock.patch.dict(os.environ, {}, clear=True),
			self.assertRaisesRegex(config.ConfigError, "nix run"),
		):
			config.load()

	def test_a_presets_file_loads_in_order(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp, "presets.json")
			path.write_text(json.dumps(PRESETS))
			loaded = presets.load(path)
			with self.assertRaisesRegex(presets.PresetError, "cannot read"):
				presets.load(Path(tmp, "missing.json"))
		self.assertEqual(list(loaded), ["develop", "version-16", "version-15"])
		self.assertEqual(loaded["version-15"].pyver, "3.12")
		self.assertEqual(loaded["version-16"].pytag, "py314")
		self.assertEqual(loaded["version-16"].node_version, "24")

	def test_optional_keys(self):
		p = presets.parse(
			{"x": {"label": "x", "branch": "x", "python": "python311", "requiresPython": ">=3.11"}}
		)["x"]
		self.assertEqual((p.nodejs, p.override_dependencies, p.overrides_json), (None, (), "[]"))
		with self.assertRaisesRegex(presets.PresetError, "names no nodejs"):
			_ = p.node

	def test_overrides_are_one_line_of_json_a_toml_array(self):
		p = presets.parse(PRESETS)["version-16"]
		self.assertEqual(p.overrides_json, "[\"pypika-stubs; python_full_version < '3.14'\"]")

	def test_refused_shapes(self):
		good = PRESETS["version-15"]
		for data, message in (
			([], "non-empty object"),
			({}, "non-empty object"),
			({"x": []}, "expected an object"),
			({"x": {**good, "series": "15"}}, r"unknown key\(s\): series"),
			({"x": {k: v for k, v in good.items() if k != "branch"}}, r"missing key\(s\): branch"),
			({"x": {**good, "python": "py312"}}, "python3xx"),
			({"x": {**good, "overrideDependencies": "click"}}, "list of strings"),
			({"x": {**good, "label": ""}}, "non-empty string"),
		):
			with self.subTest(message=message), self.assertRaisesRegex(presets.PresetError, message):
				presets.parse(data)


class TestNames(unittest.TestCase):
	def test_normalize_name(self):
		for raw, want in (
			("My_Bench!", "my-bench"),
			("--a--b--", "a-b"),
			(".", "frappe-bench"),
			("é", "frappe-bench"),
		):
			with self.subTest(raw=raw):
				self.assertEqual(normalize_name(raw), want)

	def test_normalize_dist(self):
		self.assertEqual(normalize_dist("Print_Designer"), normalize_dist("print-designer"))

	def test_app_urls(self):
		self.assertEqual(resolve_app_url("erpnext"), "https://github.com/frappe/erpnext.git")
		self.assertEqual(resolve_app_url("acme/widgets"), "https://github.com/acme/widgets.git")
		for url in ("https://example.com/x.git", "git@github.com:a/b.git", "file:///r"):
			self.assertEqual(resolve_app_url(url), url)
		self.assertEqual(url_basename("https://github.com/acme/widgets.git"), "widgets")
		self.assertEqual(url_basename("git@github.com:a/b.git/"), "b")


class TestDetect(unittest.TestCase):
	def setUp(self):
		self.tmp = Path(tempfile.mkdtemp())
		self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)
		self.env = mock.patch.dict(
			os.environ,
			{"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(self.tmp)},
		)
		self.env.start()
		self.addCleanup(self.env.stop)

	def bench(self, name: str, version: str = "15.1.0") -> Path:
		d = self.tmp / name
		(d / "apps/frappe/frappe").mkdir(parents=True)
		(d / "sites").mkdir()
		(d / "apps/frappe/frappe/hooks.py").write_text('app_name = "frappe"\n')
		(d / "apps/frappe/frappe/__init__.py").write_text(f'__version__ = "{version}"\n')
		return d

	def test_dir_is_empty(self):
		with chdir(self.tmp):
			self.assertTrue(detect.dir_is_empty("missing"))
			Path("e/.git").mkdir(parents=True)
			self.assertTrue(detect.dir_is_empty("e"))
			Path("e/.x").write_text("")
			self.assertFalse(detect.dir_is_empty("e"))
			Path("f").write_text("")
			self.assertFalse(detect.dir_is_empty("f"))

	def test_shapes_and_modes(self):
		bench = self.bench("b")
		with chdir(bench):
			self.assertTrue(detect.looks_like_bench())
			self.assertFalse(detect.is_frappe_nix_bench())
			self.assertEqual(command.decide_mode("auto", False), "migrate")
			Path("flake.nix").write_text("{ }\n")
			with self.assertRaises(ui.Die) as e:
				command.decide_mode("auto", False)
			self.assertEqual(e.exception.code, 4)
			Path("flake.nix").write_text('{ inputs.frappe-nix.url = "x"; benchName = "acme"; }\n')
			Path("pyproject.toml").write_text("[tool.uv.workspace]\nmembers = []\n")
			self.assertTrue(detect.is_frappe_nix_bench())
			self.assertEqual(detect.existing_bench_name(), "")
			Path("flake.nix").write_text('{\n  frappe-nix = {\n    benchName = "acme";\n  };\n}\n')
			self.assertEqual(detect.existing_bench_name(), "acme")
			# The name now (python-devops-nix docs/conventions.md, Identity
			# options); a nested `name` is not it.
			Path("flake.nix").write_text(
				'{\n  inputs.frappe-nix.url = "x";\n  frappe-nix = {\n'
				'    app = { name = "inner"; };\n    siblings = [ { name = "erpnext"; } ];\n'
				'    # name = "comment";\n    name = "acme2";\n  };\n}\n'
			)
			self.assertEqual(detect.existing_bench_name(), "acme2")
			Path("flake.nix").write_text('{\n  frappe-nix = {\n    app = { name = "inner"; };\n  };\n}\n')
			self.assertEqual(detect.existing_bench_name(), "")
			with contextlib.redirect_stdout(io.StringIO()):
				self.assertEqual(command.decide_mode("auto", False), "migrate")

		skel = self.tmp / "skel"
		(skel / "apps").mkdir(parents=True)
		(skel / "sites").mkdir()
		with chdir(skel):
			self.assertTrue(detect.looks_like_empty_bench())
			with self.assertRaises(ui.Die) as e:
				command.decide_mode("auto", False)
			self.assertEqual(e.exception.code, 7)
			with self.assertRaises(ui.Die) as e:
				command.decide_mode("migrate", False)
			self.assertEqual(e.exception.code, 6)
			self.assertEqual(command.decide_mode("migrate", True), "migrate")
			with self.assertRaises(ui.Die) as e:
				command.decide_mode("init", False)
			self.assertEqual(e.exception.code, 2)

	def test_app_detection(self):
		app = self.tmp / "app"
		(app / "my_app").mkdir(parents=True)
		(app / "pyproject.toml").write_text('[project]\nname = "my_app"\n\n[tool.frappe-nix]\nschema = 1\n')
		with chdir(app):
			self.assertFalse(detect.looks_like_frappe_app())
			(app / "my_app/hooks.py").write_text("")
			self.assertTrue(detect.looks_like_frappe_app())
			self.assertTrue(detect.app_opted_in())
			with contextlib.redirect_stdout(io.StringIO()):
				self.assertEqual(command.decide_mode("auto", False), "app")
			Path("pyproject.toml").write_text("[project]\n")
			self.assertEqual(detect.dist_name("."), "")
			self.assertFalse(detect.app_opted_in())

	def test_frappe_version(self):
		ps = presets.parse(PRESETS)
		cases = (
			("b15", "15.42.0", None, ("version-15", "apps/frappe __version__ 15.42.0")),
			("b14", "14.9.0", None, ("UNSUPPORTED", "14.9.0")),
			("b9", "9.0", None, ("UNSUPPORTED", "9.0")),
			("b99", "99.0", None, None),
			(
				"bjson",
				"",
				{"frappe": {"resolution": {"branch": "version-16"}}},
				("version-16", "sites/apps.json"),
			),
			(
				"bnot",
				"",
				{"frappe": {"resolution": "not calculated", "version": "15.1"}},
				("version-15", "apps/frappe __version__ 15.1"),
			),
		)
		for name, version, apps_json, want in cases:
			bench = self.bench(name, version)
			if not version:
				(bench / "apps/frappe/frappe/__init__.py").write_text("")
			if apps_json is not None:
				(bench / "sites/apps.json").write_text(json.dumps(apps_json))
			with self.subTest(name=name), chdir(bench):
				self.assertEqual(detect.detect_frappe_version(ps), want)

		bench = self.bench("bdev", "16.0.0-dev")
		git("init", "-q", "-b", "develop", cwd=bench / "apps/frappe")
		git(
			"-c",
			"user.name=t",
			"-c",
			"user.email=t@t",
			"commit",
			"-q",
			"--allow-empty",
			"-m",
			"x",
			cwd=bench / "apps/frappe",
		)
		with chdir(bench):
			self.assertEqual(detect.detect_frappe_version(ps), ("develop", "apps/frappe branch"))
		git("checkout", "-q", "--detach", cwd=bench / "apps/frappe")
		git("branch", "-q", "-m", "develop", "version-16", cwd=bench / "apps/frappe")
		with chdir(bench):
			self.assertEqual(detect.detect_frappe_version(ps), ("version-16", "ref at apps/frappe HEAD"))

	def test_same_git_url(self):
		self.assertTrue(detect.same_git_url("https://x/a.git", "https://x/a/"))
		self.assertTrue(detect.same_git_url("https://x/a.git/", "https://x/a"))
		self.assertFalse(detect.same_git_url("", ""))


class TestTemplate(unittest.TestCase):
	def test_tokens_render_once_and_unknown_ones_stay(self):
		values = {"BENCH_NAME": "@SITE_NAME@", "SITE_NAME": "s|&\\1"}
		self.assertEqual(
			template.render_bytes(b"@BENCH_NAME@ @SITE_NAME@ @OTHER@ }@inputs", values),
			b"@SITE_NAME@ s|&\\1 @OTHER@ }@inputs",
		)

	def test_overrides_replace_the_quoted_token(self):
		self.assertEqual(
			template.render_overrides(b'override-dependencies = "@OVERRIDES@"\n', '["a|b"]'),
			b'override-dependencies = ["a|b"]\n',
		)

	def test_render_and_install(self):
		tmp = Path(tempfile.mkdtemp())
		self.addCleanup(subprocess.run, ["rm", "-rf", str(tmp)], check=False)
		src = tmp / "tpl"
		(src / "sub").mkdir(parents=True)
		(src / "flake.nix").write_text('name = "@BENCH_NAME@"; python = pkgs.@PYTHON@;\n')
		(src / "pyproject.toml").write_text('name = "@PROJECT_NAME@"\no = "@OVERRIDES@"\n')
		(src / "sub/keep").write_text("mine\n")
		(src / ".gitignore").write_text("env\n")
		for p in src.rglob("*"):
			p.chmod(0o555 if p.is_dir() else 0o444)
		self.addCleanup(lambda: [p.chmod(0o755) for p in src.rglob("*") if p.is_dir()] and None)
		st = run_state()
		st.version, st.name, st.project_name, st.site, st.template = (
			"version-15",
			"acme",
			"acme-bench",
			"s",
			str(src),
		)
		st.resolve_preset()
		dest = tmp / "bench"
		(dest / "sub").mkdir(parents=True)
		(dest / "sub/keep").write_text("theirs\n")
		with chdir(dest), contextlib.redirect_stdout(io.StringIO()) as out:
			template.install_template(st, keep=True)
			template.install_gitignore_block(st)
			template.install_gitignore_block(st)
		self.assertEqual((dest / "flake.nix").read_text(), 'name = "acme"; python = pkgs.python312;\n')
		self.assertEqual((dest / "pyproject.toml").read_text(), 'name = "acme-bench"\no = []\n')
		self.assertEqual((dest / "sub/keep").read_text(), "theirs\n")
		self.assertIn(". .gitignore already current", out.getvalue())
		self.assertEqual(
			(dest / ".gitignore").read_text(), f"{template.GITIGNORE_BEGIN}\nenv\n{template.GITIGNORE_END}\n"
		)

	def test_gitignore_block(self):
		b, e = template.GITIGNORE_BEGIN, template.GITIGNORE_END
		self.assertEqual(template.gitignore_with_block(None, "x"), f"{b}\nx\n{e}\n")
		self.assertEqual(template.gitignore_with_block("", "x"), f"{b}\nx\n{e}\n")
		self.assertEqual(template.gitignore_with_block("mine", "x"), f"mine\n{b}\nx\n{e}\n")
		self.assertEqual(
			template.gitignore_with_block(f"top\n{b}\nold\n{e}\nbottom", "new\nlines"),
			f"top\n{b}\nnew\nlines\n{e}\nbottom",
		)
		with self.assertRaisesRegex(Exception, "never closed"):
			template.gitignore_with_block(f"top\n{b}\nold\n", "x")

	def test_web_port_is_portOffsetFor(self):
		# 8662 for "bench": tests/migrate-classic.sh and modules/devenv.nix agree.
		self.assertEqual(template.frappe_web_port("bench"), 8662)

	def test_site_config(self):
		cur = {
			"gunicorn_workers": 17,
			"redis_queue": "redis://x",
			"workers": {"a": {"timeout": 1}},
			"webserver_port": 8000,
			"mariadb_root_password": "pw",
		}
		new = template.reconciled_site_config(cur, site="s.local", port=8123, keep_root_pw=False)
		self.assertEqual(list(new)[:3], ["default_site", "background_workers", "gunicorn_workers"])
		self.assertEqual(
			(new["default_site"], new["gunicorn_workers"], new["webserver_port"], new["socketio_port"]),
			("s.local", 17, 8123, 8123),
		)
		self.assertEqual((new["workers"], new["mariadb_root_password"]), ({"a": {"timeout": 1}}, ""))
		self.assertNotIn("redis_queue", new)
		kept = template.reconciled_site_config(cur, site="s", port=1, keep_root_pw=True)
		self.assertEqual(kept["mariadb_root_password"], "pw")
		self.assertEqual(
			template.deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 3}}), {"a": {"b": 3, "c": 2}}
		)


class TestPrompts(unittest.TestCase):
	def test_choose_by_number_or_name(self):
		options = [("develop", "develop — bleeding edge"), ("version-16", "version-16 — latest")]
		with (
			mock.patch("rich.prompt.Prompt.ask", side_effect=["9", "2"]),
			contextlib.redirect_stdout(io.StringIO()),
		):
			self.assertEqual(ui.choose("Frappe version:", options), "version-16")
		with (
			mock.patch("rich.prompt.Prompt.ask", return_value="develop"),
			contextlib.redirect_stdout(io.StringIO()),
		):
			self.assertEqual(ui.choose("Frappe version:", options), "develop")

	def test_choose_many(self):
		options = [("erpnext", "erpnext"), ("hrms", "hrms"), ("crm", "crm")]
		with (
			mock.patch("rich.prompt.Prompt.ask", return_value="1, crm"),
			contextlib.redirect_stdout(io.StringIO()),
		):
			self.assertEqual(ui.choose_many("Apps:", options), ["erpnext", "crm"])
		with mock.patch("rich.prompt.Prompt.ask", return_value=""), contextlib.redirect_stdout(io.StringIO()):
			self.assertEqual(ui.choose_many("Apps:", options), [])

	def test_confirm_is_no_without_a_terminal(self):
		with (
			mock.patch.object(ui, "has_tty", return_value=False),
			mock.patch("rich.prompt.Confirm.ask") as ask,
		):
			self.assertFalse(ui.confirm("Migrate?"))
			ask.assert_not_called()


class TestMain(unittest.TestCase):
	def run_main(self, *argv: str, cfg: dict | None = None) -> tuple[int, str, str]:
		out, err = io.StringIO(), io.StringIO()
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp, "cfg.json")
			Path(tmp, "presets.json").write_text(json.dumps(PRESETS))
			path.write_text(
				json.dumps(
					cfg
					or {
						"presets": f"{tmp}/presets.json",
						"template": tmp,
						"appTemplate": tmp,
						"sources": None,
					}
				)
			)
			with (
				mock.patch.dict(os.environ, {ENV: str(path)}),
				contextlib.redirect_stdout(out),
				contextlib.redirect_stderr(err),
				chdir(Path(tmp)),
			):
				code = command.main(list(argv))
		return code, out.getvalue(), err.getvalue()

	def test_help_and_usage_errors(self):
		code, out, _ = self.run_main("--help")
		self.assertEqual(code, 0)
		self.assertTrue(out.startswith("Usage: frappe-init"))
		code, _, err = self.run_main("--bogus")
		self.assertEqual(code, 1)
		self.assertIn("Usage: frappe-init", err)
		self.assertIn("unknown flag: --bogus", err)

	def test_a_bad_configuration_is_reported(self):
		code, _, err = self.run_main("--init", cfg={"presets": "/nonexistent"})
		self.assertEqual(code, 1)
		self.assertIn("missing key(s)", err)

	def test_scaffold_dry_run_needs_no_writes(self):
		code, out, err = self.run_main("--dry-run", "--version", "version-15", "new")
		self.assertEqual(code, 0, err)
		self.assertIn("scaffold bench 'new' (version-15 → python 3.12 / node 20) in new", out)


if __name__ == "__main__":
	unittest.main()
