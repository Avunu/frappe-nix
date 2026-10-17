"""Turning apps/ into something the flake can see.

A flake's source tree is exactly its git-tracked files (plus submodule
contents, via `self.submodules = true`), and lib/bench.nix discovers apps by
reading apps/. So every app ends up a registered submodule or committed
source; an untracked nested repository is invisible to the build.
"""

import json
import os
import shutil
from pathlib import Path

from frappe_nix_tools.frappe_init import ui
from frappe_nix_tools.frappe_init.detect import bench_repo_state, dist_name
from frappe_nix_tools.frappe_init.proc import attempt, git, output, probe, probe_ok, run
from frappe_nix_tools.frappe_init.state import Run, normalize_dist

BACKUP_README = """\
# frappe-nix migration backups

Written by `frappe-init --migrate`. Nothing here is used at runtime, and the
directory is gitignored — but do not delete it until you are sure you no longer
need what is in it.

| Entry | What it is | How to restore |
| --- | --- | --- |
| `env/` | The bench's original virtualenv. frappe-nix symlinks `env/` to a Nix-built one, and `ln -s` cannot replace a real directory, so it had to move. | Not restorable in place — the Nix venv owns `env/` now. |
| `<app>.git/` | The full git history of an app that had no usable remote and was **vendored** (its source is now committed directly into the bench). | `mv .frappe-nix-backup/<app>.git apps/<app>/.git` — but first `git rm -r --cached apps/<app>` in the bench, or the two will fight. |
| `<app>.json` | That app's original remote / branch / commit, recorded before its `.git` moved. | Reference only. |
| `nested/<app>/*.git` | A nested repository found inside a vendored app. | `mv` it back to the path encoded in the filename (`_` separates path segments). |
| `common_site_config.json.orig` | `sites/common_site_config.json` as it was before reconciliation. | Diff it to see which keys were forced or dropped. |

The right long-term fix for a vendored app is to push it to a real remote and
re-add it as a submodule:

    git rm -r --cached apps/<app>
    rm -rf apps/<app>            # after pushing it somewhere!
    bench-get-app <owner>/<app>
"""

WORKSPACE = "frappe-nix-workspace"


def write_backup_readme() -> None:
	"""Whoever finds .frappe-nix-backup/ months from now needs to know it is not scratch."""
	Path(".frappe-nix-backup").mkdir(exist_ok=True)
	readme = Path(".frappe-nix-backup/README.md")
	if readme.is_file():
		return
	readme.write_text(BACKUP_README)
	ui.info("+ .frappe-nix-backup/README.md")


def ensure_bench_repo() -> None:
	if bench_repo_state() != "own":
		run(git("init", "-q"))
		ui.info("+ git repository")


def _head(path: str, *extra: str) -> str:
	return output(git("rev-parse", *extra, "HEAD", cwd=path)).strip()


def register_submodule(name: str, url: str, branch: str) -> None:
	path = f"apps/{name}"
	# Repair in place when already registered. `submodule add --force` on a known
	# name does not fail: it invents apps/frappe1, and `bench-update`'s `submodule
	# foreach` then looks the branch up under the wrong name.
	if probe_ok(git("config", "-f", ".gitmodules", "--get", f"submodule.{path}.url")):
		for key, value in (("path", path), ("url", url), ("branch", branch), ("shallow", "true")):
			run(git("config", "-f", ".gitmodules", f"submodule.{path}.{key}", value))
		run(git("config", "--local", f"submodule.{path}.url", url))
		run(git("update-index", "--add", "--cacheinfo", f"160000,{_head(path)},{path}"))
		ui.info(f"· apps/{name} → submodule (repaired) @ {branch}")
		return

	# An app the bench already tracked as ordinary files leaves the index before
	# it can become a gitlink.
	if probe_ok(git("ls-files", "--error-unmatch", "--", path)):
		mode = (probe(git("ls-files", "-s", "--", path)) or "").split(" ", 1)[0]
		if mode != "160000":
			run(git("rm", "-r", "-q", "--cached", "--", path))

	# <path> already holds a repository, so git stages it without cloning, and the
	# gitlink lands on its current HEAD. -b writes submodule.<name>.branch, which
	# lib/scripts.nix requires; --force only bypasses .gitignore, which real
	# benches routinely apply to apps/.
	run(git("submodule", "add", "-q", "--force", "-b", branch, "--", url, path))
	run(git("config", "-f", ".gitmodules", f"submodule.{path}.shallow", "true"))
	ui.info(f"+ apps/{name} → submodule @ {branch} ({_head(path, '--short')})")


