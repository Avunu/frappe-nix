"""Scaffold mode: lay a bench down from scratch.

Once the apps are cloned this hands over to the pipeline migration uses, so the
two modes cannot drift apart.
"""

import os
from pathlib import Path

from frappe_nix_tools.frappe_init import ui
from frappe_nix_tools.frappe_init.apps import ensure_bench_repo
from frappe_nix_tools.frappe_init.detect import classify_apps, dir_is_empty
from frappe_nix_tools.frappe_init.pipeline import run_pipeline
from frappe_nix_tools.frappe_init.proc import git, probe, run
from frappe_nix_tools.frappe_init.secret_files import scaffold_secrets_dir
from frappe_nix_tools.frappe_init.state import Run, normalize_name, resolve_app_url, url_basename

#: The interactive picker's catalogue. Anything else (owner/repo, a git URL)
#: goes through --apps.
APP_CATALOG = (
	"erpnext",
	"hrms",
	"payments",
	"helpdesk",
	"crm",
	"lms",
	"builder",
	"insights",
	"wiki",
	"print_designer",
	"webshop",
	"drive",
)


def choose_frappe_version(st: Run) -> str:
	return ui.choose("Frappe version:", [(key, p.label) for key, p in st.presets.items()])


def require_version(st: Run) -> None:
	"""st.version, asked for when there is a terminal; then its preset."""
	if not st.version:
		if ui.has_tty():
			st.version = choose_frappe_version(st)
		else:
			ui.die(f"--version is required (one of: {st.preset_keys})", 5)
	if st.version not in st.presets:
		ui.die(f"unknown frappe version '{st.version}' (expected: {st.preset_keys})", 5)
	st.resolve_preset()


def clone_app(st: Run, url: str, name: str) -> None:
	"""A partial clone of the preset's branch, as shell entry's first checkout
	makes one (lib/apps-report.nix): every commit and folder, only the files the
	checkout needs. `git clone -b` rather than `submodule add -b`, which honours
	only the remote's default branch; registration comes later, in the pipeline.
	"""
	path = f"apps/{name}"
	branch = st.p.branch
	has_branch = bool(probe(git("ls-remote", "--heads", url, branch)))
	ui.info(f"+ {path} ({branch if has_branch else 'default branch'}) — {url}")
	run(
		git(
			"clone",
			"-q",
			"--filter=blob:none",
			"--single-branch",
			*(["--branch", branch] if has_branch else []),
			"--",
			url,
			path,
		)
	)


def cmd_init(st: Run) -> None:
	a = st.args
	require_version(st)

	if a.apps_csv:
		selected = a.apps_csv.split(",")
	elif ui.has_tty():
		selected = ui.choose_many(
			"Apps (none = frappe only):",
			[(app, app) for app in APP_CATALOG],
		)
	else:
		selected = []
	apps = [(url_basename(url), url) for url in (resolve_app_url(s) for s in selected if s)]

	if not st.target:
		if ui.has_tty():
			st.target = ui.ask("Bench directory", "frappe-bench")
		st.target = st.target or "frappe-bench"
	st.name = normalize_name(a.name or os.path.basename(os.path.abspath(st.target)))
	st.site = a.site or "frappe.localhost"

	if not dir_is_empty(st.target) and not a.force:
		ui.die(
			f"target '{st.target}' already exists and is not empty. Use --migrate to convert an existing bench, or --init --force to scaffold into it anyway."
		)

	what = f"bench '{st.name}' ({st.version} → python {st.p.pyver} / node {st.p.node_version}) in {st.target}"
	if a.dry_run:
		ui.step("Plan")
		ui.info(f"scaffold {what}")
		ui.info(f"apps           : {' '.join(['frappe', *(n for n, _ in apps)])}")
		ui.info(f"default site   : {st.site}")
		ui.out("\n--dry-run: nothing was changed.")
		return

	ui.step(f"Creating {what}")
	Path(st.target).mkdir(parents=True, exist_ok=True)
	try:
		os.chdir(st.target)
	except OSError:
		ui.die(f"cannot enter {st.target}")
	ui.info(os.getcwd())

	ensure_bench_repo()
	Path("apps").mkdir(exist_ok=True)
	ui.step("Cloning apps")
	if not os.path.isdir("apps/frappe"):
		clone_app(st, "https://github.com/frappe/frappe.git", "frappe")
	for name, url in apps:
		if not os.path.isdir(f"apps/{name}"):
			clone_app(st, url, name)

	classify_apps(st)
	# Only on a fresh scaffold: an existing bench that keeps its secrets
	# elsewhere should not grow a directory it never asked for.
	scaffold_secrets_dir()
	run_pipeline(st)
