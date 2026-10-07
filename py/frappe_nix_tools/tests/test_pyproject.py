import tempfile
import unittest
from pathlib import Path

from frappe_nix_tools.common import pyproject
from frappe_nix_tools.common.report import ConfigError, EnvError


class TestPyproject(unittest.TestCase):
	def test_load(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "pyproject.toml"
			with self.assertRaises(EnvError):
				pyproject.load(path)
			path.write_text("[tool\n")
			with self.assertRaises(ConfigError):
				pyproject.load(path)
			path.write_text("[tool.frappe-nix]\nschema = 1\n")
			self.assertEqual(pyproject.load(path)["tool"]["frappe-nix"]["schema"], 1)

	def test_table_and_opt_in(self):
		self.assertIsNone(pyproject.tool_frappe_nix({}))
		self.assertFalse(pyproject.opted_in({"tool": {"ruff": {}}}))
		self.assertTrue(pyproject.opted_in({"tool": {"frappe-nix": {}}}))
		with self.assertRaises(ConfigError):
			pyproject.tool_frappe_nix({"tool": {"frappe-nix": 1}})

	def test_project_name(self):
		self.assertEqual(pyproject.project_name({"project": {"name": "demo_app"}}), "demo_app")
		self.assertIsNone(pyproject.project_name({}))


if __name__ == "__main__":
	unittest.main()
