"""``ironclad sync``: the N3 acceptance cases of docs/ironclad/spec.md §7, on throwaway apps."""

import json
import os
import tomllib
import unittest
from typing import ClassVar
from unittest import mock

from scaffold_helpers import AppCase, git


class TestRoundTrip(AppCase):
	def test_sync_then_check_is_clean_and_idempotent(self):
		self.synced()
		before = self.snapshot()
		code, _, err = self.ironclad("sync", "--write")
		self.assertEqual(code, 0, err)
		self.assertEqual(before, self.snapshot(), "a second --sync changed bytes")

	def test_new_app_gets_the_managed_files(self):
		self.synced()
		for path in (
			"flake.nix",
			".envrc",
			".gitignore",
			".editorconfig",
			".pre-commit-config.yaml",
			"committed.toml",
			"tools/pyproject.toml",
			"package.json",
			".oxlintrc.json",
			".oxfmtrc.jsonc",
			"release-please-config.json",
			".release-please-manifest.json",
			".git-blame-ignore-revs",
			"nix/node-locks/frappe/ui/yarn.lock",
		):
			self.assertTrue((self.root / path).is_file(), path)
		self.assertFalse((self.root / "tsconfig.json").exists(), "no TS project, no solution")
		self.assertTrue(self.read(".envrc").startswith("# ironclad:managed"))

	def test_editing_a_managed_file_is_drift_with_a_diff(self):
		self.synced()
		self.write(
			"committed.toml",
			self.read("committed.toml").replace("subject_length = 100", "subject_length = 72"),
		)
		code, out = self.check()
		self.assertEqual(code, 1, out)
		self.assertIn("committed.toml (whole)", out)
		self.assertIn("--- current", out)
		self.assertIn("-subject_length = 72", out)
		self.assertIn("+subject_length = 100", out)

	def test_check_json_format(self):
		self.synced()
		self.write(".envrc", "use flake\n")
		code, out, _ = self.ironclad("sync", "--check", "--format", "json")
		self.assertEqual(code, 1)
		doc = json.loads(out)
		self.assertEqual(doc["status"], "drift")
		self.assertEqual([f["path"] for f in doc["files"]], [".envrc"])

	def test_only(self):
		self.synced()
		self.write(".envrc", "use flake\n")
		self.write("committed.toml", "x = 1\n")
		code, _, _ = self.ironclad("sync", "--write", "--only", ".envrc")
		self.assertEqual(code, 0)
		self.assertTrue(self.read(".envrc").startswith("# ironclad:managed"))
		self.assertEqual(self.read("committed.toml"), "x = 1\n")

	def test_not_an_app_is_exit_3(self):
		(self.root / "demo_app" / "hooks.py").unlink()
		code, _ = self.check()
		self.assertEqual(code, 3)


class TestLocalRegions(AppCase):
	def test_region_content_survives(self):
		self.synced()
		text = self.read(".pre-commit-config.yaml").replace(
			"  # ironclad:local-begin repos\n",
			"  # ironclad:local-begin repos\n  - repo: https://example.com/hooks\n    rev: v1.0.0\n    hooks:\n      - id: mine\n",
		)
		self.write(".pre-commit-config.yaml", text)
		code, out = self.check()
		self.assertEqual(code, 0, out)
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		self.assertIn("      - id: mine\n", self.read(".pre-commit-config.yaml"))

	def test_malformed_region_is_exit_2(self):
		self.synced()
		self.write(
			".editorconfig", self.read(".editorconfig").replace("# ironclad:local-end editorconfig\n", "")
		)
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("never closed", out)

	def test_unknown_region_is_exit_2(self):
		self.synced()
		self.write(
			".editorconfig", self.read(".editorconfig") + "# ironclad:local-begin x\n# ironclad:local-end x\n"
		)
		self.assertEqual(self.check()[0], 2)

	def test_region_may_not_redefine_a_managed_hook(self):
		self.synced()
		text = self.read(".pre-commit-config.yaml").replace(
			"  # ironclad:local-begin repos\n",
			"  # ironclad:local-begin repos\n  - repo: local\n    hooks:\n      - id: oxlint\n",
		)
		self.write(".pre-commit-config.yaml", text)
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("hook id oxlint", out)


