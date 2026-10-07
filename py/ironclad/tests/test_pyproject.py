import tempfile
import unittest
from pathlib import Path

from ironclad.common import pyproject
from ironclad.common.report import ConfigError, EnvError

DOC = {
	"tool": {
		"ironclad": {
			"schema": 1,
			"frappe-major": 16,
			"siblings": ["erpnext"],
			"test": {"setup": ["execute:x.y"]},
			"typescript": {"browser": False},
		}
	}
}


class TestPyproject(unittest.TestCase):
	def test_load(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "pyproject.toml"
			with self.assertRaises(EnvError):
				pyproject.load(path)
			path.write_text("[tool\n")
			with self.assertRaises(ConfigError):
				pyproject.load(path)
			path.write_text("[tool.ironclad]\nschema = 1\n")
			self.assertEqual(pyproject.load(path)["tool"]["ironclad"]["schema"], 1)

	def test_defaults_come_from_the_schema(self):
		d = pyproject.defaults()
		self.assertIs(d["pilot-assets"], False)
		self.assertEqual(d["js-coverage-min"], 50)
		self.assertEqual(d["nightly-suites"], [])
		self.assertIs(d["typescript"]["browser"], True)
		self.assertEqual(d["test"]["setup"], [])
		self.assertNotIn("site", d)
		self.assertNotIn("schema", d)

	def test_config_merges_over_defaults(self):
		cfg = pyproject.config(DOC)
		self.assertEqual(cfg["siblings"], ["erpnext"])
		self.assertEqual(cfg["test"]["setup"], ["execute:x.y"])
		self.assertIs(cfg["typescript"]["browser"], False)
		self.assertEqual(cfg["typescript"]["exclude"], [])
		self.assertEqual(cfg["generated"], [])

	def test_get(self):
		cfg = pyproject.config(DOC)
		self.assertEqual(pyproject.get(cfg, "frappe-major"), 16)
		self.assertEqual(pyproject.get(cfg, "test.setup"), ["execute:x.y"])
		self.assertIsNone(pyproject.get(cfg, "site"))
		with self.assertRaises(ConfigError):
			pyproject.get(cfg, "nonsense")
		with self.assertRaises(ConfigError):
			pyproject.get(cfg, "test.nonsense")

	def test_no_table(self):
		self.assertIsNone(pyproject.tool_ironclad({}))
		self.assertEqual(pyproject.config({}), pyproject.defaults())
		with self.assertRaises(ConfigError):
			pyproject.tool_ironclad({"tool": {"ironclad": 1}})


if __name__ == "__main__":
	unittest.main()
