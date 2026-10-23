"""Read-only probes: what is this directory, what Frappe is it, and what is each
app under apps/.

Nothing here writes, so ``--dry-run`` is exact rather than a best guess. Paths
are relative to the working directory, which is the target by the time these
run (command.main changes into it).
"""

import glob
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from frappe_nix_tools.frappe_init.presets import Preset
from frappe_nix_tools.frappe_init.proc import git, probe, probe_ok
from frappe_nix_tools.frappe_init.state import App, Run

# ── directory shape ───────────────────────────────────────────────────────


def dir_is_empty(path: str) -> bool:
	"""Missing, or a directory holding nothing but a .git: somewhere to scaffold."""
	if not os.path.lexists(path):
		return True
	if not os.path.isdir(path):
		return False
	return all(entry == ".git" for entry in os.listdir(path))


def has_any_frappe_app() -> bool:
	return bool(glob.glob("apps/*/*/hooks.py"))


def looks_like_bench() -> bool:
	return (
		os.path.isdir("apps")
		and os.path.isdir("sites")
		and (os.path.isfile("apps/frappe/frappe/hooks.py") or has_any_frappe_app())
	)


def looks_like_empty_bench() -> bool:
	"""apps/ and sites/ with no app in them: migrating it would make an empty bench."""
	return os.path.isdir("apps") and os.path.isdir("sites") and not has_any_frappe_app()


def _read(path: str) -> str | None:
	try:
		return Path(path).read_text(errors="replace")
	except OSError:
		return None


def is_frappe_nix_bench() -> bool:
	flake = _read("flake.nix") if os.path.isfile("flake.nix") else None
	pyproject = _read("pyproject.toml") if os.path.isfile("pyproject.toml") else None
	return (
		flake is not None
		and "frappe-nix" in flake
		and pyproject is not None
		and any(line.startswith("[tool.uv.workspace]") for line in pyproject.splitlines())
	)


def has_foreign_flake() -> bool:
	return os.path.isfile("flake.nix") and not is_frappe_nix_bench()


#: ``frappe-nix = {`` in a flake.nix, where the block's own keys start.
_FRAPPE_NIX_BLOCK = re.compile(r"\bfrappe-nix\s*=\s*\{")


def _top_level_string(text: str, key: str) -> str | None:
	"""``key = "<v>";`` at the top level of the attribute set ``text`` opens, or None.

	Nested sets (``app = { name = …; }``) and lists (``siblings``) are skipped by
	depth, so only the block's own ``key`` is read."""
	depth = 0
	pat = re.compile(rf'{re.escape(key)}\s*=\s*"([^"$\\]*)"\s*;')
	i = 0
	while i < len(text):
		c = text[i]
		if c == '"':
			end = text.find('"', i + 1)
			i = len(text) if end < 0 else end + 1
			continue
		if c == "#":
			end = text.find("\n", i)
			i = len(text) if end < 0 else end
			continue
		if c in "{[":
			depth += 1
		elif c in "}]":
			if depth == 0:
				return None
			depth -= 1
		elif depth == 0 and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "-_.")):
			m = pat.match(text, i)
			if m:
				return m.group(1)
		i += 1
	return None


def existing_bench_name() -> str:
	"""The project name a frappe-nix bench's flake.nix already sets, or "".

	``frappe-nix.name`` (python-devops-nix docs/conventions.md, Identity options), or ``benchName``, its
	name before, which a bench scaffolded earlier still has."""
	if not is_frappe_nix_bench():
		return ""
	text = _read("flake.nix") or ""
	m = re.search(r'^\s*benchName\s*=\s*"([^"]*)"\s*;', text, re.M)
	if m:
		return m.group(1)
	for block in _FRAPPE_NIX_BLOCK.finditer(text):
		name = _top_level_string(text[block.end() :], "name")
		if name is not None:
			return name
	return ""


def bench_repo_state() -> str:
	"""own, nested (inside another repository's worktree) or none.

	``--show-toplevel``, not ``--git-dir``: a bench may sit inside another repo's
	worktree (a monorepo), and that is not a bench repo of its own.
	"""
	top = probe(git("rev-parse", "--show-toplevel"))
	if not top:
		return "none"
	return "own" if top == os.getcwd() else "nested"


def dist_name(app_dir: str) -> str:
	"""An app's [project].name, which is what uv resolves it to; else the directory's name."""
	try:
		data = tomllib.loads(Path(app_dir, "pyproject.toml").read_text())
		name = data["project"]["name"]
		if name:
			return str(name)
	except (OSError, ValueError, KeyError, TypeError):
		pass
	return Path(app_dir).name


def looks_like_frappe_app() -> bool:
	"""A pyproject.toml whose [project].name names a sibling package holding hooks.py.

	That is what `bench get-app` looks for, and it is enough to tell an app apart
	from any other Python project.
	"""
	if not os.path.isfile("pyproject.toml"):
		return False
	name = dist_name(".")
	return bool(name) and os.path.isfile(os.path.join(name, "hooks.py"))


