import os
import unittest
from unittest import mock

import ironclad
from helpers import run_cli
from ironclad.commands import paths
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
			self.assertRegex(err, rf"\b'?{name}'?\b")

	def test_no_command_exits_2_with_help(self):
		code, _, err = run_cli()
		self.assertEqual(code, 2)
		self.assertIn("data-path", err)

	def test_error_maps_to_its_code(self):
		code, _, err = run_cli("data-path", "does/not/exist.json")
		self.assertEqual(code, ConfigError.code)
		self.assertIn("ironclad data-path: no such data file", err)

	def test_unexpected_exception_exits_3_not_1(self):
		with mock.patch.object(paths, "data_path", side_effect=RuntimeError("boom")):
			code, _, err = run_cli("data-path", "known-apps.json")
		self.assertEqual(code, 3)
		self.assertEqual(err, "ironclad data-path: internal error: RuntimeError: boom\n")

	def test_unexpected_exception_traceback_on_request(self):
		with (
			mock.patch.object(paths, "data_path", side_effect=RuntimeError("boom")),
			mock.patch.dict(os.environ, {"IRONCLAD_DEBUG": "1"}),
		):
			code, _, err = run_cli("data-path", "known-apps.json")
		self.assertEqual(code, 3)
		self.assertIn("Traceback", err)


if __name__ == "__main__":
	unittest.main()
