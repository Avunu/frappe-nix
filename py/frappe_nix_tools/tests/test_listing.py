"""N5's acceptance cases for ``frappe-listing`` (docs/app-standards/spec.md §7 "N5: product tools"):
the rules L1 to L12 on a throwaway app, the multiset semgrep baseline, the registry dry run,
and the README blocks.

The registry semgrep scan (L7) and pilot's get-app validator (L9) need the network and the
pinned trees; here the scan is replaced by recorded findings and L9 is skipped. Both run for
real in .github/workflows/selftest-product.yml.
"""

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

from frappe_nix_tools.listing import baseline, check, readme, rules, target
from frappe_nix_tools.listing.baseline import Hit
from scaffold_helpers import AppCase, git

PRODUCT_PROFILE = """schema = 1
name = "example-org"
description = "Example Org's app standards (a test fixture)"
extends = "recommended@1.0"

[org]
publisher = "Example Org"
email = "apps@example.org"
license = "MIT"
github-owner = "example-org"
website-url = "https://example.org/apps/{app}/"
docs-url = "https://{app_hyphen}.docs.example.org/"

[org.brand]
tile-color = "#336699"

[listing]
enable = true
publish = true
registry-fork = "Example-Org/marketplace"

[readme]
enable = true

[icons]
enable = true
"""

LISTING = """schema = 1
title = "Demo App"
tagline = "A demo app for the frappe-listing tests, with a long enough tagline"
type = "extension"
category = "Extensions"
categories = ["Testing"]
website = "https://example.org/apps/demo_app/"
documentation = "https://demo-app.docs.example.org/"
apps_screen = false
"""

HOOKS = """app_name = "demo_app"
app_title = "Demo App"
app_publisher = "Example Org"
app_description = "A demo app for the frappe-listing tests, with a long enough tagline"
app_email = "apps@example.org"
app_license = "MIT"
required_apps = []
"""

SYMBOLIC = (
	'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><title>Demo</title>'
	'<path fill="currentColor" d="M16 4L26 8V15C26 21 21.5 25.5 16 28C10.5 25.5 6 21 6 15V8Z"/></svg>\n'
)
LOGO = (
	'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><rect width="1024" height="1024"'
	' rx="293" ry="293" fill="#336699"/><g fill="#FFFFFF" transform="translate(512 512) scale(23.875)'
	' translate(-16 -16)"><path d="M16 4L26 8V15C26 21 21.5 25.5 16 28C10.5 25.5 6 21 6 15V8Z"/></g></svg>\n'
)

README = "\n\n".join(
	[
		"<!-- frappe-nix:begin header -->\n<!-- frappe-nix:end header -->",
		"Hand-written introduction.",
		*[f"<!-- frappe-nix:begin {b} -->\n<!-- frappe-nix:end {b} -->" for b in readme.BLOCKS[1:]],
	]
)
README += "\n"

DEPS = '\n[tool.bench.frappe-dependencies]\nfrappe = ">=16.0.0,<17.0.0"\n'


class ListingCase(AppCase):
	"""A listed app on an org profile with listing, readme and icons on, synced and committed."""

	profile = "./.standards-profile"
	extra_pyproject = DEPS
	profile_text = PRODUCT_PROFILE

	def setUp(self) -> None:
		super().setUp()
		self.write(".standards-profile/profile.toml", self.profile_text)
		self.write("marketplace/listing.toml", LISTING)
		self.write("demo_app/hooks.py", HOOKS)
		self.write("demo_app/public/images/demo_app-symbolic.svg", SYMBOLIC)
		self.write("demo_app/public/images/demo_app-logo.svg", LOGO)
		self.write("README.md", README)
		self.commit()
		self.synced()

	def target(self) -> target.Target:
		return target.load(self.root)

	def run_check(self, hits: list[Hit] | None = None, **options) -> check.Outcome:
		"""``check.run`` with L7's scan replaced by ``hits`` and L9 off."""
		with (
			mock.patch.object(check, "marketplace_tree", return_value=(self.root, "c" * 40)),
			mock.patch.object(baseline, "scan", return_value=(hits or [], [], "")),
			# L10 and L11 (with --release) ask the network; here every URL answers.
			mock.patch.object(rules, "fetch_status", side_effect=lambda url, **_: (200, url)),
			mock.patch.object(rules, "upstream_apps", return_value=[]),
		):
			return check.run(self.target(), check.Options(getapp=False, **options))

	def errors(self, outcome: check.Outcome, rule: str) -> list[str]:
		return [r.message for r in outcome.results if r.rule == rule and r.level == "error"]


