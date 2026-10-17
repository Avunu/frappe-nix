"""``frappe-nix gate lint|typecheck|marketplace`` on the fixture app, outside GitHub Actions
(spec S43, §4.2, §7 N4 "gate commands").

The fixture (tests/fixtures/standards-app) is copied into a git repository of its own.
``uv`` and ``yarn`` are stubs on PATH that log their arguments (and fail when the log
line matches ``$STUB_FAIL``), and the pinned trees (``pin-path``) are a temporary
directory, so no test needs the network; ``sync --check`` runs for real.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.commands import gate
from helpers import run_cli
from scaffold_helpers import git

# frappe-nix's fixture app: the checkout's, or FRAPPE_NIX_FIXTURE where the tests run without
# one (the package build's source is py/frappe_nix_tools alone; tests/standards/ci.nix sets it).
FIXTURE = Path(
	os.environ.get("FRAPPE_NIX_FIXTURE")
	or Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "standards-app"
)

STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$STUB_LOG"
if [ -n "${STUB_FAIL:-}" ]; then
  case "$(basename "$0") $*" in *"$STUB_FAIL"*) exit 1 ;; esac
fi
exit 0
"""


@unittest.skipUnless(FIXTURE.is_dir(), "needs the fixture app (FRAPPE_NIX_FIXTURE)")
class GateCase(unittest.TestCase):
	def setUp(self) -> None:
		self._tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self._tmp.cleanup)
		tmp = Path(self._tmp.name)
		self.root = tmp / "app"
		shutil.copytree(FIXTURE, self.root, symlinks=True)
		for path in (self.root, *self.root.rglob("*")):
			if not path.is_symlink():
				path.chmod(path.stat().st_mode | 0o200)  # writable, also when copied from the Nix store
		bin_dir = tmp / "bin"
		bin_dir.mkdir()
		for name in ("uv", "yarn"):
			(bin_dir / name).write_text(STUB)
			(bin_dir / name).chmod(0o755)
		self.log = tmp / "stub.log"
		self.log.write_text("")
		self.pins = tmp / "pins"
		(self.pins / "rules").mkdir(parents=True)
		env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
		env.update(
			{
				"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
				"STUB_LOG": str(self.log),
				"FRAPPE_NIX_OFFLINE": "1",
				"GIT_CONFIG_GLOBAL": os.devnull,
				"HOME": str(tmp),
			}
		)
		for key in ("STUB_FAIL", "FRAPPE_PATH", "FRAPPE_NIX_ALLOW_SKEW", "BASE_SHA", "CI"):
			env.pop(key, None)
		patcher = mock.patch.dict(os.environ, env, clear=True)
		patcher.start()
		self.addCleanup(patcher.stop)
		patcher = mock.patch.object(gate, "pin_path", return_value=self.pins)
		patcher.start()
		self.addCleanup(patcher.stop)
		git(self.root, "init", "-q", "-b", "develop")
		self.commit()

	def commit(self) -> None:
		git(self.root, "add", "-A")
		git(self.root, "commit", "-q", "--allow-empty", "-m", "fixture")

	def gate(self, name: str, *args: str) -> tuple[int, dict, str]:
		code, out, err = run_cli("gate", name, *args, cwd=self.root)
		report = json.loads((self.root / ".dev-dist" / "gate" / f"{name}.json").read_text())
		return code, report, out + err

	@staticmethod
	def steps(report: dict) -> dict[str, dict]:
		return {s["name"]: s for s in report["steps"]}

	def calls(self) -> list[str]:
		return self.log.read_text().splitlines()

	def set_module(self, module: str, on: bool) -> None:
		text = (self.root / "pyproject.toml").read_text()
		text += f"\n[tool.frappe-nix.{module}]\nenable = {'true' if on else 'false'}\n"
		(self.root / "pyproject.toml").write_text(text)
		self.commit()