class TestAppOwnedKeys(AppCase):
	def test_app_owned_keys_survive(self):
		self.synced()
		py = self.read("pyproject.toml").replace(
			"[tool.ruff]\n", '[tool.ruff]\nextend-exclude = ["vendor"]\n'
		)
		self.write("pyproject.toml", py)
		pkg = json.loads(self.read("package.json"))
		pkg["scripts"]["codegen"] = "node scripts/codegen.ts"
		pkg["dependencies"] = {"left-pad": "1.3.0"}
		self.write("package.json", json.dumps(pkg, indent=2) + "\n")
		self.fake_yarn_lock()
		code, out = self.check()
		self.assertEqual(code, 0, out)
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		self.assertIn('extend-exclude = ["vendor"]', self.read("pyproject.toml"))
		pkg = json.loads(self.read("package.json"))
		self.assertEqual(pkg["scripts"]["codegen"], "node scripts/codegen.ts")
		self.assertEqual(pkg["dependencies"], {"left-pad": "1.3.0"})

	def test_extra_ruff_ignore_is_exit_2(self):
		self.synced()
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace('ignore = ["E501", "W191"]', 'ignore = ["F401"]'),
		)
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("F401", out)
		code, _, err = self.ironclad("sync", "--write")
		self.assertEqual(code, 2, err)

	def test_forbidden_keys_are_exit_2(self):
		self.synced()
		base = self.read("pyproject.toml")
		for extra in (
			"\n[tool.poetry]\nname = 'x'\n",
			"\n[tool.ty.overrides]\nx = 1\n",
			'\n[tool.ruff.lint.per-file-ignores]\n"demo_app/**" = ["F401"]\n',
		):
			with self.subTest(extra=extra):
				self.write("pyproject.toml", base + extra)
				self.assertEqual(self.check()[0], 2)

	def test_package_wide_ignores_in_any_spelling_are_exit_2(self):
		"""ruff's globs let ``*`` cross ``/`` and match a bare pattern against the basename,
		and it reads the pre-0.2 [tool.ruff] spellings and extend-per-file-ignores too."""
		self.synced()
		base = self.read("pyproject.toml")
		for extra in (
			'\n[tool.ruff.lint.per-file-ignores]\n"demo_app/*" = ["F401"]\n',
			'\n[tool.ruff.lint.per-file-ignores]\n"*.py" = ["F401"]\n',
			'\n[tool.ruff.lint.per-file-ignores]\n"**" = ["F"]\n',
			'\n[tool.ruff.lint.per-file-ignores]\n"!demo_app/tests/*" = ["E402"]\n',
			'\n[tool.ruff.lint.extend-per-file-ignores]\n"**" = ["F401"]\n',
			'\n[tool.ruff.per-file-ignores]\n"demo_app/**" = ["E402"]\n',
			'\n[tool.ruff.extend-per-file-ignores]\n"*" = ["ALL"]\n',
		):
			with self.subTest(extra=extra):
				self.write("pyproject.toml", base + extra)
				code, out = self.check()
				self.assertEqual(code, 2, out)
				self.assertIn("package-wide", out)

	def test_narrow_ignores_are_the_apps(self):
		self.synced()
		base = self.read("pyproject.toml")
		for extra in (
			'\n[tool.ruff.lint.per-file-ignores]\n"__init__.py" = ["F401"]\n',
			'\n[tool.ruff.lint.per-file-ignores]\n"demo_app/overrides/*" = ["F401"]\n',
			'\n[tool.ruff.lint.per-file-ignores]\n"**/tests/**" = ["E402"]\n',
			'\n[tool.ruff.lint.per-file-ignores]\n"**" = ["F841"]\n',
		):
			with self.subTest(extra=extra):
				self.write("pyproject.toml", base + extra)
				code, out = self.check()
				self.assertEqual(code, 0, out)

	def test_locked_oxlint_rules_in_any_spelling_are_exit_2(self):
		self.synced()
		base = self.read("pyproject.toml")
		for rule in (
			"typescript/no-explicit-any",
			"@typescript-eslint/no-explicit-any",
			"typescript-eslint/ban-ts-comment",
			"no-explicit-any",
			"consistent-type-imports",
		):
			with self.subTest(rule=rule):
				self.write(
					"pyproject.toml",
					base.replace(
						"[tool.ironclad]\n",
						f'[tool.ironclad]\noxlint.overrides = [{{ files = ["**/*.ts"], rules = {{ "{rule}" = "off" }} }}]\n',
					),
				)
				code, out = self.check()
				self.assertEqual(code, 2, out)
				self.assertIn("may not change", out)

	def test_bench_app_dependency_is_exit_1(self):
		self.synced()
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace("dependencies = []", 'dependencies = ["frappe>=16"]'),
		)
		code, out = self.check()
		self.assertEqual(code, 1, out)
		self.assertIn("must not name frappe", out)

	def test_eslint_and_reserved_scripts_are_exit_2(self):
		self.synced()
		for key, value in (("devDependencies", {"eslint": "^9"}), ("scripts", {"ironclad:x": "true"})):
			with self.subTest(key=key):
				pkg = json.loads(self.read("package.json"))
				pkg[key] = {**pkg.get(key, {}), **value}
				self.write("package.json", json.dumps(pkg))
				self.assertEqual(self.check()[0], 2)

	def test_stray_build_script_is_exit_2(self):
		self.synced()
		pkg = json.loads(self.read("package.json"))
		pkg["scripts"]["build"] = "echo hi"
		self.write("package.json", json.dumps(pkg))
		self.assertEqual(self.check()[0], 2)


