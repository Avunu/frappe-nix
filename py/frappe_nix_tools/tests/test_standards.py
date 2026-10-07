"""The 1.2 acceptance cases of docs/app-standards/spec.md §7 N3: opt-in, ``minimal``, module
toggles and retraction, profiles, and the review-round parameters (Appendix R2)."""

import json
import os
import tomllib
import unittest
from typing import ClassVar
from unittest import mock

from frappe_nix_tools.common import config
from frappe_nix_tools.scaffold import defaults, engine, manifest
from packaging.version import Version
from scaffold_helpers import EXAMPLE_ORG, AppCase, flake_lock, git, write_profile

BARE = """[project]
name = "demo_app"
requires-python = ">=3.14"
dynamic = ["version"]

[build-system]
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[tool.ruff]
line-length = 110
target-version = "py314"

[tool.ruff.format]
quote-style = "double"
indent-style = "tab"
docstring-code-format = true

[tool.coverage.report]
fail_under = 10
show_missing = true
"""


class OptOutCase(AppCase):
	"""An app with no ``[tool.frappe-nix]``: what frappe-nix ``main`` scaffolds."""

	def setUp(self) -> None:
		super().setUp()
		self.write("pyproject.toml", BARE)
		self.commit()


class TestDefaultAppModeUnchanged(OptOutCase):
	"""S35: without opting in, sync and check refuse with the hint and write nothing."""

	def test_check_and_sync_refuse(self):
		for argv in (("sync", "--check"), ("sync", "--write"), ("compat",)):
			with self.subTest(argv=argv):
				code, out, err = self.fn(*argv)
				self.assertEqual(code, 2, out + err)
				self.assertIn("has not opted in", err)
				self.assertIn("--standards minimal|recommended", err)
				self.assertEqual(git(self.root, "status", "--porcelain"), "")

	def test_json_report_says_invalid(self):
		code, out, _ = self.fn("sync", "--check", "--format", "json")
		self.assertEqual(code, 2)
		self.assertEqual(json.loads(out)["status"], "invalid")


class TestMinimalRendersNothingExtra(OptOutCase):
	def test_minimal(self):
		before = self.read("pyproject.toml")
		code, _, err = self.fn("sync", "--write", "--standards", "minimal", "--frappe-version", "version-16")
		self.assertEqual(code, 0, err)
		changed = sorted(line[3:] for line in git(self.root, "status", "--porcelain").splitlines())
		self.assertEqual(changed, [".envrc", ".gitignore", "flake.nix", "pyproject.toml"])
		after = self.read("pyproject.toml")
		self.assertTrue(after.startswith(before), "a key outside [tool.frappe-nix] changed")
		table = tomllib.loads(after)["tool"]["frappe-nix"]
		self.assertEqual(table, {"schema": 1, "profile": "minimal", "frappe-major": 16})
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 0, out)
		plan = engine.build(self.root)
		live = [i.path for i in plan.items if i.current is not None]
		self.assertEqual(sorted(live), [".envrc", ".gitignore", "flake.nix", "pyproject.toml"])
		self.assertEqual(next(i for i in plan.items if i.path == "pyproject.toml").code, 0)


class ToggleCase(AppCase):
	"""An app synced with ``recommended`` that has a scripts/ TypeScript file (a TS project)."""

	def setUp(self) -> None:
		super().setUp()
		self.write("scripts/x.ts", "export const x = 1;\n")
		self.commit()
		self.synced()

	def resync(self) -> tuple[int, str]:
		code, out, err = self.fn("sync", "--write")
		return code, out + err


