"""``bench update`` / ``bench-update``: the submodule-aware replacement for
``bench update``. Was bash in lib/scripts.nix;
tests/bench-update.sh is its acceptance suite.

    bench-update [--pull | --migrate | --build | --node-locks [<app>[/<subdir>]…]]

With no flags: pull every app submodule, refresh node-locks/ for what moved,
re-lock the Python workspace when an app's pyproject.toml moved, migrate, then
build. The flags are read word by word, each one switching steps off, as the
bash read them. In app mode there is nothing to pull and nothing here to write:
the apps are pinned by flake.lock and the locks belong to the repository, so
only --migrate and --build mean anything.
"""

import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.proc import Abort, fail, say, warn
from frappe_nix_tools.deps.runtime_pin import runtime_at_pin

if TYPE_CHECKING:
	from frappe_nix_tools.bench_config import CliConfig

#: Regenerable lockfile basenames: the one place to add one.
REGEN_LOCKS = ("yarn.lock", "package-lock.json")

#: The re-lock both updates run. A plain `uv lock` keeps a git source at the
#: revision it already resolved, and frappe-runtime is one: its code is built
#: from this repository's runtime/, but its dependency ranges still come from
#: uv.lock, frozen at whatever revision the bench first locked. An app bump past
#: those ranges then fails to resolve against a runtime that already accepts it.
#: uv ignores the flag in a bench without frappe-runtime. Each caller runs it
#: inside ``runtime_pin.runtime_at_pin``, so the refresh lands on the revision
#: flake.lock pins rather than on frappe-nix's branch head.
UV_LOCK = ("uv", "lock", "--upgrade-package", "frappe-runtime")

BENCH_HELP = """\
Usage: bench-update [--pull | --migrate | --build | --node-locks [<app>[/<subdir>]…]]

  (no flags)     Pull apps, refresh node-locks/, migrate, build
  --pull         Pull latest commits + refresh node-locks/ for what moved
  --migrate      Run DB migrations only
  --build        Build JS/CSS assets only
  --node-locks   (Re)generate node-locks/: a fallback yarn.lock for every app and nested
                 frontend without one of its own (needs the network). Name a target to
                 force a lock over the yarn.lock it ships — for one that cannot resolve offline."""

APP_HELP = """\
Usage: bench-update [--migrate | --build]

  (no flags)     Migrate, then build
  --migrate      Run DB migrations only
  --build        Build JS/CSS assets only

To move the pinned apps: nix flake update && nix run .#relock"""


class _Exit(Exception):
	def __init__(self, status: int) -> None:
		self.status = status


@dataclass
class Plan:
	pull: bool
	migrate: bool = True
	build: bool = True
	node_locks: bool = False
	targets: list[str] = field(default_factory=list)


def parse(argv: Sequence[str], *, app_mode: bool, lock_dir: str = "nix") -> Plan:
	"""The steps ARGV leaves on, each flag switching others off in turn."""
	plan = Plan(pull=not app_mode)
	for arg in argv:
		if app_mode and arg in ("--pull", "--node-locks", "--node-hashes"):
			warn(
				f"bench-update: {arg} has no meaning in app mode — the apps are pinned by",
				f"flake.lock, not pulled, and {lock_dir}/node-locks/ is generated from",
				"those pins. Use:",
				"",
				"    nix flake update        # move the pins",
				f"    nix run .#relock        # re-resolve and rewrite {lock_dir}/",
			)
			raise _Exit(1)
		if arg == "--pull":
			plan.migrate = plan.build = False
		elif arg in ("--node-locks", "--node-hashes"):
			if arg == "--node-hashes":
				warn(
					"bench-update: --node-hashes is now --node-locks"
					" (node-offline-hashes.json was replaced by node-locks/)"
				)
			plan.pull = plan.migrate = plan.build = False
			plan.node_locks = True
		elif arg == "--migrate":
			plan.pull = plan.build = False
		elif arg == "--build":
			plan.pull = plan.migrate = False
		elif arg in ("--help", "-h"):
			say(APP_HELP if app_mode else BENCH_HELP)
			raise _Exit(0)
		elif arg.startswith("-"):
			warn(f"Unknown flag: {arg}")
			raise _Exit(1)
		elif plan.node_locks:
			plan.targets.append(arg)
		else:
			warn(f"Unexpected argument: {arg} (targets only follow --node-locks)")
			raise _Exit(1)
	return plan