class TestFloors(AppCase):
	def test_raised_floors_are_kept_and_lowered_ones_are_drift(self):
		self.synced()
		pre = self.read(".pre-commit-config.yaml")
		pkg = json.loads(self.read("package.json"))
		raised_pkg = {**pkg, "devDependencies": {**pkg["devDependencies"], "oxlint": "^1.99.0"}}
		self.write(".pre-commit-config.yaml", pre.replace("rev: 0.17.0", "rev: 0.18.0"))
		self.write("package.json", json.dumps(raised_pkg, indent="\t") + "\n")
		self.fake_locks({"ruff": "0.17.0"})
		code, out = self.check()
		self.assertEqual(code, 0, out)
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		self.assertIn("rev: 0.18.0", self.read(".pre-commit-config.yaml"))
		self.assertEqual(json.loads(self.read("package.json"))["devDependencies"]["oxlint"], "^1.99.0")

		for name, apply in (
			(
				"ssort",
				lambda: self.write(".pre-commit-config.yaml", pre.replace("rev: 0.17.0", "rev: 0.16.0")),
			),
			(
				"oxlint",
				lambda: self.write(
					"package.json",
					json.dumps({**pkg, "devDependencies": {**pkg["devDependencies"], "oxlint": "^1.50.0"}}),
				),
			),
			("ruff", lambda: self.fake_locks({"ruff": "0.15.0"})),
		):
			with self.subTest(name=name):
				self.synced()
				apply()
				code, out = self.check()
				self.assertEqual(code, 1, out)

	def test_non_registry_specifier_is_left_alone(self):
		self.synced()
		pkg = json.loads(self.read("package.json"))
		url = "https://codeload.github.com/Avunu/x/tar.gz/" + "c" * 40
		pkg["devDependencies"]["oxfmt"] = url
		self.write("package.json", json.dumps(pkg))
		self.fake_yarn_lock()
		self.assertEqual(self.check()[0], 0)


class TestInitPy(AppCase):
	def test_trailing_comment_form_becomes_the_block(self):
		self.write("demo_app/__init__.py", '__version__ = "16.2.3"  # x-release-please-version\n')
		self.commit()
		self.synced()
		self.assertEqual(
			self.read("demo_app/__init__.py"),
			'# x-release-please-start-version\n__version__ = "16.2.3"\n# x-release-please-end\n',
		)

	def test_code_outside_the_block_is_reported(self):
		self.write("demo_app/__init__.py", 'import os\n__version__ = "16.0.0"\n')
		self.commit()
		code, _, err = self.ironclad("sync", "--write")
		self.assertEqual(code, 1, err)
		self.assertIn("import os", err)

	def test_first_sync_aligns_versions(self):
		self.write("demo_app/__init__.py", '__version__ = "0.0.1"\n')
		self.write("package.json", json.dumps({"name": "demo-app", "version": "1.0.0", "private": True}))
		self.commit()
		self.synced()
		self.assertEqual(json.loads(self.read("package.json"))["version"], "0.0.1")
		self.assertEqual(json.loads(self.read(".release-please-manifest.json")), {".": "0.0.1"})
		_code, out, _ = self.ironclad("compat")
		self.assertNotIn("C5", out)


def _strip(text: str) -> str:
	"""A tsconfig without its header comment."""
	return "\n".join(line for line in text.splitlines() if not line.startswith("//"))


