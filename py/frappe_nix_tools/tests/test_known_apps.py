import unittest

from frappe_nix_tools.common import known_apps
from frappe_nix_tools.common.report import ConfigError


class TestKnownApps(unittest.TestCase):
	def test_frappe_and_erpnext(self):
		s = known_apps.resolve("erpnext", 16)
		self.assertEqual(
			(s.repo, s.branch, s.range, s.desk_global),
			("frappe/erpnext", "version-16", ">=16.0.0,<17.0.0", "erpnext"),
		)
		self.assertEqual(s.flake_url, "github:frappe/erpnext/version-16")
		self.assertEqual(s.input, "erpnext")
		self.assertIsNone(known_apps.resolve("frappe", 16).desk_global)

	def test_payments_range_is_fixed(self):
		self.assertEqual(known_apps.resolve("payments", 17).range, ">=0.0.1,<1.0.0")

	def test_generic_rule_for_any_owner(self):
		s = known_apps.resolve("example/esign", 16)
		self.assertEqual((s.name, s.spelling, s.repo), ("esign", "example/esign", "example/esign"))
		self.assertEqual((s.branch, s.range), ("version-16", ">=16.0.0,<17.0.0"))
		self.assertEqual(s.flake_url, "github:example/esign/version-16")

	def test_no_owner_is_special(self):
		self.assertNotIn("Avunu/*", known_apps.builtin())
		self.assertEqual(known_apps.resolve("someone/app_x", 16).repo, "someone/app_x")

	def test_object_form_off_the_frappe_convention(self):
		s = known_apps.resolve(
			{"repo": "example/shared_lib", "branch": "main", "range": ">=2.0.0,<3.0.0"}, 16
		)
		self.assertEqual(s.flake_url, "github:example/shared_lib/main")
		self.assertEqual((s.name, s.range), ("shared_lib", ">=2.0.0,<3.0.0"))

	def test_object_form_on_another_host(self):
		url = "git+https://git.example.org/libs/shared_lib?ref=main"
		s = known_apps.resolve(
			{"repo": "git.example.org/libs/shared_lib", "flake-url": url, "branch": "main"}, 16
		)
		self.assertEqual((s.name, s.flake_url, s.branch), ("shared_lib", url, "main"))
		with self.assertRaisesRegex(ConfigError, "flake-url"):
			known_apps.resolve({"repo": "git.example.org/libs/shared_lib"}, 16)

	def test_profile_and_app_entries_merge_by_key(self):
		apps = known_apps.merged(
			{
				"example/shared_lib": {
					"repo": "example/shared_lib",
					"branch": "v{n}",
					"range": ">=1,<2",
					"desk_global": "shared",
				}
			},
			{
				"example/shared_lib": {"branch": "main"},
				"lending": {
					"repo": "frappe/lending",
					"branch": "develop",
					"range": ">=0",
					"desk_global": None,
				},
			},
		)
		s = known_apps.resolve("example/shared_lib", 16, apps)
		self.assertEqual((s.branch, s.range, s.desk_global), ("main", ">=1,<2", "shared"))
		self.assertEqual(known_apps.resolve("lending", 16, apps).flake_url, "github:frappe/lending/develop")
		self.assertNotIn("lending", known_apps.builtin())

	def test_unknown(self):
		for spelling in ("lending", "*/*", "a/", "/b", "a/.git"):
			with self.subTest(spelling=spelling), self.assertRaises(ConfigError):
				known_apps.resolve(spelling, 16)
		with self.assertRaisesRegex(ConfigError, "write it as <owner>/<repo>"):
			known_apps.resolve("lending", 16)


if __name__ == "__main__":
	unittest.main()