def _git(*args: str, cwd: str | None = None) -> tuple[int, str]:
	"""A git question: its status and stdout, stderr discarded."""
	return proc.probe(["git", *args], cwd=cwd)


def _git_ok(*args: str, cwd: str | None = None) -> bool:
	return proc.attempt(["git", *args], cwd=cwd, quiet=True, stdin=subprocess.DEVNULL) == 0


def _hash(path: str) -> str:
	status, out = _git("hash-object", path)
	return out.strip() if status == 0 else "none"


def _pyprojects() -> dict[str, str]:
	"""apps/*/pyproject.toml, each with its git blob hash."""
	found = sorted(p for p in Path("apps").glob("*/pyproject.toml") if not p.parent.name.startswith("."))
	return {str(p): _hash(str(p)) for p in found if p.exists()}


def _strip_url(url: str) -> str:
	"""Two spellings of one GitHub URL usually differ by a trailing / or .git."""
	return url.removesuffix("/").removesuffix(".git")


def remote_for_url(url: str, cwd: str) -> str | None:
	"""Which remote of a submodule has the URL .gitmodules declares.

	That URL is the app's source of truth — it is what `nix build` fetches the
	pinned commit from — and `origin` is not a safe stand-in for it: a
	developer's checkout commonly has origin on a fork that does not carry the
	release branch at all, and upstream on the real thing."""
	want = _strip_url(url)
	_, remotes = _git("remote", cwd=cwd)
	for remote in remotes.split():
		status, got = _git("remote", "get-url", remote, cwd=cwd)
		got = _strip_url(got.strip() if status == 0 else "")
		if got and got == want:
			return remote
	return None


def clear_lock_obstacles(cwd: str) -> None:
	"""Clear what would stop `checkout -B` from moving to FETCH_HEAD, of the
	regenerable lockfiles only.

	`checkout -B` refuses over a tracked file with local changes that the target
	commit also changes, and over an untracked file it would create. Lockfiles
	in an app checkout are tool output — a postinstall's non-frozen `yarn
	install`, a manual `yarn` — and the install that follows regenerates them
	from the commit's own, so exactly those obstacles go and nothing else. What
	git would carry across untouched is left alone, which keeps node_modules'
	sentinel valid. Run after the ancestry guard has passed: only uncommitted
	changes are discarded here, never commits."""
	specs = [f":(glob)**/{name}" for name in REGEN_LOCKS]
	_, out = _git(
		"diff-tree", "-r", "-z", "--no-renames", "--name-status", "HEAD", "FETCH_HEAD", "--", *specs, cwd=cwd
	)
	fields = out.split("\0")
	for st, path in zip(fields[0::2], fields[1::2], strict=False):
		if not st or not path:
			continue
		obstacle = False
		if st == "A":
			# Not in HEAD: a file (untracked, or staged new) where upstream adds
			# one. git overwrites an ignored one by itself; check-ignore says 1 for
			# "not ignored" (0 ignored, 128 error: both leave it alone).
			full = os.path.join(cwd, path)
			if os.path.lexists(full):
				status = proc.attempt(
					["git", "check-ignore", "-q", "--", path], cwd=cwd, quiet=True, stdin=subprocess.DEVNULL
				)
				obstacle = status == 1
		elif not _git_ok("--literal-pathspecs", "diff", "--quiet", "HEAD", "--", path, cwd=cwd):
			obstacle = True
		if not obstacle:
			continue
		# A lock beside a package.json that is edited too may be the other half
		# of a hand edit (`yarn add`), not tool output: leave it, and let the
		# checkout say so.
		manifest = path[: len(path) - len(path.rsplit("/", 1)[-1])] + "package.json"
		if not _git_ok("--literal-pathspecs", "diff", "--quiet", "HEAD", "--", manifest, cwd=cwd):
			say(f"     keeping {path}: the package.json beside it has local edits too")
			continue
		if st == "A":
			_git_ok(
				"--literal-pathspecs", "rm", "-q", "-f", "--cached", "--ignore-unmatch", "--", path, cwd=cwd
			)
			try:
				os.remove(os.path.join(cwd, path))
			except FileNotFoundError:
				pass
			say(f"     discarding local {path} (regenerated by yarn install; upstream adds one)")
		elif _git_ok("--literal-pathspecs", "checkout", "-q", "HEAD", "--", path, cwd=cwd):
			say(f"     discarding local changes to {path} (regenerated by yarn install; upstream changes it)")