class TestDiscovery(AppCase):
	def test_first_scripts_ts_is_drift(self):
		self.synced()
		self.write("scripts/x.ts", "export const x = 1;\n")
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 1, out)
		self.assertIn("tsconfig.scripts.json (whole): missing", out)
		self.assertIn("tsconfig.json (whole): missing", out)

	def test_desk_web_and_unchecked(self):
		self.write("demo_app/demo_app/doctype/thing/thing.js", "frappe.ui.form.on('Thing', {});\n")
		self.write("demo_app/public/js/web/form.js", "frappe.ready(() => {});\n")
		self.write("demo_app/public/js/legacy.js", "var x = 1;\n")
		self.write("demo_app/www/page.js", "frappe.ready(() => {});\n")
		self.commit()
		self.synced()
		desk = json.loads(_strip(self.read("tsconfig.desk.json")))
		web = json.loads(_strip(self.read("tsconfig.web.json")))
		ox = json.loads(self.read(".oxlintrc.json"))
		desk_files = ox["overrides"][2]["files"]
		self.assertIn("demo_app/public/js/web/form.js", desk_files)

		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				"[tool.ironclad]\n",
				'[tool.ironclad]\ntypescript = { web-include = ["demo_app/public/js/web/**"] }\n'
				'unchecked-js = [{ path = "demo_app/public/js/legacy.js", reason = "typed in a later PR" }]\n',
			),
		)
		self.commit()
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		desk = json.loads(_strip(self.read("tsconfig.desk.json")))
		web = json.loads(_strip(self.read("tsconfig.web.json")))
		ox = json.loads(self.read(".oxlintrc.json"))
		self.assertIn("demo_app/public/js/web/**", desk["exclude"])
		self.assertIn("demo_app/public/js/legacy.js", desk["exclude"])
		self.assertIn("demo_app/public/js/web/**", web["include"])
		desk_files, web_files = ox["overrides"][2]["files"], ox["overrides"][3]["files"]
		self.assertNotIn("demo_app/public/js/web/form.js", desk_files)
		self.assertIn("demo_app/public/js/web/form.js", web_files)
		self.assertIn("demo_app/www/page.js", web_files)

	def test_untracked_unchecked_js_is_c9(self):
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				"[tool.ironclad]\n",
				'[tool.ironclad]\nunchecked-js = [{ path = "demo_app/public/js/gone.js", reason = "typed in a later PR" }]\n',
			),
		)
		self.commit()
		self.synced()
		code, out, _ = self.ironclad("compat")
		self.assertEqual(code, 1)
		self.assertIn("C9", out)

	def test_typescript_exclude_of_a_source_is_exit_2(self):
		self.write("demo_app/public/js/a.js", "var a = 1;\n")
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				"[tool.ironclad]\n", '[tool.ironclad]\ntypescript = { exclude = ["demo_app/public/**"] }\n'
			),
		)
		self.commit()
		self.assertEqual(self.check()[0], 2)

	def test_spa_owning_tsconfig(self):
		self.write("tsconfig.json", '{ "compilerOptions": {} }\n')
		self.write("demo_app/public/js/app/main.ts", "export {};\n")
		self.write("demo_app/public/js/bundle.ts", "export {};\n")
		self.write("vite.config.ts", "export default {};\n")
		self.write(
			"package.json",
			json.dumps({"name": "demo-app", "version": "16.0.0", "scripts": {"build": "vite build"}}),
		)
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				"[tool.ironclad]\n",
				"[tool.ironclad]\n"
				'typescript = { spa = [{ root = ".", include = ["demo_app/public/js/app/**"], tsconfig = "tsconfig.json",'
				' check = "vue-tsc --noEmit -p tsconfig.json" }] }\n',
			),
		)
		self.commit()
		self.synced()
		self.assertEqual(self.read("tsconfig.json"), '{ "compilerOptions": {} }\n')
		self.assertTrue((self.root / "tsconfig.ironclad.json").is_file())
		pkg = json.loads(self.read("package.json"))
		self.assertEqual(
			pkg["scripts"]["typecheck"],
			"tsc --build tsconfig.ironclad.json && vue-tsc --noEmit -p tsconfig.json",
		)
		self.assertTrue(pkg["scripts"]["build"].endswith("&& node scripts/ironclad-vite-register.mjs"))
		browser = json.loads(_strip(self.read("tsconfig.browser.json")))
		self.assertIn("demo_app/public/js/app/**", browser["exclude"])

		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace("typescript = { spa", "typescript = { browser = false, spa"),
		)
		self.commit()
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		self.assertFalse((self.root / "tsconfig.browser.json").exists())
		self.assertFalse((self.root / "tsconfig.ironclad.json").exists())

	def test_generated_globs_reach_every_tool(self):
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				"[tool.ironclad]\n", '[tool.ironclad]\ngenerated = ["demo_app/public/js/generated/**"]\n'
			),
		)
		self.commit()
		self.synced()
		pre = self.read(".pre-commit-config.yaml")
		self.assertIn("|demo_app/public/js/generated/.*$)\n", pre.split("repos:")[0])
		self.assertEqual(
			pre.count("demo_app/public/js/generated/.*$"), 2, "global and validate_copyright excludes"
		)
		self.assertIn('"demo_app/public/js/generated/**",', self.read(".oxfmtrc.jsonc"))
		self.assertIn(
			"demo_app/public/js/generated/**", json.loads(self.read(".oxlintrc.json"))["ignorePatterns"]
		)

	def test_scss_brings_stylelint(self):
		self.write("demo_app/public/scss/x.scss", "a { color: red; }\n")
		self.commit()
		self.synced()
		self.assertEqual(
			json.loads(self.read(".stylelintrc.json"))["ignoreFiles"], ["demo_app/public/dist/**"]
		)
		pkg = json.loads(self.read("package.json"))
		self.assertEqual(pkg["scripts"]["lint"], 'oxlint && stylelint "demo_app/public/**/*.scss"')
		self.assertIn("stylelint", pkg["devDependencies"])
		self.assertIn("id: stylelint", self.read(".pre-commit-config.yaml"))
		# Removing the last SCSS file removes the config sync wrote.
		git(self.root, "rm", "-q", "demo_app/public/scss/x.scss")
		self.commit()
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		self.assertFalse((self.root / ".stylelintrc.json").exists())