class TestCheck(ListingCase):
	def test_the_listed_app_passes(self):
		outcome = self.run_check()
		self.assertEqual(outcome.code, 0, outcome.results)
		self.assertFalse([r for r in outcome.results if r.level == "warning"])

	def test_l1_tagline_too_long(self):
		self.write(
			"marketplace/listing.toml",
			LISTING.replace("with a long enough tagline", "x" * 40 + " and more words"),
		)
		self.commit()
		self.assertTrue(
			any("tagline is" in m and "40 to 80" in m for m in self.errors(self.run_check(), "L1"))
		)

	def test_l1_trailing_period_and_marks(self):
		self.write(
			"marketplace/listing.toml",
			LISTING.replace('title = "Demo App"', 'title = "Frappe Demo"').replace(
				"long enough tagline", "long enough tagline."
			),
		)
		self.commit()
		found = self.errors(self.run_check(), "L1")
		self.assertTrue(any("must not end with a period" in m for m in found), found)
		self.assertTrue(any("'Frappe'" in m for m in found), found)

	def test_l2_expects_the_profile_publisher(self):
		self.write(
			"demo_app/hooks.py", HOOKS.replace('app_publisher = "Example Org"', 'app_publisher = "Someone"')
		)
		self.commit()
		found = self.errors(self.run_check(), "L2")
		self.assertTrue(any("app_publisher is 'Someone', expected 'Example Org'" in m for m in found), found)

	def test_l2_without_org_publisher_needs_any_publisher(self):
		self.write(
			".standards-profile/profile.toml",
			PRODUCT_PROFILE.replace('publisher = "Example Org"', 'publisher = ""'),
		)
		self.write(
			"demo_app/hooks.py", HOOKS.replace('app_publisher = "Example Org"', 'app_publisher = "Someone"')
		)
		self.commit()
		self.assertFalse(self.errors(self.run_check(), "L2"))
		self.write("demo_app/hooks.py", HOOKS.replace('app_publisher = "Example Org"', 'app_publisher = ""'))
		self.commit()
		found = self.errors(self.run_check(), "L2")
		self.assertTrue(any("app_publisher must be set" in m for m in found), found)

	def test_l2_conditional_assignment_warns(self):
		self.write("demo_app/hooks.py", HOOKS + 'if True:\n\tapp_color = "blue"\n')
		self.commit()
		warnings = [r.message for r in self.run_check().results if r.level == "warning"]
		self.assertTrue(any("app_color is assigned conditionally" in m for m in warnings), warnings)

	def test_l3_dev_range(self):
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace('">=16.0.0,<17.0.0"', '">=16.0.0-dev <17.0.0"'),
		)
		self.commit()
		self.assertTrue(self.errors(self.run_check(), "L3"))

	def test_l3_frappe_in_required_apps(self):
		self.write("demo_app/hooks.py", HOOKS.replace("required_apps = []", 'required_apps = ["frappe"]'))
		self.commit()
		found = self.errors(self.run_check(), "L3")
		self.assertTrue(any("must not name frappe" in m for m in found), found)

	def test_l4_trailing_comment_version(self):
		self.write("demo_app/__init__.py", '__version__ = "16.0.0"  # x-release-please-version\n')
		self.commit()
		found = self.errors(self.run_check(), "L4")
		self.assertTrue(any("block form" in m for m in found), found)

	def test_l5_print_in_init(self):
		self.write("demo_app/__init__.py", self.read("demo_app/__init__.py") + 'print("hello")\n')
		self.commit()
		found = self.errors(self.run_check(), "L5")
		self.assertTrue(any('print("hello")' in m for m in found), found)

	def test_l5_link_outside(self):
		os.symlink("/etc/hostname", self.root / "outside")
		self.commit()
		found = self.errors(self.run_check(), "L5")
		self.assertTrue(any("outside the repository" in m for m in found), found)

	def test_l6_override_without_allow_list(self):
		self.write("demo_app/hooks.py", HOOKS + 'override_doctype_class = {"ToDo": "demo_app.todo.ToDo"}\n')
		self.commit()
		found = self.errors(self.run_check(), "L6")
		self.assertTrue(any("override_doctype_class['ToDo']" in m for m in found), found)
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml")
			+ '\n[[tool.frappe-nix.override-doctype-class]]\ndoctype = "ToDo"\nreason = "Must replace ToDo: the fixture says so"\n',
		)
		self.commit()
		self.assertFalse(self.errors(self.run_check(), "L6"))

	def test_l8_logo_structure(self):
		self.write("demo_app/public/images/demo_app-logo.svg", LOGO.replace("#336699", "#000000"))
		self.commit()
		found = self.errors(self.run_check(), "L8")
		self.assertTrue(any("not org.brand.tile-color" in m for m in found), found)

	def test_l12_warns_and_is_skipped_without_templates(self):
		self.write(
			"marketplace/listing.toml",
			LISTING.replace("https://demo-app.docs.example.org/", "https://docs.example.org/demo/"),
		)
		self.commit()
		warnings = [r for r in self.run_check().results if r.rule == "L12"]
		self.assertEqual(len(warnings), 1)
		self.assertEqual(warnings[0].level, "warning")
		self.write(
			".standards-profile/profile.toml",
			PRODUCT_PROFILE.replace('docs-url = "https://{app_hyphen}.docs.example.org/"\n', ""),
		)
		self.commit()
		self.assertFalse([r for r in self.run_check().results if r.rule == "L12"])

	def test_release_needs_the_tag(self):
		outcome = self.run_check(release=True, tag="v16.0.1")
		self.assertTrue(any("but the tag is 'v16.0.1'" in m for m in self.errors(outcome, "L4")))


