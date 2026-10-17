"""App mode: a frappe-nix dev environment in one Frappe app's own repository;
and ``--sync``/``--check``, its managed files (docs/app-standards/spec.md §3.3).

The bench modes reconcile a directory that is a bench. App mode writes a flake
that assembles a bench around the app, and touches nothing else: in particular
not the app's pyproject.toml, which is its packaging metadata.

That is all it does for an app that has not opted in to the app standards
(S35). With --standards <profile>, or on an app whose pyproject.toml has a
[tool.frappe-nix] table, it writes the same template and then hands over to
`frappe-nix sync --write`. `frappe-nix sync` owns the templates, the profiles,
the phases and the exit codes (0 clean, 1 drift, 2 invalid configuration or
not opted in, 3 environment); this module turns frappe-init's flags into its
arguments.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import NoReturn

from frappe_nix_tools.frappe_init import ui
from frappe_nix_tools.frappe_init.args import USAGE
from frappe_nix_tools.frappe_init.detect import app_opted_in, dist_name
from frappe_nix_tools.frappe_init.proc import attempt, git, probe, probe_ok, run
from frappe_nix_tools.frappe_init.scaffold import require_version
from frappe_nix_tools.frappe_init.state import Run, normalize_dist
from frappe_nix_tools.frappe_init.template import (
	GITIGNORE_BEGIN,
	install_gitignore_block,
	install_template,
	render_template,
)


def sync_write_args(st: Run) -> list[str]:
	"""What every `frappe-nix sync --write` from frappe-init gets."""
	a = st.args
	words = ["--write"]
	if a.standards:
		words += ["--standards", a.standards]
	if st.version:
		words += ["--frappe-version", st.version]
	# Only a site the user gave: sync otherwise keeps the defaultSite an existing
	# flake names, and a defaulted one would orphan every developer's dev site.
	if a.site:
		words += ["--site", a.site]
	if a.force:
		words.append("--force")
	if a.dry_run:
		words.append("--dry-run")
	if a.skip_lock:
		words.append("--skip-lock")
	return words + a.sync_args


def cmd_app_sync(st: Run) -> NoReturn:
	a = st.args
	if a.app_action == "check":
		if a.standards:
			ui.die(
				f"--standards opts an app in, which --check never does: run frappe-init --sync --standards {a.standards}",
				2,
			)
		words = ["--check", *a.sync_args]
	else:
		words = sync_write_args(st)
	sys.stdout.flush()
	sys.stderr.flush()
	try:
		os.execvp("frappe-nix", ["frappe-nix", "sync", *words])
	except OSError as e:
		print(f"frappe-init: frappe-nix: {e.strerror}", file=sys.stderr)
		raise ui.Exit(127) from None


def require_git_repo() -> None:
	if not (os.path.isdir(".git") or probe_ok(git("rev-parse", "--git-dir"))):
		ui.die(
			f"'{os.getcwd()}' is not a git repository. A flake's source tree is exactly its tracked files, so frappe-nix cannot see an app that git cannot.",
			6,
		)


def cmd_app_init(st: Run) -> None:
	a = st.args
	opted_in = app_opted_in()
	if not st.version and opted_in:
		# An app that opted in names its Frappe major in [tool.frappe-nix]
		# frappe-major, which is what sync renders: nothing to ask.
		major = probe(["frappe-nix", "config", "frappe-major"])
		if major:
			st.version = f"version-{major}"
	require_version(st)

	st.app_name = dist_name(".")
	if not st.app_name:
		ui.die("cannot read [project].name from pyproject.toml", 5)
	if not os.path.isfile(f"{st.app_name}/hooks.py"):
		ui.die(
			f"'{os.getcwd()}' does not look like a Frappe app: [project].name is '{st.app_name}' but there is no {st.app_name}/hooks.py",
			6,
		)

	# The bench's name fixes its ports and its image prefix. Derived, not asked:
	# an app repository has exactly one bench.
	st.name = normalize_dist(st.app_name)
	st.site = a.site or f"{st.name}.localhost"

	ui.step(f"Plan for {os.getcwd()}")
	ui.info(f"app            : {st.app_name}")
	ui.info(f"frappe version : {st.versions_line}")
	ui.info(f"bench name     : {st.name}")
	ui.info(f"default site   : {st.site}")
	ui.out()
	ui.info("flake.nix and .envrc are written here; the bench itself is generated")
	ui.info("into .frappe-nix/ on shell entry and is gitignored.")
	ui.out()

	standards = bool(a.standards) or opted_in
	if standards:
		ui.info(
			f"app standards  : {a.standards or 'the [tool.frappe-nix] table'} (frappe-nix sync --write follows)"
		)
		ui.out()
	elif a.app_flag:
		# Nothing would read it: this app has not opted in (S35).
		print(USAGE, file=sys.stderr, end="")
		ui.die(
			f"unknown flag: {a.app_flag} (it applies to an app that opted in to the app standards: add --standards <profile>)"
		)

	if standards:
		require_git_repo()
		# Before anything is written, --dry-run included: what sync will render must
		# resolve (the profile, a --standards or --version the table contradicts, the
		# table itself), and the release branch its flake follows must exist (S32).
		rc = attempt(["frappe-nix", "sync", *sync_write_args(st), "--phase", "preflight"])
		if rc != 0:
			ui.die("nothing was changed: fix what frappe-nix sync reported above", rc)

	if a.dry_run:
		ui.out("--dry-run: nothing was changed.")
		return

	require_git_repo()

	ui.step("Writing the flake")
	st.template = st.config.app_template
	render_template(st)
	install_template(st, keep=True)
	gitignore = Path(".gitignore")
	if standards and gitignore.is_file() and GITIGNORE_BEGIN in gitignore.read_text().split("\n"):
		# frappe-nix sync owns the block from here (§2.6): templates/app's body would
		# put main's back, and leave it staged when sync fails or --only skips it.
		ui.info(". .gitignore's frappe-nix block is left to frappe-nix sync")
	else:
		install_gitignore_block(st)

	# Staged, not only written: an untracked flake.nix is one `nix run` away from
	# "does not provide attribute".
	Path("nix").mkdir(exist_ok=True)
	run(git("add", "--", "flake.nix", ".envrc", ".gitignore"))
	for p in ("flake.nix", ".envrc"):
		if probe_ok(git("check-ignore", "-q", "--", p)):
			ui.die(f"{p} is excluded by .gitignore — the Nix build cannot see it")

	if standards:
		ui.step("Writing the managed files (frappe-nix sync --write)")
		rc = attempt(["frappe-nix", "sync", *sync_write_args(st)])
		if rc != 0:
			ui.die(
				f"'frappe-nix sync --write' exited {rc} — fix what it reported above and run 'frappe-init --sync'",
				rc,
			)
	else:
		ui.step("Resolving the Python workspace")
		if a.skip_lock:
			ui.info("skipping (--skip-lock)")
		elif shutil.which("nix") is None:
			st.warn("nix is not on PATH — run 'nix run .#relock' yourself before entering the shell")
		elif attempt(["nix", "run", "--impure", ".#relock"]) != 0:
			# The first lock comes from the flake just written; it cannot come from the
			# dev shell, which is exactly what refuses to evaluate without nix/uv.lock.
			st.warn(
				"'nix run .#relock' failed — fix the error above and re-run it; everything else is already written"
			)

	ui.step(f"Done — {os.getcwd()}")
	ui.out(
		f"""
Next steps:
  git diff --cached --stat     # review; nothing has been committed
  direnv allow                 # or: nix develop --no-pure-eval
  devenv up                    # MariaDB, Redis, web, scheduler, worker, …
  provision-site               # (in another shell) create {st.site} + install {st.app_name}
"""
	)
	if standards:
		ui.out(
			"""To add a sibling app (erpnext, hrms, …), list it in [tool.frappe-nix] siblings and in
hooks.py's required_apps, then run 'frappe-init --sync'.
Commit nix/ — a flake's source tree is only its tracked files."""
		)
	else:
		ui.out(
			"""To add a sibling app (erpnext, hrms, …), declare it as a flake input and list it
under frappe-nix.app.siblings in flake.nix, then re-run 'nix run .#relock'.
Commit nix/ — a flake's source tree is only its tracked files."""
		)
