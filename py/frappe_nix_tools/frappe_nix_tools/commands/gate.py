"""``frappe-nix gate lint|typecheck|marketplace``: a no-Nix gate's steps, on any CI (spec S43, §4.2).

The reusable workflows only check out, install node, uv and frappe-nix-tools (and, for
``lint``, the ``tools/`` project and the app's ``node_modules``), and then run this
command; any other CI, or a laptop, runs the same command after the same setup
(``docs/app-standards/ci.md``). It resolves the app's configuration itself (§8.4), runs
every step of the gate in order, each only while its module is on (a step whose module is
off is ``skipped``, with the reason), and exits 1 when any step failed, after running all
of them. A step that needs a file the app lacks (``.pre-commit-config.yaml``,
``tools/pyproject.toml``, a ``package.json`` script) is skipped too.

lint
  sync-check     ``frappe-nix sync --check`` (``--expect-rev`` when given): always
  dependabot-cooldown  [dependabot] every ``.github/dependabot.yml`` entry has a ``cooldown``
  prek           ``uv run --frozen --project tools prek run --all-files --show-diff-on-failure``
                 while ``.pre-commit-config.yaml`` is tracked
  customizations [test-utils, with validate_customizations or clean_customized_doctypes in
                 ``test-utils.hooks``] ``frappe-nix minibench --mode customizations`` into
                 ``.dev-dist/minibench``, then those manual-stage hooks from its app worktree
  semgrep        [semgrep] the frappe rules at the pinned ``frappe-semgrep-rules`` rev
                 (``semgrep.frappe-rules``), ``semgrep/test-correctness.yml`` over test files
                 (``semgrep.test-correctness``) and the app's own ``semgrep/`` rules
  ratchet        ``frappe-nix ratchet --base <base>`` (its rules follow their modules); skipped on
                 a push or schedule, or with no base
  ci-lint        ``yarn -s ci:lint`` when ``package.json`` has that script
typecheck
  frappe-path    ``FRAPPE_PATH`` = ``frappe-nix pin-path frappe`` (kept when already set), passed on
  frappe-node-modules  [typescript, ``frappe-node-modules = true``] ``yarn --cwd $FRAPPE_PATH install``
  typecheck      [typescript] ``yarn -s typecheck`` when the script exists
  unchecked-js   [typescript with check-js, a non-empty list] ``frappe-nix unchecked-js --stale``
  js-unit        [tests with js-unit] when ``test:unit`` exists: a ``test/unit/**/*.test.ts`` is
                 tracked, then ``yarn -s test:unit``
  nested         each nested frontend with a ``typecheck`` script: install and typecheck it
  doctypes       [typescript, ``types/doctypes.d.ts`` tracked] minibench doctypes, then
                 ``frappe-types gen-doctypes … --check``
  audit-consumer [typescript] ``frappe-types audit-consumer``: ``--strict`` with
                 ``typescript.audit-consumer``, else advisory when the subcommand exists
  ci-typecheck   ``yarn -s ci:typecheck`` when present
marketplace
  listing        [listing] ``EIO_BACKEND=posix frappe-nix listing check``

The ratchet base: ``--base``, else from ``GITHUB_EVENT_NAME``: on ``pull_request`` the
merge-base of ``$BASE_SHA`` and ``HEAD``; on ``workflow_dispatch`` the merge-base of
``origin/<integration>`` and ``HEAD``; on ``push`` and ``schedule`` (and with no event) the
ratchet is skipped.

The result is written to ``--report`` (default ``.dev-dist/gate/<gate>.json``) and, when
``$GITHUB_STEP_SUMMARY`` is set, as a table there. ``--format github`` groups each step's
output and annotates failures.
"""

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from frappe_nix_tools.common import data_path, repo
from frappe_nix_tools.common.appgates import AppConfig
from frappe_nix_tools.common.appgates import load as load_app
from frappe_nix_tools.common.pins import pin_path
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError, EnvError, FrappeNixError
from frappe_nix_tools.scaffold import context, discover, globs

GATES = ("lint", "typecheck", "marketplace")
TOOLS_PYTHON = "3.14"
CUSTOMIZATION_HOOKS = ("validate_customizations", "clean_customized_doctypes")


@dataclass
class Step:
	name: str
	module: str
	status: str = "skipped"
	reason: str = ""