def _force() -> bool:
	"""FRAPPE_NIX_BENCH_UPDATE_FORCE (FRAPPE_BENCH_UPDATE_FORCE, its 1.x name, is not
	read since frappe-nix 2.0.0)."""
	return bool(os.environ.get("FRAPPE_NIX_BENCH_UPDATE_FORCE"))


def _combined(cmd: Sequence[str], *, cwd: str) -> tuple[int, str]:
	"""CMD's status and its stdout and stderr together, as `$(cmd 2>&1)` has them."""
	sys.stdout.flush()
	try:
		done = subprocess.run(
			list(cmd),
			cwd=cwd,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
		)
	except KeyboardInterrupt:
		raise Abort(130) from None
	return done.returncode, done.stdout.rstrip("\n")


def pull_app(app: str, branch: str, url: str) -> bool:
	"""Fetch apps/APP's BRANCH and move it there; False when it was skipped."""
	cwd = f"apps/{app}"
	# The declared URL's remote when there is one; else the URL itself, which
	# git fetches just as well (only the remote-tracking ref goes unrefreshed).
	# The name-based guesses are a last resort for a submodule registered
	# without a URL.
	remote = remote_for_url(url, cwd)
	if not remote and url:
		remote = url
		say(f"  · {app}: no remote has the .gitmodules URL ({url}); fetching it directly")
	if not remote:
		_, names = _git("remote", cwd=cwd)
		listed = names.split("\n")
		remote = "origin" if "origin" in listed else (listed[0] if listed else "")
	if not remote:
		say(f"  ⚠  {app}: no git remote — skipping")
		return False
	say(f"  → {app} ({branch} from {remote})")
	# No --depth: a depth-1 fetch grafts the new tip with no parents, so the
	# ancestry check below could never pass once the remote had moved. A plain
	# fetch on a shallow clone stops at what the clone already has. A partial
	# clone (shell entry's lib.sources makes them) keeps its paired branch with
	# the folders of every commit: this pulls that branch, so ask for them.
	_, promisor = _git("config", "--get", f"remote.{remote}.promisor", cwd=cwd)
	filt = ["--filter=blob:none"] if promisor.strip() == "true" else []
	if proc.attempt(["git", "fetch", *filt, remote, branch], cwd=cwd, stdin=subprocess.DEVNULL) != 0:
		fail(
			f"  ✗ {app}: could not fetch '{branch}' from {remote}",
			f"     .gitmodules says apps/{app} is {url} @ {branch}; fix either the entry",
			f"     (git config -f .gitmodules submodule.apps/{app}.branch <branch>) or the remote.",
		)
	# A shallow clone can be unable to answer the ancestry question at all: a
	# depth-limited fetch (this did one until 2026-09-11) leaves the pin and the
	# tip as two disconnected islands, and a plain fetch never asks for the gap.
	# "No merge-base whatsoever" is the exact signature, and only ever a
	# shallow artefact: a real local commit shares its parent with the tip. So
	# then, and only then, deepen back to HEAD's own commit date, less a day for
	# a backport whose committer date runs a little behind the pin's.
	shallow = _git("rev-parse", "--is-shallow-repository", cwd=cwd)[1].strip() == "true"
	if shallow:
		status, base = _git("merge-base", "HEAD", "FETCH_HEAD", cwd=cwd)
		if not base.strip():
			say(f"     shallow clone with no path between HEAD and {remote}/{branch} — deepening to check")
			_, ct = _git("log", "-1", "--format=%ct", "HEAD", cwd=cwd)
			since = int(ct.strip() or 0) - 86400
			proc.attempt(
				["git", "fetch", "-q", remote, branch, f"--shallow-since=@{since}"],
				cwd=cwd,
				stdin=subprocess.DEVNULL,
			)
	# `checkout -B` discards anything not on the remote branch: refuse when this
	# submodule carries commits that are not in what was just fetched.
	if not _git_ok("merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD", cwd=cwd):
		if _force():
			say("     ⚠  local commits will be discarded (FRAPPE_NIX_BENCH_UPDATE_FORCE=1)")
		else:
			say(
				f"  ⚠  {app}: HEAD is not an ancestor of {remote}/{branch} — it has local or",
				"     unpushed commits that checkout -B would discard. Skipping.",
				"     Push them, or re-run with FRAPPE_NIX_BENCH_UPDATE_FORCE=1 to overwrite.",
			)
			if _git("rev-parse", "--is-shallow-repository", cwd=cwd)[1].strip() == "true":
				say(
					"     (This clone is shallow. If you made no commits here, 'git fetch --unshallow'",
					f"     in apps/{app} settles it.)",
				)
			return False
	clear_lock_obstacles(cwd)
	# Whatever else is in the way — git names it — is the user's to decide: the
	# app is skipped like one with local commits, and listed at the end, rather
	# than ending the whole update for one app's dirty tree.
	status, said = _combined(["git", "checkout", "-B", branch, "FETCH_HEAD"], cwd=cwd)
	if status != 0:
		say(f"  ⚠  {app}: git could not check out {remote}/{branch} — skipping. git says:")
		say(*(f"       {line}" for line in said.split("\n")))
		say(
			f"     If that names local changes: commit them, stash them (git -C apps/{app} stash),",
			f"     or discard them (git -C apps/{app} checkout -- <path>), then re-run.",
		)
		return False
	if said:
		say(said)
	for pyc in Path(cwd).rglob("*.pyc"):
		try:
			pyc.unlink()
		except OSError:
			pass
	return True