def app_opted_in() -> bool:
	"""pyproject.toml has a [tool.frappe-nix] table header (lib/standards/shell.nix; S35)."""
	text = _read("pyproject.toml") if os.path.isfile("pyproject.toml") else None
	return text is not None and re.search(r"^\s*\[{1,2}tool\.frappe-nix[].]", text, re.M) is not None


# ── frappe version → preset ───────────────────────────────────────────────


def _apps_json() -> Any:
	try:
		return json.loads(Path("sites/apps.json").read_text())
	except (OSError, ValueError):
		return None


def _resolution_branch(apps_json: Any, app: str) -> str:
	"""sites/apps.json's branch for APP. `resolution` degrades to the string
	"not calculated" in some bench versions, so its type is checked."""
	if not isinstance(apps_json, dict):
		return ""
	entry = apps_json.get(app)
	resolution = entry.get("resolution") if isinstance(entry, dict) else None
	if isinstance(resolution, dict):
		branch = resolution.get("branch")
		if isinstance(branch, str):
			return branch
	return ""


VERSION_RE = re.compile(r"""^__version__[ \t\f\v]*=[ \t\f\v]*["']([0-9][^"'\n]*)""", re.M)


def detect_frappe_version(presets: dict[str, Preset]) -> tuple[str, str] | None:
	"""(preset, where it was read from), ("UNSUPPORTED", version), or None.

	Branch names are consulted before __version__, because `develop` and
	`version-16` both report 16.0.0-dev.
	"""
	d = "apps/frappe"
	if not os.path.isdir(d):
		return None

	b = probe(git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=d)) or ""
	if b in presets:
		return b, "apps/frappe branch"

	# Detached HEAD: any local or remote ref at this commit named like a preset.
	refs = probe(
		git(
			"for-each-ref",
			"--points-at",
			"HEAD",
			"--format=%(refname:lstrip=2)",
			"refs/heads",
			"refs/remotes",
			cwd=d,
		)
	)
	for ref in (refs or "").splitlines():
		ref = ref.removeprefix("origin/").removeprefix("upstream/")
		if ref in presets:
			return ref, "ref at apps/frappe HEAD"

	apps_json = _apps_json() if os.path.isfile("sites/apps.json") else None
	b = _resolution_branch(apps_json, "frappe")
	if b in presets:
		return b, "sites/apps.json"

	v = ""
	m = VERSION_RE.search(_read(f"{d}/frappe/__init__.py") or "")
	if m:
		v = m.group(1)
	if not v and isinstance(apps_json, dict) and isinstance(apps_json.get("frappe"), dict):
		version = apps_json["frappe"].get("version")
		if isinstance(version, str | int | float) and not isinstance(version, bool):
			v = str(version)
	major = v.split(".", 1)[0]
	if not major:
		return None
	if re.fullmatch(r"1[0-4]|[0-9]", major):
		return "UNSUPPORTED", v
	if f"version-{major}" in presets:
		return f"version-{major}", f"apps/frappe __version__ {v}"
	return None


# ── per-app classification ────────────────────────────────────────────────


def resolve_app_branch(d: str, remote: str, sha: str, default: str) -> str:
	"""Always a branch: `bench-update --pull` skips any submodule without
	`submodule.<name>.branch`, so an empty one would freeze the app."""
	head = probe(git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=d)) or ""
	if head and probe_ok(git("rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{head}", cwd=d)):
		return head
	# Detached, or a branch that exists only locally: the first remote-tracking
	# ref pointing at this commit.
	refs = probe(
		git(
			"for-each-ref",
			"--points-at",
			sha,
			"--format=%(refname:lstrip=3)",
			f"refs/remotes/{remote}",
			cwd=d,
		)
	)
	for ref in (refs or "").splitlines():
		if ref and ref != "HEAD":
			return ref
	# A `clone --depth 1 -b X` has exactly one ref; its config records X.
	if head:
		merge = probe(git("config", "--get", f"branch.{head}.merge", cwd=d)) or ""
		merge = merge.removeprefix("refs/heads/")
		if merge:
			return merge
	b = _resolution_branch(_apps_json(), os.path.basename(d))
	return b or default


def gitmodules_url_for_path(path: str) -> str:
	"""The URL .gitmodules records for PATH, found by the entry's `path`, not its
	section name, which a half-converted bench need not have named after it."""
	if not os.path.isfile(".gitmodules"):
		return ""
	lines = probe(git("config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$")) or ""
	for line in lines.splitlines():
		key, _, value = line.partition(" ")
		if value == path:
			name = key.removeprefix("submodule.").removesuffix(".path")
			return probe(git("config", "-f", ".gitmodules", "--get", f"submodule.{name}.url")) or ""
	return ""


def same_git_url(a: str, b: str) -> bool:
	"""Two spellings of one repository: a trailing / or .git is as far as they usually differ."""
	a = a.removesuffix("/").removesuffix(".git")
	b = b.removesuffix("/").removesuffix(".git")
	return bool(a) and a == b