class TestBaseline(ListingCase):
	def hits(self, n: int, text: str = "frappe.db.commit()") -> list[Hit]:
		return [Hit("frappe-manual-commit", "demo_app/api.py", 10 + i, f"\t{text}") for i in range(n)]

	def write_baseline(self, count: int, text: str = "frappe.db.commit()") -> None:
		doc = {
			"schema": 1,
			"findings": [
				{
					"rule": "frappe-manual-commit",
					"path": "demo_app/api.py",
					"line_sha1": baseline.line_sha1(text),
					"count": count,
					"reason": "Background job commits per batch on purpose",
				}
			],
		}
		self.write(baseline.PATH, json.dumps(doc))
		self.commit()

	def test_seeded_by_sync(self):
		self.assertEqual(json.loads(self.read(baseline.PATH)), {"schema": 1, "findings": []})

	def test_new_findings_over_the_count(self):
		self.write_baseline(2)
		found = self.errors(self.run_check(self.hits(3)), "L7")
		self.assertEqual(len(found), 1)
		self.assertIn("3 found, the baseline allows 2", found[0])

	def test_fewer_findings_than_the_count(self):
		self.write_baseline(2)
		found = self.errors(self.run_check(self.hits(1)), "L7")
		self.assertEqual(len(found), 1)
		self.assertIn("lower its count to 1", found[0])

	def test_stale_entry(self):
		self.write_baseline(1, text="frappe.db.commit()  # gone")
		found = self.errors(self.run_check([]), "L7")
		self.assertTrue(any("remove the entry" in m for m in found), found)

	def test_exact_count_passes_and_release_wants_empty(self):
		self.write_baseline(2)
		self.assertFalse(self.errors(self.run_check(self.hits(2)), "L7"))
		self.assertTrue(
			any(
				"needs an empty baseline" in m
				for m in self.errors(self.run_check(self.hits(2), release=True, tag="v16.0.0"), "L7")
			)
		)

	def test_the_line_is_keyed_stripped(self):
		self.write_baseline(2)
		hits = [*self.hits(1, "frappe.db.commit()"), *self.hits(1, "    frappe.db.commit()   ")]
		self.assertFalse(self.errors(self.run_check(hits), "L7"))

	def test_malformed_baseline_is_invalid(self):
		self.write(baseline.PATH, '{"schema": 1, "findings": [{"rule": "x", "count": 0}]}')
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("count must be an integer of at least 1", out)
		self.assertTrue(baseline.problems('{"schema": 1, "findings": [{"rule": "x", "count": 0}]}'))


def make_registry(root: Path) -> Path:
	"""A bare registry repository with main: apps.json (2-space) listing one other app."""
	work = root / "registry-work"
	work.mkdir()
	git(work, "init", "-q", "-b", "main")
	(work / "apps").mkdir()
	(work / "apps.json").write_text(
		json.dumps([{"name": "other", "releases": "apps/other.json"}], indent=2) + "\n"
	)
	(work / "apps" / "other.json").write_text(json.dumps({"name": "other", "releases": []}, indent=2) + "\n")
	git(work, "add", "-A")
	git(work, "commit", "-q", "-m", "registry")
	bare = root / "example" / "marketplace"
	bare.parent.mkdir(parents=True)
	subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
	return bare