def _tracked_count(path: str) -> int:
	return len((probe(git("ls-files", "--", path)) or "").splitlines())


def vendor_app(st: Run, name: str) -> None:
	path = f"apps/{name}"
	backup = Path(".frappe-nix-backup")
	backup.mkdir(exist_ok=True)
	fresh = False
	if os.path.lexists(f"{path}/.git"):
		fresh = True
		if os.path.lexists(backup / f"{name}.git"):
			ui.die(
				f"apps/{name} has a .git but .frappe-nix-backup/{name}.git already exists — resolve by hand"
			)
		# Provenance first: once the gitdir moves, it is only recoverable from there.
		provenance = {
			"url": probe(git("remote", "get-url", "origin", cwd=path)) or "",
			"branch": probe(git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=path)) or "",
			"commit": probe(git("rev-parse", "HEAD", cwd=path)) or "",
		}
		(backup / f"{name}.json").write_text(json.dumps(provenance, indent=2) + "\n")
		shutil.move(f"{path}/.git", backup / f"{name}.git")
	# Nested repositories would be staged as stray gitlinks with no .gitmodules
	# entry: empty directories in the flake source.
	nested: list[str] = []
	for root, dirs, files in os.walk(path):
		if os.path.relpath(root, path) == ".":
			continue
		if ".git" in dirs or ".git" in files:
			nested.append(os.path.join(root, ".git"))
		dirs[:] = [d for d in dirs if d != ".git"]
	for found in sorted(nested):
		rel = os.path.dirname(os.path.relpath(found, path))
		dest = backup / "nested" / name
		dest.mkdir(parents=True, exist_ok=True)
		shutil.move(found, dest / f"{rel.replace('/', '_')}.git")
		st.warn(f"apps/{name}: moved nested repo {rel} aside")

	# A nested repository that was `git add`ed as-is is a gitlink (mode 160000)
	# with no .gitmodules entry. `git add` will not turn a gitlink into a tree
	# even once the .git is gone, so the entry is dropped first. update-index,
	# not `git rm --cached`, which insists on a clean .gitmodules.
	staged = probe(git("ls-files", "-s", "--", path)) or ""
	if any(line.startswith("160000 ") for line in staged.splitlines()):
		run(git("update-index", "--force-remove", "--", path))
		ui.info(f"- apps/{name}: dropped the stray gitlink (it had no .gitmodules entry)")
	# Not `git add -f`: forcing past .gitignore would pull in node_modules and
	# public/dist. Correctness rests on the ignore rules, which
	# template.verify_not_ignored checks.
	run(git("add", "--", path))
	count = _tracked_count(path)
	if count == 0:
		ui.die(f"vendored apps/{name} produced 0 tracked files — check .gitignore")
	st.vendored.append(name)
	if fresh or not os.path.lexists(backup / f"{name}.git"):
		ui.info(f"+ apps/{name} → vendored ({count} files)")
	else:
		ui.info(f"· apps/{name} already vendored ({count} files)")


def convert_apps(st: Run) -> None:
	for name, app in st.apps.items():
		if app.disp == "submodule":
			register_submodule(name, app.url, app.branch)
			# bench-update fetches from origin; benches made by `bench get-app` often
			# only have upstream.
			if app.remote != "origin" and not probe_ok(
				git("remote", "get-url", "origin", cwd=f"apps/{name}")
			):
				run(git("remote", "add", "origin", app.url, cwd=f"apps/{name}"))
				ui.info(f"  added an 'origin' alias in apps/{name} (remote was '{app.remote}')")
		elif app.disp == "vendor":
			if st.args.no_vendor:
				ui.die(
					f"apps/{name} has no usable git remote ({app.flag_text}) and --no-vendor was given. Push it to a remote and re-run, or drop --no-vendor to commit its source into the bench."
				)
			vendor_app(st, name)
		elif app.disp == "skip":
			st.warn(
				f"apps/{name} skipped ({app.flag_text}) — it will not be part of the bench: not a workspace member, not in sites/apps.txt"
			)
	if st.args.absorb_gitdirs and st.apps and attempt(git("submodule", "absorbgitdirs")) != 0:
		st.warn("git submodule absorbgitdirs failed")


