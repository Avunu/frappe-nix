"""``frappe-nix testmap`` and ``frappe-nix composition``: frappe-test's stages 5 and 6 (spec §5.1).

Both run a script from ``frappe_nix_tools/bench/`` under the bench's interpreter (``--python``,
normally ``$FRAPPE_BENCH_ROOT/env/bin/python``), which is the one that can import frappe
and the app, then read back the JSON it wrote and print a table.

``testmap`` (§5.1.1) exits 1 when a whitelisted function or hook target has no line of its
body run by the tests and no ``[[tool.frappe-nix.untested]]`` entry, or when an entry is
stale; ``composition`` (§5.1.2) exits 1 when a controller the app extends or overrides
does not compose in the real app order or with the app first. Both exit 3 when the probe
itself fails (the site is unreachable, an input is unreadable).
"""

import argparse
import json
import subprocess
from pathlib import Path

from frappe_nix_tools.bench import script
from frappe_nix_tools.common.report import CLEAN, DRIFT, EnvError


def run_probe(python: str, name: str, args: list[str], out: Path) -> dict:
	"""Run ``frappe_nix_tools/bench/<name>.py`` with ``python`` and return the report it wrote."""
	out.parent.mkdir(parents=True, exist_ok=True)
	out.unlink(missing_ok=True)
	cmd = [python, str(script(name)), *args, "--out", str(out)]
	try:
		proc = subprocess.run(cmd, check=False)
	except FileNotFoundError as e:
		raise EnvError(f"{python}: {e.strerror}") from e
	if proc.returncode != 0 or not out.is_file():
		raise EnvError(f"the {name} probe exited {proc.returncode} without a report")
	try:
		return json.loads(out.read_text())
	except ValueError as e:
		raise EnvError(f"{out}: {e}") from e


def _cell(text) -> str:
	return str(text).replace("|", "\\|").replace("\n", " ")


def testmap_markdown(report: dict) -> str:
	"""The testmap as a markdown section: the failures first, then every target."""
	lines = ["### Testmap", ""]
	targets = report.get("targets", [])
	lines.append(
		f"{len(targets)} target(s), {len(report.get('untested', []))} untested, "
		f"{report.get('exempt', 0)} exempt, {len(report.get('stale_exemptions', []))} stale exemption(s)."
	)
	lines.append("")
	if report.get("untested"):
		lines += ["**Untested** (add a test, or a `[[tool.frappe-nix.untested]]` entry with a reason):", ""]
		lines += [f"- `{p}`" for p in report["untested"]]
		lines.append("")
	if report.get("stale_exemptions"):
		lines += ["**Stale exemptions** (remove them from `[[tool.frappe-nix.untested]]`):", ""]
		lines += [f"- `{p}`" for p in report["stale_exemptions"]]
		lines.append("")
	if targets:
		lines += ["| Target | Kind | Where | Tested |", "|---|---|---|---|"]
		for t in targets:
			where = f"{t['file']}:{t['line']}" if t.get("file") else ""
			state = "yes" if t["tested"] else ("exempt" if t["exempt"] else "**no**")
			if t.get("error"):
				state += f" ({_cell(t['error'])})"
			lines.append(f"| `{_cell(t['path'])}` | {t['kind']} | {_cell(where)} | {state} |")
		lines.append("")
	return "\n".join(lines) + "\n"


def composition_markdown(report: dict) -> str:
	"""The composition check as a markdown section."""
	lines = ["### Composition", ""]
	doctypes = report.get("doctypes", {})
	if not doctypes:
		return "\n".join([*lines, "The app extends or overrides no controller.", ""]) + "\n"
	lines += ["| DocType | Real order | App first | MRO |", "|---|---|---|---|"]
	for doctype, d in doctypes.items():
		real = "ok" if d.get("real_ok") else f"**{_cell(d.get('real_error', 'failed'))}**"
		first = "ok" if d.get("app_first_ok") else f"**{_cell(d.get('app_first_error', 'failed'))}**"
		mro = " → ".join(f"`{_cell(c)}`" for c in d.get("layers", []))
		lines.append(f"| {_cell(doctype)} | {real} | {first} | {mro} |")
	lines.append("")
	return "\n".join(lines) + "\n"


def testmap_failures(report: dict) -> list[str]:
	out = [f"untested: {p}" for p in report.get("untested", [])]
	out += [f"stale exemption: {p}" for p in report.get("stale_exemptions", [])]
	for t in report.get("targets", []):
		if t.get("error") and not t["tested"]:
			out.append(f"  {t['path']}: {t['error']}")
	return out


def run_testmap(args: argparse.Namespace) -> int:
	report = run_probe(
		args.python,
		"testmap_probe",
		[
			"--site",
			args.site,
			"--app",
			args.app,
			"--sites-path",
			args.sites_path,
			"--coverage-json",
			args.coverage_json,
			"--repo-root",
			args.repo_root,
			"--pyproject",
			args.pyproject,
		],
		Path(args.out),
	)
	if args.markdown:
		Path(args.markdown).write_text(testmap_markdown(report))
	failures = testmap_failures(report)
	for line in failures:
		print(f"testmap: {line}")
	if report.get("untested") or report.get("stale_exemptions"):
		return DRIFT
	print(
		f"testmap: {len(report.get('targets', []))} target(s), every one tested or exempt "
		f"({report.get('exempt', 0)} exempt)"
	)
	return CLEAN


def run_composition(args: argparse.Namespace) -> int:
	report = run_probe(
		args.python,
		"composition",
		["--site", args.site, "--app", args.app, "--sites-path", args.sites_path],
		Path(args.out),
	)
	if args.markdown:
		Path(args.markdown).write_text(composition_markdown(report))
	for doctype, d in report.get("doctypes", {}).items():
		for label, order in (("real", report.get("order")), ("app_first", report.get("app_first_order"))):
			if not d.get(f"{label}_ok"):
				print(f"composition: {doctype} with apps in order {order}: {d.get(f'{label}_error')}")
	if not report.get("ok"):
		return DRIFT
	print(f"composition: {len(report.get('doctypes', {}))} controller(s) compose in both orders")
	return CLEAN


def _probe_args(p: argparse.ArgumentParser) -> None:
	p.add_argument(
		"--python", required=True, help="the bench's interpreter ($FRAPPE_BENCH_ROOT/env/bin/python)"
	)
	p.add_argument("--site", required=True)
	p.add_argument("--app", required=True)
	p.add_argument("--sites-path", required=True, help="the bench's sites/ directory")
	p.add_argument("--out", required=True, help="where the JSON report goes")
	p.add_argument("--markdown", help="also write the report as a markdown section here")


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"testmap",
		help="check that every whitelisted function and hook target was run by the tests",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	_probe_args(p)
	p.add_argument("--coverage-json", required=True, help="coverage.json of the test run")
	p.add_argument(
		"--repo-root", required=True, help="the app repository (coverage.json's keys are relative to it)"
	)
	p.add_argument(
		"--pyproject", required=True, help="the app's pyproject.toml ([[tool.frappe-nix.untested]])"
	)
	p.set_defaults(func=run_testmap)

	p = subparsers.add_parser(
		"composition",
		help="check that the app's controller extensions compose in any app order",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	_probe_args(p)
	p.set_defaults(func=run_composition)
