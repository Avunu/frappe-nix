"""`bench restore`'s pieces that tests/bench-restore.sh reaches only from outside:
argument parsing, the configuration schema, the backup layout's naming and the
unguarded-restore rule. The command itself is driven end to end by that script."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest import mock

from frappe_nix_tools import bench_cli
from frappe_nix_tools.restore import backup_layout, command
from frappe_nix_tools.restore.args import RestoreArgs, UsageError, parse_args
from frappe_nix_tools.restore.config import ENV, ConfigError, load, parse

RESTORE = {
	"enable": True,
	"fetch": "/bin/fetch",
	"secretsEnv": None,
	"prefix": "",
	"files": "none",
	"carryConfigKeys": ["encryption_key", "backup_encryption_key"],
	"migrate": True,
	"neutralize": True,
	"requireDevguard": True,
	"devguard": True,
}


# The apps commands' section, which the same file carries
# (frappe_nix_tools.bench_config); tests/test_apps.py checks its schema.
APPS = {
	"bench": "bench",
	"python": "python3",
	"workspace": "/bin/frappe-nix-workspace",
	"sources": "/bin/frappe-nix-sources",
	"buildDepsSync": None,
	"renameTool": "/lib/frappe_rename_app.py",
	"renamedApps": [],
	"replacedApps": [],
}

# The dependency commands' section (frappe_nix_tools.deps.config);
# tests/test_deps.py checks its schema.
DEPS = {
	"appsWithNode": [],
	"appMode": False,
	"nodeVerify": "/bin/frappe-nix-node-verify",
	"nodeModules": "/bin/frappe-nix-node-modules",
	"requirementsCheck": "/lib/requirements-check.py",
}


def restore_config(**overrides):
	return parse({"restore": {**RESTORE, **overrides}, "apps": APPS, "deps": DEPS}).restore


class TestArgs(unittest.TestCase):
	def test_defaults(self):
		self.assertEqual(parse_args([]), RestoreArgs())

	def test_flags(self):
		args = parse_args(
			[
				"--list",
				"--files",
				"--private-files",
				"--no-cache",
				"--no-site-config",
				"--no-migrate",
				"--no-force",
			]
		)
		self.assertTrue(args.list_only and args.files and args.private_files and args.no_cache)
		self.assertFalse(args.seed_config or args.migrate is not False or args.force)

	def test_neutralize_both_ways_last_wins(self):
		self.assertIsNone(parse_args([]).neutralize)
		self.assertTrue(parse_args(["--neutralize"]).neutralize)
		self.assertFalse(parse_args(["--neutralize", "--no-neutralize"]).neutralize)

	def test_at_and_encryption_key_take_a_value_either_way(self):
		for argv in (["--at", "2026-10-02T03:00"], ["--at=2026-10-02T03:00"]):
			self.assertEqual(parse_args(argv).at, "2026-10-02T03:00")
		args = parse_args(["--encryption-key=K", "dump.sql.gz"])
		self.assertEqual((args.encryption_key, args.sql_file, args.passthru), ("K", "dump.sql.gz", []))
		# The bash version took the value of an option it did not know as the
		# file; a declared one is not.
		self.assertEqual(parse_args(["--encryption-key", "K"]).sql_file, None)

	def test_a_valued_option_without_its_value_is_refused(self):
		with self.assertRaisesRegex(UsageError, "--at needs a value"):
			parse_args(["--at"])

	def test_unknown_options_and_the_rest_after_double_dash_pass_through(self):
		args = parse_args(["f.sql", "--verbose", "--", "--admin-password", "x", "--list"])
		self.assertEqual(args.sql_file, "f.sql")
		self.assertFalse(args.list_only)
		self.assertEqual(args.passthru, ["--verbose", "--admin-password", "x", "--list"])

	def test_one_file_at_most(self):
		with self.assertRaisesRegex(UsageError, "unexpected argument 'b.sql'"):
			parse_args(["a.sql", "b.sql"])

	def test_help_stops_parsing_where_it_is(self):
		self.assertTrue(parse_args(["--at", "x", "-h", "a", "b"]).help)
		with self.assertRaises(UsageError):
			parse_args(["a", "b", "--help"])

	def test_click_keeps_a_leading_double_dash(self):
		seen = []
		with mock.patch("frappe_nix_tools.restore.command.main", side_effect=lambda a: seen.append(a) or 0):
			for argv in (["restore", "--", "--foo", "bar"], ["restore", "--at", "x", "--", "--foo"]):
				with self.assertRaises(SystemExit):
					bench_cli.main(argv)
		self.assertEqual(seen, [["--", "--foo", "bar"], ["--at", "x", "--", "--foo"]])


class TestConfig(unittest.TestCase):
	def test_reads_the_generated_file(self):
		cfg = restore_config(secretsEnv="/bin/env0", files="all")
		self.assertEqual((cfg.secrets_env, cfg.files, cfg.require_devguard), ("/bin/env0", "all", True))
		self.assertEqual(cfg.carry_config_keys, ["encryption_key", "backup_encryption_key"])

	def test_unknown_and_missing_keys_fail(self):
		with self.assertRaisesRegex(ConfigError, r"config\.restore: unknown key\(s\): withFiles"):
			parse({"restore": {**RESTORE, "withFiles": "none"}, "apps": APPS, "deps": DEPS})
		partial = dict(RESTORE)
		del partial["neutralize"]
		with self.assertRaisesRegex(ConfigError, r"missing key\(s\): neutralize"):
			parse({"restore": partial, "apps": APPS, "deps": DEPS})
		with self.assertRaisesRegex(ConfigError, "unknown key"):
			parse({"restore": RESTORE, "apps": APPS, "deps": DEPS, "update": {}})

	def test_types_are_checked(self):
		for key, value, message in (
			("files", "some", "is not one of"),
			("migrate", "yes", "expected true or false"),
			("carryConfigKeys", ["a", 1], r"carryConfigKeys\[1\]: expected a string"),
			("secretsEnv", 3, "expected a string or null"),
			("fetch", None, "expected a string"),
		):
			with self.subTest(key=key), self.assertRaisesRegex(ConfigError, message):
				parse({"restore": {**RESTORE, key: value}, "apps": APPS, "deps": DEPS})

	def test_load_names_the_variable(self):
		with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ConfigError, ENV):
			load()
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp, "c.json")
			path.write_text(json.dumps({"restore": RESTORE, "apps": APPS, "deps": DEPS}))
			with mock.patch.dict(os.environ, {ENV: str(path)}):
				self.assertTrue(load().restore.enable)
			path.write_text("{")
			with self.assertRaisesRegex(ConfigError, "cannot read"):
				load(path)


@dataclass(frozen=True)
class Entry:
	key: str
	size: int = 1
	is_dir: bool = False


class TestBackupLayout(unittest.TestCase):
	"""Backup naming: what a folder of Frappe's S3 backup integration becomes."""

	def parse(self, *keys):
		return {s["name"]: s for s in backup_layout.parse([Entry(k) for k in keys])}

	def test_one_folder_per_backup_with_its_roles(self):
		ts = "20261002_030405"
		sets = self.parse(
			f"{ts}/{ts}-erp_example_com-database-enc.sql.gz",
			f"{ts}/{ts}-erp_example_com-site_config_backup-enc.json",
			f"{ts}/{ts}-erp_example_com-files-enc.tar",
			f"{ts}/{ts}-erp_example_com-private-files-enc.tgz",
			"not-a-backup/x",
			"20261002/short",
		)
		self.assertEqual(list(sets), [ts])
		s = sets[ts]
		self.assertEqual(s["at"], datetime(2026, 10, 2, 3, 4, 5))
		self.assertEqual(s["meta"], {"folder": ts, "slug": "erp_example_com", "encrypted": True})
		self.assertEqual(
			{role: e.key.rsplit("-", 1)[0].rsplit("/", 1)[1] for role, e in s["objects"].items()},
			{
				"database": f"{ts}-erp_example_com-database",
				"site_config": f"{ts}-erp_example_com-site_config_backup",
				"files": f"{ts}-erp_example_com-files",
				"private_files": f"{ts}-erp_example_com-private-files",
			},
		)
		self.assertIsNone(s["problem"])

	def test_a_partial_dump_is_a_problem_not_a_database(self):
		ts = "20261002_030405"
		s = self.parse(f"{ts}/{ts}-erp-partial-database.sql.gz")[ts]
		self.assertNotIn("database", s["objects"])
		self.assertIn("PARTIAL", s["problem"])

	def test_a_slug_ending_in_l_is_not_partial(self):
		ts = "20261002_030405"
		s = self.parse(f"{ts}/{ts}-mail-database.sql.gz")[ts]
		self.assertEqual(s["meta"]["slug"], "mail")