def compute_membership(st: Run) -> None:
	"""The workspace members, after vendoring and shimming (a vendored setup.py
	app may just have been given a pyproject.toml). Membership is registration:
	sites/apps.txt is generated from the members."""
	st.members = []
	for name, app in st.apps.items():
		if app.disp == "skip" or app.has("not-a-frappe-app"):
			continue
		if os.path.isfile(f"apps/{name}/pyproject.toml"):
			st.members.append(name)
		else:
			st.warn(
				f"apps/{name} has no pyproject.toml — kept in apps/ and on PYTHONPATH, but not a uv workspace member: its dependencies are not resolved and it is NOT in sites/apps.txt, so frappe will not see it. Vendor it (--vendor {name}) to get a pyproject.toml shim, or add one upstream."
			)


def shim_legacy_apps(st: Run) -> None:
	"""A setup.py-only app cannot be a uv workspace member. A vendored one is
	ours, so it gets a committed shim; for a submodule the shim would sit outside
	the pinned commit, so only vendoring or an upstream fix will do."""
	for name, app in st.apps.items():
		if not (app.has("no-pyproject") and app.has("setup-py")):
			continue
		policy = st.args.legacy_apps or ("shim" if app.disp == "vendor" else "skip")
		if policy == "abort":
			ui.die(f"apps/{name} has only a setup.py and --legacy-apps=abort was given")
		elif policy == "shim":
			if app.disp != "vendor":
				ui.die(
					f"apps/{name} is a submodule; a generated pyproject.toml would not be part of its pinned commit. Use --vendor {name}, or add the file upstream."
				)
			run(
				[
					WORKSPACE,
					"shim-app",
					"--app-dir",
					f"apps/{name}",
					"--name",
					name,
					"--requires-python",
					st.p.requires_python,
				]
			)
		elif policy != "skip":
			ui.die(f"unknown --legacy-apps policy '{policy}' (shim|skip|abort)")


def relocate_legacy_env() -> None:
	"""A classic bench's env/ is a real virtualenv, and the dev shell's `ln -sfn`
	cannot replace a directory: it would make env/<store-path> inside it."""
	if not os.path.exists("env") or os.path.islink("env"):
		return
	backup = Path(".frappe-nix-backup")
	backup.mkdir(exist_ok=True)
	if os.path.lexists(backup / "env"):
		shutil.rmtree("env")
		ui.info("- env/ (classic virtualenv; .frappe-nix-backup/env already present)")
	else:
		shutil.move("env", backup / "env")
		ui.info(
			"- env/ → .frappe-nix-backup/env (classic virtualenv; frappe-nix symlinks env/ to the Nix venv)"
		)


def resolve_project_name(st: Run) -> None:
	"""The workspace root's [project].name: the bench's, unless an app shares it
	(bench_acme / apps/acme is common), which uv refuses. Once per run."""
	if st.project_name:
		return
	st.project_name = st.name
	ours = normalize_dist(st.name)
	for name in st.apps:
		if ours in (normalize_dist(dist_name(f"apps/{name}")), normalize_dist(name)):
			st.project_name = f"{st.name}-bench"
			st.warn(
				f"the bench and the app '{name}' are both named '{st.name}'; the workspace root is [project].name = '{st.project_name}' so uv can tell them apart"
			)
			return


def reconcile_workspace(st: Run) -> None:
	staging = st.staging
	assert staging is not None
	if not os.path.isfile("pyproject.toml"):
		shutil.copy(staging / "pyproject.toml", "pyproject.toml")
		ui.info("+ pyproject.toml")
	p = st.p
	run(
		[
			WORKSPACE,
			"ensure-root",
			"--pyproject",
			"pyproject.toml",
			"--name",
			st.project_name,
			"--requires-python",
			p.requires_python,
			"--overrides",
			p.overrides_json,
			"--preset",
			st.version,
			"--template",
			str(staging / "pyproject.toml"),
		]
	)
	run([WORKSPACE, "sync-apps", "--pyproject", "pyproject.toml", *st.members])
	# sites/apps.txt and sites/apps.json from the members just registered: git is
	# on PATH here, so each submodule's commit is recorded, and .gitmodules
	# (which register_submodule has just corrected) supplies the branch.
	run(
		[
			WORKSPACE,
			"sync-registry",
			"--pyproject",
			"pyproject.toml",
			"--apps-dir",
			"apps",
			"--sites-dir",
			"sites",
		]
	)
