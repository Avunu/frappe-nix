"""``ci/npmrange.py``: npm ranges as node-semver's ``satisfies`` reads them."""

import unittest

from frappe_nix_tools.ci import npmrange


class TestSatisfies(unittest.TestCase):
	def test_ranges(self):
		# (range, versions it allows, versions it does not), node-semver's answers.
		cases = [
			("^1.2.3", ["1.2.3", "1.9.9"], ["1.2.2", "2.0.0", "2.0.0-0", "1.3.0-beta"]),
			("^0.2.3", ["0.2.3", "0.2.9"], ["0.3.0", "0.2.2"]),
			("^0.0.3", ["0.0.3"], ["0.0.4", "0.0.2"]),
			("^0.0", ["0.0.0", "0.0.9"], ["0.1.0"]),
			("^1.x", ["1.0.0", "1.9.0"], ["2.0.0", "0.9.0"]),
			(
				"^1.2.3-beta.2",
				["1.2.3-beta.2", "1.2.3-beta.10", "1.2.3", "1.5.0"],
				["1.2.3-beta.1", "1.2.4-beta.1"],
			),
			("~1.2.3", ["1.2.3", "1.2.9"], ["1.3.0", "1.2.2"]),
			("~1.2", ["1.2.0", "1.2.9"], ["1.3.0"]),
			("~1", ["1.0.0", "1.9.9"], ["2.0.0"]),
			("~>1.2", ["1.2.5"], ["1.3.0"]),
			("1.x", ["1.0.0", "1.9.9"], ["2.0.0", "0.9.9"]),
			("1.2.*", ["1.2.0", "1.2.9"], ["1.3.0"]),
			("1", ["1.0.0", "1.4.0"], ["2.0.0"]),
			("*", ["0.0.0", "9.9.9"], ["1.0.0-beta"]),
			("", ["1.2.3"], ["1.2.3-0"]),
			(">=1.2.3 <2", ["1.2.3", "1.99.0"], ["2.0.0", "1.2.2"]),
			(">= 1.2.3 < 2", ["1.5.0"], ["2.0.0"]),
			(">1", ["2.0.0"], ["1.9.9"]),
			(">1.2", ["1.3.0"], ["1.2.9"]),
			("<1.2", ["1.1.9"], ["1.2.0", "1.2.0-beta"]),
			("<=1.2", ["1.2.9"], ["1.3.0"]),
			("=1.2.3", ["1.2.3", "1.2.3+build"], ["1.2.4"]),
			("v1.2.3", ["1.2.3"], ["1.2.4"]),
			("1.2.3 - 2.3.4", ["1.2.3", "2.3.4"], ["2.3.5", "1.2.2"]),
			("1.2.3 - 2.3", ["2.3.9"], ["2.4.0"]),
			("1.2 - 2", ["1.2.0", "2.9.9"], ["3.0.0", "1.1.9"]),
			("^1.0.0 || ^2.0.0", ["1.5.0", "2.5.0"], ["3.0.0"]),
			("* || 1.2.3-beta.1", ["1.0.0"], ["1.2.3-beta.1"]),
			(">=0 || =1.2.3-beta.1", ["1.0.0"], ["1.2.3-beta.1"]),
		]
		for rng, yes, no in cases:
			for v in yes:
				with self.subTest(range=rng, version=v):
					self.assertIs(npmrange.satisfies(v, rng), True)
			for v in no:
				with self.subTest(range=rng, version=v):
					self.assertIs(npmrange.satisfies(v, rng), False)

	def test_what_it_does_not_read(self):
		for rng in ("latest", "next", "github:a/b", "file:../x", "https://x/y.tgz", "workspace:*", "1.2.3.4"):
			with self.subTest(range=rng):
				self.assertIsNone(npmrange.satisfies("1.0.0", rng))
		for version in ("1.0", "latest", "01.0.0", ""):
			with self.subTest(version=version):
				self.assertIsNone(npmrange.satisfies(version, "*"))


if __name__ == "__main__":
	unittest.main()
