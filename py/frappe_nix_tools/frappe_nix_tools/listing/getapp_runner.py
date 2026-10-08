"""L9's bench half: pilot's get-app validator, run with pilot on ``sys.path`` (spec §5.2).

Run by ``frappe_nix_tools.listing.getapp`` as ``python getapp_runner.py <request.json>`` with
``PYTHONPATH`` naming the pinned ``frappe/pilot`` tree; never imported by frappe-nix-tools
itself, which does not depend on pilot. The request names the validation bench (whose
``apps/`` already holds frappe, the dependency apps and the app, each a git checkout), the
app, its repository and branch, and the Python frappe asks for.

Every check of ``pilot.core.app.validator.validator._all_checks()`` runs on its own, as the
registry dry run did, so one failure does not hide the next. The result is JSON on the
request's ``out`` path: ``[{"check", "ok", "error"}]``.
"""

import json
import sys
import traceback
from pathlib import Path

from pilot.config import AppConfig, BenchConfig  # ty: ignore[unresolved-import]
from pilot.core.app import App  # ty: ignore[unresolved-import]
from pilot.core.app.validator.validator import _all_checks  # ty: ignore[unresolved-import]
from pilot.core.bench import Bench  # ty: ignore[unresolved-import]
from pilot.managers.environment import PythonEnvManager  # ty: ignore[unresolved-import]


def main(request_path: str) -> int:
	request = json.loads(Path(request_path).read_text())
	bench = Bench(BenchConfig.default(name="validation"), Path(request["bench"]))
	# The validator builds its throwaway venvs on the bench's interpreter (frappe's Python).
	bench.config.python_version = request["python"]
	PythonEnvManager(bench).create_venv()
	app = App(AppConfig(name=request["app"], repo=request["repo"], branch=request["branch"]), bench)
	results = []
	for check in _all_checks():
		name = type(check).__name__
		try:
			check.run(app)
		except Exception as e:  # every failure is a finding, never a crash of the run
			detail = str(e) or type(e).__name__
			if not isinstance(e, ValueError | RuntimeError) and "pilot" not in type(e).__module__:
				detail += "\n" + traceback.format_exc(limit=3)
			results.append({"check": name, "ok": False, "error": detail})
			print(f"FAIL {name}: {detail}", flush=True)
		else:
			results.append({"check": name, "ok": True, "error": ""})
			print(f"ok   {name}", flush=True)
	Path(request["out"]).write_text(json.dumps(results, indent=2) + "\n")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1]))
