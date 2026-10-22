"""The phases scaffolding and migration share.

Scaffolding is migration over an empty directory: once the apps are on disk,
both run the same reconcile → convert → register → stage sequence. Every step is
check-then-act, so an interrupted run is resumed by running it again.
"""

import os
import shutil
from pathlib import Path

from frappe_nix_tools.frappe_init import ui
from frappe_nix_tools.frappe_init.apps import (
	compute_membership,
	convert_apps,
	ensure_bench_repo,
	reconcile_workspace,
	relocate_legacy_env,
	resolve_project_name,
	shim_legacy_apps,
	write_backup_readme,
)
from frappe_nix_tools.frappe_init.proc import attempt, git, probe, run
from frappe_nix_tools.frappe_init.secret_files import (
	audit_tracked_site_configs,
	secrets_declared,
	verify_secrets_visible,
)
from frappe_nix_tools.frappe_init.state import Run
from frappe_nix_tools.frappe_init.template import (
	install_gitignore_block,
	install_template,
	reconcile_common_site_config,
	render_template,
	verify_not_ignored,
)

#: More files than this to stage usually means .gitignore is wrong.
STAGE_LIMIT = 20000

#: lib/build-deps.nix's `sync`, on PATH when lib/init.nix was given python-devops-nix.
BUILD_DEPS_SYNC = "frappe-nix-build-deps-sync"


def print_plan(st: Run) -> None:
	ui.step(f"Plan for {os.getcwd()}")
	ui.info(f"frappe version : {st.versions_line}")
	ui.info(f"bench name     : {st.name}")
	ui.info(f"default site   : {st.site}")
	ui.out()
	for name, app in st.apps.items():
		flags = app.flag_text
		if app.disp == "submodule":
			ui.out(f"  {name:<24} submodule  {app.branch} @ {app.sha[:10]}{f'  [{flags}]' if flags else ''}")
		elif app.disp == "vendor":
			ui.out(
				f"  {name:<24} vendor     (source committed to the bench){f'  [{flags}]' if flags else ''}"
			)
		elif app.disp == "skip":
			ui.out(f"  {name:<24} SKIP       {flags}")
	ui.out()
	if os.path.exists("env") and not os.path.islink("env"):
		ui.info("env/ will move to .frappe-nix-backup/env")
	for name, app in st.apps.items():
		if app.has("unpushed"):
			st.warn(
				f"apps/{name} is pinned at a commit that is on no remote branch — the bench will build here and nowhere else"
			)
		if app.has("dirty"):
			st.warn(
				f"apps/{name} has uncommitted changes; the submodule pin records HEAD, so they are not captured"
			)
		if app.has("local-remote"):
			st.warn(
				f"apps/{name}'s remote is a filesystem path; recording it as a submodule URL breaks other clones"
			)
		if app.has("branch-unverified"):
			st.warn(
				f"apps/{name}: '{app.branch}' is a guess — no such branch on remote '{app.remote}'. Check .gitmodules before running 'bench-update --pull'."
			)
	# Pure inspection, so it is part of the plan: --dry-run prints the warnings,
	# and a committed site_config is the one most worth seeing before migrating.
	audit_tracked_site_configs(st)

	if st.args.strict:
		for name, app in st.apps.items():
			if app.has("unpushed") or app.has("dirty"):
				ui.die(f"--strict: apps/{name} is dirty or unpushed")


