"""``ironclad coverage-ratchet`` and ``ironclad test-report``: frappe-test's stages 4 and 9 (spec §5.1).

``coverage-ratchet`` is the upward half of the coverage gate (S24, decision 5): while
``[tool.coverage.report] fail_under`` is below 80, a total 2 points or more above it
exits 1 with the value to raise it to. coverage's own ``report`` enforces the floor.

``test-report`` gathers what the stages left in the output directory (``coverage.json``,
``testmap.json``, ``composition.json``, the JUnit file) and the verdicts frappe-test hands
it, and writes ``ironclad-report.json`` (validated by ``schema/ironclad-report.schema.json``
in ironclad's data) and ``summary.md``, which it also appends to ``$GITHUB_STEP_SUMMARY``.
"""

import argparse
import json
import math
import os
import re
import subprocess
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from ironclad import __version__
from ironclad.commands.testmap import composition_markdown, testmap_markdown
from ironclad.common import flakelock
from ironclad.common.report import CLEAN, DRIFT, ConfigError, EnvError, IroncladError

# frappe-test's stages in verdict order, with the exit status each failure means.
STAGES = (
	("tests", 1),
	("coverage", 2),
	("testmap", 3),
	("composition", 4),
	("ty", 5),
	("nix-lint", 6),
	("shell-checks", 7),
)
# Every stage name, in the order they run: the environment's two, then the verdict ones.
STAGE_NAMES = ("up", "site", *(name for name, _ in STAGES))
VERDICTS = ("ok", "failed", "skipped", "error")
RATCHET_CEILING = 80.0
RATCHET_STEP = 2.0


def fail_under(pyproject: Path) -> float | None:
	"""``[tool.coverage.report] fail_under``, or None when unset."""
	try:
		doc = tomllib.loads(pyproject.read_text())
	except FileNotFoundError as e:
		raise EnvError(f"{pyproject} does not exist") from e
	except tomllib.TOMLDecodeError as e:
		raise ConfigError(f"{pyproject}: {e}") from e
	value = doc.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under")
	if value is None:
		return None
	if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 100:
		raise ConfigError(f"[tool.coverage.report] fail_under must be a number from 0 to 100, not {value!r}")
	return float(value)


def ratchet_message(total: float, floor: float | None) -> str | None:
	"""The raise the upward ratchet asks for, or None when ``floor`` may stay."""
	floor = floor or 0.0
	if floor >= RATCHET_CEILING or total < floor + RATCHET_STEP:
		return None
	target = min(int(RATCHET_CEILING), math.floor(total) - 1)
	return f"coverage is {total:.1f}; raise [tool.coverage.report] fail_under to {target}"


def _coverage_total(path: Path) -> float:
	try:
		return float(json.loads(path.read_text())["totals"]["percent_covered"])
	except FileNotFoundError as e:
		raise EnvError(f"{path} does not exist") from e
	except (OSError, ValueError, KeyError, TypeError) as e:
		raise EnvError(f"{path}: not a coverage JSON report ({e})") from e


def run_ratchet(args: argparse.Namespace) -> int:
	total = _coverage_total(Path(args.coverage_json))
	floor = fail_under(Path(args.pyproject))
	message = ratchet_message(total, floor)
	if message:
		print(message)
		return DRIFT
	print(f"coverage is {total:.1f}; fail_under is {floor if floor is not None else 'unset'}")
	return CLEAN


def junit_documents(text: str) -> list[ET.Element]:
	"""Every XML document in a JUnit file.

	frappe's run-tests runs one xmlrunner per test category into the same file, so the
	file can hold several documents back to back.
	"""
	chunks = [c for c in re.split(r"(?=<\?xml\b)", text) if c.strip()]
	return [ET.fromstring(c.encode()) for c in chunks]