class TestRetire(AppCase):
	def test_legacy_files_are_reported_then_deleted(self):
		self.synced()
		self.write(
			".github/workflows/old.yml",
			"jobs: { r: { steps: [ { uses: googleapis/release-please-action@v4 } ] } }\n",
		)
		self.write(".github/workflows/docs.yml", "uses: googleapis/release-please-action@v4\n")
		self.write(".oxfmtrc.json", "{}\n")
		self.write("update-assets.mjs", "\n")
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 1, out)
		for path in (".github/workflows/old.yml", ".oxfmtrc.json", "update-assets.mjs"):
			self.assertIn(f"{path} (retire): legacy file", out)
		self.assertNotIn("docs.yml", out)
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		for path in (".github/workflows/old.yml", ".oxfmtrc.json", "update-assets.mjs"):
			self.assertFalse((self.root / path).exists(), path)
		self.assertTrue((self.root / ".github/workflows/docs.yml").exists())
		self.assertEqual(self.check()[0], 0)


class TestConfigCreation(AppCase):
	extra_pyproject = ""

	def test_config_is_created_from_the_flake(self):
		py = self.read("pyproject.toml").split("[tool.ironclad]")[0]
		self.write("pyproject.toml", py)
		self.write("demo_app/hooks.py", 'required_apps = ["erpnext"]\n')
		self.write(
			"flake.nix",
			'{ frappeVersion = "version-16"; siblings = [ { name = "erpnext"; } { name = "hrms"; } ]; }\n',
		)
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 1)
		self.assertIn("[tool.ironclad] is missing", out)
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		cfg = tomllib.loads(self.read("pyproject.toml"))["tool"]["ironclad"]
		self.assertEqual(cfg, {"schema": 1, "frappe-major": 16, "siblings": ["erpnext", "hrms"]})
		self.assertIn("erpnext = {", self.read("flake.nix"))
		deps = tomllib.loads(self.read("pyproject.toml"))["tool"]["bench"]["frappe-dependencies"]
		self.assertEqual(deps, {"frappe": ">=16.0.0,<17.0.0", "erpnext": ">=16.0.0,<17.0.0"})
		for key in ("frappe/ui", "erpnext/banking", "hrms/frontend", "hrms/roster"):
			self.assertTrue((self.root / "nix/node-locks" / key / "yarn.lock").is_file(), key)

	def test_no_major_anywhere_is_exit_2(self):
		self.write("pyproject.toml", self.read("pyproject.toml").split("[tool.ironclad]")[0])
		self.commit()
		self.assertEqual(self.check()[0], 2)

	def test_unknown_key_is_exit_2(self):
		self.write("pyproject.toml", self.read("pyproject.toml") + "nonsense = 1\n")
		self.commit()
		self.assertEqual(self.check()[0], 2)


class TestNodeLocks(AppCase):
	extra_pyproject = 'siblings = ["erpnext", "hrms"]\n'

	def test_seeds_and_leaves_existing_alone(self):
		self.write("nix/node-locks/hrms/roster/yarn.lock", "mine\n")
		self.commit()
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		for key in ("frappe/ui", "erpnext/banking", "hrms/frontend"):
			self.assertTrue((self.root / "nix/node-locks" / key / "yarn.lock").is_file(), key)
			self.assertTrue((self.root / "nix/node-locks" / key / "source.json").is_file(), key)
		self.assertEqual(self.read("nix/node-locks/hrms/roster/yarn.lock"), "mine\n")
		self.assertFalse((self.root / "nix/node-locks/hrms/roster/source.json").exists())


class TestSkew(AppCase):
	def test_expect_rev(self):
		self.synced()
		code, out, _ = self.ironclad("sync", "--check", "--expect-rev", "f" * 40)
		self.assertEqual(code, 3, out)
		self.assertIn("version skew", out)
		self.assertEqual(self.ironclad("sync", "--check", "--expect-rev", "a" * 40)[0], 0)
		with mock.patch.dict(os.environ, {"IRONCLAD_ALLOW_SKEW": "1"}):
			self.assertEqual(self.ironclad("sync", "--check", "--expect-rev", "f" * 40)[0], 0)

	def test_caller_workflow_pin(self):
		self.synced()
		self.write(
			".github/workflows/ci.yml",
			"jobs:\n  ci:\n    uses: Avunu/frappe-nix/.github/workflows/app-ci.yml@"
			+ "e" * 40
			+ " # v0.0.0\n",
		)
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 3, out)

	def test_override_needs_allow_skew(self):
		with mock.patch.dict(os.environ, {"IRONCLAD_FRAPPE_NIX_URL": "path:/x"}):
			code, _, err = self.ironclad("sync", "--write")
		self.assertEqual(code, 3, err)


