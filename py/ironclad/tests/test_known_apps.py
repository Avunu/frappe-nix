import unittest

from ironclad.common import known_apps
from ironclad.common.report import ConfigError


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

	def test_avunu(self):
		s = known_apps.resolve("Avunu/esign", 16)
		self.assertEqual((s.name, s.spelling, s.repo), ("esign", "Avunu/esign", "Avunu/esign"))
		self.assertEqual(s.flake_url, "github:Avunu/esign/version-16")

	def test_unknown(self):
		for spelling in ("lending", "Avunu/*", "Avunu/"):
			with self.subTest(spelling=spelling), self.assertRaises(ConfigError):
				known_apps.resolve(spelling, 16)


if __name__ == "__main__":
	unittest.main()
