import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.common import config
from frappe_nix_tools.common.report import ConfigError, EnvError
from helpers import run_cli

PYPROJECT = """\
[project]
name = "demo_app"

[tool.frappe-nix]
schema = 1
frappe-major = 16
profile = "recommended"
nightly-suites = ["node scripts/test-tables.ts", "echo two"]

[tool.frappe-nix.ssort]
enable = true

[[tool.frappe-nix.untested]]
target = "demo_app.api.legacy"
reason = "Kept for old kiosk bundles"
"""

# An org profile layer for the merge-order tests: fictitious values only.
ORG_PROFILE = """\
schema = 1
name = "example-org"
extends = "recommended@1.0"

[org]
publisher = "Example Org"
license = "MIT"

[python-lint]
line-length = 100
ignore = ["E501", "W191", "E731"]

[tests.coverage]
target = 90

[[retire]]
paths = [".github/workflows/check.yml"]
module = "ci"
"""


def app(**table):
	return {
		"project": {"name": "demo_app"},
		"tool": {"frappe-nix": {"schema": 1, "frappe-major": 16, **table}},
	}


def code_of(fn):
	try:
		fn()
	except (ConfigError, EnvError) as e:
		return e.code, str(e)
	return 0, ""


class TestResolve(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.root = Path(self.tmp.name)
		profile = self.root / ".standards-profile"
		profile.mkdir()
		(profile / "profile.toml").write_text(ORG_PROFILE)

	def tearDown(self):
		self.tmp.cleanup()

	def resolve(self, **table):
		return config.resolve_doc(app(**table), root=self.root)

	def test_not_opted_in(self):
		with self.assertRaises(config.NotOptedIn) as ctx:
			config.resolve_doc({"project": {"name": "demo_app"}}, root=self.root)
		self.assertEqual(ctx.exception.code, 2)
		self.assertIn("--standards", str(ctx.exception))

	def test_absent_profile_is_minimal(self):
		r = self.resolve()
		self.assertEqual(r.profile, {"name": "minimal", "source": "builtin", "rev": ""})
		self.assertEqual([m for m, on in r.modules.items() if on], ["dev-shell"])
		# A module turned on over minimal gets the schema defaults: the recommended values.
		r = self.resolve(**{"python-lint": {"enable": True}})
		self.assertTrue(r.modules["python-lint"])
		self.assertEqual(r.cfg["python-lint"]["select"][:3], ["F", "E", "W"])

	def test_recommended_is_the_newest_snapshot(self):
		plain, pinned = self.resolve(profile="recommended"), self.resolve(profile="recommended@1.0")
		self.assertEqual(plain.cfg | {"profile": None}, pinned.cfg | {"profile": None})
		self.assertEqual(plain.modules, pinned.modules)
		self.assertEqual(plain.profile, pinned.profile)
		self.assertEqual(plain.profile["name"], "recommended@1.0")

	def test_unknown_snapshot_is_exit_2(self):
		code, message = code_of(lambda: self.resolve(profile="recommended@9.9"))
		self.assertEqual(code, 2)
		self.assertIn("recommended@9.9", message)

	def test_merge_order(self):
		r = self.resolve(
			profile="./.standards-profile",
			**{
				"python-lint": {"line-length": 120, "select": ["E"]},
				"tests": {"coverage": {"raise-margin": 0}},
			},
		)
		# A value set in all three layers resolves to the app's.
		self.assertEqual(r.cfg["python-lint"]["line-length"], 120)
		self.assertEqual(r.source("python-lint.line-length"), "app")
		# A table merges key by key: the org's target, the app's margin, the built-in's floor.
		self.assertEqual(
			r.cfg["tests"]["coverage"], {"enable": True, "target": 90, "raise-margin": 0, "initial-floor": 0}
		)
		self.assertEqual(r.source("tests.coverage.target"), "org:./.standards-profile")
		self.assertEqual(r.source("tests.coverage.initial-floor"), "builtin:recommended@1.0")
		# An array replaces: the app's select, the org's ignore (not a union with the built-in's).
		self.assertEqual(r.cfg["python-lint"]["select"], ["E"])
		self.assertEqual(r.cfg["python-lint"]["ignore"], ["E501", "W191", "E731"])
		# Org values, the copyright fallback, and profile-only lists from the org profile.
		self.assertEqual(
			(r.cfg["org"]["publisher"], r.cfg["org"]["copyright-holder"]), ("Example Org", "Example Org")
		)
		self.assertEqual(r.cfg["retire"], [{"paths": [".github/workflows/check.yml"], "module": "ci"}])
		self.assertEqual(r.profile, {"name": "example-org", "source": "./.standards-profile", "rev": ""})

	def test_defaults_are_marked(self):
		r = self.resolve(profile="recommended")
		self.assertEqual(r.source("tests.setup"), "default")
		self.assertEqual(r.cfg["tests"]["setup"], [])
		self.assertEqual(r.cfg["integration-branch"], "develop")
		self.assertEqual(r.cfg["site"], "demo-app.localhost")
		r = self.resolve(profile="recommended", releases={"branching": "main+tags"})
		self.assertEqual(r.cfg["integration-branch"], "main")
		r = self.resolve(profile="recommended", **{"integration-branch": "trunk"})
		self.assertEqual(r.cfg["integration-branch"], "trunk")

	def test_app_only_key_in_a_profile_is_exit_2(self):
		for text in (
			"frappe-major = 16\n" + ORG_PROFILE,
			ORG_PROFILE + "[typescript]\nbrowser = false\n",
			ORG_PROFILE + '[js.oxlint]\nignore = ["x"]\n',
		):
			with self.subTest(text=text[-40:]):
				(self.root / ".standards-profile" / "profile.toml").write_text(text)
				code, message = code_of(lambda: self.resolve(profile="./.standards-profile"))
				self.assertEqual(code, 2)
				self.assertIn("app-only key", message)

	def test_profile_only_key_in_the_app_is_exit_2(self):
		for table in ({"extends": "recommended"}, {"retire": []}, {"name": "x"}):
			with self.subTest(table=table):
				code, message = code_of(lambda: self.resolve(**table))
				self.assertEqual(code, 2)
				self.assertIn("profile-only key", message)

	def test_unmet_module_dependency_names_both(self):
		code, message = code_of(lambda: self.resolve(profile="recommended", commits={"enable": False}))
		self.assertEqual(code, 2)
		self.assertIn("releases", message)
		self.assertIn("commits", message)
		code, message = code_of(lambda: self.resolve(readme={"enable": True}))
		self.assertEqual(code, 2)
		self.assertIn("readme needs module listing", message)
		# tool = "none" is the same as enable = false, needs included.
		code, message = code_of(
			lambda: self.resolve(profile="recommended", releases={"tool": "none"}, ci={"enable": True})
		)
		self.assertEqual(code, 0, message)
		self.assertFalse(self.resolve(profile="recommended", js={"tool": "none"}).modules["js"])

	def test_dev_shell_is_required(self):
		self.assertEqual(code_of(lambda: self.resolve(**{"dev-shell": {"enable": False}}))[0], 2)

	def test_schema_errors_are_exit_2(self):
		for table in (
			{"siblings": [1]},
			{"frappe-major": "16"},
			{"python-lint": {"indent-style": "tabs"}},
			{"nope": True},
		):
			with self.subTest(table=table):
				self.assertEqual(code_of(lambda: self.resolve(**table))[0], 2)
		del_major = app()
		del del_major["tool"]["frappe-nix"]["frappe-major"]
		self.assertEqual(code_of(lambda: config.resolve_doc(del_major, root=self.root))[0], 2)

	def test_object_siblings_and_known_apps_validate(self):
		r = self.resolve(
			siblings=["erpnext", {"repo": "example/shared_lib", "branch": "main"}],
			**{"known-apps": {"example/shared_lib": {"repo": "example/shared_lib", "range": ">=2,<3"}}},
		)
		self.assertEqual(r.cfg["known-apps"]["example/shared_lib"]["range"], ">=2,<3")

	def test_github_only_modules_off_on_other_hosts(self):
		r = config.resolve_doc(
			app(profile="recommended", repo="gitlab.example.org/group/demo_app"), root=self.root
		)
		self.assertEqual(r.repo_host, "gitlab.example.org")
		self.assertEqual(r.cfg["repo"], "group/demo_app")
		for m in config.GITHUB_ONLY:
			self.assertFalse(r.modules[m], m)
		self.assertTrue(r.modules["commits"] and r.modules["python-lint"])
		self.assertTrue(r.notices)
		code, message = code_of(
			lambda: config.resolve_doc(
				app(profile="recommended", repo="gitlab.example.org/g/a", ci={"enable": True}), root=self.root
			)
		)
		self.assertEqual(code, 2)
		self.assertIn("GitHub-only", message)

	def test_repo_from_origin_and_owner(self):
		subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
		self.assertNotIn("repo", self.resolve().cfg)
		self.assertEqual(self.resolve(org={"github-owner": "example"}).cfg["repo"], "example/demo_app")
		subprocess.run(
			["git", "-C", str(self.root), "remote", "add", "origin", "git@gitlab.com:group/sub/demo_app.git"],
			check=True,
		)
		r = self.resolve(profile="recommended")
		self.assertEqual((r.repo_host, r.cfg["repo"]), ("gitlab.com", "group/sub/demo_app"))
		self.assertFalse(r.modules["ci"])

	def test_requires_frappe_nix(self):
		profile = self.root / ".standards-profile" / "profile.toml"
		profile.write_text(
			ORG_PROFILE.replace(
				'extends = "recommended@1.0"\n',
				'extends = "recommended@1.0"\nrequires-frappe-nix = ">=1.2,<2"\n',
			)
		)
		self.assertEqual(code_of(lambda: self.resolve(profile="./.standards-profile"))[0], 3)
		profile.write_text(
			ORG_PROFILE.replace(
				'extends = "recommended@1.0"\n', 'extends = "recommended@1.0"\nrequires-frappe-nix = ">=0"\n'
			)
		)
		self.assertEqual(code_of(lambda: self.resolve(profile="./.standards-profile"))[0], 0)

	def test_org_profile_rules(self):
		profile = self.root / ".standards-profile" / "profile.toml"
		profile.write_text(ORG_PROFILE.replace('extends = "recommended@1.0"\n', ""))
		code, message = code_of(lambda: self.resolve(profile="./.standards-profile"))
		self.assertIn("extends is required", message)
		self.assertEqual(code_of(lambda: self.resolve(profile="./missing"))[0], 2)
		self.assertEqual(code_of(lambda: self.resolve(profile="./../outside"))[0], 2)
		# A flake URL is read from the locked standards-profile input, which must exist.
		(self.root / "flake.lock").write_text(
			json.dumps({"version": 7, "root": "root", "nodes": {"root": {}}})
		)
		code, message = code_of(lambda: self.resolve(profile="github:example/profile/v1"))
		self.assertEqual(code, 2)
		self.assertIn("not locked", message)

	def test_profile_dir_override(self):
		r = config.resolve_doc(
			app(profile="github:example/profile/v1"),
			root=self.root,
			profile_dir=self.root / ".standards-profile",
		)
		self.assertEqual(r.profile["source"], "github:example/profile/v1")
		self.assertEqual(r.cfg["org"]["publisher"], "Example Org")

	def test_get(self):
		r = self.resolve(profile="recommended")
		self.assertIs(r.get("modules.ssort"), False)
		self.assertEqual(r.get("tests.coverage.target"), 80)
		self.assertEqual(r.get("profile.name"), "recommended@1.0")
		self.assertIsNone(r.get("releases.bootstrap-sha"))
		for key in ("nonsense", "tests.nonsense", "modules.nonsense", "profile.nonsense"):
			with self.subTest(key=key), self.assertRaises(ConfigError):
				r.get(key)


class TestConfigCommand(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.path = Path(self.tmp.name) / "pyproject.toml"
		self.path.write_text(PYPROJECT)

	def tearDown(self):
		self.tmp.cleanup()

	def config(self, *args):
		return run_cli("config", *args, "--pyproject", str(self.path))

	def test_modules_print_the_resolved_boolean(self):
		self.assertEqual(self.config("modules.ssort"), (0, "true\n", ""))
		self.assertEqual(self.config("modules.icons"), (0, "false\n", ""))

	def test_list_one_item_per_line(self):
		self.assertEqual(self.config("nightly-suites"), (0, "node scripts/test-tables.ts\necho two\n", ""))

	def test_scalars_defaults_and_unset(self):
		self.assertEqual(self.config("frappe-major")[1], "16\n")
		self.assertEqual(self.config("tests.coverage.target")[1], "80\n")
		self.assertEqual(self.config("shell-checks"), (0, "", ""))
		self.assertEqual(self.config("releases.bootstrap-sha"), (0, "", ""))
		self.assertEqual(self.config("typescript.browser")[1], "true\n")

	def test_tables_print_as_json(self):
		code, out, _ = self.config("untested")
		self.assertEqual(code, 0)
		self.assertEqual(
			json.loads(out), {"target": "demo_app.api.legacy", "reason": "Kept for old kiosk bundles"}
		)
		code, out, _ = self.config("python-lint.ignore", "--json")
		self.assertEqual(json.loads(out), ["E501", "W191"])

	def test_github_output(self):
		out = Path(self.tmp.name) / "out"
		out.write_text("before=1\n")
		with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}):
			self.assertEqual(self.config("--github-output", "modules")[0], 0)
		lines = out.read_text().splitlines()
		self.assertEqual(lines[0], "before=1")
		self.assertEqual(len(lines), 1 + len(config.MODULES))
		self.assertIn("ssort=true", lines)
		self.assertIn("icons=false", lines)
		with mock.patch.dict(os.environ, {}, clear=False):
			os.environ.pop("GITHUB_OUTPUT", None)
			self.assertEqual(self.config("--github-output", "modules")[0], 3)
		self.assertEqual(self.config("--github-output", "cfg")[0], 2)

	def test_unknown_key_exits_2(self):
		code, _, err = self.config("nope")
		self.assertEqual(code, 2)
		self.assertIn("no key 'nope'", err)
		self.assertEqual(self.config()[0], 2)

	def test_not_opted_in_exits_2(self):
		self.path.write_text('[project]\nname = "demo_app"\n')
		code, _, err = self.config("modules.ssort")
		self.assertEqual(code, 2)
		self.assertIn("has not opted in", err)

	def test_missing_pyproject_exits_3(self):
		self.path.unlink()
		self.assertEqual(self.config("site")[0], 3)

	def test_default_is_the_nearest_pyproject_in_the_work_tree(self):
		outer = Path(self.tmp.name)
		subprocess.run(["git", "-C", str(outer), "init", "-q"], check=True)
		app_dir = outer / "tests" / "fixtures" / "app"
		(app_dir / "pkg").mkdir(parents=True)
		self.path.rename(app_dir / "pyproject.toml")
		for cwd in (app_dir, app_dir / "pkg"):
			self.assertEqual(run_cli("config", "frappe-major", cwd=cwd), (0, "16\n", ""))
		# Nothing at or above tests/ up to the root: the root's, which does not exist.
		self.assertEqual(run_cli("config", "frappe-major", cwd=outer / "tests")[0], 3)

	def test_unreadable_pyproject_exits_3(self):
		self.assertEqual(run_cli("config", "site", "--pyproject", self.tmp.name)[0], 3)

	def test_profile_lists_print(self):
		# retire, replace-apps and extra-files are the org profile's alone (§8.4), so the
		# app schema lacks them, but they are resolved values all the same (§5.13).
		profile = Path(self.tmp.name) / "prof"
		profile.mkdir()
		(profile / "profile.toml").write_text(
			ORG_PROFILE + '\n[[replace-apps]]\nfrom = "old_app"\nto = "new_app"\n'
		)
		self.path.write_text(PYPROJECT.replace('profile = "recommended"', 'profile = "./prof"'))
		code, out, err = self.config("replace-apps", "--json")
		self.assertEqual((code, err), (0, ""))
		self.assertEqual(json.loads(out), [{"from": "old_app", "to": "new_app"}])
		code, out, _ = self.config("retire", "--json")
		self.assertEqual(json.loads(out), [{"paths": [".github/workflows/check.yml"], "module": "ci"}])
		self.assertEqual(self.config("extra-files", "--json"), (0, "[]\n", ""))
		self.assertEqual(self.config("replace-apps.from")[0], 2)
		# A built-in profile has none of them.
		self.path.write_text(PYPROJECT)
		self.assertEqual(self.config("replace-apps", "--json"), (0, "[]\n", ""))


