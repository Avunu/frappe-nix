"""The engine's parts: globs, the oxfmt-shaped JSON, the manifest, blocks, compat and unchecked-js."""

import json
import unittest

from ironclad.scaffold import blocks, globs, jsonfmt, manifest
from scaffold_helpers import AppCase


class TestGlobs(unittest.TestCase):
	def test_match(self):
		self.assertTrue(globs.match("**/web_form/**", "a/web_form/x.js"))
		self.assertTrue(globs.match("**/web_form/**", "web_form/x.js"))
		self.assertTrue(globs.match("a/**/*.js", "a/b.js"))
		self.assertTrue(globs.match("a/**/*.js", "a/x/y/b.js"))
		self.assertFalse(globs.match("a/*.js", "a/x/b.js"))
		self.assertFalse(globs.match("*.bundle.js", "a/x.bundle.js"))

	def test_glob_to_regex(self):
		self.assertEqual(globs.glob_to_regex("app/public/js/generated/**"), "app/public/js/generated/.*$")
		self.assertEqual(globs.glob_to_regex("components.d.ts"), r"components\.d\.ts$")
		self.assertEqual(globs.glob_to_regex("a/*.ts"), "a/[^/]*\\.ts$")


class TestJsonfmt(unittest.TestCase):
	"""Outputs taken from oxfmt 0.72.0 with useTabs and printWidth 110."""

	def test_objects_expand_and_short_arrays_stay_inline(self):
		self.assertEqual(
			jsonfmt.dumps({"a": ["x", "y"], "b": {"c": 1}, "e": []}),
			'{\n\t"a": ["x", "y"],\n\t"b": {\n\t\t"c": 1\n\t},\n\t"e": []\n}\n',
		)

	def test_width_counts_tabs_as_two_and_the_comma(self):
		def line(n: int) -> str:
			return jsonfmt.dumps({"o": {f"k{n}": ["x" * n], "z": 1}}).split("\n")[2]

		self.assertTrue(line(94).endswith("],"), "110 columns fits")
		self.assertTrue(line(95).endswith("["), "111 columns breaks")

	def test_jsonc_trailing_commas(self):
		self.assertEqual(
			jsonfmt.dumps({"a": [{"b": 1}]}, jsonc=True),
			'{\n\t"a": [\n\t\t{\n\t\t\t"b": 1,\n\t\t},\n\t],\n}\n',
		)


class TestManifest(unittest.TestCase):
	def test_packaged_manifest_loads(self):
		m = manifest.load()
		paths = [e.path for e in m.entries]
		self.assertEqual(len(paths), len(set(paths)))
		self.assertIn("flake.nix", paths)

	def test_a_path_in_two_fragments_is_refused(self):
		entry = {"path": "x", "template": "t", "strategy": "whole"}
		with self.assertRaisesRegex(
			manifest.ManifestError, "listed in manifest.d/a.json and manifest.d/b.json"
		):
			manifest.parse({"a.json": {"entries": [entry]}, "b.json": {"entries": [entry]}})

	def test_unknown_strategy_is_refused(self):
		with self.assertRaises(manifest.ManifestError):
			manifest.parse({"a.json": {"entries": [{"path": "x", "strategy": "magic"}]}})


class TestBlocks(unittest.TestCase):
	def test_gitignore_appends_then_replaces(self):
		first = blocks.gitignore("node_modules", "a\nb\n")
		self.assertEqual(first, f"node_modules\n{blocks.GITIGNORE_BEGIN}\na\nb\n{blocks.GITIGNORE_END}\n")
		self.assertEqual(
			blocks.gitignore(first + "mine\n", "c\n"),
			f"node_modules\n{blocks.GITIGNORE_BEGIN}\nc\n{blocks.GITIGNORE_END}\nmine\n",
		)

	def test_init_py_forms(self):
		block = '# x-release-please-start-version\n__version__ = "1.2.3"\n# x-release-please-end\n'
		for text in ('__version__ = "1.2.3"\n', "__version__ = '1.2.3'  # x-release-please-version\n", block):
			self.assertEqual(blocks.init_py(text), (block, []))
		stamped = "# Copyright\n\n" + block
		self.assertEqual(blocks.init_py(stamped), (stamped, []))
		self.assertEqual(blocks.init_py('"""Doc."""\n' + block)[1], ['"""Doc."""'])


class TestCompat(AppCase):
	def test_clean_after_sync(self):
		self.synced()
		code, out, _ = self.ironclad("compat")
		self.assertEqual(code, 0, out)

	def test_rules(self):
		self.synced()
		pkg = json.loads(self.read("package.json"))
		pkg["version"] = "17.0.0"
		pkg["frappe"] = {"major": "15"}
		self.write("package.json", json.dumps(pkg))
		self.write("types/frappe.d.ts", "declare var frappe: any;\ndeclare namespace frappe { }\n")
		self.write(
			"types/demo_app.augment.d.ts",
			"declare global {\n\tnamespace frappe.ui {\n\t\t// app-owned: the picker\n\t\tconst ok: number;\n\t\tconst bad: number;\n\t}\n}\nexport {};\n",
		)
		self.write(".git-blame-ignore-revs", "deadbeef style\n")
		self.write("demo_app/hooks.py", 'required_apps = ["frappe", "erpnext"]\n')
		self.commit()
		code, out, _ = self.ironclad("compat")
		self.assertEqual(code, 1)
		for rule in (
			"C1 ",
			"C2 ",
			"C4 ",
			"C5 ",
			"C6 ",
			"C7 types/frappe.d.ts",
			"`const bad",
			".git-blame-ignore-revs",
		):
			self.assertIn(rule, out)
		self.assertNotIn("`const ok", out)

	def test_add_blame_ignore(self):
		self.synced()
		sha = self.ironclad("compat", "--add-blame-ignore", "HEAD")
		self.assertEqual(sha[0], 0)
		lines = self.read(".git-blame-ignore-revs").splitlines()
		self.assertRegex(lines[-1], r"^[0-9a-f]{40}  # test$")
		self.commit()
		self.assertEqual(self.ironclad("compat")[0], 0)


class TestUncheckedJs(AppCase):
	extra_pyproject = 'unchecked-js = [{ path = "demo_app/public/js/a.js", reason = "typed in a later PR" }, { path = "demo_app/public/js/b.js", reason = "typed in a later PR" }]\n'

	def test_stale_entries(self):
		self.write("demo_app/public/js/a.js", "var a = 1;\n")
		self.write("demo_app/public/js/b.js", "var b = 1;\n")
		tsc = self.write(
			"fake-tsc",
			"#!/bin/sh\necho 'demo_app/public/js/a.js(1,5): error TS2304: Cannot find name x.'\nexit 2\n",
		)
		tsc.chmod(0o755)
		self.commit()
		code, out, err = self.ironclad("unchecked-js", "--stale", "--tsc", str(tsc))
		self.assertEqual(code, 1, err)
		self.assertIn("demo_app/public/js/b.js has no type errors left", out)
		self.assertNotIn("a.js has", out)
		project = json.loads(self.read(".dev-dist/unchecked-js/tsconfig.json"))
		self.assertEqual(project["files"], ["../../demo_app/public/js/a.js", "../../demo_app/public/js/b.js"])

	def test_no_tsc_is_exit_3(self):
		self.assertEqual(self.ironclad("unchecked-js", "--stale")[0], 3)


if __name__ == "__main__":
	unittest.main()