def node_locks_cmd(config: "CliConfig", *extra: str) -> list[str]:
	"""frappe-nix-node-locks over the bench, with the nested frontends it must
	leave alone; EXTRA names targets."""
	deps = config.deps
	return [
		deps.node_locks,
		*(f"--exclude={key}" for key in deps.node_nested_frontend_excludes),
		".",
		"node-locks",
		*extra,
	]


def node_locks_soft(config: "CliConfig") -> None:
	"""The fallback locks, downgraded to a warning: a resolve failure must not
	abort a pull (the previous locks stay, and nix build keeps working from
	them)."""
	if proc.attempt(node_locks_cmd(config)) != 0:
		warn(
			"  ⚠  node-locks/ not fully regenerated — nix build keeps the previous locks;"
			" re-run: bench-update --node-locks"
		)


def refresh_node_modules_soft(config: "CliConfig") -> None:
	"""Every app's node_modules in step with its manifests, repaired first, a
	failure only a warning (lib/scripts.nix, refreshNodeModulesSoft)."""
	apps = config.deps.apps_with_node
	if apps:
		proc.attempt([config.deps.node_verify, ".", *apps])
		proc.attempt([config.deps.node_modules, ".", *apps])


def build_deps_sync(config: "CliConfig") -> None:
	if config.apps.build_deps_sync:
		proc.run([config.apps.build_deps_sync])