def classify_app(st: Run, name: str) -> App:
	app = App(name)
	d = f"apps/{name}"
	real = os.path.realpath(d)
	top = probe(git("rev-parse", "--show-toplevel", cwd=d)) or ""

	if not os.path.isfile(f"{d}/{name}/hooks.py"):
		app.flags.append("not-a-frappe-app")
	if not os.path.isfile(f"{d}/pyproject.toml"):
		app.flags.append("no-pyproject")
	if os.path.isfile(f"{d}/setup.py"):
		app.flags.append("setup-py")

	if top != real:
		app.disp = "vendor"
		app.flags.append("no-git")
		return app
	# `.git` as a file is a gitlink: the normal shape for a submodule whose gitdir
	# has been absorbed into the superproject (.git/modules/<name>), which is what
	# a half-converted bench looks like. Accepted when it points inside this
	# bench; refused when the repository lives elsewhere (a linked worktree,
	# another superproject), because then it would not travel with the bench.
	if os.path.isfile(f"{d}/.git"):
		gitdir = probe(git("rev-parse", "--absolute-git-dir", cwd=d)) or ""
		if not gitdir.startswith(f"{st.bench_git_dir}/modules/"):
			app.disp = "skip"
			app.flags.append("external-gitdir")
			return app
	if not probe_ok(git("rev-parse", "--verify", "-q", "HEAD", cwd=d)):
		app.disp = "skip"
		app.flags.append("no-commits")
		return app

	# An already-registered submodule keeps the URL the bench declares, when a
	# remote still carries it: origin is often a developer's fork, and recording
	# it would send `nix build` and `bench-update --pull` to a repository with
	# neither the pinned commit nor the release branch.
	remote = url = ""
	remotes = (probe(git("remote", cwd=d)) or "").split()
	declared = gitmodules_url_for_path(d)
	if declared:
		for r in remotes:
			r_url = probe(git("remote", "get-url", r, cwd=d)) or ""
			if same_git_url(r_url, declared):
				remote, url = r, r_url
				break
	# origin → upstream → the sole remote. bench's own get_remote() prefers
	# upstream, so an origin-only lookup misses real benches.
	if not remote:
		for r in ("origin", "upstream"):
			r_url = probe(git("remote", "get-url", r, cwd=d))
			if r_url is not None:
				remote, url = r, r_url
				break
	if not remote and len(remotes) == 1:
		remote = remotes[0]
		url = probe(git("remote", "get-url", remote, cwd=d)) or ""

	# A filesystem path as the recorded submodule URL breaks every other clone of
	# the bench and trips protocol.file.allow on `submodule update --init`.
	if not url:
		app.disp = "vendor"
		app.flags.append("no-remote")
		return app
	if url.startswith(("/", "./", "../", "file://")):
		app.flags.append("local-remote")
		if not st.args.allow_file_remotes:
			app.disp = "vendor"
			app.url = url
			return app

	sha = probe(git("rev-parse", "HEAD", cwd=d)) or ""
	branch = resolve_app_branch(d, remote, sha, st.p.branch)
	# A branch that is not a known remote ref is a guess (the preset's, or a stale
	# sites/apps.json): `bench-update --pull` fetches it by name.
	if not probe_ok(git("rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{branch}", cwd=d)):
		app.flags.append("branch-unverified")
	if not (probe_ok(git("diff", "--quiet", cwd=d)) and probe_ok(git("diff", "--cached", "--quiet", cwd=d))):
		app.flags.append("dirty")
	if probe(git("rev-parse", "--is-shallow-repository", cwd=d)) == "true":
		app.flags.append("shallow")
	elif not probe(git("branch", "-r", "--contains", sha, cwd=d)):
		# Meaningless on a shallow clone, which has no remote history to search.
		app.flags.append("unpushed")

	app.disp = "submodule"
	app.url, app.branch, app.sha, app.remote = url, branch, sha, remote
	return app


def classify_apps(st: Run) -> None:
	"""Every app under apps/, in sites/apps.txt order (the install order the site
	was built with), then the rest on disk; frappe first."""
	forced = st.args.force_vendor.split(",")
	if bench_repo_state() == "own":
		st.bench_git_dir = probe(git("rev-parse", "--absolute-git-dir")) or ""
	order: list[str] = []
	if os.path.isfile("sites/apps.txt"):
		for line in (_read("sites/apps.txt") or "").splitlines():
			name = "".join(line.split())
			if not name or name in order:
				continue
			if os.path.isdir(f"apps/{name}"):
				order.append(name)
			else:
				st.warn(f"sites/apps.txt lists '{name}' but apps/{name} does not exist")
	if os.path.isdir("apps"):
		for name in sorted(os.listdir("apps")):
			if not name.startswith(".") and os.path.isdir(f"apps/{name}") and name not in order:
				order.append(name)
	if "frappe" in order:
		order.remove("frappe")
		order.insert(0, "frappe")

	st.apps = {}
	for name in order:
		app = classify_app(st, name)
		if name in forced:
			app.disp = "vendor"
			app.flags.append("forced-vendor")
		st.apps[name] = app