def junit_counts(path: Path) -> tuple[dict | None, list[ET.Element]]:
	"""``{ran, failures, errors, skipped}`` summed over every test suite, and the suites."""
	if not path.is_file():
		return None, []
	try:
		docs = junit_documents(path.read_text())
	except ET.ParseError:
		return None, []
	suites = []
	for doc in docs:
		suites += [doc] if doc.tag == "testsuite" else list(doc.iter("testsuite"))
	counts = {"ran": 0, "failures": 0, "errors": 0, "skipped": 0}
	for suite in suites:
		counts["ran"] += int(suite.get("tests", 0))
		counts["failures"] += int(suite.get("failures", 0))
		counts["errors"] += int(suite.get("errors", 0))
		counts["skipped"] += int(suite.get("skipped", 0))
	return counts, suites


def merge_junit(path: Path, suites: list[ET.Element]) -> None:
	"""Rewrite a multi-document JUnit file as one ``<testsuites>`` document."""
	root = ET.Element("testsuites")
	root.extend(suites)
	ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _load_json(path: Path) -> dict | None:
	try:
		return json.loads(path.read_text())
	except (OSError, ValueError):
		return None


def frappe_nix_rev(repo_root: Path) -> str | None:
	"""The frappe-nix commit the app's ``flake.lock`` pins, or None."""
	try:
		lock = flakelock.load(repo_root / "flake.lock")
		found = flakelock.node_at(lock, ["frappe-nix"])
	except IroncladError:
		return None
	if not found:
		return None
	return found[1].get("locked", {}).get("rev")


