"""``bench new-app``: scaffold a brand-new app with upstream's `bench new-app`
and register it in the uv workspace. Was bash in lib/scripts.nix (PLAN.md,
Track D, D3).

Upstream's trailing pip-install step fails in the read-only Nix environment;
that is expected and ignored, and whether the scaffold exists is what counts.
"""

import os
from collections.abc import Sequence
from pathlib import Path

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.config import AppsConfig
from frappe_nix_tools.apps.workspace import register, sync_registry, uv_then_build_deps

USAGE = """\
Usage: bench-new-app <app-name>

Creates a new Frappe app and integrates it into the uv workspace."""


def new_app(argv: Sequence[str], config: AppsConfig | None = None) -> int:
	"""The command; CONFIG is read from FRAPPE_NIX_CLI_CONFIG once the arguments
	are known to need it (so --help and usage errors need none)."""
	if not argv or not argv[0]:
		proc.say(USAGE)
		return 1
	name = argv[0]
	app_dir = f"apps/{name}"
	config = config or proc.apps_config()
	os.chdir(proc.bench_root())

	if Path(app_dir).is_dir() and Path(app_dir, "pyproject.toml").is_file():
		proc.say(f"Error: App '{name}' already exists in {app_dir}")
		return 1

	proc.say(
		f"Creating app scaffold with 'bench new-app --no-git {name}'...",
		"⚠  The pip-install step will fail (read-only Nix env) — this is expected.",
	)
	# The dev shell's `bench`, which hands a raw call straight to upstream.
	proc.attempt(["bench", "new-app", "--no-git", name], env={**os.environ, "_FRAPPE_BENCH_RAW": "1"})

	if not Path(app_dir, "pyproject.toml").is_file():
		proc.say(f"Error: App scaffold was not created at {app_dir}")
		return 1

	register(config, name)
	sync_registry(config)

	proc.say("Syncing Python dependencies...")
	uv_then_build_deps(config, "sync")

	if proc.which("direnv"):
		proc.attempt(["direnv", "reload"])

	site = os.environ.get("FRAPPE_SITE") or "<site>"
	proc.say(
		"",
		f"✅ App '{name}' created and integrated!",
		f"   Install it with: bench --site {site} install-app {name}",
	)
	return 0


def main(argv: Sequence[str]) -> int:
	return proc.main_guard(lambda: new_app(argv))
