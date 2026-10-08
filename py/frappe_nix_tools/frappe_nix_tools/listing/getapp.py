"""L9: pilot's get-app validator on a bench that has the app's dependency apps (spec §5.2, S20).

The registry validates an app on a throwaway bench with ``pilot.core.bench.Bench``. Without
the apps it depends on, an app that imports erpnext or hrms fails ImportCheck for reasons
that aren't its own; ``frappe/marketplace#29`` puts them on the bench, and so does this:

1. ``apps/frappe`` and each dependency app (``[tool.bench.frappe-dependencies]`` beyond
   frappe) are the source trees the app's ``flake.lock`` pins, fetched like every pin
   (``frappe-nix pin-path``, narHash-verified) and copied into the bench as git checkouts,
   which pilot's inventory needs;
2. the app is its tracked files, as the registry clones them;
3. ``getapp_runner.py`` runs every check of pilot's ``_all_checks()`` on its own, with the
   pinned ``frappe/pilot`` on ``PYTHONPATH`` and frappe's ``requires-python`` (3.14 for
   version-16) as the bench's interpreter, which uv provides.

Building frappe's ``mysqlclient`` (ImportCheck installs frappe into a throwaway venv when the
app imports anything the bench lacks) needs ``pkg-config`` and the MySQL client headers:
CI's ``marketplace`` job installs them, the dev shell has them. A bench that cannot be
built is an environment error (exit 3); a check that fails is an L9 error.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from frappe_nix_tools.common import pins, repo
from frappe_nix_tools.common.report import EnvError
from frappe_nix_tools.listing import baseline
from frappe_nix_tools.listing.rules import Result
from frappe_nix_tools.listing.target import Target

RUNNER = Path(__file__).with_name("getapp_runner.py")


def _checkout(tree: Path, dest: Path, origin: str) -> None:
	"""``tree`` copied to ``dest`` as a git checkout with ``origin`` (pilot reads the remote)."""
	shutil.copytree(tree, dest, symlinks=True)
	for path in [dest, *dest.rglob("*")]:
		if not path.is_symlink():
			path.chmod(path.stat().st_mode | 0o200)
	env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
	for argv in (
		["git", "init", "-q", "-b", "main"],
		["git", "remote", "add", "origin", origin],
	):
		subprocess.run(argv, cwd=dest, check=True, capture_output=True, env=env)


def dependencies(t: Target) -> list[dict]:
	"""The siblings the app declares in ``[tool.bench.frappe-dependencies]`` beyond frappe."""
	declared = t.app.pyproject.get("tool", {}).get("bench", {}).get("frappe-dependencies", {}) or {}
	by_name = {s.name: s for s in t.ctx.siblings}
	out = []
	for name in declared:
		if name == "frappe":
			continue
		if name not in by_name:
			raise EnvError(
				f"L9: {name} is a frappe-dependency but not a [tool.frappe-nix] sibling, so flake.lock pins no tree for it"
			)
		out.append(dict(by_name[name]))
	return out


def run(t: Target, report_dir: Path) -> list[Result]:
	"""L9's results; an environment that cannot build the bench raises ``EnvError``."""
	lock = t.root / "flake.lock"
	if not lock.is_file():
		raise EnvError("L9 needs flake.lock: the pilot, frappe and sibling revisions come from it")
	pilot = pins.pin_path("pilot", lock)
	frappe = pins.pin_path("frappe", lock)
	deps = [(d, pins.pin_path(d["input"], lock)) for d in dependencies(t)]
	if not shutil.which("uv"):
		raise EnvError("L9 needs uv on PATH (pilot builds the validation bench's venvs with it)")
	try:
		requires = tomllib.loads((frappe / "pyproject.toml").read_text())["project"]["requires-python"]
	except (OSError, KeyError, tomllib.TOMLDecodeError) as e:
		raise EnvError(f"L9: the pinned frappe declares no requires-python: {e}") from e
	branch = t.ctx.branches.release or t.ctx.branches.integration
	with tempfile.TemporaryDirectory(prefix="frappe-listing-getapp-") as tmp:
		bench = Path(tmp) / "bench"
		apps = bench / "apps"
		apps.mkdir(parents=True)
		_checkout(frappe, apps / "frappe", "https://github.com/frappe/frappe")
		for dep, tree in deps:
			_checkout(tree, apps / dep["name"], f"https://github.com/{dep['repo']}")
		app_dir = apps / t.name
		baseline.export(t.root, app_dir)
		subprocess.run(["git", "init", "-q", "-b", branch], cwd=app_dir, check=True, capture_output=True)
		out = Path(tmp) / "result.json"
		request = Path(tmp) / "request.json"
		request.write_text(
			json.dumps(
				{
					"bench": str(bench),
					"app": t.name,
					"repo": f"https://github.com/{t.ctx.repo}" if t.ctx.repo else str(app_dir),
					"branch": branch,
					"python": requires,
					"out": str(out),
				}
			)
		)
		env = dict(os.environ, PYTHONPATH=str(pilot))
		log = report_dir / "getapp.log"
		report_dir.mkdir(parents=True, exist_ok=True)
		with log.open("w") as handle:
			proc = subprocess.run(
				[sys.executable, str(RUNNER), str(request)],
				stdout=handle,
				stderr=subprocess.STDOUT,
				env=env,
				check=False,
			)
		if proc.returncode != 0 or not out.is_file():
			tail = log.read_text()[-2000:]
			raise EnvError(f"L9: the validation bench could not be built (log {log}):\n{tail}")
		results = json.loads(out.read_text())
	return [
		Result("L9", "error", t.name, f"pilot {r['check']} fails: {r['error']}")
		if not r["ok"]
		else Result("L9", "note", t.name, f"pilot {r['check']} passes")
		for r in results
	]