class TestDeterminism(AppCase):
	def test_no_clock_in_the_context(self):
		import datetime

		from ironclad.scaffold import context, engine

		self.synced()
		app = engine.load_app(self.root)
		cfg, _ = engine.config_for(app, None)

		class Dec31(datetime.date):
			@classmethod
			def today(cls):
				return cls(2026, 12, 31)

		class Jan1(datetime.date):
			@classmethod
			def today(cls):
				return cls(2027, 1, 1)

		ctxs = []
		for fake in (Dec31, Jan1):
			with mock.patch("datetime.date", fake):
				ctxs.append(context.build(app, cfg, rev="a" * 40, floors={}))
		self.assertEqual(ctxs[0], ctxs[1])
		self.assertEqual(
			ctxs[0]["first_commit_year"], int(git(self.root, "log", "--format=%cs").split("-")[0])
		)


class TestShallowHistory(AppCase):
	def test_shallow_clone_never_renders_a_wrong_year(self):
		from ironclad.common.report import EnvError
		from ironclad.scaffold import context

		git(self.root, "commit", "-q", "--allow-empty", "-m", "second", "--date", "2030-01-01T00:00:00")
		full = context.first_commit_year(self.root)
		self.assertIsInstance(full, int)
		clone = self.root.parent / (self.root.name + "-shallow")
		self.addCleanup(__import__("shutil").rmtree, clone, True)
		git(self.root, "clone", "-q", "--depth", "1", f"file://{self.root}", str(clone))
		shallow = context.first_commit_year(clone)
		with self.assertRaises(EnvError):
			str(shallow)
		with self.assertRaises(EnvError):
			bool(shallow == full)


class TestVersionSeed(AppCase):
	def test_no_version_anywhere_agrees_from_the_first_sync(self):
		self.write("demo_app/__init__.py", "# only a comment\n")
		self.commit()
		self.synced()
		self.assertIn('__version__ = "0.1.0"', self.read("demo_app/__init__.py"))
		self.assertEqual(json.loads(self.read("package.json"))["version"], "0.1.0")
		self.assertEqual(json.loads(self.read(".release-please-manifest.json")), {".": "0.1.0"})
		self.assertEqual(self.ironclad("compat")[0], 0)

	def test_package_version_fills_the_gap(self):
		self.write("demo_app/__init__.py", "# only a comment\n")
		self.write("package.json", '{"name": "demo-app", "version": "16.0.0"}\n')
		self.commit()
		self.synced()
		self.assertIn('__version__ = "16.0.0"', self.read("demo_app/__init__.py"))
		self.assertEqual(json.loads(self.read(".release-please-manifest.json")), {".": "16.0.0"})
		self.assertEqual(self.ironclad("compat")[0], 0)


class TestSymlinks(AppCase):
	def test_a_managed_symlink_is_refused_unread(self):
		self.synced()
		outside = self.root.parent / (self.root.name + "-secret")
		outside.write_text("SECRET_TOKEN=hunter2\n")
		self.addCleanup(outside.unlink)
		(self.root / ".editorconfig").unlink()
		(self.root / ".editorconfig").symlink_to(outside)
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertNotIn("hunter2", out)
		self.assertIn("symlink", out)
		code, out, err = self.ironclad("sync", "--write")
		self.assertEqual(code, 2, out + err)
		self.assertEqual(outside.read_text(), "SECRET_TOKEN=hunter2\n")

	def test_a_retired_symlink_is_removed_unread(self):
		self.synced()
		outside = self.root.parent / (self.root.name + "-old")
		outside.write_text("{}\n")
		self.addCleanup(outside.unlink)
		(self.root / ".oxfmtrc.json").symlink_to(outside)
		self.commit()
		self.assertEqual(self.check()[0], 1)
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		self.assertFalse((self.root / ".oxfmtrc.json").is_symlink())
		self.assertEqual(outside.read_text(), "{}\n")


class TestSchemaPattern(AppCase):
	def test_trailing_newline_is_refused(self):
		self.write("pyproject.toml", self.read("pyproject.toml") + 'site = "demo.localhost\\n"\n')
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("does not match", out)


class TestRetireGuards(AppCase):
	def test_requirements_naming_a_missing_dependency_is_exit_2(self):
		self.synced()
		self.write("requirements.txt", "# pinned\nPyJWT>=2  # tokens\n-r other.txt\n")
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("pyjwt", out)
		self.assertEqual(self.ironclad("sync", "--write")[0], 2)
		self.assertTrue((self.root / "requirements.txt").exists())
		text = self.read("pyproject.toml").replace("dependencies = []", 'dependencies = ["pyjwt>=2"]')
		self.write("pyproject.toml", text)
		self.commit()
		self.assertEqual(self.check()[0], 1)
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		self.assertFalse((self.root / "requirements.txt").exists())

	def test_update_assets_steps_go_with_the_file(self):
		self.write("vite.config.ts", "export default {};\n")
		self.write("update-assets.mjs", "\n")
		self.write(
			"package.json",
			json.dumps(
				{
					"name": "demo-app",
					"scripts": {
						"build": "vite build && node update-assets.mjs",
						"dev": "node update-assets.mjs",
					},
				}
			),
		)
		self.commit()
		self.synced()
		scripts = json.loads(self.read("package.json"))["scripts"]
		self.assertEqual(scripts["build"], "vite build && node scripts/ironclad-vite-register.mjs")
		self.assertNotIn("dev", scripts)
		self.assertFalse((self.root / "update-assets.mjs").exists())
		pkg = json.loads(self.read("package.json"))
		pkg["scripts"]["watch"] = "x update-assets.mjs"
		self.write("package.json", json.dumps(pkg))
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("scripts.watch runs update-assets.mjs", out)


