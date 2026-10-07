"""What ``sync --write`` runs around the plan: the two phases (spec S32, §3.3).

Phase A writes ``flake.nix`` and ``.envrc``, locks the flake, and re-executes sync from the
frappe-nix the lock pins when that is another release. Phase B renders everything else,
then brings the derived files up: ``tools/uv.lock``, ``yarn.lock``, the node-lock seeds, the
bench lock (relock) and the README blocks.

Every external command goes through ``Runner``, which prints it, and which ``--dry-run``
and ``--offline`` turn into a report of what would run.
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import frappe_nix_tools
from frappe_nix_tools.common import data_path, flakelock
from frappe_nix_tools.common.report import ConfigError, EnvError
from frappe_nix_tools.scaffold import context, engine

# Set on a re-exec or a dev-shell re-entry when phase A changed flake.lock, so the phase B
# that runs there still relocks the bench (step 11): it sees an unchanged lock itself.
LOCK_CHANGED = "FRAPPE_NIX_SYNC_LOCK_CHANGED"


# The file a handed-over sync (re-exec or dev-shell re-entry) writes its exit code to, so
# the parent can tell the child's result from a failure of `nix run`/`nix develop` itself.
RESULT = "FRAPPE_NIX_SYNC_RESULT"


def report_result(code: int) -> None:
	"""In a handed-over sync: record its exit code for the sync that started it."""
	path = os.environ.get(RESULT)
	if path:
		with contextlib.suppress(OSError):
			Path(path).write_text(f"{code}\n")


def lock_changed_env(changed: bool) -> dict[str, str]:
	return {LOCK_CHANGED: "1"} if changed else {}


def inherited_lock_change() -> bool:
	"""Whether the sync that handed over to this one changed ``flake.lock``."""
	return os.environ.get(LOCK_CHANGED) == "1"


# Node locks frappe-nix seeds per sibling: nix/node-locks/<key> (§2.4, §3.3 step 10).
NODE_LOCKS = {
	"frappe": ("frappe/ui",),
	"erpnext": ("erpnext/banking",),
	"hrms": ("hrms/frontend", "hrms/roster"),
}


@dataclass
class Runner:
	root: Path
	dry_run: bool = False
	offline: bool = False
	ran: list[str] = field(default_factory=list)

	def say(self, text: str) -> None:
		print(text, file=sys.stderr)

	def run(
		self, argv: list[str], *, env: dict | None = None, check: bool = True, network: bool = True
	) -> int:
		shown = " ".join(argv)
		if self.dry_run or (self.offline and network):
			self.say(
				f"frappe-nix sync: would run: {shown}" + (" (skipped: --offline)" if self.offline else "")
			)
			return 0
		self.say(f"frappe-nix sync: $ {shown}")
		self.ran.append(shown)
		try:
			proc = subprocess.run(argv, cwd=self.root, env={**os.environ, **(env or {})}, check=False)
		except FileNotFoundError as e:
			raise EnvError(f"{argv[0]} is not on PATH (needed for: {shown})") from e
		if check and proc.returncode != 0:
			raise EnvError(f"`{shown}` exited {proc.returncode}")
		return proc.returncode


def override_url() -> str | None:
	"""``FRAPPE_NIX_URL_OVERRIDE``: frappe-nix's self-tests only, and only with skew allowed."""
	url = os.environ.get("FRAPPE_NIX_URL_OVERRIDE") or None
	if url and os.environ.get("FRAPPE_NIX_ALLOW_SKEW") != "1":
		raise EnvError(
			"FRAPPE_NIX_URL_OVERRIDE is for frappe-nix's self-tests and needs FRAPPE_NIX_ALLOW_SKEW=1"
		)
	return url


def nix_args() -> list[str]:
	url = override_url()
	return ["--override-input", "frappe-nix", url] if url else []


def _frappe_nix_original(lock: dict) -> str | None:
	"""What the lock's frappe-nix node was locked from, in ``engine.input_spec``'s form."""
	ref = lock["nodes"].get(lock["root"], {}).get("inputs", {}).get("frappe-nix")
	return engine.locked_spec(lock, ref) if ref is not None else None