ADD_RELEASE = """import json, os, sys
from pathlib import Path
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
path = Path(args["--registry"]) / "apps" / (os.environ["APP"] + ".json")
doc = json.loads(path.read_text())
doc["releases"].append({"version": "16.0.0", "branch": os.environ["BRANCH"], "commit": os.environ["COMMIT"],
	"frappe_core": ">=16.0.0,<17.0.0", "dependencies": {}, "channel": os.environ["CHANNEL"]})
path.write_text(json.dumps(doc, indent=2) + "\\n")
"""


class TestRegistry(ListingCase):
	def setUp(self) -> None:
		super().setUp()
		self.remotes = self.root.parent / f"{self.root.name}-remotes"
		self.remotes.mkdir()
		make_registry(self.remotes)
		origin = self.remotes / "origin.git"
		subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
		git(self.root, "remote", "add", "origin", str(origin))
		git(self.root, "push", "-q", "origin", "HEAD:refs/heads/develop", "HEAD:refs/heads/version-16")
		git(self.root, "fetch", "-q", "origin")
		git(self.root, "tag", "v16.0.0")
		self.fake_marketplace = self.remotes / "marketplace-pin"
		(self.fake_marketplace / "tools").mkdir(parents=True)
		(self.fake_marketplace / "tools" / "add_release.py").write_text(ADD_RELEASE)
		env = mock.patch.dict(os.environ, {"FRAPPE_NIX_REGISTRY_URL": f"file://{self.remotes}/{{repo}}"})
		env.start()
		self.addCleanup(env.stop)

	def registry(self, *argv: str) -> tuple[int, str]:
		with mock.patch.object(check, "marketplace_tree", return_value=(self.fake_marketplace, "c" * 40)):
			code, out, err = self.fn("listing", "registry", *argv)
		return code, out + err

	def test_dry_run_uses_the_fork_and_its_branch(self):
		code, out = self.registry("--dry-run", "--onboard", "--no-check", "--upstream", "example/marketplace")
		self.assertEqual(code, 0, out)
		self.assertIn("example/marketplace main ← Example-Org/marketplace:example-org/demo_app", out)
		self.assertIn("title: demo_app: onboard and 16.0.0", out)
		self.assertIn('"name": "demo_app"', out)
		self.assertIn("nothing was pushed", out)

	def test_without_yes_nothing_is_pushed(self):
		with mock.patch("frappe_nix_tools.listing.registry.publish") as publish:
			code, out = self.registry("--onboard", "--no-check", "--upstream", "example/marketplace")
		self.assertEqual(code, 0, out)
		publish.assert_not_called()

	def test_no_check_is_refused_for_a_real_run(self):
		code, out = self.registry("--yes", "--no-check", "--upstream", "example/marketplace")
		self.assertEqual(code, 2, out)

	def test_empty_fork_is_exit_2(self):
		self.write(
			".standards-profile/profile.toml",
			PRODUCT_PROFILE.replace('registry-fork = "Example-Org/marketplace"\n', ""),
		)
		self.commit()
		code, out = self.registry("--dry-run", "--no-check")
		self.assertEqual(code, 2, out)
		self.assertIn("listing.registry-fork is empty", out)

	def test_publish_off_is_exit_2(self):
		self.write(".standards-profile/profile.toml", PRODUCT_PROFILE.replace("publish = true\n", ""))
		self.commit()
		code, out = self.registry("--dry-run", "--no-check")
		self.assertEqual(code, 2, out)
		self.assertIn("needs listing.publish", out)


