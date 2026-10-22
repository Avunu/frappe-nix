"""frappe-test: run an app's tests the way CI does (docs/app-standards/spec.md §5.1).

Run by lib/scripts.d/frappe-test.nix, with git, frappe-nix, nixfmt, statix and
deadnix on PATH, from an app-mode dev shell, which provides FRAPPE_BENCH_ROOT,
FRAPPE_SITE, DEVENV_ROOT, PC_SOCKET_PATH, `devenv`, `process-compose`,
`bench` (its `bench provision`) and `uv`.

Stages, in order; every stage after 3 runs even when an earlier one failed:

  1 up            devenv up -D unless process-compose is already listening   (exit 10)
  2 site          bench provision, allow_tests, the [tool.frappe-nix.tests] setup (exit 10)
  3 tests         coverage run … frappe … run-tests --app A                   verdict 1
  4 coverage      coverage report (fail_under) and the upward ratchet         verdict 2
  5 testmap       frappe-nix testmap                                          verdict 3
  6 composition   frappe-nix composition                                      verdict 4
  7 ty            uv run --frozen --project tools ty check                    verdict 5
  8 nix-lint      nixfmt --check, statix, deadnix                             verdict 6
  8b shell-checks [tool.frappe-nix] shell-checks, each leaving the tree as found verdict 7
  9 report        frappe-test-report.json, summary.md, $GITHUB_STEP_SUMMARY

--ci runs exactly the stages whose modules the app's resolved configuration
turns on (`frappe-nix test-plan`): with `tests` off, stages 1 to 6 are skipped
and no bench is started. An app without [tool.frappe-nix] has no configuration
to read and gets the recommended profile's stages. The coverage target and
raise margin of the upward ratchet come from the same plan, with or without
--ci.

[tool.frappe-nix.tests] devguard-disable names development guards that the
processes running the app's tests (stage 2's setup steps, 3, 5 and 6) go without,
through FRAPPE_DEVGUARD_DISABLE. The bench devenv up starts, and the dev shell
itself, keep every guard.

The exit status is the verdict of the first failing stage, 10 for an
environment failure (stage 1 or 2, an unreadable configuration, or no dev
shell), 64 (EX_USAGE) for a usage error, and 0 otherwise. Neither collides with
a verdict.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from frappe_nix_tools.app_tools import devshell
from frappe_nix_tools.app_tools.devshell import Compose, Stop, call, capture, ok, say, warn

USAGE = """\
Usage: frappe-test [--app APP] [--site SITE] [--reuse-site | --recreate-site] [--keep-up | --down] [--ci]
                   [--module M] [--doctype DT] [--test T]
                   [--no-coverage] [--no-testmap] [--no-composition] [--ty] [--nix-lint] [--shell-checks]
                   [--junit PATH] [--out DIR]

Runs the app's tests on a site of this dev shell's bench, with coverage scoped to the
app, then the testmap and composition checks. --ci runs what CI's `ci / test` runs:
the stages the app's configuration turns on ([tool.frappe-nix] and its profile; ty,
the Nix linters and the shell checks among them), a JUnit report and the step summary.

  --app APP          the app under test (default: the app this repository is)
  --site SITE        the site (default: $FRAPPE_SITE)
  --reuse-site       keep an existing site instead of recreating it
  --recreate-site    recreate an existing site: DROPS ITS DATABASE and resets
                     Administrator's password to $FRAPPE_NIX_TEST_ADMIN_PASSWORD (admin).
                     Without --ci, an existing site needs one of the two (or a yes
                     at the prompt on a terminal); --ci recreates it
  --keep-up          leave the bench running afterwards (default: stop what this started)
  --down             stop a bench --keep-up left running, and exit
  --ci               the configured stages, plus --junit $OUT/junit.xml
  --module, --doctype, --test
                     run only these tests (passed to run-tests); skips the coverage
                     gate and the testmap
  --no-coverage      run the tests without coverage (skips the gate and the testmap)
  --no-testmap, --no-composition, --ty, --nix-lint, --shell-checks
                     turn one check off or on, over --ci's choice
  --junit PATH       write a JUnit report here
  --out DIR          where the reports go (default: $DEVENV_ROOT/.dev-dist/test)

