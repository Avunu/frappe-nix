import json
import tempfile
import unittest
from pathlib import Path

from helpers import run_cli
from ironclad.commands.config import lines

PYPROJECT = """\
[project]
name = "demo_app"

[tool.ironclad]
schema = 1
frappe-major = 16
nightly-suites = ["node scripts/test-tables.ts", "echo two"]
pilot-assets = true

[[tool.ironclad.untested]]
target = "demo_app.api.legacy"
reason = "Kept for old kiosk bundles"
"""


class TestConfig(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.path = Path(self.tmp.name) / "pyproject.toml"
		self.path.write_text(PYPROJECT)

	def tearDown(self):
		self.tmp.cleanup()

	def config(self, key):
		return run_cli("config", key, "--pyproject", str(self.path))

	def test_list_one_item_per_line(self):
		self.assertEqual(self.config("nightly-suites"), (0, "node scripts/test-tables.ts\necho two\n", ""))

	def test_scalars(self):
		self.assertEqual(self.config("frappe-major")[1], "16\n")
		self.assertEqual(self.config("pilot-assets")[1], "true\n")
		self.assertEqual(self.config("track-overrides")[1], "false\n")

	def test_default_and_unset(self):
		self.assertEqual(self.config("shell-checks"), (0, "", ""))
		self.assertEqual(self.config("site"), (0, "", ""))
		self.assertEqual(self.config("typescript.browser")[1], "true\n")

	def test_tables_print_as_json(self):
		code, out, _ = self.config("untested")
		self.assertEqual(code, 0)
		self.assertEqual(
			json.loads(out), {"target": "demo_app.api.legacy", "reason": "Kept for old kiosk bundles"}
		)

	def test_unknown_key_exits_2(self):
		code, _, err = self.config("nope")
		self.assertEqual(code, 2)
		self.assertIn("no key 'nope'", err)

	def test_missing_pyproject_exits_3(self):
		self.path.unlink()
		self.assertEqual(self.config("site")[0], 3)

	def test_lines(self):
		self.assertEqual(lines(None), [])
		self.assertEqual(lines({"b": 1, "a": 2}), ['{"a": 2, "b": 1}'])


if __name__ == "__main__":
	unittest.main()