class TestSpaGuard(AppCase):
	def test_an_unmanaged_tsconfig_beside_vite_is_never_overwritten(self):
		own = '{"compilerOptions": {"strict": true, "jsx": "preserve"}}\n'
		self.write("tsconfig.json", own)
		self.write("vite.config.ts", "export default {};\n")
		self.write("demo_app/public/js/a.ts", "export {};\n")
		self.write("package.json", '{"name": "demo-app", "scripts": {"build": "vite build"}}\n')
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("[[tool.ironclad.typescript.spa]]", out)
		code, _, err = self.ironclad("sync", "--write")
		self.assertEqual(code, 2, err)
		self.assertEqual(self.read("tsconfig.json"), own)

	def test_an_spa_tsconfig_where_sync_renders_none_says_so(self):
		"""No browser, desk or scripts project (frappe_editor, timeclock): the tsconfig.json
		entry's ``when`` is false, and the app's own file is still the §2.9 exit 2, with the remedy."""
		own = '{"compilerOptions": {"strict": true}, "include": ["src/**"]}\n'
		self.write("tsconfig.json", own)
		self.write("vite.config.ts", "export default {};\n")
		self.write("src/main.ts", "export {};\n")
		self.write("package.json", '{"name": "demo-app", "scripts": {"build": "vite build"}}\n')
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("tsconfig.json is the app's own TypeScript config", out)
		self.assertIn("[[tool.ironclad.typescript.spa]]", out)
		code, _, err = self.ironclad("sync", "--write")
		self.assertEqual(code, 2, err)
		self.assertEqual(self.read("tsconfig.json"), own)


class TestPatchesHook(AppCase):
	def test_validate_patches_needs_a_patches_dir(self):
		self.synced()
		self.assertNotIn("validate_patches", self.read(".pre-commit-config.yaml"))
		self.write("demo_app/patches/__init__.py", "")
		self.commit()
		self.assertEqual(self.check()[0], 1)
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		self.assertIn("id: validate_patches", self.read(".pre-commit-config.yaml"))


class TestViteAgreement(AppCase):
	def test_docs_site_vite_is_no_vite_for_sync_or_compat(self):
		self.synced()
		self.write("docs-site/package.json", '{"name": "docs"}\n')
		self.write("docs-site/vite.config.ts", "export default {};\n")
		self.commit()
		self.assertEqual(self.check()[0], 0)
		code, out, _ = self.ironclad("compat")
		self.assertEqual(code, 0, out)


class TestOnlyLimitsPhaseA(AppCase):
	def test_only_writes_only_what_it_names(self):
		self.ironclad("sync", "--write", "--only", ".editorconfig")
		status = git(self.root, "status", "--porcelain")
		self.assertIn(".editorconfig", status)
		self.assertNotIn("flake.nix", status)
		self.assertNotIn(".envrc", status)


class TestSiteCarried(AppCase):
	def test_existing_site_name_is_kept(self):
		self.write("pyproject.toml", self.read("pyproject.toml").split("[tool.ironclad]")[0])
		self.write("flake.nix", '{ frappeVersion = "version-16"; siteName = "demo.localhost"; }\n')
		self.commit()
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		cfg = tomllib.loads(self.read("pyproject.toml"))["tool"]["ironclad"]
		self.assertEqual(cfg["site"], "demo.localhost")
		self.assertIn('siteName = "demo.localhost"', self.read("flake.nix"))