# The spellings of the table the dev shell's line match (lib/standards/shell.nix) and TOML
# disagree on, beside the ones they agree on; tests/standards/fixtures/optin has the same
# cases for the Nix side. None: opted in; otherwise the error the config command prints.
TABLE = "schema = 1\nfrappe-major = 16\n"
SPELLINGS = {
	"header": ("[tool.frappe-nix]\n" + TABLE, None),
	"indented": ("  [tool.frappe-nix]\n" + TABLE, None),
	"subtable only": ('[[tool.frappe-nix.untested]]\ntarget = "a.b"\nreason = "r"\n', "schema"),
	"quoted key": ('[tool."frappe-nix"]\n' + TABLE, "write the table as a [tool.frappe-nix] header"),
	"spaces in brackets": ("[ tool.frappe-nix ]\n" + TABLE, "write the table as a [tool.frappe-nix] header"),
	"dotted keys": (
		"[tool]\nfrappe-nix.schema = 1\nfrappe-nix.frappe-major = 16\n",
		"write the table as a [tool.frappe-nix] header",
	),
	"inline table": (
		"[tool]\nfrappe-nix = { schema = 1, frappe-major = 16 }\n",
		"write the table as a [tool.frappe-nix] header",
	),
	"in a string": ('[tool.notes]\ntext = """\n[tool.frappe-nix]\n"""\n', "inside a multi-line string"),
	"commented": ("# [tool.frappe-nix]\n", "has not opted in"),
}


class TestOptInSpelling(unittest.TestCase):
	def test_the_shell_and_the_tools_agree_or_it_is_exit_2(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "pyproject.toml"
			for name, (table, error) in SPELLINGS.items():
				with self.subTest(name):
					path.write_text('[project]\nname = "demo_app"\n\n' + table)
					code, out, err = run_cli("config", "frappe-major", "--pyproject", str(path))
					if error is None:
						self.assertEqual((code, out, err), (0, "16\n", ""))
					else:
						self.assertEqual(code, 2)
						self.assertIn(error, err)


if __name__ == "__main__":
	unittest.main()