class TestToggleOff(ToggleCase):
	def test_js_off_and_on(self):
		before = self.snapshot()
		self.table('js.tool = "none"\n')
		code, out = self.resync()
		self.assertEqual(code, 0, out)
		self.assertFalse((self.root / ".oxlintrc.json").exists())
		self.assertFalse((self.root / ".oxfmtrc.jsonc").exists())
		pre = self.read(".pre-commit-config.yaml")
		self.assertNotIn("id: oxfmt", pre)
		self.assertNotIn("id: oxlint", pre)
		pkg = json.loads(self.read("package.json"))
		for key in ("format", "format:check", "lint", "check"):
			self.assertNotIn(key, pkg["scripts"], key)
		self.assertNotIn("oxlint", pkg["devDependencies"])
		self.assertNotIn("oxfmt", pkg["devDependencies"])
		self.assertIn("typecheck", pkg["scripts"])
		self.assertEqual(self.check()[0], 0)
		# Back on: every file byte for byte.
		self.write("pyproject.toml", before["pyproject.toml"].decode())
		code, out = self.resync()
		self.assertEqual(code, 0, out)
		self.assertEqual(self.snapshot(), before)

	def test_typescript_off(self):
		self.table("typescript.enable = false\n")
		code, out = self.resync()
		self.assertEqual(code, 0, out)
		for name in ("tsconfig.json", "tsconfig.base.json", "tsconfig.scripts.json"):
			self.assertFalse((self.root / name).exists(), name)
		pkg = json.loads(self.read("package.json"))
		self.assertNotIn("typecheck", pkg["scripts"])
		self.assertNotIn("yarn -s typecheck", pkg["scripts"]["check"])
		self.assertNotIn("typescript", pkg["devDependencies"])
		self.assertEqual(self.check()[0], 0)

	def test_ci_off_removes_what_sync_rendered_only(self):
		"""Through an org profile's [[extra-files]] entry of module ci (N4's callers follow the
		same rule): the file sync rendered goes, the app's own workflow stays."""
		write_profile(
			self.root,
			text=EXAMPLE_ORG
			+ '\n[[extra-files]]\npath = ".github/workflows/rendered.yml"\ntemplate = "rendered.yml.j2"\n'
			'strategy = "whole"\nmodule = "ci"\nheader = "yaml"\n',
		)
		self.write(".standards-profile/templates/rendered.yml.j2", "name: rendered\non: [push]\n")
		self.write(".github/workflows/custom.yml", "name: custom\non: [push]\n")
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				'profile = "recommended"', 'profile = "./.standards-profile"'
			),
		)
		self.commit()
		code, out = self.resync()
		self.assertEqual(code, 0, out)
		self.assertTrue(self.read(".github/workflows/rendered.yml").startswith("# frappe-nix:managed"))
		self.fake_locks()
		self.commit()
		self.table("ci.enable = false\n")
		code, out = self.resync()
		self.assertEqual(code, 0, out)
		self.assertFalse((self.root / ".github/workflows/rendered.yml").exists())
		self.assertTrue((self.root / ".github/workflows/custom.yml").exists())
		self.assertEqual(self.check()[0], 0)

	def test_local_region_content_stops_the_retraction(self):
		text = self.read(".pre-commit-config.yaml").replace(
			"  # frappe-nix:local-begin repos\n",
			"  # frappe-nix:local-begin repos\n  - repo: https://example.org/hooks\n    rev: v1.0.0\n    hooks:\n      - id: mine\n",
		)
		self.write(".pre-commit-config.yaml", text)
		self.commit()
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace('profile = "recommended"', 'profile = "minimal"'),
		)
		before = self.snapshot()
		code, out = self.resync()
		self.assertEqual(code, 2, out)
		self.assertIn("local region repos holds content", out)
		self.assertEqual(self.snapshot(), before, "a refused retraction deleted something")

	def test_an_edited_managed_key_is_left_with_a_warning(self):
		self.write(
			"pyproject.toml", self.read("pyproject.toml").replace("line-length = 110", "line-length = 120")
		)
		self.commit()
		self.table("python-lint.enable = false\n")
		code, out = self.resync()
		self.assertEqual(code, 0, out)
		doc = tomllib.loads(self.read("pyproject.toml"))
		self.assertEqual(doc["tool"]["ruff"]["line-length"], 120)
		self.assertNotIn("lint", doc["tool"]["ruff"], "the unedited keys go")
		self.assertIn("tool.ruff.line-length is the app's now", out)
		self.assertEqual(self.check()[0], 0)