def lock_reasons(root: Path, inputs: dict[str, str | None], url: str) -> tuple[bool, bool]:
	"""Whether ``flake.lock`` must be (re)locked, and whether frappe-nix must move to ``url``.

	``inputs`` is ``engine.flake_input_specs`` of the rendered ``flake.nix``. The lock is stale
	when its input set differs, or when an input is locked from another URL or ``follows``
	than ``flake.nix`` now gives it: a frappe-major bump keeps the input names and changes
	``frappe``'s (and every sibling's) branch, and a changed ``profile`` re-points
	``standards-profile``; ``nix flake lock`` relocks those. frappe-nix moves (``nix flake
	update frappe-nix``) only when its node's ``original`` is not ``url`` (the release branch,
	or ``dev-shell.frappe-nix-url``), so a pinned tag, rev, fork or mirror is never undone."""
	path = root / "flake.lock"
	if not path.is_file():
		return True, False
	try:
		lock = flakelock.load(path)
	except EnvError:
		return True, False
	have = set(lock["nodes"].get(lock["root"], {}).get("inputs", {}))
	relock = have != set(inputs) or bool(engine.stale_inputs(lock, inputs, skip=("frappe-nix",)))
	original = _frappe_nix_original(lock)
	return relock, original is not None and original != engine.input_spec("url", url)


def locked_frappe_nix_version(runner: Runner) -> str | None:
	"""The ``version.txt`` of the frappe-nix ``flake.lock`` pins, read from the Nix store."""
	if runner.dry_run or runner.offline:
		return None
	try:
		lock = flakelock.load(runner.root / "flake.lock")
	except EnvError:
		return None
	found = flakelock.node_at(lock, ["frappe-nix"])
	locked = found[1].get("locked") if found else None
	if not isinstance(locked, dict) or locked.get("type") != "github":
		return None
	# The lock's attributes reach Nix as data (an environment variable the expression reads),
	# never spliced into the expression: a `${…}` in a crafted lock would be evaluated.
	expr = 'builtins.readFile ((builtins.fetchTree (builtins.fromJSON (builtins.getEnv "FRAPPE_NIX_LOCKED_REV"))) + "/version.txt")'
	try:
		out = subprocess.run(
			["nix", "eval", "--raw", "--impure", "--expr", expr],
			cwd=runner.root,
			env={**os.environ, "FRAPPE_NIX_LOCKED_REV": json.dumps(locked)},
			capture_output=True,
			text=True,
			check=True,
		)
	except (FileNotFoundError, subprocess.CalledProcessError):
		return None
	return out.stdout.strip() or None


FRAPPE_NIX_REPO = "https://github.com/Avunu/frappe-nix"


def release_ref_needed(root: Path, url: str) -> bool:
	"""Whether phase A's lock would fetch frappe-nix's release branch: the flake follows the
	default ``release-<N>`` URL, and there is no ``flake.lock`` or its frappe-nix node was
	locked from something else."""
	if url != context.default_frappe_nix_url():
		return False
	path = root / "flake.lock"
	if not path.is_file():
		return True
	try:
		lock = flakelock.load(path)
	except EnvError:
		return True
	return _frappe_nix_original(lock) != engine.input_spec("url", url)


def release_branch_exists(root: Path, branch: str) -> bool | None:
	"""Whether frappe-nix has ``branch``: ``None`` when ``git ls-remote`` can't tell (no git,
	no network); only its "no matching ref" exit (2) is a no."""
	try:
		proc = subprocess.run(
			["git", "ls-remote", "--exit-code", "--heads", FRAPPE_NIX_REPO, branch],
			cwd=root,
			env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
			capture_output=True,
			timeout=120,
			check=False,
		)
	except (FileNotFoundError, subprocess.TimeoutExpired):
		return None
	return {0: True, 2: False}.get(proc.returncode)


def require_release_branch(runner: Runner, url: str) -> None:
	"""Refuse, before anything is written, when the ``release-<major>`` branch the rendered
	flake follows does not exist: it is created when ``v<major>.0.0`` is tagged (S32), and
	``nix flake lock`` would otherwise fail halfway through (GitHub answers 422). A network
	failure is left to the lock step to report. A ``dev-shell.frappe-nix-url`` is the app's
	own choice and is not checked."""
	if runner.dry_run or runner.offline or override_url() or not release_ref_needed(runner.root, url):
		return
	major = context.frappe_nix_major(frappe_nix_tools.__version__)
	branch = f"release-{major}"
	if release_branch_exists(runner.root, branch) is False:
		raise EnvError(
			f"{FRAPPE_NIX_REPO} has no {branch} branch yet: the app's flake follows it, and it is"
			f" created when frappe-nix v{major}.0.0 is tagged (docs/app-standards/spec.md S32). Nothing"
			" was written; app mode is available from that release on."
		)


def phase_a_lock(runner: Runner, inputs: dict[str, str | None], url: str) -> bool:
	"""Step 3: lock the flake when needed. Returns whether ``flake.lock`` changed."""
	before = (runner.root / "flake.lock").read_bytes() if (runner.root / "flake.lock").is_file() else None
	relock, move = lock_reasons(runner.root, inputs, url)
	if override_url():
		# The self-tests lock the checkout under test in place of release-<N>.
		runner.run(["nix", "flake", "lock", *nix_args()])
	else:
		if relock:
			runner.run(["nix", "flake", "lock"])
		if move or (relock and lock_reasons(runner.root, inputs, url)[1]):
			runner.run(["nix", "flake", "update", "frappe-nix"])
	after = (runner.root / "flake.lock").read_bytes() if (runner.root / "flake.lock").is_file() else None
	return before != after


