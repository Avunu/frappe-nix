import json
import unittest

from frappe_nix_tools.common import data_path
from frappe_nix_tools.common.report import ConfigError


class TestDataPath(unittest.TestCase):
	def test_shipped_files(self):
		for rel in (
			"known-apps.json",
			"schema/tool-frappe-nix.schema.json",
			"schema/profile.schema.json",
		):
			path = data_path(rel)
			self.assertTrue(path.is_file(), path)
			self.assertTrue(path.is_absolute())
			json.loads(path.read_text())

	def test_directory(self):
		self.assertTrue(data_path("schema").is_dir())

	def test_outside_or_missing(self):
		for rel in ("", "../pyproject.toml", "/etc/passwd", "nope.json"):
			with self.subTest(rel=rel), self.assertRaises(ConfigError):
				data_path(rel)


if __name__ == "__main__":
	unittest.main()