@dataclass
class Gate:
	"""One gate's run: the app, the options, the environment the steps share."""

	app: AppConfig
	fmt: str
	base: str | None
	expect_rev: str
	env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
	steps: list[Step] = field(default_factory=list)

	@property
	def root(self) -> Path:
		return self.app.root

	def run(self, *cmd: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
		"""Run ``cmd`` with its output passed through; its exit status."""
		try:
			return subprocess.run(
				list(cmd), cwd=cwd or self.root, env={**self.env, **(env or {})}, check=False
			).returncode
		except FileNotFoundError as e:
			print(f"frappe-nix gate: {cmd[0]}: not found ({e})", file=sys.stderr)
			return 127

	def step(self, name: str, module: str, body: Callable[[], tuple[str, str]]) -> None:
		"""Run one step; ``body`` returns ``(status, reason)``. An error in it fails the step."""
		s = Step(name, module)
		if self.fmt == "github":
			print(f"::group::{name}", flush=True)
		else:
			print(f"── {name}", flush=True)
		try:
			s.status, s.reason = body()
		except FrappeNixError as e:
			s.status, s.reason = "failed", str(e)
		if self.fmt == "github":
			print("::endgroup::", flush=True)
			if s.status == "failed":
				print(f"::error title=gate {name}::{s.reason or 'failed'}", flush=True)
		print(f"   {name}: {s.status}{f' ({s.reason})' if s.reason else ''}", flush=True)
		self.steps.append(s)

	def skip(self, name: str, module: str, reason: str) -> None:
		self.steps.append(Step(name, module, "skipped", reason))
		print(f"── {name}: skipped ({reason})", flush=True)

	def on(self, module: str) -> bool:
		return bool(self.app.modules.get(module))

	def cfg(self, dotted: str, default=None):
		value = self.app.cfg
		for part in dotted.split("."):
			if not isinstance(value, dict) or part not in value:
				return default
			value = value[part]
		return value


def frappe_nix(*args: str) -> list[str]:
	"""This frappe-nix-tools' own CLI, whatever ``frappe-nix`` is on PATH."""
	return [sys.executable, "-m", "frappe_nix_tools.cli", *args]


def ok(code: int, what: str = "") -> tuple[str, str]:
	return ("ok", "") if code == 0 else ("failed", f"{what or 'exit'} {code}".strip())


def scripts(root: Path) -> dict:
	try:
		doc = json.loads((root / "package.json").read_text())
	except (OSError, json.JSONDecodeError):
		return {}
	value = doc.get("scripts") if isinstance(doc, dict) else None
	return value if isinstance(value, dict) else {}


def tools_cmd(root: Path, tool: str) -> list[str]:
	return ["uv", "run", "--frozen", "--project", str(root / "tools"), tool]


def ratchet_base(g: Gate) -> tuple[str | None, str]:
	"""The ratchet's base and, without one, why it is skipped (S24)."""
	if g.base:
		return g.base, ""
	event = g.env.get("GITHUB_EVENT_NAME", "")
	if event in ("push", "schedule"):
		return None, f"no ratchet on a {event}"
	if event == "pull_request" or event == "pull_request_target":
		sha = g.env.get("BASE_SHA", "")
		if not sha:
			return None, "BASE_SHA is not set"
		return repo.git(g.root, "merge-base", sha, "HEAD").strip(), ""
	if event == "workflow_dispatch":
		integration = g.env.get("INTEGRATION") or g.app.integration
		return repo.git(g.root, "merge-base", f"origin/{integration}", "HEAD").strip(), ""
	return None, "no --base"


# ── lint ──────────────────────────────────────────────────────────────────────


def cooldowns(path: Path) -> tuple[str, str]:
	"""Every dependabot entry, the local region's too, has a ``cooldown`` (§7 N4).

	zizmor's dependabot-cooldown audit (1.30.1) reads a missing cooldown as Dependabot's
	implicit 3 days and reports one only on the first entry it checks, so an entry an app adds
	in the ``updates`` region below the managed ones would pass it."""
	try:
		doc = yaml.safe_load(path.read_text()) or {}
	except (OSError, yaml.YAMLError) as e:
		return "failed", f"{path.name}: {e}"
	missing = [
		f"{u.get('package-ecosystem', '?')} {u.get('directory', '?')}"
		for u in doc.get("updates") or []
		if isinstance(u, dict) and not u.get("cooldown")
	]
	if missing:
		return "failed", f"dependabot entries without a cooldown: {', '.join(missing)}"
	return "ok", ""


def lint(g: Gate) -> None:
	root, tracked = g.root, set(repo.ls_files(g.root))

	def sync_check() -> tuple[str, str]:
		args = ["sync", "--check", "--format", "github" if g.fmt == "github" else "text"]
		if g.expect_rev:
			args += ["--expect-rev", g.expect_rev]
		return ok(g.run(*frappe_nix(*args)))

	g.step("sync-check", "ci", sync_check)

	if not g.on("dependabot"):
		g.skip("dependabot-cooldown", "dependabot", "module dependabot is off")
	elif ".github/dependabot.yml" not in tracked:
		g.skip("dependabot-cooldown", "dependabot", ".github/dependabot.yml is not tracked")
	else:
		g.step("dependabot-cooldown", "dependabot", lambda: cooldowns(root / ".github" / "dependabot.yml"))

	has_tools = (root / "tools" / "pyproject.toml").is_file()
	if ".pre-commit-config.yaml" not in tracked:
		g.skip("prek", "", ".pre-commit-config.yaml is not live")
	elif not has_tools:
		g.skip("prek", "", "tools/pyproject.toml is not live")
	else:
		g.step(
			"prek",
			"",
			lambda: ok(g.run(*tools_cmd(root, "prek"), "run", "--all-files", "--show-diff-on-failure")),
		)

	hooks = [h for h in CUSTOMIZATION_HOOKS if h in (g.cfg("test-utils.hooks") or [])]
	if not g.on("test-utils"):
		g.skip("customizations", "test-utils", "module test-utils is off")
	elif not hooks:
		g.skip("customizations", "test-utils", "neither customization hook is in test-utils.hooks")
	else:

		def customizations() -> tuple[str, str]:
			dest = root / ".dev-dist" / "minibench"
			code = g.run(*frappe_nix("minibench", "--mode", "customizations", "--dest", str(dest)))
			if code != 0:
				return "failed", f"minibench exit {code} (the bench layout guard)"
			worktree = dest / "apps" / g.app.app
			failed = [
				h
				for h in hooks
				if g.run(
					*tools_cmd(root, "prek"),
					"run",
					"--hook-stage",
					"manual",
					h,
					"--all-files",
					"--show-diff-on-failure",
					cwd=worktree,
				)
				!= 0
			]
			return ("failed", f"{', '.join(failed)} failed") if failed else ("ok", "")

		g.step("customizations", "test-utils", customizations)

	if not g.on("semgrep"):
		g.skip("semgrep", "semgrep", "module semgrep is off")
	else:

		def semgrep() -> tuple[str, str]:
			app = g.app.app
			semgrep_cmd = tools_cmd(root, "semgrep") if has_tools else ["semgrep"]
			scan = [*semgrep_cmd, "scan", "--error", "--metrics=off"]
			failed = []
			if g.cfg("semgrep.frappe-rules", True):
				rules = pin_path("frappe-semgrep-rules", repo.find_up("flake.lock", root).resolve())
				if g.run(*scan, "--config", str(Path(rules) / "rules"), app) != 0:
					failed.append("frappe rules")
			if g.cfg("semgrep.test-correctness", True):
				# The tracked test files, named: semgrep's default ignores skip every tests/
				# directory, where most of an app's tests live.
				config = str(data_path("semgrep/test-correctness.yml"))
				tests = [
					p
					for p in sorted(tracked)
					if p.startswith(f"{app}/") and fnmatch.fnmatchcase(p.rsplit("/", 1)[-1], "test_*.py")
				]
				if tests and g.run(*scan, "--config", config, *tests) != 0:
					failed.append("test correctness")
			if (root / "semgrep").is_dir():
				if g.run(*scan, "--config", "semgrep", app) != 0:
					failed.append("the app's semgrep/ rules")
			return ("failed", ", ".join(failed)) if failed else ("ok", "")

		g.step("semgrep", "semgrep", semgrep)

	base, why = ratchet_base(g)
	if base is None:
		g.skip("ratchet", "", why)
	else:
		g.step("ratchet", "", lambda: ok(g.run(*frappe_nix("ratchet", "--base", base))))

	if "ci:lint" in scripts(root):
		g.step("ci-lint", "", lambda: ok(g.run("yarn", "-s", "ci:lint")))
	else:
		g.skip("ci-lint", "", "package.json has no ci:lint script")


# ── typecheck ─────────────────────────────────────────────────────────────────


def typecheck(g: Gate) -> None:
	root = g.root
	tracked = repo.ls_files(root)
	pkg = scripts(root)
	ts_on = g.on("typescript")

	def frappe_path() -> tuple[str, str]:
		have = g.env.get("FRAPPE_PATH", "")
		if have and Path(have).is_dir():
			return "ok", "FRAPPE_PATH from the environment"
		g.env["FRAPPE_PATH"] = str(pin_path("frappe", repo.find_up("flake.lock", root).resolve()))
		return "ok", ""

	g.step("frappe-path", "", frappe_path)
	fp = g.env.get("FRAPPE_PATH", "")

	if not ts_on:
		g.skip("frappe-node-modules", "typescript", "module typescript is off")
	elif not g.cfg("frappe-node-modules", False):
		g.skip("frappe-node-modules", "typescript", "frappe-node-modules is false")
	elif not fp:
		g.skip("frappe-node-modules", "typescript", "no FRAPPE_PATH")
	else:
		g.step(
			"frappe-node-modules",
			"typescript",
			lambda: ok(
				g.run(
					"yarn",
					"--cwd",
					fp,
					"install",
					"--frozen-lockfile",
					"--ignore-scripts",
					"--non-interactive",
				)
			),
		)

	if not ts_on:
		g.skip("typecheck", "typescript", "module typescript is off")
	elif "typecheck" not in pkg:
		g.skip("typecheck", "typescript", "package.json has no typecheck script")
	else:
		g.step("typecheck", "typescript", lambda: ok(g.run("yarn", "-s", "typecheck")))

	if not (ts_on and g.cfg("typescript.check-js", False)):
		g.skip("unchecked-js", "typescript", "typescript.check-js is off")
	elif not g.cfg("unchecked-js", []):
		g.skip("unchecked-js", "typescript", "[[tool.frappe-nix.unchecked-js]] is empty")
	else:
		g.step("unchecked-js", "typescript", lambda: ok(g.run(*frappe_nix("unchecked-js", "--stale"))))

	if not (g.on("tests") and g.cfg("tests.js-unit", True)):
		g.skip("js-unit", "tests", "module tests (js-unit) is off")
	elif "test:unit" not in pkg:
		g.skip("js-unit", "tests", "package.json has no test:unit script")
	else:

		def js_unit() -> tuple[str, str]:
			if not globs.select(["test/unit/**/*.test.ts"], tracked):
				return "failed", "test:unit exists, but no test/unit/**/*.test.ts is tracked"
			return ok(g.run("yarn", "-s", "test:unit"))

		g.step("js-unit", "tests", js_unit)

	nested = discover.facts(root, g.app.app, g.app.cfg, tracked, g.app.modules)["nested_frontends"]
	with_script = [d for d in nested if "typecheck" in scripts(root / d)]
	if not with_script:
		g.skip("nested", "", "no nested frontend has a typecheck script")
	else:

		def nested_step() -> tuple[str, str]:
			failed = [
				d
				for d in with_script
				if g.run("yarn", "--cwd", d, "install", "--frozen-lockfile") != 0
				or g.run("yarn", "--cwd", d, "typecheck") != 0
			]
			return ("failed", ", ".join(failed)) if failed else ("ok", "")

		g.step("nested", "", nested_step)

	if not ts_on:
		g.skip("doctypes", "typescript", "module typescript is off")
	elif "types/doctypes.d.ts" not in tracked:
		g.skip("doctypes", "typescript", "types/doctypes.d.ts is not tracked")
	else:

		def doctypes() -> tuple[str, str]:
			dest = root / ".dev-dist" / "minibench"
			code = g.run(*frappe_nix("minibench", "--mode", "doctypes", "--dest", str(dest)))
			if code != 0:
				return "failed", f"minibench exit {code}"
			major = int(g.app.cfg["frappe-major"])
			siblings = ",".join(s["name"] for s in context.siblings(g.app.cfg, major))
			return ok(
				g.run(
					"yarn",
					"-s",
					"frappe-types",
					"gen-doctypes",
					"--bench",
					str(dest),
					"--app",
					g.app.app,
					"--include-siblings",
					siblings,
					"--out",
					"types/doctypes.d.ts",
					"--check",
				)
			)

		g.step("doctypes", "typescript", doctypes)

	if not ts_on:
		g.skip("audit-consumer", "typescript", "module typescript is off")
	elif g.cfg("typescript.audit-consumer", False):
		g.step(
			"audit-consumer",
			"typescript",
			lambda: ok(
				g.run(
					"yarn",
					"-s",
					"frappe-types",
					"audit-consumer",
					"--app",
					".",
					"--strict",
					"--types",
					"types/*.d.ts",
				)
			),
		)
	elif not (root / "node_modules" / ".bin" / "frappe-types").exists():
		g.skip("audit-consumer", "typescript", "frappe-types is not installed")
	else:

		def advisory() -> tuple[str, str]:
			try:
				help_text = subprocess.run(
					["yarn", "-s", "frappe-types", "--help"],
					cwd=root,
					env=g.env,
					capture_output=True,
					text=True,
					check=False,
				).stdout
			except FileNotFoundError:
				help_text = ""
			if "audit-consumer" not in help_text:
				return "skipped", "this frappe-types has no audit-consumer"
			code = g.run("yarn", "-s", "frappe-types", "audit-consumer", "--app", ".")
			if code != 0 and g.fmt == "github":
				print(
					"::notice title=audit-consumer::advisory: findings above (typescript.audit-consumer is off)"
				)
			return "ok", "advisory" + (f" (exit {code})" if code else "")

		g.step("audit-consumer", "typescript", advisory)

	if "ci:typecheck" in pkg:
		g.step("ci-typecheck", "", lambda: ok(g.run("yarn", "-s", "ci:typecheck")))
	else:
		g.skip("ci-typecheck", "", "package.json has no ci:typecheck script")


# ── marketplace ───────────────────────────────────────────────────────────────


def marketplace(g: Gate) -> None:
	if not g.on("listing"):
		g.skip("listing", "listing", "module listing is off")
		return
	fmt = "github" if g.fmt == "github" else "text"
	g.step(
		"listing",
		"listing",
		lambda: ok(g.run(*frappe_nix("listing", "check", "--format", fmt), env={"EIO_BACKEND": "posix"})),
	)


RUNNERS: dict[str, Callable[[Gate], None]] = {
	"lint": lint,
	"typecheck": typecheck,
	"marketplace": marketplace,
}


def summary(gate: str, steps: list[Step]) -> str:
	lines = [f"### frappe-nix gate {gate}", "", "| Step | Module | Result |", "|---|---|---|"]
	for s in steps:
		result = (s.status + (f": {s.reason}" if s.reason else "")).replace("|", "\\|")
		lines.append(f"| {s.name} | {s.module or '—'} | {result} |")
	return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
	if args.gate not in GATES:
		raise ConfigError(f"the gate is one of {', '.join(GATES)}, not {args.gate!r}")
	root = repo.find_up("pyproject.toml").parent
	app = load_app(root)
	g = Gate(app, args.format, args.base or None, args.expect_rev or "")
	# The tools/ project's interpreter: the one frappe-nix-tools is installed with (§4.1), not
	# uv's newest (semgrep 1.179.0's engine crashes on 3.15); and semgrep's posix I/O backend,
	# which hosted runners' thread limits need (frappe-listing's L7 scan sets it too).
	g.env.setdefault("UV_PYTHON", TOOLS_PYTHON)
	g.env.setdefault("EIO_BACKEND", "posix")
	RUNNERS[args.gate](g)
	failed = [s for s in g.steps if s.status == "failed"]
	report = Path(args.report) if args.report else root / ".dev-dist" / "gate" / f"{args.gate}.json"
	report.parent.mkdir(parents=True, exist_ok=True)
	doc = {
		"schema": 1,
		"gate": args.gate,
		"app": app.app,
		"status": "failed" if failed else "ok",
		"steps": [
			{"name": s.name, "module": s.module, "status": s.status, "reason": s.reason} for s in g.steps
		],
	}
	report.write_text(json.dumps(doc, indent=2) + "\n")
	table = summary(args.gate, g.steps)
	if os.environ.get("GITHUB_STEP_SUMMARY"):
		try:
			with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
				f.write(table)
		except OSError as e:
			raise EnvError(f"cannot write $GITHUB_STEP_SUMMARY: {e}") from e
	print(table)
	return DRIFT if failed else CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"gate",
		help="run a no-Nix CI gate's steps: lint, typecheck or marketplace (S43)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("gate", help="lint, typecheck or marketplace")
	p.add_argument("--base", help="the ratchet's base commit (default: from GITHUB_EVENT_NAME)")
	p.add_argument("--expect-rev", default="", help="lint: passed to sync --check --expect-rev")
	p.add_argument("--format", choices=("text", "github"), default="text")
	p.add_argument("--report", help="where to write the JSON result (default: .dev-dist/gate/<gate>.json)")
	p.set_defaults(func=run)