class TestJsOffKeepsTheAdoptersScripts(OptOutCase):
	def test_eslint_users(self):
		self.write("demo_app/public/scss/a.scss", "a { color: red; }\n")
		self.write(".eslintrc.json", "{}\n")
		self.write(
			"package.json",
			json.dumps(
				{
					"name": "demo-app",
					"version": "16.0.0",
					"scripts": {
						"lint": "eslint . && stylelint 'x/**/*.scss'",
						"check": "yarn lint && vitest",
					},
				},
				indent="\t",
			)
			+ "\n",
		)
		self.write(
			"pyproject.toml",
			BARE
			+ '\n[tool.frappe-nix]\nschema = 1\nprofile = "recommended"\nfrappe-major = 16\njs.tool = "none"\n',
		)
		self.commit()
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		scripts = json.loads(self.read("package.json"))["scripts"]
		self.assertEqual(scripts["lint"], "eslint . && stylelint 'x/**/*.scss'")
		self.assertEqual(scripts["check"], "yarn lint && vitest")
		self.assertEqual(scripts["lint:css"], 'stylelint "demo_app/public/**/*.scss"')
		self.assertTrue((self.root / ".eslintrc.json").exists())
		self.fake_locks()
		self.commit()
		self.assertEqual(self.check()[0], 0)


class TestThirdPartyFormatter(AppCase):
	"""With js.tool = "none", a formatter the app chose may restyle the managed YAML, JSON and
	TOML: the same data is not drift, and sync leaves it as it is (§2.10, §3.2)."""

	extra_pyproject = 'js.tool = "none"\n'

	def test_restyled_files_are_clean(self):
		self.synced()
		pre = self.read(".pre-commit-config.yaml")
		restyled = (
			pre.replace(
				"default_install_hook_types: [pre-commit, commit-msg]",
				"default_install_hook_types:\n  - pre-commit\n  - commit-msg",
			)
			.replace("language: system", 'language: "system"')
			.replace(
				"        args: [--allow-multiple-documents]",
				"        args:\n          - --allow-multiple-documents",
			)
		)
		self.assertNotEqual(restyled, pre)
		self.write(".pre-commit-config.yaml", restyled)
		rp = json.loads(self.read("release-please-config.json"))
		self.write("release-please-config.json", json.dumps(rp, indent=2) + "\n")
		committed = self.read("committed.toml")
		types = tomllib.loads(committed)["allowed_types"]
		head, _, tail = committed.partition("allowed_types = [")
		tail = tail.split("]\n", 1)[1]
		self.write("committed.toml", head + "allowed_types = " + json.dumps(types) + "\n" + tail)
		self.commit()
		before = self.snapshot()
		code, out = self.check()
		self.assertEqual(code, 0, out)
		self.assertEqual(self.fn("sync", "--write")[0], 0)
		self.assertEqual(self.snapshot(), before, "sync rewrote a semantically equal file")
		self.write(
			"committed.toml",
			self.read("committed.toml").replace("subject_length = 100", "subject_length = 72"),
		)
		self.assertEqual(self.check()[0], 1)


class TestMetadataParameters(OptOutCase):
	def test_app_owned_packaging(self):
		self.write(
			"pyproject.toml",
			BARE.replace(
				'requires = ["flit_core >=3.4,<4"]\nbuild-backend = "flit_core.buildapi"',
				'requires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"',
			)
			+ '\n[tool.poetry]\nname = "demo_app"\n\n[tool.frappe-nix]\nschema = 1\nprofile = "recommended"\nfrappe-major = 16\n'
			'metadata.build-backend = "any"\nmetadata.package-type = ""\nmetadata.package-manager = ""\n',
		)
		self.write("requirements.txt", "requests\n")
		self.write("MANIFEST.in", "include *.md\n")
		self.write(
			"package.json",
			'{"name": "demo-app", "version": "16.0.0", "type": "commonjs", "packageManager": "pnpm@9"}\n',
		)
		self.commit()
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		doc = tomllib.loads(self.read("pyproject.toml"))
		self.assertEqual(doc["build-system"]["build-backend"], "setuptools.build_meta")
		self.assertIn("poetry", doc["tool"])
		self.assertTrue((self.root / "requirements.txt").exists())
		self.assertTrue((self.root / "MANIFEST.in").exists())
		pkg = json.loads(self.read("package.json"))
		self.assertEqual(pkg["type"], "commonjs")
		self.assertEqual(pkg["packageManager"], "pnpm@9")
		self.fake_locks()
		self.commit()
		self.assertEqual(self.check()[0], 0)
		plan = engine.build(self.root)
		for module in ("typescript", "js", "releases"):
			self.assertTrue(plan.ctx.modules[module], module)