class TestLint(GateCase):
	def test_fixture_passes(self) -> None:
		code, report, out = self.gate("lint")
		self.assertEqual(code, 0, out)
		steps = self.steps(report)
		self.assertEqual(steps["sync-check"]["status"], "ok", out)
		self.assertEqual(steps["prek"]["status"], "ok")
		self.assertEqual(steps["semgrep"]["status"], "ok")
		self.assertEqual(steps["customizations"]["status"], "skipped")
		self.assertIn("test-utils is off", steps["customizations"]["reason"])
		self.assertEqual(steps["ratchet"]["status"], "skipped")
		self.assertEqual(steps["ci-lint"]["status"], "skipped")
		calls = self.calls()
		self.assertTrue(any("prek run --all-files --show-diff-on-failure" in c for c in calls), calls)
		semgrep = [c for c in calls if " semgrep scan " in c]
		self.assertEqual(len(semgrep), 2, calls)
		self.assertIn(str(self.pins / "rules"), semgrep[0])
		self.assertIn("test-correctness.yml", semgrep[1])

	def test_base_runs_the_ratchet(self) -> None:
		head = git(self.root, "rev-parse", "HEAD").strip()
		code, report, out = self.gate("lint", "--base", head)
		self.assertEqual(code, 0, out)
		self.assertEqual(self.steps(report)["ratchet"]["status"], "ok")

	def test_push_skips_the_ratchet(self) -> None:
		with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push"}):
			_code, report, _out = self.gate("lint")
		self.assertIn("push", self.steps(report)["ratchet"]["reason"])

	def test_a_failing_step_fails_the_gate_after_all_steps(self) -> None:
		with mock.patch.dict(os.environ, {"STUB_FAIL": "prek run"}):
			code, report, out = self.gate("lint")
		self.assertEqual(code, 1, out)
		steps = self.steps(report)
		self.assertEqual(steps["prek"]["status"], "failed")
		self.assertEqual(steps["semgrep"]["status"], "ok")
		self.assertEqual(report["status"], "failed")

	def test_module_off_is_skipped(self) -> None:
		self.set_module("semgrep", False)
		_code, report, _out = self.gate("lint")
		self.assertEqual(self.steps(report)["semgrep"]["status"], "skipped")
		self.assertIn("semgrep is off", self.steps(report)["semgrep"]["reason"])
		self.assertFalse(any(" semgrep scan " in c for c in self.calls()))

	def test_a_dependabot_entry_without_cooldown_fails(self) -> None:
		"""§7 N4: a planted entry in the updates region, where zizmor would not see it."""
		self.assertEqual(self.steps(self.gate("lint")[1])["dependabot-cooldown"]["status"], "ok")
		path = self.root / ".github" / "dependabot.yml"
		path.write_text(
			path.read_text().replace(
				"  # frappe-nix:local-begin updates\n",
				"  # frappe-nix:local-begin updates\n  - package-ecosystem: npm\n    directory: /planted\n"
				"    schedule:\n      interval: weekly\n",
			)
		)
		self.commit()
		code, report, out = self.gate("lint")
		self.assertEqual(code, 1, out)
		step = self.steps(report)["dependabot-cooldown"]
		self.assertEqual(step["status"], "failed")
		self.assertIn("npm /planted", step["reason"])

	def test_ci_lint_script_runs(self) -> None:
		pkg = json.loads((self.root / "package.json").read_text())
		pkg["scripts"]["ci:lint"] = "true"
		(self.root / "package.json").write_text(json.dumps(pkg))
		self.commit()
		_code, report, _out = self.gate("lint")
		self.assertEqual(self.steps(report)["ci-lint"]["status"], "ok")
		self.assertIn("yarn -s ci:lint", self.calls())


class TestTypecheck(GateCase):
	def test_fixture_passes(self) -> None:
		code, report, out = self.gate("typecheck")
		self.assertEqual(code, 0, out)
		steps = self.steps(report)
		self.assertEqual(steps["frappe-path"]["status"], "ok")
		self.assertEqual(steps["typecheck"]["status"], "ok")
		self.assertIn("yarn -s typecheck", self.calls())
		for name in ("frappe-node-modules", "unchecked-js", "js-unit", "doctypes", "ci-typecheck"):
			self.assertEqual(steps[name]["status"], "skipped", name)

	def test_typescript_off_skips_its_steps(self) -> None:
		self.set_module("typescript", False)
		_code, report, _out = self.gate("typecheck")
		steps = self.steps(report)
		for name in ("typecheck", "frappe-node-modules", "doctypes", "audit-consumer"):
			self.assertEqual(steps[name]["status"], "skipped", name)
			self.assertIn("typescript is off", steps[name]["reason"])
		self.assertNotIn("yarn -s typecheck", self.calls())

	def test_js_unit_without_tests_fails(self) -> None:
		pkg = json.loads((self.root / "package.json").read_text())
		pkg["scripts"]["test:unit"] = "vitest run"
		(self.root / "package.json").write_text(json.dumps(pkg))
		self.commit()
		code, report, _out = self.gate("typecheck")
		self.assertEqual(code, 1)
		self.assertEqual(self.steps(report)["js-unit"]["status"], "failed")


class TestMarketplace(GateCase):
	def test_listing_off_is_skipped(self) -> None:
		code, report, out = self.gate("marketplace")
		self.assertEqual(code, 0, out)
		self.assertEqual(self.steps(report)["listing"]["status"], "skipped")

	def test_unknown_gate_is_exit_2(self) -> None:
		code, _out, _err = run_cli("gate", "test", cwd=self.root)
		self.assertEqual(code, 2)


class TestSummary(unittest.TestCase):
	def test_step_summary_table(self) -> None:
		table = gate.summary("lint", [gate.Step("prek", "", "failed", "exit 1 | x")])
		self.assertIn("| prek | — | failed: exit 1 \\| x |", table)


class TestSelfInvocation(unittest.TestCase):
	def test_runs_without_the_callers_environment(self) -> None:
		# The dev shell's wrapped frappe-nix finds its package only through the script's own
		# sys.path: its interpreter is a bare one (here, a venv's base) and PYTHONPATH is unset.
		bare = getattr(sys, "_base_executable", sys.executable)
		env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/")}
		with mock.patch.object(gate.sys, "executable", bare):
			cmd = gate.frappe_nix("--help")
		proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
		self.assertEqual(proc.returncode, 0, proc.stderr)
		self.assertIn("gate", proc.stdout)


if __name__ == "__main__":
	unittest.main()