class TestReadme(ListingCase):
	def test_rendered_and_clean(self):
		text = self.read("README.md")
		self.assertIn("# Demo App", text)
		self.assertIn("Hand-written introduction.", text)
		self.assertIn("bench get-app https://github.com/example-org/demo_app --branch version-16", text)
		self.assertIn("MIT, Copyright (c) ", text)
		self.assertIn("\u2013present Example Org → [license.txt](license.txt)", text)
		self.assertIn("(https://github.com/Avunu/frappe-nix/tree/main/docs/app-standards)", text)
		self.assertIn('<img src="demo_app/public/images/demo_app-logo.svg" width="80" alt="">', text)
		code, out, err = self.fn("listing", "readme", "--check")
		self.assertEqual(code, 0, out + err)

	def test_a_missing_marker_is_exit_2(self):
		self.write("README.md", self.read("README.md").replace("<!-- frappe-nix:end support -->\n", ""))
		self.commit()
		code, out, err = self.fn("listing", "readme", "--check")
		self.assertEqual(code, 2, out + err)
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("block support", out)

	def test_an_edited_block_is_drift(self):
		self.write("README.md", self.read("README.md").replace("## Support", "## Help"))
		self.commit()
		code, out, err = self.fn("listing", "readme", "--check")
		self.assertEqual(code, 1, out + err)
		code, out, err = self.fn("listing", "readme", "--write")
		self.assertEqual(code, 0, out + err)
		self.assertIn("## Support", self.read("README.md"))

	def test_repo_url_template(self):
		self.write(
			".standards-profile/profile.toml",
			PRODUCT_PROFILE.replace(
				'github-owner = "example-org"\n', 'repo-url = "https://git.example.org/{repo}"\n'
			),
		)
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				"[tool.frappe-nix]\n", '[tool.frappe-nix]\nrepo = "example-org/demo_app"\n'
			),
		)
		self.commit()
		self.fn("sync", "--write")
		self.assertIn(
			"bench get-app https://git.example.org/example-org/demo_app --branch", self.read("README.md")
		)

	def test_main_tags_names_the_tag_inside_the_markers(self):
		self.write(
			".standards-profile/profile.toml", PRODUCT_PROFILE + '\n[releases]\nbranching = "main+tags"\n'
		)
		self.commit()
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		text = self.read("README.md")
		start = text.index("<!-- x-release-please-start-version -->")
		end = text.index("<!-- x-release-please-end -->")
		self.assertIn("--branch v16.0.0", text[start:end])
		rp = json.loads(self.read("release-please-config.json"))
		self.assertIn({"type": "generic", "path": "README.md"}, rp["packages"]["."]["extra-files"])
		# release-please bumps __version__ and the README line together; the blocks still check.
		self.write("demo_app/__init__.py", self.read("demo_app/__init__.py").replace("16.0.0", "16.1.0"))
		self.write("README.md", self.read("README.md").replace("--branch v16.0.0", "--branch v16.1.0"))
		code, out, err = self.fn("listing", "readme", "--check")
		self.assertEqual(code, 0, out + err)

	def test_dev_docs_and_copyright_holder(self):
		self.write(
			".standards-profile/profile.toml",
			PRODUCT_PROFILE.replace(
				'license = "MIT"\n',
				'license = "AGPL-3.0"\ncopyright-holder = "Example Holdings"\ndev-docs-url = "https://docs.example.org/dev/"\n',
			),
		)
		self.write("demo_app/hooks.py", HOOKS.replace('"MIT"', '"AGPL-3.0"'))
		self.commit()
		self.fn("sync", "--write")
		text = self.read("README.md")
		self.assertIn("AGPL-3.0, Copyright (c) ", text)
		self.assertIn("\u2013present Example Holdings →", text)
		self.assertIn("(https://docs.example.org/dev/)", text)
		self.assertIn("license-AGPL--3.0-blue", text)

	def test_an_empty_license_is_exit_2(self):
		self.write(".standards-profile/profile.toml", PRODUCT_PROFILE.replace('license = "MIT"\n', ""))
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 2, out)
		self.assertIn("org.license", out)

	def test_the_profile_overrides_a_block(self):
		self.write(
			".standards-profile/templates/readme/support.md.j2",
			"## Support\n\nWrite to {{ org.email }} ({{ readme.issues_url }}).\n",
		)
		self.commit()
		self.fn("sync", "--write")
		self.assertIn(
			"Write to apps@example.org (https://github.com/example-org/demo_app/issues).",
			self.read("README.md"),
		)

	def test_readme_off_removes_the_blocks(self):
		self.write(
			".standards-profile/profile.toml", PRODUCT_PROFILE.replace("[readme]\nenable = true\n", "")
		)
		self.commit()
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		text = self.read("README.md")
		self.assertNotIn("frappe-nix:begin", text)
		self.assertIn("Hand-written introduction.", text)
		self.assertNotIn("## Support", text)


class TestOffByDefault(AppCase):
	"""recommended has listing, readme and icons off: the tools say so and do nothing."""

	def test_notices(self):
		for argv in (("listing", "check"), ("listing", "readme", "--check"), ("icon", "check")):
			code, _, err = self.fn(*argv)
			self.assertEqual(code, 0, err)
			self.assertIn("module is off", err)

	def test_rules_module_shape(self):
		self.assertEqual(rules.LISTING, "marketplace/listing.toml")