Exit status: the first failing stage's verdict: 1 tests, 2 coverage, 3 testmap,
4 composition, 5 ty, 6 nix-lint, 7 shell checks; 10 when the bench or the site
could not be brought up, the configuration could not be read, or outside the
app dev shell; 64 for a usage error; 0 when everything passed.
"""

EX_USAGE = 64
ENVIRONMENT = 10

#: The verdict stages in spec order, and the exit status each fails with.
VERDICTS = (
	("tests", 1),
	("coverage", 2),
	("testmap", 3),
	("composition", 4),
	("ty", 5),
	("nix-lint", 6),
	("shell-checks", 7),
)

#: What a run leaves in --out, removed before it starts.
STALE = (
	".coverage",
	"coverage.json",
	"coverage.xml",
	"testmap.json",
	"testmap.md",
	"composition.json",
	"composition.md",
	"frappe-test-report.json",
	"summary.md",
	"shell-checks.log",
	"ports.json",
)

#: Seconds the bench has to come up in.
UP_TIMEOUT = 300


class UsageError(Exception):
	"""A command line frappe-test refuses: exit 64, after the usage when SHOW_USAGE."""

	def __init__(self, message: str, *, show_usage: bool = False) -> None:
		super().__init__(message)
		self.show_usage = show_usage


class Help(Exception):
	"""-h or --help: the usage, exit 0."""


#: Flags that take a value, and the field each sets (--test appends).
VALUED = {
	"--app": "app",
	"--site": "site",
	"--module": "module",
	"--doctype": "doctype",
	"--test": "tests",
	"--junit": "junit",
	"--out": "out",
}
#: Switches, and the field and value each sets.
SWITCHES: dict[str, tuple[str, Any]] = {
	"--reuse-site": ("reuse", True),
	"--recreate-site": ("recreate", True),
	"--keep-up": ("keep_up", True),
	"--down": ("down", True),
	"--ci": ("ci", True),
	"--no-coverage": ("coverage", False),
	"--no-testmap": ("testmap", False),
	"--no-composition": ("composition", False),
	"--ty": ("ty", True),
	"--nix-lint": ("nix_lint", True),
	"--shell-checks": ("shell_checks", True),
}


@dataclass
class TestArgs:
	app: str = ""
	# None: not given, so $FRAPPE_SITE.
	site: str | None = None
	reuse: bool = False
	recreate: bool = False
	keep_up: bool = False
	down: bool = False
	ci: bool = False
	module: str = ""
	doctype: str = ""
	tests: list[str] = field(default_factory=list)
	# None: not given on the command line, so --ci's plan (or the default) decides.
	coverage: bool | None = None
	testmap: bool | None = None
	composition: bool | None = None
	ty: bool | None = None
	nix_lint: bool | None = None
	shell_checks: bool | None = None
	junit: str = ""
	out: str = ""

	@property
	def filtered(self) -> bool:
		"""Only some tests run: the coverage gate and the testmap are skipped."""
		return bool(self.module or self.doctype or self.tests)


def parse_args(argv: list[str]) -> TestArgs:
	"""The command line, read left to right as the bash read it: a value is the
	next word and may not be empty; -h stops at once; the first unknown word is a
	usage error."""
	args = TestArgs()
	i = 0
	while i < len(argv):
		word = argv[i]
		if word in VALUED:
			if i + 1 >= len(argv) or not argv[i + 1]:
				raise UsageError(f"{word} needs a value")
			name = VALUED[word]
			if name == "tests":
				args.tests.append(argv[i + 1])
			else:
				setattr(args, name, argv[i + 1])
			i += 2
			continue
		if word in SWITCHES:
			name, value = SWITCHES[word]
			setattr(args, name, value)
		elif word in ("-h", "--help"):
			raise Help
		else:
			raise UsageError(f"unknown argument: {word}", show_usage=True)
		i += 1
	return args


def pick(given: bool | None, ci: bool, planned: bool, default: bool) -> bool:
	"""A stage's switch: the command line's, else --ci's plan, else the
	interactive default."""
	if given is not None:
		return given
	return planned if ci else default


def find_app(bench: Path, repo: Path) -> str:
	"""The app under development: the one apps/<name> links back to this checkout."""
	apps = bench / "apps"
	try:
		links = sorted(apps.iterdir(), key=lambda p: p.name)
	except OSError:
		return ""
	for link in links:
		if link.is_symlink() and os.path.realpath(link) == str(repo):
			return link.name
	return ""


def default_setup(siblings: list[str], bench: Path) -> list[str]:
	"""The setup when the app names none (spec §2.1): erpnext's test bootstrap
	when erpnext is a sibling or installed, else the setup wizard frappe's own UI
	tests complete."""
	try:
		installed = (bench / "sites" / "apps.txt").read_text().split("\n")
	except OSError:
		installed = []
	if "erpnext" in siblings or "erpnext" in installed:
		return ["module:erpnext.tests.bootstrap_test_data"]
	return ["execute:frappe.utils.install.complete_setup_wizard"]


def ty_diagnostics(output: str) -> int:
	"""The count ty's last `Found N diagnostic(s)` line gives, 0 without one."""
	found = re.findall(r"Found ([0-9]+) diagnostic", output)
	return int(found[-1]) if found else 0


