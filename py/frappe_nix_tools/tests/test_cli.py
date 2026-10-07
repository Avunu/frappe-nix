import importlib
import os
import unittest
from unittest import mock

import frappe_nix_tools
from frappe_nix_tools import cli
from frappe_nix_tools.commands import paths
from frappe_nix_tools.common.report import ConfigError
from helpers import run_cli


class TestCli(unittest.TestCase):
	def test_version(self):
		code, out, _ = run_cli("--version")
		self.assertEqual(code, 0)
		self.assertEqual(out.strip(), frappe_nix_tools.__version__)

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
		self.assertIn("frappe-nix data-path: no such data file", err)

	def test_unexpected_exception_exits_3_not_1(self):
		with mock.patch.object(paths, "data_path", side_effect=RuntimeError("boom")):
			code, _, err = run_cli("data-path", "known-apps.json")
		self.assertEqual(code, 3)
		self.assertEqual(err, "frappe-nix data-path: internal error: RuntimeError: boom\n")

	def test_unexpected_exception_traceback_on_request(self):
		with (
			mock.patch.object(paths, "data_path", side_effect=RuntimeError("boom")),
			mock.patch.dict(os.environ, {"FRAPPE_NIX_DEBUG": "1"}),
		):
			code, _, err = run_cli("data-path", "known-apps.json")
		self.assertEqual(code, 3)
		self.assertIn("Traceback", err)

	def test_a_command_module_that_fails_to_import_exits_3(self):
		# A broken commands/* module, or a dependency missing from the environment, fails
		# while the parser is built: an environment error, never 1 (drift).
		real = importlib.import_module

		def broken(name, *args):
			if name.startswith("frappe_nix_tools.commands."):
				raise ModuleNotFoundError("No module named 'packaging'")
			return real(name, *args)

		with mock.patch.object(cli.importlib, "import_module", side_effect=broken):
			code, _, err = run_cli("config", "modules.ci")
		self.assertEqual(code, 3)
		self.assertEqual(
			err, "frappe-nix: internal error: ModuleNotFoundError: No module named 'packaging'\n"
		)


if __name__ == "__main__":
	unittest.main()
