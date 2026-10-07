import unittest

import ironclad
from helpers import run_cli
from ironclad.common.report import ConfigError


class TestCli(unittest.TestCase):
	def test_version(self):
		code, out, _ = run_cli("--version")
		self.assertEqual(code, 0)
		self.assertEqual(out.strip(), ironclad.__version__)

	def test_unknown_command_exits_2_listing_commands(self):
		code, _, err = run_cli("nonexistent")
		self.assertEqual(code, 2)
		for name in ("config", "data-path", "pin-path"):
			self.assertIn(f"'{name}'", err)

	def test_no_command_exits_2_with_help(self):
		code, _, err = run_cli()
		self.assertEqual(code, 2)
		self.assertIn("data-path", err)

	def test_error_maps_to_its_code(self):
		code, _, err = run_cli("data-path", "does/not/exist.json")
		self.assertEqual(code, ConfigError.code)
		self.assertIn("ironclad data-path: no such data file", err)


if __name__ == "__main__":
	unittest.main()
