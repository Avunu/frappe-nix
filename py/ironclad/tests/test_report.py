import json
import unittest

from ironclad.common import report
from ironclad.common.report import Finding

FINDINGS = [Finding("ci.yml", "whole", "differs", "--- current\n+++ rendered\n")]


class TestReport(unittest.TestCase):
	def test_worst(self):
		self.assertEqual(report.worst(), 0)
		self.assertEqual(report.worst(1, 3, 2), 3)

	def test_text(self):
		out = report.render(FINDINGS, "text", 1)
		self.assertIn("ci.yml (whole): differs", out)
		self.assertTrue(out.endswith("ironclad: 1 file(s) drifted — run `frappe-init --sync`\n"))
		self.assertEqual(report.render([], "text", 0), "ironclad: clean\n")

	def test_github(self):
		self.assertIn("::error file=ci.yml::differs", report.render(FINDINGS, "github", 1))

	def test_json(self):
		doc = json.loads(report.render(FINDINGS, "json", 1, {"rev": "abc", "version": "1.0.0"}))
		self.assertEqual(doc["status"], "drift")
		self.assertEqual(doc["frappe_nix"]["rev"], "abc")
		self.assertEqual(doc["files"][0]["path"], "ci.yml")

	def test_unknown_format(self):
		with self.assertRaises(report.ConfigError):
			report.render([], "xml", 0)


if __name__ == "__main__":
	unittest.main()
