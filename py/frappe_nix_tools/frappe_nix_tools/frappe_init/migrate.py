"""Migrate mode: convert an existing bench in place.

Runs inside the bench. Nothing here is destructive: superseded files move to
.frappe-nix-backup/ rather than being deleted, and existing files are merged
rather than overwritten.
"""

import json
import os
import shutil
from pathlib import Path

from frappe_nix_tools.frappe_init import ui
from frappe_nix_tools.frappe_init.apps import resolve_project_name
from frappe_nix_tools.frappe_init.detect import (
	bench_repo_state,
	classify_apps,
	detect_frappe_version,
	existing_bench_name,
	has_foreign_flake,
)
from frappe_nix_tools.frappe_init.pipeline import print_plan, run_pipeline
from frappe_nix_tools.frappe_init.proc import git, probe
from frappe_nix_tools.frappe_init.scaffold import choose_frappe_version
from frappe_nix_tools.frappe_init.state import Run, normalize_name
from frappe_nix_tools.frappe_init.template import render_template


def resolve_migration_preset(st: Run) -> None:
	detected = detect_frappe_version(st.presets)
	if st.version:
		if st.version not in st.presets:
			ui.die(f"unknown frappe version '{st.version}' (expected: {st.preset_keys})", 5)
		if detected and detected[0] != "UNSUPPORTED" and detected[0] != st.version:
			st.warn(
				f"--version {st.version}, but this bench looks like {detected[0]} — the python/node pins will not match the code"
			)
		st.resolve_preset()
		return

	if detected and detected[0] == "UNSUPPORTED":
		ui.die(
			f"apps/frappe is version {detected[1]}. frappe-nix has no preset below version-15: those apps ship a setup.py with no pyproject.toml and cannot be uv workspace members, and the python/node pins differ. Upgrade the bench first (bench switch-to-branch version-15), or re-run with --version version-15 to proceed anyway.",
			5,
		)
	if detected is None:
		if ui.has_tty():
			st.warn("could not detect the frappe version from apps/frappe")
			st.version = choose_frappe_version(st)
		else:
			ui.die(f"could not detect the frappe version; pass --version (one of: {st.preset_keys})", 5)
	else:
		st.version = detected[0]
		ui.info(f"frappe version : {st.version} (detected from {detected[1]})")
	st.resolve_preset()


def site_candidates() -> list[str]:
	"""The sites: directories under sites/ that hold a site_config.json."""
	if not os.path.isdir("sites"):
		return []
	return sorted(
		d for d in os.listdir("sites") if os.path.isfile(os.path.join("sites", d, "site_config.json"))
	)


def resolve_migration_site(st: Run) -> None:
	"""The bench's own default_site, else its only site, else frappe.localhost."""
	if st.args.site:
		st.site = st.args.site
		return
	try:
		config = json.loads(Path("sites/common_site_config.json").read_text())
		default = config.get("default_site") if isinstance(config, dict) else None
		if isinstance(default, str) and default:
			st.site = default
			return
	except (OSError, ValueError):
		pass
	candidates = site_candidates()
	if not candidates:
		st.site = "frappe.localhost"
	elif len(candidates) == 1:
		st.site = candidates[0]
	elif ui.has_tty():
		st.site = ui.choose("Default site:", [(c, c) for c in candidates])
	else:
		ui.die("this bench has several sites and no default_site; pass --default-site", 5)


def cmd_migrate(st: Run) -> None:
	a = st.args
	if bench_repo_state() == "nested":
		top = probe(git("rev-parse", "--show-toplevel")) or ""
		st.warn(
			f"this bench is inside another git repository ({top}); frappe-nix needs the bench root to be its own repo"
		)
		if not a.force:
			ui.die("re-run with --force to create a nested repository anyway", 8)

	resolve_migration_preset(st)
	resolve_migration_site(st)
	st.name = normalize_name(a.name or existing_bench_name() or os.path.basename(os.getcwd()))

	classify_apps(st)
	print_plan(st)

	if a.dry_run:
		ui.out("\n--dry-run: nothing was changed.")
		return
	if not a.assume_yes and not ui.confirm("Migrate this bench to frappe-nix?"):
		ui.die("aborted. Re-run with --yes (or --dry-run to inspect the plan).", 6)

	if has_foreign_flake():
		if not a.force:
			ui.die(
				"flake.nix exists and is not a frappe-nix wrapper. Re-run with --force: the frappe-nix wrapper will be written to flake.nix.frappe-nix for you to merge.",
				4,
			)
		# The template's tokens include the workspace root's name.
		resolve_project_name(st)
		staging = render_template(st)
		shutil.copy(staging / "flake.nix", "flake.nix.frappe-nix")
		st.warn(
			"flake.nix is not a frappe-nix wrapper; the wrapper was written to flake.nix.frappe-nix — merge it by hand"
		)

	run_pipeline(st)