class TestRetireByFunction(AppCase):
	RELEASE = "jobs: { r: { steps: [ { uses: googleapis/release-please-action@v4 } ] } }\n"
	MERGE = "jobs: { m: { steps: [ { uses: dependabot/fetch-metadata@v2 } ] } }\n"

	def test_rules_follow_their_module(self):
		self.synced()
		self.write(".github/workflows/release.yml", self.RELEASE)
		self.write(".github/workflows/automerge.yml", self.MERGE)
		self.write(".github/workflows/check.yml", "name: check\n")
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 1, out)
		self.assertIn(".github/workflows/release.yml (retire): legacy file", out)
		self.assertIn(".github/workflows/automerge.yml (retire): legacy file", out)
		self.assertNotIn("check.yml", out, "name-only rules are the profile's, not frappe-nix's")
		self.table("releases.enable = false\ndependabot.enable = false\n")
		code, out = self.check()
		self.assertNotIn("(retire)", out)

	def test_retire_keep(self):
		self.synced()
		self.write(".github/workflows/release.yml", self.RELEASE)
		self.table('retire-keep = [".github/workflows/release.yml"]\n')
		self.commit()
		self.assertNotIn("(retire)", self.check()[1])

	def test_the_profiles_rule(self):
		write_profile(self.root)
		self.write(".github/workflows/check.yml", "name: check\n")
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				'profile = "recommended"', 'profile = "./.standards-profile"'
			),
		)
		self.commit()
		_code, out = self.check()
		self.assertIn(
			".github/workflows/check.yml (retire): legacy file (the profile's [[retire]] rule)", out
		)


class TestTheAppsFlake(AppCase):
	def test_regions_systems_and_inputs(self):
		self.synced()
		flake = self.read("flake.nix").replace(
			"    # frappe-nix:local-begin inputs\n",
			'    # frappe-nix:local-begin inputs\n    extra = {\n      url = "github:example/extra";\n      flake = false;\n    };\n',
		)
		self.write("flake.nix", flake)
		self.write(
			".envrc",
			self.read(".envrc").replace(
				"# frappe-nix:local-begin envrc\n", "# frappe-nix:local-begin envrc\ndotenv\n"
			),
		)
		self.table('dev-shell.systems = ["x86_64-linux"]\n')
		self.commit()
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		flake = self.read("flake.nix")
		self.assertIn('url = "github:example/extra";', flake)
		self.assertIn('systems = [ "x86_64-linux" ];', flake)
		self.assertIn("dotenv\n", self.read(".envrc"))
		self.write("flake.nix", flake.replace("extra = {", "frappe = {"))
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("flake input frappe", out)

	def test_caches_and_a_pinned_frappe_nix(self):
		self.table(
			'dev-shell.extra-substituters = ["https://cache.example.org"]\n'
			'dev-shell.extra-trusted-public-keys = ["cache.example.org-1:abc="]\n'
			'dev-shell.frappe-nix-url = "github:Avunu/frappe-nix/v1.0.0"\n'
		)
		self.commit()
		self.assertEqual(self.fn("sync", "--write")[0], 0)
		flake = self.read("flake.nix")
		self.assertIn('"https://cache.example.org"', flake)
		self.assertIn('"cache.example.org-1:abc="', flake)
		self.assertIn('frappe-nix.url = "github:Avunu/frappe-nix/v1.0.0";', flake)


class TestFirstOptIn(OptOutCase):
	OWN = '{\n  inputs.frappe-nix.url = "github:Avunu/frappe-nix";\n  outputs = _: { };\n  # mine\n}\n'

	def test_an_own_flake_needs_force(self):
		self.write("flake.nix", self.OWN)
		self.commit()
		code, _, err = self.fn(
			"sync", "--write", "--standards", "recommended", "--frappe-version", "version-16"
		)
		self.assertEqual(code, 2, err)
		self.assertIn("pass --force", err)
		self.assertIn("+# frappe-nix:managed", err)
		self.assertEqual(git(self.root, "status", "--porcelain"), "")
		code, _, err = self.fn(
			"sync", "--write", "--standards", "recommended", "--frappe-version", "version-16", "--force"
		)
		self.assertEqual(code, 0, err)
		self.assertTrue(self.read("flake.nix").startswith("# frappe-nix:managed"))

	def test_the_app_templates_flake_is_replaced_silently(self):
		from frappe_nix_tools.common import data_path

		text = (
			data_path("app-template/flake.nix.in")
			.read_text()
			.replace("@APP_NAME@", "demo_app")
			.replace("@FRAPPE_BRANCH@", "version-16")
			.replace("@SITE_NAME@", "demo-app.localhost")
			.replace("@FRAPPE_VERSION@", "version-16")
		)
		self.write("flake.nix", text)
		self.write(".envrc", data_path("app-template/envrc").read_text())
		self.commit()
		code, _, err = self.fn("sync", "--write", "--standards", "minimal", "--frappe-version", "version-16")
		self.assertEqual(code, 0, err)