class TestSiteOption(AppCase):
	def test_site_option_is_recorded_when_the_table_is_created(self):
		self.write("pyproject.toml", self.read("pyproject.toml").split("[tool.ironclad]")[0])
		self.commit()
		code, _, err = self.ironclad(
			"sync", "--write", "--frappe-version", "version-16", "--site", "custom.localhost"
		)
		self.assertEqual(code, 0, err)
		cfg = tomllib.loads(self.read("pyproject.toml"))["tool"]["ironclad"]
		self.assertEqual(cfg["site"], "custom.localhost")
		self.assertIn('siteName = "custom.localhost"', self.read("flake.nix"))

	def test_default_site_option_adds_no_key(self):
		self.write("pyproject.toml", self.read("pyproject.toml").split("[tool.ironclad]")[0])
		self.commit()
		code, _, err = self.ironclad(
			"sync", "--write", "--frappe-version", "version-16", "--site", "demo-app.localhost"
		)
		self.assertEqual(code, 0, err)
		self.assertNotIn("site", tomllib.loads(self.read("pyproject.toml"))["tool"]["ironclad"])

	def test_bad_site_is_exit_2(self):
		self.write("pyproject.toml", self.read("pyproject.toml").split("[tool.ironclad]")[0])
		self.commit()
		code, _, _ = self.ironclad("sync", "--write", "--frappe-version", "version-16", "--site", "Bad Site")
		self.assertEqual(code, 2)

	def test_site_option_leaves_an_existing_table_alone(self):
		self.synced()
		code, _, err = self.ironclad("sync", "--write", "--site", "custom.localhost")
		self.assertEqual(code, 0, err)
		self.assertNotIn("site", tomllib.loads(self.read("pyproject.toml"))["tool"]["ironclad"])


class TestStaticAnalysisWhitelist(AppCase):
	required: ClassVar[list[str]] = ["erpnext"]
	extra_pyproject = 'siblings = ["erpnext"]\n'

	def whitelist(self) -> list[str]:
		doc = tomllib.loads(self.read("pyproject.toml"))
		return doc["tool"]["test_utils"]["static-analysis"]["whitelist"]

	def test_frappe_and_siblings_are_whitelisted_and_app_entries_kept(self):
		self.synced()
		self.assertEqual(self.whitelist(), ["frappe.*", "erpnext.*"])
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				'whitelist = ["frappe.*", "erpnext.*"]', 'whitelist = ["flow.api.*"]'
			),
		)
		code, out = self.check()
		self.assertEqual(code, 1, out)
		self.assertEqual(self.ironclad("sync", "--write")[0], 0)
		self.assertEqual(self.whitelist(), ["flow.api.*", "frappe.*", "erpnext.*"])
		self.assertEqual(self.check()[0], 0)


class TestStaleYarnLock(AppCase):
	def test_a_dependency_the_lock_lacks_is_drift(self):
		"""An earlier (offline) sync changed package.json; yarn.lock must not pass as current."""
		self.synced()
		pkg = json.loads(self.read("package.json"))
		pkg["devDependencies"]["oxlint"] = "^1.99.0"
		self.write("package.json", json.dumps(pkg, indent="\t") + "\n")
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 1, out)
		self.assertIn("yarn.lock (seed): does not lock package.json's oxlint@^1.99.0", out)
		self.fake_yarn_lock()
		self.assertEqual(self.check()[0], 0)

	def test_lock_keys(self):
		from ironclad.scaffold import package_json

		v1 = '# yarn lockfile v1\n\n"@a/b@^1.0.0", "@a/b@^1.2.0":\n  version "1.2.0"\n\nc@~2:\n  version "2.0.1"\n'
		self.assertEqual(package_json.yarn_lock_keys(v1), {"@a/b@^1.0.0", "@a/b@^1.2.0", "c@~2"})
		berry = '__metadata:\n  version: 8\n\n"@a/b@npm:^1.0.0":\n  version: 1.0.0\n'
		self.assertEqual(package_json.yarn_lock_keys(berry), {"__metadata", "@a/b@^1.0.0"})
		pkg = {"dependencies": {"c": "~2", "w": "workspace:*", "l": "link:../l", "mine": "1.0.0"}}
		self.assertEqual(package_json.yarn_lock_missing(pkg, v1, {"mine"}), [])
		self.assertEqual(
			package_json.yarn_lock_missing({"devDependencies": {"c": "^3"}}, v1, set()), ["c@^3"]
		)


class TestLockFollowsFlake(AppCase):
	"""A lock whose inputs carry the right names but another URL is drift (review: a
	frappe-major bump left flake.lock on version-16 and --check stayed clean)."""

	def stale(self, node: str, ref: str) -> None:
		lock = json.loads(self.read("flake.lock"))
		lock["nodes"][node]["original"]["ref"] = ref
		self.write("flake.lock", json.dumps(lock))
		self.commit()

	def test_frappe_on_another_branch_is_drift(self):
		self.synced()
		self.stale("frappe", "version-15")
		code, out = self.check()
		self.assertEqual(code, 1, out)
		self.assertIn("frappe (locked from github:frappe/frappe/version-15, flake.nix has", out)

	def test_frappe_nix_on_another_branch_is_drift_unless_overridden(self):
		self.synced()
		self.stale("frappe-nix", "main")
		code, out = self.check()
		self.assertEqual(code, 1, out)
		with mock.patch.dict(os.environ, {"IRONCLAD_ALLOW_SKEW": "1"}):
			code, out = self.check()
		self.assertEqual(code, 0, out)


if __name__ == "__main__":
	unittest.main()