def handover(runner: Runner, argv: list[str], env: dict[str, str]) -> NoReturn:
	"""Run a sync somewhere else (steps 4 and the phase-B re-entry) and exit with its code.

	Nix exits 1 on any evaluation, fetch or build failure, and 1 means drift (§3.3), so the
	child's own code comes back through ``FRAPPE_NIX_SYNC_RESULT``; when the child never got
	to write it, a non-zero exit is ``nix``'s and is an environment error (3)."""
	fd, path = tempfile.mkstemp(prefix="frappe-nix-sync-result-")
	os.close(fd)
	try:
		code = runner.run(argv, env={**env, RESULT: path}, check=False)
		recorded = Path(path).read_text().strip()
	finally:
		Path(path).unlink(missing_ok=True)
	if recorded.isdigit():
		raise SystemExit(int(recorded))
	if code == 0:
		raise SystemExit(0)
	raise EnvError(f"`{' '.join(argv)}` exited {code} before the sync it runs reported a result")


def maybe_reexec(runner: Runner, argv: list[str], lock_changed: bool = False) -> None:
	"""Step 4: phase B renders with the frappe-nix-tools the lock pins, so a bootstrap from
	another release hands over to it (once)."""
	if override_url() or os.environ.get("FRAPPE_NIX_SYNC_REEXEC") == "1":
		return
	pinned = locked_frappe_nix_version(runner)
	if pinned is None or pinned == frappe_nix_tools.__version__:
		return
	runner.say(
		f"frappe-nix sync: flake.lock pins frappe-nix {pinned}; this is {frappe_nix_tools.__version__}: re-running from the lock"
	)
	handover(
		runner,
		["nix", "run", "--no-pure-eval", ".#frappe-init", "--", "--sync", *argv],
		{"FRAPPE_NIX_SYNC_REEXEC": "1", **lock_changed_env(lock_changed)},
	)


def ensure_tools(runner: Runner, argv: list[str], lock_changed: bool = False) -> None:
	"""Phase B needs ``uv`` and ``yarn``; without them, re-enter through the app's dev shell (once)."""
	if runner.offline or runner.dry_run or (shutil.which("uv") and shutil.which("yarn")):
		return
	if os.environ.get("FRAPPE_NIX_SYNC_REENTERED") == "1":
		raise EnvError("uv and yarn are still not on PATH inside the dev shell")
	if not (runner.root / "nix" / "uv.lock").is_file():
		# The dev shell evaluates the bench workspace, which needs nix/uv.lock first.
		runner.run(["nix", "run", "--no-pure-eval", *nix_args(), ".#relock"])
	handover(
		runner,
		[
			"nix",
			"develop",
			"--no-pure-eval",
			*nix_args(),
			"-c",
			"frappe-nix",
			"sync",
			"--write",
			"--phase",
			"b",
			*argv,
		],
		{"FRAPPE_NIX_CI": "1", "FRAPPE_NIX_SYNC_REENTERED": "1", **lock_changed_env(lock_changed)},
	)


def seed_node_locks(root: Path, major: int, siblings: list[str], *, dry_run: bool) -> list[str]:
	"""Step 10: copy ``frappe_nix_tools/data/node-locks/version-<major>/<key>`` for each present app
	whose ``nix/node-locks/<key>`` has no ``yarn.lock`` yet. Returns the paths written."""
	written: list[str] = []
	if not siblings:
		# frappe's own lock comes with the siblings' (§7 N3: minimal on an app without
		# siblings writes nothing else); relock makes it otherwise.
		return written
	for app in ["frappe", *siblings]:
		for key in NODE_LOCKS.get(app, ()):
			dest = root / "nix" / "node-locks" / key
			# Every component, the seeds included, must be a real directory or file: a committed
			# (even dangling) link would have copyfile write outside the checkout. Exit 2.
			for name in ("yarn.lock", "source.json"):
				engine.inside(root, f"nix/node-locks/{key}/{name}")
			if os.path.lexists(dest / "yarn.lock"):
				continue
			try:
				src = data_path(f"node-locks/version-{major}/{key}")
			except ConfigError:
				continue
			for name in ("yarn.lock", "source.json"):
				rel = f"nix/node-locks/{key}/{name}"
				if not dry_run:
					dest.mkdir(parents=True, exist_ok=True)
					shutil.copyfile(src / name, dest / name)
				written.append(rel)
	return written