class TestObjectSiblings(AppCase):
	extra_pyproject = (
		'siblings = [{ repo = "example/shared_lib", branch = "main", range = ">=2.0.0,<3.0.0" }]\n'
	)
	required: ClassVar[list[str]] = ["example/shared_lib"]

	def test_branch_and_range(self):
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		self.assertIn('url = "github:example/shared_lib/main";', self.read("flake.nix"))
		deps = tomllib.loads(self.read("pyproject.toml"))["tool"]["bench"]["frappe-dependencies"]
		self.assertEqual(deps["shared_lib"], ">=2.0.0,<3.0.0")
		self.fake_locks(refs={"shared_lib": "main"})
		self.commit()
		self.assertEqual(self.check()[0], 0)
		code, out, _ = self.fn("compat")
		self.assertEqual(code, 0, out)
		self.write("flake.lock", json.dumps(flake_lock(["frappe", "example/shared_lib"])))
		self.commit()
		code, out, _ = self.fn("compat")
		self.assertEqual(code, 1, out)
		self.assertIn("C3 flake.lock: shared_lib follows 'version-16', not 'main'", out)


class TestTypescriptPreset(AppCase):
	extra_pyproject = 'typescript.preset = "inline"\n'

	def test_inline(self):
		self.write("demo_app/public/js/a.ts", "export const a = 1;\n")
		self.commit()
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		base = json.loads(
			"\n".join(
				line for line in self.read("tsconfig.base.json").splitlines() if not line.startswith("//")
			)
		)
		self.assertNotIn("extends", base)
		self.assertTrue(base["compilerOptions"]["strict"])
		self.assertTrue(base["compilerOptions"]["noUnusedLocals"])
		browser = self.read("tsconfig.browser.json")
		self.assertNotIn("frappe-types", browser)
		self.assertNotIn("frappe-types", json.loads(self.read("package.json"))["devDependencies"])
		self.table("typescript.check-js = true\n")
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("typescript.check-js needs the Frappe declarations", out)


class ProfileCase(AppCase):
	"""An app on the in-repo example-org profile (every module the managed files need on)."""

	profile = "./.standards-profile"

	def setUp(self) -> None:
		super().setUp()
		write_profile(self.root)
		self.commit()


