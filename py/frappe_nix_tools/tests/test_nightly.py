"""The nightly integration script, ``data/ci/nightly.sh`` (spec §4.6, §7 N4 "nightly script").

``frappe-test``, ``bench`` and ``frappe-nix`` are stubs on PATH: each exit status is set
per test, and ``frappe-nix config nightly-suites`` prints the suite commands.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from frappe_nix_tools.common import data_path


def stub(path: Path, body: str) -> None:
	path.write_text("#!/bin/sh\n" + body)
	path.chmod(0o755)


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
class TestNightly(unittest.TestCase):
	def run_script(self, *, test_rc: int, build_rc: int = 0, suites: list[str]) -> tuple[int, str]:
		with tempfile.TemporaryDirectory() as d:
			tmp = Path(d)
			bin_dir = tmp / "bin"
			bin_dir.mkdir()
			log = tmp / "log"
			stub(
				bin_dir / "frappe-test",
				f'echo "frappe-test $*" >> {log}\ncase "$*" in *--down*) exit 0;; esac\nexit {test_rc}\n',
			)
			stub(bin_dir / "bench", f'echo "bench $*" >> {log}\nexit {build_rc}\n')
			(tmp / "suites").write_text("".join(f"{s}\n" for s in suites))
			stub(bin_dir / "frappe-nix", f"cat {tmp / 'suites'}\n")
			bench = tmp / "bench"
			(bench / "sites").mkdir(parents=True)
			(bench / "sites" / "common_site_config.json").write_text('{"webserver_port": 8123}\n')
			work = tmp / "app"
			work.mkdir()
			env = {
				**os.environ,
				"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
				"FRAPPE_BENCH_ROOT": str(bench),
				"LOG": str(log),
			}
			proc = subprocess.run(
				["bash", str(data_path("ci/nightly.sh"))], cwd=work, env=env, capture_output=True, text=True
			)
			return proc.returncode, log.read_text() + proc.stdout + proc.stderr

	def test_all_pass(self) -> None:
		code, out = self.run_script(test_rc=0, suites=['test "$FRAPPE_SITE_URL" = http://127.0.0.1:8123'])
		self.assertEqual(code, 0, out)
		self.assertIn("frappe-test --ci --keep-up", out)
		self.assertIn("frappe-test --down", out)

	def test_failing_tests_fail_even_when_suites_pass(self) -> None:
		code, out = self.run_script(test_rc=1, suites=["true"])
		self.assertNotEqual(code, 0, out)
		# Every step still ran.
		self.assertIn("bench build", out)
		self.assertIn("frappe-test --down", out)

	def test_failing_suite_fails(self) -> None:
		code, out = self.run_script(test_rc=0, suites=["true", "false"])
		self.assertEqual(code, 1, out)

	def test_failing_build_fails(self) -> None:
		code, out = self.run_script(test_rc=0, build_rc=1, suites=[])
		self.assertEqual(code, 1, out)


if __name__ == "__main__":
	unittest.main()
