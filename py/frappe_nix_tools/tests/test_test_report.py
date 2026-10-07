import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.commands import test_report
from frappe_nix_tools.common import data_path
from helpers import run_cli

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
		# Unset: nothing to raise (an app that has not opted in has no fail_under).
		self.assertIsNone(test_report.ratchet_message(41.0, None))
		self.assertIsNone(test_report.ratchet_message(99.0, None, 80.0, 2.0))

	def test_after_the_raise_it_settles(self):
		for total in (12.0, 50.5, 63.2, 78.9, 81.0, 99.9):
			message = test_report.ratchet_message(total, 0.0)
			raised = float(message.rsplit(" ", 1)[1])
			self.assertIsNone(test_report.ratchet_message(total, raised), (total, raised))

	def test_target_and_margin(self):
		# tests.coverage.target = 60 caps the raise; fail_under 55 with raise-margin 2.
		self.assertIsNone(test_report.ratchet_message(56.9, 55.0, 60.0, 2.0))
		self.assertEqual(
			test_report.ratchet_message(57.0, 55.0, 60.0, 2.0),
			"coverage is 57.0; raise [tool.coverage.report] fail_under to 56",
		)
		self.assertEqual(
			test_report.ratchet_message(59.0, 55.0, 60.0, 2.0),
			"coverage is 59.0; raise [tool.coverage.report] fail_under to 58",
		)
		self.assertEqual(
			test_report.ratchet_message(95.0, 55.0, 60.0, 2.0),
			"coverage is 95.0; raise [tool.coverage.report] fail_under to 60",
		)
		# At the target, nothing more is asked.
		self.assertIsNone(test_report.ratchet_message(95.0, 60.0, 60.0, 2.0))
		# raise-margin = 0 turns the ratchet off.
		self.assertIsNone(test_report.ratchet_message(95.0, 10.0, 80.0, 0.0))
		# A margin under a point never asks to lower fail_under.
		self.assertIsNone(test_report.ratchet_message(55.5, 55.0, 80.0, 0.5))
		self.assertEqual(
			test_report.ratchet_message(70.0, 55.0, 62.5, 2.0),
			"coverage is 70.0; raise [tool.coverage.report] fail_under to 62.5",
		)

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
			py.write_text('[project]\nname = "app"\n')
			code, out, _ = run_cli("coverage-ratchet", "--coverage-json", str(cov), "--pyproject", str(py))
			self.assertEqual(
				(code, out),
				(0, "coverage is 91.2; no [tool.coverage.report] fail_under, so nothing to ratchet\n"),
			)
			py.write_text("[tool.coverage.report]\nfail_under = 60\n")
			code, out, _ = run_cli(
				"coverage-ratchet",
				"--coverage-json",
				str(cov),
				"--pyproject",
				str(py),
				"--target",
				"75",
				"--raise-margin",
				"2",
			)
			self.assertEqual(
				(code, out), (1, "coverage is 91.2; raise [tool.coverage.report] fail_under to 75\n")
			)
			code, out, _ = run_cli(
				"coverage-ratchet", "--coverage-json", str(cov), "--pyproject", str(py), "--raise-margin", "0"
			)
			self.assertEqual(code, 0, out)
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
		return json.loads((self.out / "frappe-test-report.json").read_text()), summary

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
		schema = json.loads(data_path("schema/frappe-test-report.schema.json").read_text())
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


class TestPlan(unittest.TestCase):
	"""``frappe-nix test-plan``: what frappe-test reads from the resolved configuration."""

	APP = '[project]\nname = "demo"\n\n[tool.frappe-nix]\nschema = 1\nfrappe-major = 16\n'

	def plan(self, text: str) -> dict:
		with tempfile.TemporaryDirectory() as tmp:
			py = Path(tmp) / "pyproject.toml"
			py.write_text(text)
			code, out, err = run_cli("test-plan", "--pyproject", str(py))
			self.assertEqual(code, 0, err)
			return json.loads(out)

	def test_not_opted_in_gets_recommended(self):
		plan = self.plan('[project]\nname = "demo"\n\n[tool.coverage.report]\nfail_under = 10\n')
		self.assertFalse(plan["opted_in"])
		self.assertEqual(
			plan["stages"],
			{
				"tests": True,
				"coverage": True,
				"testmap": True,
				"composition": True,
				"ty": True,
				"nix-lint": True,
				"shell-checks": False,
			},
		)
		self.assertEqual(plan["coverage"], {"target": 80.0, "raise-margin": 2.0})
		self.assertEqual((plan["setup"], plan["shell-checks"], plan["siblings"]), ([], [], []))

	def test_recommended(self):
		plan = self.plan(
			self.APP
			+ 'profile = "recommended"\nsiblings = ["erpnext", "example/shared_lib", {repo = "example/other", branch = "main"}]\n'
			+ 'shell-checks = ["yarn codegen"]\n\n[tool.frappe-nix.tests]\nsetup = ["execute:demo.setup"]\n'
		)
		self.assertTrue(plan["opted_in"])
		self.assertTrue(all(plan["stages"].values()), plan["stages"])
		self.assertEqual(plan["setup"], ["execute:demo.setup"])
		self.assertEqual(plan["shell-checks"], ["yarn codegen"])
		self.assertEqual(plan["siblings"], ["erpnext", "shared_lib", "other"])

	def test_minimal_turns_every_stage_off(self):
		plan = self.plan(self.APP)
		self.assertTrue(plan["opted_in"])
		self.assertFalse(any(plan["stages"].values()), plan["stages"])

	def test_module_switches(self):
		plan = self.plan(
			self.APP
			+ 'profile = "recommended"\n\n[tool.frappe-nix.tests]\nenable = false\n'
			+ "\n[tool.frappe-nix.python-types]\nenable = false\n"
		)
		stages = plan["stages"]
		self.assertEqual(
			[stages[s] for s in ("tests", "coverage", "testmap", "composition", "ty", "nix-lint")],
			[False, False, False, False, False, True],
		)
		plan = self.plan(
			self.APP
			+ 'profile = "recommended"\n\n[tool.frappe-nix.tests]\ntestmap = false\n\n'
			+ "[tool.frappe-nix.tests.coverage]\nenable = false\ntarget = 60\nraise-margin = 0\n"
			+ '\n[tool.frappe-nix.python-types]\ntool = "none"\n\n[tool.frappe-nix.nix-lint]\nenable = false\n'
		)
		stages = plan["stages"]
		self.assertEqual(
			[stages[s] for s in ("tests", "coverage", "testmap", "composition", "ty", "nix-lint")],
			[True, False, False, True, False, False],
		)
		self.assertEqual(plan["coverage"], {"target": 60.0, "raise-margin": 0.0})

	def test_invalid_configuration_is_exit_2(self):
		with tempfile.TemporaryDirectory() as tmp:
			py = Path(tmp) / "pyproject.toml"
			py.write_text(self.APP + "[tool.frappe-nix.tests]\nno-such-key = 1\n")
			code, _, err = run_cli("test-plan", "--pyproject", str(py))
			self.assertEqual(code, 2, err)