#: The variable the devguard core reads its per-process guard switches from.
DEVGUARD_DISABLE = "FRAPPE_DEVGUARD_DISABLE"


def guards_off_env(guards: list[str], environ: dict[str, str]) -> dict[str, str]:
	"""ENVIRON with GUARDS added to $FRAPPE_DEVGUARD_DISABLE, keeping what it named.

	The core reads the variable as a comma-separated list; a guard the caller
	already turned off for one command stays off."""
	names = [n.strip() for n in environ.get(DEVGUARD_DISABLE, "").split(",") if n.strip()]
	names += [g for g in guards if g not in names]
	if not names:
		return dict(environ)
	return {**environ, DEVGUARD_DISABLE: ",".join(names)}


def stage_status(rc: int) -> str:
	"""A frappe-nix check's exit status as a stage: 0 ok, 1 failed, else error."""
	return {0: "ok", 1: "failed"}.get(rc, "error")


class Finish(Exception):
	"""Write the report and exit with the verdict now (an environment failure)."""


def tee(cmd: list[str], *, cwd: Path) -> tuple[int, str]:
	"""Run CMD with stderr merged into stdout, which streams through and is kept."""
	devshell.flush()
	try:
		proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
	except OSError as e:
		warn(f"{cmd[0]}: {e.strerror}")
		return 127, ""
	assert proc.stdout is not None
	fd = proc.stdout.fileno()
	kept = bytearray()
	try:
		while chunk := os.read(fd, 65536):
			kept += chunk
			sys.stdout.buffer.write(chunk)
			sys.stdout.buffer.flush()
		proc.stdout.close()
		return proc.wait(), kept.decode(errors="replace")
	except KeyboardInterrupt:
		proc.wait()
		raise Stop(130) from None


