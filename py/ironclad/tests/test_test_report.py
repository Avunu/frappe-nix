import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import run_cli
from ironclad.commands import test_report
from ironclad.common import data_path

JUNIT_TWO_DOCUMENTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
\t<testsuite name="a" tests="4" failures="1" errors="0" skipped="1"></testsuite>
</testsuites>
<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
\t<testsuite name="b" tests="2" failures="0" errors="1" skipped="0"></testsuite>
</testsuites>
"""

COVERAGE = {
	"totals": {"percent_covered": 91.25},
	"files": {
		"demo/api.py": {"summary": {"percent_covered": 85.714}},
		"demo/hooks.py": {"summary": {"percent_covered": 100.0}},
	},
}


class TestRatchet(unittest.TestCase):
	def test_message(self):
		self.assertEqual(
			test_report.ratchet_message(98.8, 70.0),
			"coverage is 98.8; raise [tool.coverage.report] fail_under to 80",
		)
		self.assertEqual(
			test_report.ratchet_message(72.0, 70.0),
			"coverage is 72.0; raise [tool.coverage.report] fail_under to 71",
		)
		self.assertIsNone(test_report.ratchet_message(71.9, 70.0))
		self.assertIsNone(test_report.ratchet_message(99.0, 80.0))
		self.assertIsNone(test_report.ratchet_message(99.0, 90.0))
		# Unset counts as 0.
		self.assertIn("fail_under to 40", test_report.ratchet_message(41.0, None))

	def test_after_the_raise_it_settles(self):
		for total in (12.0, 50.5, 63.2, 78.9, 81.0, 99.9):
			message = test_report.ratchet_message(total, 0.0)
			raised = float(message.rsplit(" ", 1)[1])
			self.assertIsNone(test_report.ratchet_message(total, raised), (total, raised))

	def test_cli(self):
		with tempfile.TemporaryDirectory() as tmp:
			cov = Path(tmp) / "coverage.json"
			cov.write_text(json.dumps(COVERAGE))
			py = Path(tmp) / "pyproject.toml"
			py.write_text("[tool.coverage.report]\nfail_under = 60\n")
			code, out, _ = run_cli("coverage-ratchet", "--coverage-json", str(cov), "--pyproject", str(py))
			self.assertEqual(
				(code, out), (1, "coverage is 91.2; raise [tool.coverage.report] fail_under to 80\n")
			)
			py.write_text("[tool.coverage.report]\nfail_under = 90\n")
			code, out, _ = run_cli("coverage-ratchet", "--coverage-json", str(cov), "--pyproject", str(py))
			self.assertEqual(code, 0)
			py.write_text("[tool.coverage.report]\nfail_under = 101\n")
			code, _, err = run_cli("coverage-ratchet", "--coverage-json", str(cov), "--pyproject", str(py))
			self.assertEqual(code, 2, err)


class TestReport(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.out = Path(self.tmp.name)
		(self.out / "coverage.json").write_text(json.dumps(COVERAGE))
		(self.out / "testmap.json").write_text(
			json.dumps(
				{
					"targets": [
						{
							"path": "demo.api.ping",
							"kind": "T1",
							"file": "demo/api.py",
							"line": 5,
							"tested": True,
							"exempt": False,
						}
					],
					"untested": [],
					"exempt": 0,
					"stale_exemptions": [],
				}
			)
		)
		(self.out / "composition.json").write_text(
			json.dumps(
				{
					"ok": False,
					"order": ["frappe", "demo"],
					"app_first_order": ["frappe", "demo"],
					"doctypes": {
						"ToDo": {
							"layers": ["x.T"],
							"real_ok": True,
							"app_first_ok": False,
							"app_first_error": "TypeError: MRO",
						}
					},
				}
			)
		)
		(self.out / "junit.xml").write_text(JUNIT_TWO_DOCUMENTS)
		(self.out / "pyproject.toml").write_text("[tool.coverage.report]\nfail_under = 80\n")

	def tearDown(self):
		self.tmp.cleanup()

	def report(self, *stages, extra=()):
		args = [
			"test-report",
			"--out",
			str(self.out),
			"--app",
			"demo",
			"--repo-root",
			str(self.out),
			"--pyproject",
			str(self.out / "pyproject.toml"),
			"--junit",
			str(self.out / "junit.xml"),
		]
		for s in stages:
			args += ["--stage", s]
		summary = self.out / "step-summary.md"
		with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
			code, _, err = run_cli(*args, *extra)
		self.assertEqual(code, 0, err)
		return json.loads((self.out / "ironclad-report.json").read_text()), summary

	def test_report(self):
		report, summary = self.report(
			"composition=failed",
			"tests=ok",
			"up=ok",
			"site=ok",
			"coverage=ok",
			"testmap=ok",
			"ty=skipped",
			"nix-lint=ok",
			"shell-checks=ok",
		)
		schema = json.loads(data_path("schema/ironclad-report.schema.json").read_text())
		self.assertEqual(set(report), set(schema["required"]))
		self.assertEqual(report["exit"], 4)
		self.assertEqual(
			list(report["stages"]),
			["up", "site", "tests", "coverage", "testmap", "composition", "ty", "nix-lint", "shell-checks"],
		)
		self.assertEqual(report["tests"], {"ran": 6, "failures": 1, "errors": 1, "skipped": 1})
		self.assertEqual(
			report["coverage"],
			{"percent": 91.2, "fail_under": 80.0, "modules": {"demo/api.py": 85.7, "demo/hooks.py": 100.0}},
		)
		self.assertEqual(
			report["composition"],
			{"ok": False, "doctypes": {"ToDo": {"layers": ["x.T"], "real_ok": True, "app_first_ok": False}}},
		)
		self.assertEqual(report["nix_lint"], "ok")
		self.assertIsNone(report["ty"])
		# The JUnit file is one document afterwards.
		self.assertEqual((self.out / "junit.xml").read_text().count("<?xml"), 1)
		self.assertIn("## frappe-test: demo", summary.read_text())
		self.assertIn("| composition | failed |", (self.out / "summary.md").read_text())

	def test_first_failing_stage_wins(self):
		report, _ = self.report("ty=failed", "coverage=failed", "tests=ok")
		self.assertEqual(report["exit"], 2)
		report, _ = self.report("tests=ok", extra=("--environment-failure",))
		self.assertEqual(report["exit"], 10)
		report, _ = self.report("tests=ok", "ty=ok", extra=("--ty-diagnostics", "0", "--ty-ignores", "3"))
		self.assertEqual((report["exit"], report["ty"]), (0, {"diagnostics": 0, "ignores": 3}))

	def test_unknown_stage(self):
		code, _, err = run_cli(
			"test-report",
			"--out",
			str(self.out),
			"--app",
			"demo",
			"--repo-root",
			str(self.out),
			"--pyproject",
			str(self.out / "pyproject.toml"),
			"--stage",
			"bogus=ok",
		)
		self.assertEqual(code, 2, err)


if __name__ == "__main__":
	unittest.main()