def print_report(st: Run) -> None:
	ui.step(f"Done — {os.getcwd()}")
	ui.info(f"frappe version : {st.versions_line}")
	ui.info(f"apps           : {' '.join(st.apps)}")
	if st.vendored:
		ui.info(f"vendored       : {' '.join(st.vendored)}")
		ui.info(
			"                 history preserved in .frappe-nix-backup/<app>.git (gitignored — do not delete it before you have pushed the app somewhere)"
		)

	if st.warnings:
		ui.out(f"\n\033[33m{len(st.warnings)} warning(s):\033[0m")
		for w in st.warnings:
			ui.out(f"  ⚠  {w}")

	ui.out(
		"""
Next steps:
  git diff --cached --stat     # review; nothing has been committed
  direnv allow                 # or: nix develop --no-pure-eval
  devenv up                    # MariaDB, Redis, web, scheduler, worker, …"""
	)
	try:
		flake = Path("flake.nix").read_text(errors="replace")
	except OSError:
		flake = ""
	declared = secrets_declared(flake)
	if not os.path.exists(f"sites/{st.site}"):
		if declared:
			ui.out(
				f"  bench restore                # (in another shell) clone {st.site} from the latest production backup"
			)
		else:
			ui.out(f"  bench provision              # (in another shell) create {st.site} + install apps")
	if not declared:
		ui.out(
			'\nTo restore this bench from production backups, declare its secrets —\nsee "Secrets" in the frappe-nix README, or the commented block in flake.nix.'
		)
	for name, app in st.apps.items():
		if app.disp == "skip":
			ui.out(f"\nNote: apps/{name} was skipped and is NOT part of the bench.")


def finalize(st: Run) -> None:
	if st.args.skip_lock:
		ui.info("skipping uv lock (--skip-lock)")
	else:
		ui.step("Resolving the Python workspace (uv lock)")
		if attempt(["uv", "lock"]) != 0:
			# An existing bench pins whatever its apps needed, so conflicts are
			# common and the user's to resolve; uv's own diagnosis is just above.
			st.warn(
				"uv lock failed. Resolve it in pyproject.toml and re-run 'uv lock': for a conflict between two apps, add a pin to [tool.uv] override-dependencies; for one against the [dependency-groups] dev tools, relax the pin there. Everything else about the migration is already done."
			)
		elif shutil.which(BUILD_DEPS_SYNC) and attempt([BUILD_DEPS_SYNC]) != 0:
			# The managed [tool.uv.extra-build-dependencies] block, derived from
			# the lock just written (lib/build-deps.nix). The dev shell's relock
			# runs it again, so a failure here is a warning.
			st.warn(f"{BUILD_DEPS_SYNC} failed; run `nix run .#relock` to derive the build dependencies")

	ui.step("Staging")
	staged = len((probe(git("add", "-An")) or "").splitlines())
	if staged > STAGE_LIMIT:
		st.warn(f"{staged} files would be staged — that usually means .gitignore is wrong")
		if not st.args.assume_yes and not ui.confirm(f"Stage {staged} files anyway?"):
			ui.die("aborted; nothing was staged (the working tree changes above are already applied)")
	run(git("add", "-A"))
	if attempt(
		git(
			"ls-files",
			"--error-unmatch",
			"--",
			"flake.nix",
			"pyproject.toml",
			"sites/apps.txt",
			"sites/apps.json",
		),
		quiet_stdout=True,
	):
		ui.die("flake.nix / pyproject.toml / sites/apps.{txt,json} did not reach the git index")
	for app in st.vendored:
		if not probe(git("ls-files", "--", f"apps/{app}")):
			ui.die(f"vendored app {app} has no tracked files")

	if st.args.commit:
		run(git("commit", "-q", "-m", st.args.commit_msg))
		ui.info(f"committed: {st.args.commit_msg}")

	print_report(st)


def run_pipeline(st: Run) -> None:
	ui.step("Preparing the bench tree")
	for d in (".frappe-nix-backup", "logs", "config/pids"):
		Path(d).mkdir(parents=True, exist_ok=True)
	ensure_bench_repo()
	resolve_project_name(st)
	render_template(st)
	install_template(st, keep=True)
	install_gitignore_block(st)
	relocate_legacy_env()
	reconcile_common_site_config(st)
	envrc = Path(".envrc")
	if not (envrc.is_file() and "use flake" in envrc.read_text(errors="replace")):
		st.warn(".envrc does not 'use flake' — direnv will not load the frappe-nix dev shell")

	ui.step("Apps")
	write_backup_readme()
	convert_apps(st)
	shim_legacy_apps(st)
	compute_membership(st)
	verify_not_ignored(st)
	verify_secrets_visible(st)
	audit_tracked_site_configs(st)

	ui.step("Workspace")
	reconcile_workspace(st)

	finalize(st)