def pull(config: "CliConfig") -> list[str]:
	"""The --pull half: every submodule to its branch's tip, then the registry,
	the fallback locks and the Python lock in step. The apps left behind."""
	say("── Pulling latest commits for all app submodules ────────────")
	before = _pyprojects()
	skipped: list[str] = []
	listing = proc.output([config.apps.workspace, "apps", "--apps-dir", "apps"])
	for line in listing.splitlines():
		app, kind, branch, url = [*line.split("\t", 3), "", "", "", ""][:4]
		if kind == "local":
			say(f"  · {app}: local app (source committed with the bench) — nothing to pull")
			continue
		if kind == "nested-repo":
			say(
				f"  ⚠  {app}: a git repository that is not a registered submodule — not pulled,",
				"     and NOT in the flake's source tree, so 'nix build' leaves it out. Either",
				"     vendor it (frappe-init --migrate commits its source into the bench and",
				"     keeps its history in .frappe-nix-backup/) or push it somewhere and",
				f"     re-add it: git rm --cached apps/{app} && rm -rf apps/{app} && bench-get-app <url>",
			)
			continue
		if kind == "submodule-uninitialized":
			# Shell entry checks out only what a fresh clone has never had
			# (python-devops-nix's lib.sources); a pull is where any other gets its
			# checkout. Not --recursive: Frappe apps ship nested submodules with
			# broken refs. Without a gitlink there is no commit to check out:
			# .gitmodules outlived a removal, which remove-app finishes.
			_, staged = _git("ls-files", "-s", "--", f"apps/{app}")
			gitlink = any(
				parts[0] == "160000" and parts[-1] == f"apps/{app}"
				for parts in (row.split() for row in staged.splitlines())
				if len(parts) >= 4
			)
			if not gitlink:
				say(
					f"  ⚠  {app}: .gitmodules registers it, but the bench records no commit for it — skipping.",
					f"     A removal that stopped halfway; finish it with: bench remove-app {app}",
				)
				continue
			say(f"  → {app}: registered but not checked out — checking out its pinned commit")
			if (
				proc.attempt(
					["git", "submodule", "update", "--init", "--", f"apps/{app}"], stdin=subprocess.DEVNULL
				)
				!= 0
			):
				warn(f"  ✗ {app}: could not check it out — skipping")
				continue
			# The pinned commit is what uv.lock was resolved against, so it, not
			# the empty directory before it, is what the re-lock compares with.
			before[f"apps/{app}/pyproject.toml"] = _hash(f"apps/{app}/pyproject.toml")
		if not branch:
			say(
				f"  ⚠  {app}: no branch configured in .gitmodules — skipping.",
				"     Fix it with: frappe-init --migrate"
				f" (or: git config -f .gitmodules submodule.apps/{app}.branch <branch>)",
			)
			continue
		if not pull_app(app, branch, url):
			skipped.append(app)
	if skipped:
		say(f"  ⚠  not updated: {' '.join(skipped)} — see above")
	say("")

	# The pins just moved; sites/apps.json records them. Before the locks, which
	# can be slow or fail on a network hiccup: the registry must not be left
	# describing the old commits because of that.
	proc.run(
		[
			config.apps.workspace,
			"sync-registry",
			"--pyproject",
			"pyproject.toml",
			"--apps-dir",
			"apps",
			"--sites-dir",
			"sites",
		]
	)
	say("  commit sites/apps.json along with the submodule bumps", "")

	# The fallback locks, for whatever the pull moved; the generator's own stamp
	# decides what that was, and anything else costs nothing.
	say("── Refreshing node-locks/ ──────────────────────────────────")
	node_locks_soft(config)
	say("  commit node-locks/ along with the submodule bumps, if it changed", "")

	# Re-lock when an app's pyproject.toml moved, here where the cause is on
	# screen: a stale uv.lock fails the next shell entry's *evaluation*.
	after = _pyprojects()
	moved = [Path(p).parent.name for p, h in after.items() if before.get(p, "none") != h]
	if moved:
		say(f"── Re-locking the Python workspace (pyproject.toml changed: {' '.join(moved)}) ──")
		with runtime_at_pin():
			status = proc.attempt(UV_LOCK)
		if status != 0:
			fail(
				"  ⚠  uv lock failed — the dev shell will not evaluate until this resolves.",
				"     For a conflict between two apps, add a pin to [tool.uv] override-dependencies;",
				"     for one against [dependency-groups], relax the pin there. Then re-run 'uv lock'.",
			)
		build_deps_sync(config)
		say("  commit uv.lock (and pyproject.toml) along with the submodule bumps", "")
	return skipped


def update(plan: Plan, config: "CliConfig") -> int:
	skipped: list[str] = []
	if plan.pull:
		skipped = pull(config)
		# `--pull` stops here, so this is its only chance to bring node_modules
		# back in step with what was just pulled. A full update reaches the same
		# refresh through bench-build, where a failure is fatal.
		if not plan.build:
			refresh_node_modules_soft(config)

	if plan.node_locks:
		say("── Regenerating node-locks/ ────────────────────────────────")
		proc.run(node_locks_cmd(config, *plan.targets))

	if plan.migrate:
		say("── Running migrations ───────────────────────────────────────")
		# bench-migrate, not `bench migrate`: _FRAPPE_BENCH_RAW is set, so the
		# latter would skip the offline step in front of it.
		proc.run(["bench-migrate"])
		say("")

	if plan.build:
		say("── Building assets ──────────────────────────────────────────")
		# bench-build, not `bench build`: the latter would go straight to the real
		# bench and skip the node_modules refresh the pull is the reason for.
		proc.run(["bench-build"])
		say("")

	if skipped:
		say(f"⚠  bench-update complete, but not updated: {' '.join(skipped)} (see above)")
	else:
		say("✅ bench-update complete")
	return 0


def _body(argv: Sequence[str]) -> int:
	from frappe_nix_tools.bench_config import load

	config = load()
	try:
		plan = parse(argv, app_mode=config.deps.app_mode, lock_dir=config.deps.lock_dir)
	except _Exit as e:
		return e.status
	os.chdir(proc.bench_root())
	return update(plan, config)


def main(argv: Sequence[str] | None = None) -> int:
	return proc.main_guard(lambda: _body(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
	sys.exit(main())