class TestOrgProfile(ProfileCase):
	def test_org_values_and_extra_files(self):
		self.synced()
		pkg = json.loads(self.read("package.json"))
		self.assertEqual(pkg["author"], "Example Org")
		self.assertEqual(pkg["license"], "MIT")
		self.assertEqual(
			self.read("SECURITY.md"),
			"# Security\n\nReport vulnerabilities in demo_app to apps@example.org.\n",
		)
		self.assertNotIn("standards-profile", self.read("flake.nix"), "an in-repo profile needs no input")
		self.assertIn("id: ssort", self.read(".pre-commit-config.yaml"))
		self.table("hygiene.enable = false\n")
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		self.assertFalse((self.root / "SECURITY.md").exists())

	def test_override_of_a_non_overridable_template(self):
		self.write(".standards-profile/templates/.github/workflows/ci.yml.j2", "name: ci\n")
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("templates/.github/workflows/ci.yml.j2 overrides no overridable template", out)

	def test_an_overridable_template_renders_from_the_profile(self):
		"""N3 ships no overridable template (the README blocks, the listing seed and the repo
		policy JSON are N4's and N5's), so this marks .editorconfig's entry overridable."""
		real = manifest.load()
		entries = tuple(
			manifest.Entry(**{**e.__dict__, "overridable": True}) if e.path == ".editorconfig" else e
			for e in real.entries
		)
		self.write(".standards-profile/templates/editorconfig.j2", "root = true\n# {{ org.publisher }}'s\n")
		self.commit()
		with mock.patch.object(
			manifest, "load", return_value=manifest.Manifest(entries, real.retire, real.floors)
		):
			code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		self.assertTrue(self.read(".editorconfig").endswith("root = true\n# Example Org's\n"))
		self.assertTrue(self.read(".editorconfig").startswith("# frappe-nix:managed"))

	def test_an_empty_org_value_a_live_entry_needs(self):
		real = manifest.load()
		needy = manifest.Entry(
			path="NOTICE",
			strategy="seed",
			fragment="test",
			modules=("hygiene",),
			template="git-blame-ignore-revs.j2",
			uses=("org.support-url",),
		)
		patched = manifest.Manifest((*real.entries, needy), real.retire, real.floors)
		with mock.patch.object(manifest, "load", return_value=patched):
			code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("NOTICE needs org.support-url", out)

	def test_unmet_module_need(self):
		self.table("commits.enable = false\n")
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("module releases needs module commits", out)

	def test_requires_frappe_nix(self):
		self.write(
			".standards-profile/profile.toml",
			self.read(".standards-profile/profile.toml").replace(
				'extends = "recommended@1.0"', 'extends = "recommended@1.0"\nrequires-frappe-nix = ">=9"'
			),
		)
		self.commit()
		self.assertEqual(self.check()[0], 3)

	def test_profile_validate(self):
		code, out, err = self.fn("profile", "validate", str(self.root / ".standards-profile/profile.toml"))
		self.assertEqual(code, 0, out + err)
		self.write(
			".standards-profile/profile.toml",
			self.read(".standards-profile/profile.toml") + "\n[nonsense]\nenable = true\n",
		)
		code, out, err = self.fn("profile", "validate", str(self.root / ".standards-profile/profile.toml"))
		self.assertEqual(code, 2, out + err)

	def test_profile_show_explains(self):
		code, out, err = self.fn("profile", "show", "--explain")
		self.assertEqual(code, 0, err)
		self.assertIn('org.publisher = "Example Org"  # org:./.standards-profile', out)
		self.assertIn("ssort.enable = true  # org:./.standards-profile", out)
		self.assertIn('js.tool = "oxc"  # builtin:recommended@1.0', out)
		code, out, _ = self.fn("profile", "show", "--format", "json")
		self.assertTrue(json.loads(out)["modules"]["ssort"])


class TestFlakeProfileInput(AppCase):
	"""An org profile named by flake URL is the standards-profile input (§8.3); --profile-path
	reads it from a checkout instead of the lock."""

	def test_github_profile_and_back(self):
		profile = write_profile(self.root.parent / (self.root.name + "-profile"), rel="p")
		self.addCleanup(__import__("shutil").rmtree, profile.parent, True)
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				'profile = "recommended"', 'profile = "github:example/profile"'
			),
		)
		self.commit()
		code, _, err = self.fn("sync", "--write", "--profile-path", str(profile))
		self.assertEqual(code, 0, err)
		self.assertIn("warning: reading the org profile from", err)
		flake = self.read("flake.nix")
		self.assertIn(
			'standards-profile = {\n      url = "github:example/profile";\n      flake = false;\n    };',
			flake,
		)
		self.assertEqual(json.loads(self.read("package.json"))["author"], "Example Org")
		with mock.patch.dict(os.environ, {"CI": "true"}):
			code, _, err = self.fn("sync", "--check", "--profile-path", str(profile))
		self.assertEqual(code, 3, err)
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				'profile = "github:example/profile"', 'profile = "recommended"'
			),
		)
		self.commit()
		self.assertEqual(self.fn("sync", "--write")[0], 0)
		self.assertNotIn("standards-profile", self.read("flake.nix"))

	def test_other_hosts(self):
		profile = write_profile(self.root.parent / (self.root.name + "-gl"), rel="p")
		self.addCleanup(__import__("shutil").rmtree, profile.parent, True)
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				'profile = "recommended"', 'profile = "gitlab:example/profile"'
			)
			+ 'siblings = [{ repo = "libs/shared_lib", flake-url = "git+https://git.example.org/libs/shared_lib?ref=main", branch = "main", range = ">=2.0.0,<3.0.0" }]\n',
		)
		self.commit()
		code, _, err = self.fn("sync", "--write", "--profile-path", str(profile))
		self.assertEqual(code, 0, err)
		flake = self.read("flake.nix")
		self.assertIn('url = "gitlab:example/profile";', flake)
		self.assertIn('url = "git+https://git.example.org/libs/shared_lib?ref=main";', flake)

	def test_unlocked_profile_is_a_lock_problem(self):
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				'profile = "recommended"', 'profile = "github:example/profile"'
			),
		)
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("is not locked", out)