class Run:
	def __init__(
		self, args: TestArgs, bench: Path, repo: Path, out: Path, junit: str, app: str, site: str
	) -> None:
		self.args = args
		self.bench = bench
		self.repo = repo
		self.out = out
		self.junit = junit
		self.app = app
		self.site = site
		self.py = str(bench / "env" / "bin" / "python")
		self.pyproject = str(repo / "pyproject.toml")
		self.compose = Compose.from_env()
		self.stages: dict[str, str] = {}
		self.env_failed = False
		self.ty_diagnostics: int | None = None
		self.ty_ignores = 0
		# A bench this run started, to stop at the end unless --keep-up.
		self.stop_bench = False
		# The environment of the processes that run the app's tests: the dev
		# shell's, less the guards [tool.frappe-nix.tests] devguard-disable names.
		self.test_env: dict[str, str] = dict(os.environ)

	# ── output ──────────────────────────────────────────────────────────────
	@staticmethod
	def group(title: str) -> None:
		say(f"::group::{title}" if os.environ.get("GITHUB_ACTIONS") else f"── {title} ──")

	@staticmethod
	def endgroup() -> None:
		if os.environ.get("GITHUB_ACTIONS"):
			say("::endgroup::")

	def environment_failure(self, message: str, stage: str | None = None) -> Finish:
		warn(message)
		if stage:
			self.stages[stage] = "error"
		self.env_failed = True
		return Finish()

	def bench_cmd(self, *args: str) -> int:
		"""The bench's own `bench`, from its root, past the dev shell's wrapper."""
		return call(
			[str(self.bench / "env" / "bin" / "bench"), *args],
			cwd=self.bench,
			env={**self.test_env, "_FRAPPE_BENCH_RAW": "1"},
		)

	# ── 9. report ───────────────────────────────────────────────────────────
	def report(self) -> None:
		cmd = [
			"frappe-nix",
			"test-report",
			"--out",
			str(self.out),
			"--app",
			self.app,
			"--repo-root",
			str(self.repo),
		]
		cmd += ["--pyproject", self.pyproject]
		for name, status in self.stages.items():
			cmd += ["--stage", f"{name}={status}"]
		if self.junit:
			cmd += ["--junit", self.junit]
		if self.env_failed:
			cmd.append("--environment-failure")
		if self.ty_diagnostics is not None:
			cmd += ["--ty-diagnostics", str(self.ty_diagnostics), "--ty-ignores", str(self.ty_ignores)]
		log = self.out / "shell-checks.log"
		if log.is_file() and log.stat().st_size > 0:
			cmd += ["--shell-checks-log", str(log)]
		if call(cmd) != 0:
			warn("frappe-test: could not write the report")

	def verdict(self) -> int:
		"""The first failing stage's verdict, in spec order; 10 for the environment."""
		if self.env_failed:
			return ENVIRONMENT
		for name, code in VERDICTS:
			if self.stages.get(name) in ("failed", "error"):
				return code
		return 0

	def finish(self) -> int:
		self.group("report")
		self.report()
		self.endgroup()
		rc = self.verdict()
		say(f"frappe-test: exit {rc}")
		return rc

	# ── 0. plan ─────────────────────────────────────────────────────────────
	def read_plan(self) -> dict[str, Any]:
		"""What the app's resolved configuration says (spec §5.1, §8.2): the
		stages --ci runs, the upward ratchet's target and margin, the setup
		steps, the shell checks and the siblings. Read once, before anything
		starts."""
		rc, out = capture(["frappe-nix", "test-plan", "--pyproject", self.pyproject])
		plan: Any = None
		if rc == 0:
			try:
				plan = json.loads(out)
			except ValueError:
				plan = None
		if not isinstance(plan, dict):
			raise self.environment_failure(
				"::error::frappe-test: could not read the app's configuration (frappe-nix test-plan); see above"
			)
		return plan

	def relax_guards(self, guards: list[str]) -> None:
		"""Turn GUARDS off for the test processes only, and say so."""
		if not guards:
			return
		self.test_env = guards_off_env(guards, self.test_env)
		say(
			f"frappe-test: development guards off for the test run: {', '.join(guards)} "
			"([tool.frappe-nix.tests] devguard-disable); the bench and the dev shell keep them"
		)

	# ── stages 1 to 6 ───────────────────────────────────────────────────────
	def bench_stages(self, plan: dict[str, Any], switches: dict[str, bool]) -> None:
		# The bench root's dev group brings both, unless the app has not opted
		# in and its lock's root predates them (lib/app-workspace.nix keeps such
		# a root to the dev group main rendered, S35). Said before anything starts.
		absent = []
		if switches["coverage"] and not ok([self.py, "-c", "import coverage"]):
			absent.append("coverage")
		if self.junit and not ok([self.py, "-c", "import xmlrunner"]):
			absent.append("unittest-xml-reporting")
		if absent:
			warn(
				f"::error::frappe-test: the bench's environment has no {' '.join(absent)}: this app's nix/uv.lock does not resolve it."
			)
			raise self.environment_failure(
				"  Run without it (--no-coverage, no --junit), or opt in to the app standards and run 'nix run .#relock'."
			)
		self.up()
		self.prepare_site(plan)
		self.run_tests(switches["coverage"])
		have_coverage = self.coverage(switches["coverage"], plan)
		self.testmap(switches["testmap"], have_coverage)
		self.composition(switches["composition"])

	# ── 1. up ───────────────────────────────────────────────────────────────
	def up(self) -> None:
		self.group("up")
		if self.compose.up():
			say("frappe-test: the bench is already up")
		else:
			say("frappe-test: starting the bench (devenv up -D)")
			rc = devshell.devenv_up()
			if rc != 0:
				# Its own message (a taken port and the FRAPPE_NIX_PORT_OFFSET
				# hint) is just above; nothing of this bench's is up to wait for.
				# Whatever it did start before failing is ours to stop.
				if self.compose.up():
					self.compose.down()
				self.endgroup()
				raise self.environment_failure(
					f"::error::frappe-test: devenv up -D failed (exit {rc}); see its output above", "up"
				)
			self.stop_bench = not self.args.keep_up

		deadline = time.monotonic() + UP_TIMEOUT
		db_up = web_up = False
		port = ""
		while time.monotonic() < deadline:
			if devshell.db_answers():
				db_up = True
				break
			time.sleep(2)
		while db_up and time.monotonic() < deadline:
			port = devshell.webserver_port(self.bench)
			if port and devshell.web_answers(port, devshell.bench_id()):
				web_up = True
				break
			time.sleep(2)
		self.endgroup()
		self.keep_ports()
		if not (db_up and web_up):
			holder = devshell.who(port)
			devshell.report_not_up(self.compose, "frappe-test")
			raise self.environment_failure(
				f"::error::frappe-test: the bench did not come up within {UP_TIMEOUT} s "
				f"(database: {int(db_up)}, web on port {port or '?'}: {int(web_up)})"
				+ (f"; port {port}: {holder}" if holder else ""),
				"up",
			)
		self.stages["up"] = "ok"
		say(f"frappe-test: the bench is up on http://127.0.0.1:{port}")

	def keep_ports(self) -> None:
		"""The leased ports beside the report, for a failed run's artifact."""
		data = devshell.leased()
		if data is not None:
			with contextlib.suppress(OSError):
				(self.out / "ports.json").write_text(json.dumps(data, indent=2) + "\n")

	# ── 2. site ─────────────────────────────────────────────────────────────
	def prepare_site(self, plan: dict[str, Any]) -> None:
		self.group(f"site {self.site}")
		site_ok = True
		if self.args.reuse and (self.bench / "sites" / self.site).is_dir():
			say(f"frappe-test: reusing {self.site}")
		else:
			site_ok = devshell.provision(self.site, self.compose)
		if site_ok:
			site_ok = self.bench_cmd("--site", self.site, "set-config", "allow_tests", "true") == 0

		setup = [str(s) for s in plan.get("setup") or []]
		if site_ok and not setup:
			setup = default_setup([str(s) for s in plan.get("siblings") or []], self.bench)
		if site_ok:
			for step in setup:
				say(f"frappe-test: setup {step}")
				if step.startswith("module:"):
					site_ok = (
						self.bench_cmd("--site", self.site, "run-tests", "--module", step[len("module:") :])
						== 0
					)
				elif step.startswith("execute:"):
					site_ok = self.bench_cmd("--site", self.site, "execute", step[len("execute:") :]) == 0
				else:
					# The configuration's schema refuses one; kept for a plan from elsewhere.
					warn(f"frappe-test: unknown setup step {step} (module:<dotted> or execute:<dotted>)")
					site_ok = False
				if not site_ok:
					break
		self.endgroup()
		if not site_ok:
			raise self.environment_failure(f"::error::frappe-test: could not prepare {self.site}", "site")
		self.stages["site"] = "ok"

	# ── 3. tests ────────────────────────────────────────────────────────────
	def run_tests(self, coverage: bool) -> None:
		self.group(f"tests: {self.app}")
		run_tests = [
			"-m",
			"frappe.utils.bench_helper",
			"frappe",
			"--site",
			self.site,
			"run-tests",
			"--app",
			self.app,
		]
		if self.args.module:
			run_tests += ["--module", self.args.module]
		if self.args.doctype:
			run_tests += ["--doctype", self.args.doctype]
		for test in self.args.tests:
			run_tests += ["--test", test]
		if self.junit:
			run_tests += ["--junit-xml-output", self.junit]
		# Never frappe's own --coverage: in app mode it measures every app under
		# the repository's .frappe-nix/bench/apps (spec S17). --source is the
		# package's real directory, which nothing of frappe's or a sibling's is under.
		cmd = [self.py]
		if coverage:
			cmd += [
				"-m",
				"coverage",
				"run",
				f"--rcfile={self.pyproject}",
				f"--data-file={self.out / '.coverage'}",
				f"--source={self.repo / self.app}",
			]
		rc = call([*cmd, *run_tests], cwd=self.bench / "sites", env=self.test_env)
		self.endgroup()
		self.stages["tests"] = "ok" if rc == 0 else "failed"

	# ── 4. coverage ─────────────────────────────────────────────────────────
	def cov(self, *args: str, no_stdout: bool = False) -> int:
		# From the repository, so coverage's file names are repo-relative.
		return call(
			[
				self.py,
				"-m",
				"coverage",
				*args,
				f"--rcfile={self.pyproject}",
				f"--data-file={self.out / '.coverage'}",
			],
			cwd=self.repo,
			no_stdout=no_stdout,
		)

	def coverage(self, coverage: bool, plan: dict[str, Any]) -> bool:
		"""The coverage stage; whether there is coverage data for the testmap."""
		if not (coverage and (self.out / ".coverage").is_file()):
			self.stages["coverage"] = "skipped"
			return False
		self.group("coverage")
		have = self.cov("json", "-q", "-o", str(self.out / "coverage.json"), no_stdout=True) == 0
		self.cov("xml", "-q", "-o", str(self.out / "coverage.xml"), no_stdout=True)
		if self.args.filtered:
			self.cov("report")
			say("frappe-test: a filter is set; the coverage gate is skipped")
			self.stages["coverage"] = "skipped"
		else:
			report_rc = self.cov("report")
			if report_rc == 0 and have:
				target = devshell.jq_raw((plan.get("coverage") or {}).get("target"))
				margin = devshell.jq_raw((plan.get("coverage") or {}).get("raise-margin"))
				rc = call(
					[
						"frappe-nix",
						"coverage-ratchet",
						"--coverage-json",
						str(self.out / "coverage.json"),
						"--pyproject",
						self.pyproject,
						"--target",
						target,
						"--raise-margin",
						margin,
					]
				)
				self.stages["coverage"] = stage_status(rc)
			elif report_rc == 2:
				say("frappe-test: coverage is below [tool.coverage.report] fail_under")
				self.stages["coverage"] = "failed"
			else:
				self.stages["coverage"] = "error"
		self.endgroup()
		return have

	# ── 5. testmap ──────────────────────────────────────────────────────────
	def testmap(self, testmap: bool, have_coverage: bool) -> None:
		if not testmap or self.args.filtered:
			self.stages["testmap"] = "skipped"
			return
		if not have_coverage:
			warn("frappe-test: no coverage data, so no testmap")
			self.stages["testmap"] = "skipped"
			return
		self.group("testmap")
		rc = call(
			[
				"frappe-nix",
				"testmap",
				"--python",
				self.py,
				"--site",
				self.site,
				"--app",
				self.app,
				"--sites-path",
				str(self.bench / "sites"),
				"--coverage-json",
				str(self.out / "coverage.json"),
				"--repo-root",
				str(self.repo),
				"--pyproject",
				self.pyproject,
				"--out",
				str(self.out / "testmap.json"),
				"--markdown",
				str(self.out / "testmap.md"),
			],
			env=self.test_env,
		)
		self.stages["testmap"] = stage_status(rc)
		self.endgroup()

	# ── 6. composition ──────────────────────────────────────────────────────
	def composition(self, composition: bool) -> None:
		if not composition:
			self.stages["composition"] = "skipped"
			return
		self.group("composition")
		rc = call(
			[
				"frappe-nix",
				"composition",
				"--python",
				self.py,
				"--site",
				self.site,
				"--app",
				self.app,
				"--sites-path",
				str(self.bench / "sites"),
				"--out",
				str(self.out / "composition.json"),
				"--markdown",
				str(self.out / "composition.md"),
			],
			env=self.test_env,
		)
		self.stages["composition"] = stage_status(rc)
		self.endgroup()

	# ── 7. ty ───────────────────────────────────────────────────────────────
	def ty(self, on: bool) -> None:
		if not on:
			self.stages["ty"] = "skipped"
			return
		if not (self.repo / "tools" / "pyproject.toml").is_file():
			say("::warning::frappe-test: no tools/pyproject.toml, so no ty (frappe-init --sync renders it)")
			self.stages["ty"] = "skipped"
			return
		self.group("ty")
		cmd = [
			"uv",
			"run",
			"--frozen",
			"--project",
			"tools",
			"ty",
			"check",
			"--python",
			str(self.bench / "env"),
		]
		rc, output = tee([*cmd, "--output-format", "concise"], cwd=self.repo)
		self.ty_diagnostics = ty_diagnostics(output)
		_, ignores = capture(["git", "grep", "-o", "ty: *ignore", "--", "*.py"], cwd=self.repo)
		self.ty_ignores = len(ignores.splitlines())
		self.stages["ty"] = "ok" if rc == 0 else "failed"
		self.endgroup()

	# ── 8. nix-lint ─────────────────────────────────────────────────────────
	def nix_lint(self, on: bool) -> None:
		if not on:
			self.stages["nix-lint"] = "skipped"
			return
		self.group("nix-lint")
		_, listed = capture(["git", "ls-files", "nix/*.nix"], cwd=self.repo)
		nix_files = [f for f in listed.split("\n") if f]
		results = [
			call(["nixfmt", "--check", "flake.nix", *nix_files], cwd=self.repo),
			call(
				["statix", "check", "--ignore", ".frappe-nix/*", "--ignore", ".devenv/*", "."], cwd=self.repo
			),
			call(["deadnix", "--fail", "--exclude", ".frappe-nix", ".devenv", "--", "."], cwd=self.repo),
		]
		self.stages["nix-lint"] = "ok" if all(rc == 0 for rc in results) else "failed"
		self.endgroup()

	# ── 8b. shell checks ────────────────────────────────────────────────────
	def snapshot(self) -> str:
		"""The work tree as a tree object, untracked files included and ignored
		ones not, through a scratch index; "" when it cannot be written.

		The scratch index starts as a copy of the real one: `git add -A` on an
		empty index would skip a tracked file under an ignored path (generated
		output committed with `git add -f`), which is exactly what these checks
		are for. The runtime directories are excluded only where .gitignore
		doesn't already: `git add` exits 1 when a pathspec, even an exclude,
		names an ignored path, and the managed .gitignore ignores all four.
		"""
		fd, index = tempfile.mkstemp()
		os.close(fd)
		os.unlink(index)
		try:
			_, real = capture(
				["git", "rev-parse", "--path-format=absolute", "--git-path", "index"], cwd=self.repo
			)
			if real and os.path.isfile(real):
				shutil.copyfile(real, index)
			skip = [
				f":(exclude){d}"
				for d in (".frappe-nix", ".devenv", ".direnv", ".dev-dist")
				if os.path.lexists(self.repo / d) and not ok(["git", "check-ignore", "-q", d], cwd=self.repo)
			]
			env = {**os.environ, "GIT_INDEX_FILE": index}
			rc, _ = capture(["git", "add", "-A", "--", ".", *skip], cwd=self.repo, env=env)
			if rc != 0:
				return ""
			rc, tree = capture(["git", "write-tree"], cwd=self.repo, env=env)
			return tree if rc == 0 else ""
		finally:
			if os.path.exists(index):
				os.unlink(index)

	def shell_checks(self, on: bool, checks: list[str]) -> None:
		"""Each command must leave the tree as it found it: a generator whose
		output is committed proves that output fresh. Compared as trees, so a
		tree that was already dirty is judged only on what the command changed."""
		if not on:
			self.stages["shell-checks"] = "skipped"
			return
		if not checks:
			self.stages["shell-checks"] = "ok"
			return
		self.group("shell checks")
		log_path = self.out / "shell-checks.log"
		checks_ok = True
		for check in checks:
			before = self.snapshot()
			say(f"frappe-test: shell check: {check}")
			rc = call(["bash", "-c", check], cwd=self.repo)
			after = self.snapshot()
			entry = ""
			if rc != 0:
				entry = f"{check}\n  exited {rc}\n"
			elif not before or not after:
				entry = f"{check}\n  could not compare the tree before and after\n"
			elif before != after:
				_, names = capture(
					["git", "diff", "--no-color", "--name-status", before, after], cwd=self.repo
				)
				_, diff = capture(["git", "diff", "--no-color", before, after], cwd=self.repo)
				entry = f"{check}\n  left the tree changed:\n"
				entry += "".join(f"    {line}\n" for line in names.split("\n")) if names else ""
				entry += "".join(f"{line}\n" for line in diff.split("\n")[:200]) if diff else ""
			if entry:
				checks_ok = False
				with log_path.open("a") as log:
					log.write(entry)
		if checks_ok:
			self.stages["shell-checks"] = "ok"
		else:
			sys.stdout.write(log_path.read_text())
			self.stages["shell-checks"] = "failed"
		self.endgroup()

	# ── the run ─────────────────────────────────────────────────────────────
	def run(self) -> int:
		try:
			plan = self.read_plan()
			stages = plan.get("stages") or {}

			def planned(stage: str) -> bool:
				return stages.get(stage) is True

			a = self.args
			bench_stages = pick(None, a.ci, planned("tests"), True)
			switches = {
				"coverage": pick(a.coverage, a.ci, planned("coverage"), True),
				"testmap": pick(a.testmap, a.ci, planned("testmap"), True),
				"composition": pick(a.composition, a.ci, planned("composition"), True),
			}
			self.relax_guards([str(g) for g in plan.get("devguard-disable") or []])
			if plan.get("opted_in") is True:
				configured = ", ".join(name for name, on in stages.items() if devshell.truthy(on))
				say(
					f"frappe-test: profile {devshell.jq_raw(plan.get('profile'))}; configured stages: {configured}"
				)
			else:
				say("frappe-test: no [tool.frappe-nix] in pyproject.toml; the recommended profile's stages")

			if not bench_stages:
				# The tests module is off: no bench, no site, no tests (spec §5.1).
				say("frappe-test: the tests module is off; skipping stages 1 to 6 (no bench is started)")
				for name in ("up", "site", "tests", "coverage", "testmap", "composition"):
					self.stages[name] = "skipped"
			else:
				self.bench_stages(plan, switches)

			self.ty(pick(a.ty, a.ci, planned("ty"), False))
			self.nix_lint(pick(a.nix_lint, a.ci, planned("nix-lint"), False))
			self.shell_checks(
				pick(a.shell_checks, a.ci, planned("shell-checks"), False),
				[str(c) for c in plan.get("shell-checks") or []],
			)
		except Finish:
			pass
		return self.finish()


