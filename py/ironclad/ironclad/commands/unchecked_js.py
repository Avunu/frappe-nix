"""``ironclad unchecked-js --stale``: unchecked-js entries whose file now type-checks (spec §2.9).

``[[tool.ironclad.unchecked-js]]`` lists the desk and web scripts strict checkJs skips while
they are being typed. An entry whose file has no error left is stale, and the list only
shrinks (R6), so ``typecheck`` runs this: one ``tsc -p`` over a generated project holding
every listed file under the desk-js preset, then exit 1 naming each listed file that
produced no diagnostic. An empty list is exit 0. No ``tsc`` (``node_modules/.bin/tsc``) is exit 3.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

from ironclad.common import repo
from ironclad.common.report import CLEAN, DRIFT, EnvError
from ironclad.scaffold import engine

_DIAG = re.compile(r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\): error TS\d+:", re.M)
PROJECT_DIR = ".dev-dist/unchecked-js"


def project(root: Path, app: str, paths: list[str]) -> Path:
	"""Write the temporary project; returns its tsconfig path."""
	directory = root / PROJECT_DIR
	directory.mkdir(parents=True, exist_ok=True)
	up = "../../"
	support = [
		f"{up}{p}" for p in ("types/doctypes.d.ts", f"types/{app}.augment.d.ts") if (root / p).is_file()
	]
	config = {
		"extends": "frappe-types/tsconfig/desk-js.json",
		"compilerOptions": {"noEmit": True, "composite": False, "incremental": False},
		"files": [f"{up}{p}" for p in paths] + support,
	}
	path = directory / "tsconfig.json"
	path.write_text(json.dumps(config, indent="\t") + "\n")
	return path


def diagnosed(output: str, root: Path) -> set[str]:
	"""The repo-relative files tsc reported an error in."""
	out = set()
	for m in _DIAG.finditer(output):
		name = m["file"].strip()
		full = (root / name).resolve() if not Path(name).is_absolute() else Path(name).resolve()
		try:
			out.add(full.relative_to(root.resolve()).as_posix())
		except ValueError:
			continue
	return out


def run(args: argparse.Namespace) -> int:
	root = Path.cwd()
	repo.toplevel(root)
	app = engine.load_app(root)
	cfg, _ = engine.config_for(app, None)
	paths = [u["path"] for u in cfg.get("unchecked-js", [])]
	if not args.stale:
		raise EnvError("nothing to do: pass --stale")
	if not paths:
		print("unchecked-js: the list is empty")
		return CLEAN
	tsc = Path(args.tsc) if args.tsc else root / "node_modules" / ".bin" / "tsc"
	if not tsc.exists():
		raise EnvError(f"{tsc} does not exist: run `yarn install` first")
	config = project(root, app.name, paths)
	proc = subprocess.run(
		[str(tsc), "-p", str(config.relative_to(root)), "--pretty", "false"],
		cwd=root,
		capture_output=True,
		text=True,
		check=False,
	)
	output = proc.stdout + proc.stderr
	errors = diagnosed(output, root)
	if proc.returncode != 0 and not errors:
		raise EnvError(f"tsc failed without a diagnostic in any file:\n{output.strip()}")
	stale = [p for p in paths if p not in errors]
	for p in stale:
		print(f"unchecked-js: {p} has no type errors left: remove its [[tool.ironclad.unchecked-js]] entry")
	return DRIFT if stale else CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"unchecked-js",
		help="report [[tool.ironclad.unchecked-js]] entries whose file now type-checks",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument(
		"--stale", action="store_true", help="fail naming each listed file that tsc finds no error in"
	)
	p.add_argument("--tsc", help="the tsc to run (default: node_modules/.bin/tsc)")
	p.set_defaults(func=run)