class TestBranches(AppCase):
	def test_main_tags(self):
		self.table('releases.branching = "main+tags"\nlisting.enable = true\nreadme.enable = true\n')
		self.commit()
		plan = engine.build(self.root)
		self.assertEqual(plan.ctx.branches, {"integration": "main", "release": None})
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		rp = json.loads(self.read("release-please-config.json"))
		self.assertIn({"type": "generic", "path": "README.md"}, rp["packages"]["."]["extra-files"])

	def test_integration_branch_and_tag_prefix(self):
		self.table('integration-branch = "trunk"\nreleases.enable = false\n')
		self.commit()
		self.assertEqual(engine.build(self.root).ctx.branches, {"integration": "trunk", "release": None})
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace("releases.enable = false\n", 'releases.tag-prefix = ""\n'),
		)
		self.commit()
		self.assertEqual(self.fn("sync", "--write")[0], 0)
		self.assertFalse(json.loads(self.read("release-please-config.json"))["include-v-in-tag"])

	def test_standards_reads_origins_default_branch(self):
		self.write("pyproject.toml", BARE)
		self.commit()
		git(self.root, "update-ref", "refs/remotes/origin/master", "HEAD")
		git(self.root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
		code, _, err = self.fn("sync", "--write", "--standards", "minimal", "--frappe-version", "version-16")
		self.assertEqual(code, 0, err)
		table = tomllib.loads(self.read("pyproject.toml"))["tool"]["frappe-nix"]
		self.assertEqual(table["integration-branch"], "master")


class TestSnapshots(AppCase):
	"""S42: a test copy of the package with a recommended@1.1 that turns ssort on."""

	def snapshots(self):
		real = config._builtin_doc("recommended@1.0")
		newer = json.loads(json.dumps(real))
		newer["ssort"]["enable"] = True
		docs = {"recommended@1.0": real, "recommended@1.1": newer, "minimal": config._builtin_doc("minimal")}
		return (
			mock.patch.object(
				config,
				"_snapshots",
				return_value={Version("1.0"): "recommended@1.0", Version("1.1"): "recommended@1.1"},
			),
			mock.patch.object(config, "_builtin_doc", side_effect=lambda stem: docs[stem]),
		)

	def test_pinned_and_floating(self):
		snaps, docs = self.snapshots()
		with snaps, docs:
			self.assertTrue(engine.build(self.root).ctx.modules["ssort"])
			self.write(
				"pyproject.toml",
				self.read("pyproject.toml").replace('profile = "recommended"', 'profile = "recommended@1.0"'),
			)
			self.assertFalse(engine.build(self.root).ctx.modules["ssort"])

	def test_changed_defaults_warning(self):
		self.write(
			".github/workflows/ci.yml",
			"jobs:\n  lint:\n    uses: Avunu/frappe-nix/.github/workflows/app-lint.yml@"
			+ "a" * 40
			+ " # v1.0.0\n",
		)
		self.commit()
		snaps, docs = self.snapshots()
		with snaps, docs:
			plan = engine.build(self.root)
			lines = defaults.warn_changed(self.root, plan)
		self.assertTrue(lines)
		self.assertIn("recommended@1.1 (it was recommended@1.0 at HEAD)", lines[0])
		self.assertIn("  ssort.enable: False → True", lines)
		self.assertIn('pin profile = "recommended@1.0"', lines[-1])


class TestManifestShape(unittest.TestCase):
	"""standards-manifest's static half: modules exist, ``uses`` keys exist in the schema."""

	def test_uses_name_schema_keys(self):
		from frappe_nix_tools.common import schema

		for entry in manifest.load().entries:
			for key in entry.uses:
				with self.subTest(path=entry.path, key=key):
					self.assertIsNotNone(
						schema.node_at(config.PROFILE_SCHEMA, key.removesuffix("?"))
						or schema.node_at(config.APP_SCHEMA, key.removesuffix("?"))
					)


if __name__ == "__main__":
	unittest.main()