def usage_error(message: str) -> int:
	warn(f"frappe-test: {message}")
	return EX_USAGE


def main(argv: list[str]) -> int:
	try:
		args = parse_args(argv)
	except Help:
		sys.stdout.write(USAGE)
		return 0
	except UsageError as e:
		if e.show_usage:
			sys.stderr.write(USAGE)
		return usage_error(str(e))

	bench_root = os.environ.get("FRAPPE_BENCH_ROOT", "")
	devenv_root = os.environ.get("DEVENV_ROOT", "")
	if not bench_root or not devenv_root:
		warn("frappe-test: run it inside the app dev shell (nix develop)")
		return ENVIRONMENT
	repo = Path(os.path.realpath(devenv_root))
	bench = Path(bench_root)

	if args.down:
		compose = Compose.from_env()
		if not compose.socket:
			warn("frappe-test: PC_SOCKET_PATH is not set: run it inside the app dev shell (nix develop)")
			return ENVIRONMENT
		return compose.stop()

	out = Path(args.out or repo / ".dev-dist" / "test")
	out.mkdir(parents=True, exist_ok=True)
	out = Path(os.path.realpath(out))
	junit = args.junit or (str(out / "junit.xml") if args.ci else "")
	if junit:
		junit = os.path.realpath(junit)

	app = args.app or find_app(bench, repo)
	if not app:
		return usage_error("cannot tell which app this is; pass --app")
	site = args.site if args.site is not None else os.environ.get("FRAPPE_SITE", "")
	if not site:
		return usage_error("no site: set FRAPPE_SITE or pass --site")
	if args.reuse and args.recreate:
		return usage_error("--reuse-site and --recreate-site exclude each other")

	# Stage 2 recreates the site with `bench new-site --force`, which drops its
	# database, and the default site is the dev shell's own. In CI that is the
	# point; at a desk it is somebody's work, so an existing site is only dropped
	# when asked: --recreate-site, or a yes at the prompt.
	if not args.ci and not args.reuse and not args.recreate and (bench / "sites" / site).is_dir():
		if devshell.has_terminal():
			try:
				yes = devshell.ask_recreate(
					f"frappe-test: {site} exists. Recreating it drops its database and resets Administrator's password."
				)
			except Stop as e:
				return e.code
			if not yes:
				return usage_error(
					f"kept {site}; pass --reuse-site to test on it as it is, or --recreate-site"
				)
			args.recreate = True
		else:
			return usage_error(
				f"{site} exists: pass --reuse-site to test on it as it is, or --recreate-site to drop its database and create it again"
			)

	for stale in STALE:
		(out / stale).unlink(missing_ok=True)
	if junit:
		Path(junit).unlink(missing_ok=True)

	run = Run(args, bench=bench, repo=repo, out=out, junit=junit, app=app, site=site)
	try:
		return run.run()
	except Stop as e:
		return e.code
	except KeyboardInterrupt:
		return 130
	finally:
		if run.stop_bench:
			run.compose.down()


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