def git_sha(repo_root: Path) -> str | None:
	try:
		out = subprocess.run(
			["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
		)
	except (OSError, subprocess.CalledProcessError):
		return None
	return out.stdout.strip() or None


def exit_status(stages: dict[str, str], environment: bool) -> int:
	"""The verdict of the first failing stage, 10 for an environment failure, else 0."""
	if environment:
		return 10
	for name, code in STAGES:
		if stages.get(name) in ("failed", "error"):
			return code
	return 0


def build_report(args: argparse.Namespace) -> tuple[dict, dict]:
	"""``ironclad-report.json``, and the pieces summary.md is made of."""
	out = Path(args.out)
	repo_root = Path(args.repo_root)
	given = dict(s.split("=", 1) for s in args.stage)
	for name, verdict in given.items():
		if name not in STAGE_NAMES:
			raise ConfigError(f"unknown stage {name!r} ({', '.join(STAGE_NAMES)})")
		if verdict not in VERDICTS:
			raise ConfigError(f"stage {name}: unknown verdict {verdict!r} ({', '.join(VERDICTS)})")
	stages = {name: given[name] for name in STAGE_NAMES if name in given}

	coverage = _load_json(out / "coverage.json")
	coverage_section = None
	if coverage:
		coverage_section = {
			"percent": round(float(coverage["totals"]["percent_covered"]), 1),
			"fail_under": fail_under(Path(args.pyproject)),
			"modules": {
				path: round(float(entry["summary"]["percent_covered"]), 1)
				for path, entry in sorted(coverage.get("files", {}).items())
			},
		}

	testmap = _load_json(out / "testmap.json")
	testmap_section = None
	if testmap:
		testmap_section = {
			"targets": len(testmap.get("targets", [])),
			"untested": testmap.get("untested", []),
			"exempt": testmap.get("exempt", 0),
			"stale_exemptions": testmap.get("stale_exemptions", []),
		}

	composition = _load_json(out / "composition.json")
	composition_section = None
	if composition:
		composition_section = {
			"ok": bool(composition.get("ok")),
			"doctypes": {
				dt: {
					"layers": d.get("layers", []),
					"real_ok": bool(d.get("real_ok")),
					"app_first_ok": bool(d.get("app_first_ok")),
				}
				for dt, d in composition.get("doctypes", {}).items()
			},
		}

	tests = None
	if args.junit:
		junit = Path(args.junit)
		tests, suites = junit_counts(junit)
		if suites and junit.read_text().count("<?xml") > 1:
			merge_junit(junit, suites)

	ty = None
	if stages.get("ty") in ("ok", "failed") and args.ty_diagnostics is not None:
		ty = {"diagnostics": args.ty_diagnostics, "ignores": args.ty_ignores or 0}

	nix_lint = {"ok": "ok", "failed": "failed", "error": "failed"}.get(stages.get("nix-lint", ""), "skipped")
	code = exit_status(stages, args.environment_failure)
	report = {
		"schema": 1,
		"app": args.app,
		"frappe_nix": {"rev": frappe_nix_rev(repo_root), "version": __version__},
		"git_sha": git_sha(repo_root),
		"tests": tests,
		"coverage": coverage_section,
		"testmap": testmap_section,
		"composition": composition_section,
		"ty": ty,
		"nix_lint": nix_lint,
		"stages": stages,
		"exit": code,
	}
	return report, {"testmap": testmap, "composition": composition}


def summary_markdown(report: dict, raw: dict, shell_checks_log: Path | None) -> str:
	lines = [f"## frappe-test: {report['app']}", ""]
	verdict = "passed" if report["exit"] == 0 else f"failed (exit {report['exit']})"
	lines += [f"**{verdict}**", "", "| Stage | Verdict |", "|---|---|"]
	for name, _ in STAGES:
		lines.append(f"| {name} | {report['stages'].get(name, 'skipped')} |")
	lines.append("")
	if report["tests"]:
		t = report["tests"]
		lines += [
			f"Tests: {t['ran']} ran, {t['failures']} failure(s), {t['errors']} error(s), {t['skipped']} skipped.",
			"",
		]
	cov = report["coverage"]
	if cov:
		lines += [
			"### Coverage",
			"",
			f"Total **{cov['percent']}%**, `fail_under` {cov['fail_under'] if cov['fail_under'] is not None else 'unset'}.",
			"",
			"| Module | Coverage |",
			"|---|---|",
		]
		lines += [f"| `{path}` | {pct}% |" for path, pct in cov["modules"].items()]
		lines.append("")
	if raw["testmap"]:
		lines.append(testmap_markdown(raw["testmap"]))
	if raw["composition"]:
		lines.append(composition_markdown(raw["composition"]))
	if report["ty"]:
		lines += [
			"### ty",
			"",
			f"{report['ty']['diagnostics']} diagnostic(s), {report['ty']['ignores']} `ty: ignore` comment(s).",
			"",
		]
	if shell_checks_log and shell_checks_log.is_file() and shell_checks_log.read_text().strip():
		lines += ["### Shell checks", "", "```", shell_checks_log.read_text().rstrip(), "```", ""]
	return "\n".join(lines).rstrip() + "\n"


def run_report(args: argparse.Namespace) -> int:
	out = Path(args.out)
	out.mkdir(parents=True, exist_ok=True)
	report, raw = build_report(args)
	(out / "ironclad-report.json").write_text(json.dumps(report, indent=2) + "\n")
	summary = summary_markdown(report, raw, Path(args.shell_checks_log) if args.shell_checks_log else None)
	(out / "summary.md").write_text(summary)
	step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
	if step_summary:
		with open(step_summary, "a", encoding="utf-8") as f:
			f.write(summary)
	print(f"frappe-test: report in {out / 'ironclad-report.json'}, exit {report['exit']}")
	return CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"coverage-ratchet",
		help="exit 1 when coverage has climbed 2 points past a fail_under below 80",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("--coverage-json", required=True)
	p.add_argument("--pyproject", required=True)
	p.set_defaults(func=run_ratchet)

	p = subparsers.add_parser(
		"test-report",
		help="write frappe-test's ironclad-report.json and summary.md",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("--out", required=True, help="frappe-test's output directory")
	p.add_argument("--app", required=True)
	p.add_argument("--repo-root", required=True)
	p.add_argument("--pyproject", required=True)
	p.add_argument("--junit", help="the JUnit file of the test run")
	p.add_argument(
		"--stage",
		action="append",
		default=[],
		metavar="NAME=VERDICT",
		help=f"a stage's verdict ({', '.join(VERDICTS)}); repeatable",
	)
	p.add_argument("--environment-failure", action="store_true", help="stage 1 or 2 failed: exit 10")
	p.add_argument("--ty-diagnostics", type=int)
	p.add_argument("--ty-ignores", type=int)
	p.add_argument("--shell-checks-log", help="what the failing shell checks printed")
	p.set_defaults(func=run_report)
