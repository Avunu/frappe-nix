import json
import unittest

from frappe_nix_tools.common import report
from frappe_nix_tools.common.report import Finding

FINDINGS = [Finding("ci.yml", "whole", "differs", "--- current\n+++ rendered\n")]


class TestReport(unittest.TestCase):
	def test_worst(self):
		self.assertEqual(report.worst(), 0)
		self.assertEqual(report.worst(1, 3, 2), 3)

	def test_text(self):
		out = report.render(FINDINGS, "text", 1)
		self.assertIn("ci.yml (whole): differs", out)
		self.assertTrue(out.endswith("frappe-nix: 1 file(s) drifted — run `frappe-init --sync`\n"))
		self.assertEqual(report.render([], "text", 0), "frappe-nix: clean\n")

	def test_github(self):
		self.assertIn("::error file=ci.yml::differs", report.render(FINDINGS, "github", 1))
		self.assertEqual(report.render([], "github", 0), "frappe-nix: clean\n")

	def test_github_escapes_and_fences(self):
		findings = [
			Finding("docs/a,b:c.md", "whole", "differs: 100% wrong\nsecond line"),
			Finding("x\n::add-mask::y.txt", "retire", "legacy file", " ::warning::from a diff\n"),
		]
		lines = report.render(findings, "github", 1).splitlines()
		self.assertIn("::error file=docs/a%2Cb%3Ac.md::differs: 100%25 wrong%0Asecond line", lines)
		self.assertIn("::error file=x%0A%3A%3Aadd-mask%3A%3Ay.txt::legacy file", lines)
		# Every line that would run as a workflow command is either one of ours or inside
		# the stop-commands fence, whose token nothing in the text knows.
		start = lines[0]
		self.assertTrue(start.startswith("::stop-commands::"))
		token = start.removeprefix("::stop-commands::")
		end = lines.index(f"::{token}::")
		for line in lines[end + 1 :]:
			self.assertTrue(line.startswith("::error file=") or line.startswith("frappe-nix:"), line)
		self.assertIn("::add-mask::y.txt (retire): legacy file", lines[1:end])

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