class TestUnguarded(unittest.TestCase):
	def test_refuses_only_an_unguarded_bench_that_requires_it(self):
		with mock.patch.dict(os.environ, {}, clear=True):
			command.require_devguard(restore_config())
			command.require_devguard(restore_config(devguard=False, requireDevguard=False))
			with self.assertRaises(command.Abort) as caught:
				command.require_devguard(restore_config(devguard=False))
		self.assertEqual(caught.exception.status, 1)
		self.assertIn("refusing", caught.exception.lines[0])

	def test_the_override_and_its_old_name(self):
		cfg = restore_config(devguard=False)
		with mock.patch.dict(os.environ, {command.ALLOW_UNGUARDED: "1"}, clear=True):
			command.require_devguard(cfg)
		err = io.StringIO()
		with (
			mock.patch.dict(os.environ, {command.ALLOW_UNGUARDED_LEGACY: "1"}, clear=True),
			contextlib.redirect_stderr(err),
		):
			command.require_devguard(cfg)
		self.assertIn("deprecated", err.getvalue())
		with (
			mock.patch.dict(os.environ, {command.ALLOW_UNGUARDED: "0"}, clear=True),
			self.assertRaises(command.Abort),
		):
			command.require_devguard(cfg)

	def test_jq_alternative(self):
		self.assertEqual(command.jq_alt({"a": None, "b": False, "c": ""}, "a", "b", "c"), "")
		self.assertIsNone(command.jq_alt({}, "a"))


if __name__ == "__main__":
	unittest.main()
