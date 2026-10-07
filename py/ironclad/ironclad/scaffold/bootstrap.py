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

import ironclad
from ironclad.common import data_path, flakelock
from ironclad.common.report import ConfigError, EnvError
from ironclad.scaffold import engine

# Set on a re-exec or a dev-shell re-entry when phase A changed flake.lock, so the phase B
# that runs there still relocks the bench (step 11): it sees an unchanged lock itself.
LOCK_CHANGED = "IRONCLAD_SYNC_LOCK_CHANGED"


# The file a handed-over sync (re-exec or dev-shell re-entry) writes its exit code to, so
# the parent can tell the child's result from a failure of `nix run`/`nix develop` itself.
RESULT = "IRONCLAD_SYNC_RESULT"


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
			self.say(f"ironclad sync: would run: {shown}" + (" (skipped: --offline)" if self.offline else ""))
			return 0
		self.say(f"ironclad sync: $ {shown}")
		self.ran.append(shown)
		try:
			proc = subprocess.run(argv, cwd=self.root, env={**os.environ, **(env or {})}, check=False)
		except FileNotFoundError as e:
			raise EnvError(f"{argv[0]} is not on PATH (needed for: {shown})") from e
		if check and proc.returncode != 0:
			raise EnvError(f"`{shown}` exited {proc.returncode}")
		return proc.returncode


def override_url() -> str | None:
	"""``IRONCLAD_FRAPPE_NIX_URL``: frappe-nix's self-tests only, and only with skew allowed."""
	url = os.environ.get("IRONCLAD_FRAPPE_NIX_URL") or None
	if url and os.environ.get("IRONCLAD_ALLOW_SKEW") != "1":
		raise EnvError(
			"IRONCLAD_FRAPPE_NIX_URL is for frappe-nix's self-tests and needs IRONCLAD_ALLOW_SKEW=1"
		)
	return url


def nix_args() -> list[str]:
	url = override_url()
	return ["--override-input", "frappe-nix", url] if url else []


def lock_reasons(root: Path, inputs: dict[str, str | None], major: int) -> tuple[bool, bool]:
	"""Whether ``flake.lock`` must be (re)locked, and whether frappe-nix must move to ``release-<major>``.

	``inputs`` is ``engine.flake_input_specs`` of the rendered ``flake.nix``. The lock is stale
	when its input set differs, or when an input is locked from another URL or ``follows``
	than ``flake.nix`` now gives it: a frappe-major bump keeps the input names and changes
	``frappe``'s (and every sibling's) branch, which ``nix flake lock`` then relocks.
	frappe-nix itself is the second reason (``nix flake update frappe-nix``)."""
	path = root / "flake.lock"
	if not path.is_file():
		return True, False
	try:
		lock = flakelock.load(path)
	except EnvError:
		return True, False
	have = set(lock["nodes"].get(lock["root"], {}).get("inputs", {}))
	relock = have != set(inputs) or bool(engine.stale_inputs(lock, inputs, skip=("frappe-nix",)))
	found = flakelock.node_at(lock, ["frappe-nix"])
	ref = (found[1].get("original") or {}).get("ref") if found else None
	return relock, ref != f"release-{major}"


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
	expr = 'builtins.readFile ((builtins.fetchTree (builtins.fromJSON (builtins.getEnv "IRONCLAD_LOCKED_FRAPPE_NIX"))) + "/version.txt")'
	try:
		out = subprocess.run(
			["nix", "eval", "--raw", "--impure", "--expr", expr],
			cwd=runner.root,
			env={**os.environ, "IRONCLAD_LOCKED_FRAPPE_NIX": json.dumps(locked)},
			capture_output=True,
			text=True,
			check=True,
		)
	except (FileNotFoundError, subprocess.CalledProcessError):
		return None
	return out.stdout.strip() or None


def phase_a_lock(runner: Runner, inputs: dict[str, str | None], major: int) -> bool:
	"""Step 3: lock the flake when needed. Returns whether ``flake.lock`` changed."""
	before = (runner.root / "flake.lock").read_bytes() if (runner.root / "flake.lock").is_file() else None
	relock, move = lock_reasons(runner.root, inputs, major)
	url = override_url()
	if url:
		# The self-tests lock the checkout under test in place of release-<N>.
		runner.run(["nix", "flake", "lock", *nix_args()])
	else:
		if relock:
			runner.run(["nix", "flake", "lock"])
		if move or (relock and lock_reasons(runner.root, inputs, major)[1]):
			runner.run(["nix", "flake", "update", "frappe-nix"])
	after = (runner.root / "flake.lock").read_bytes() if (runner.root / "flake.lock").is_file() else None
	return before != after


def handover(runner: Runner, argv: list[str], env: dict[str, str]) -> NoReturn:
	"""Run a sync somewhere else (steps 4 and the phase-B re-entry) and exit with its code.

	Nix exits 1 on any evaluation, fetch or build failure, and 1 means drift (§3.3), so the
	child's own code comes back through ``IRONCLAD_SYNC_RESULT``; when the child never got
	to write it, a non-zero exit is ``nix``'s and is an environment error (3)."""
	fd, path = tempfile.mkstemp(prefix="ironclad-sync-result-")
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
	"""Step 4: phase B renders with the ironclad the lock pins, so a bootstrap from another
	release hands over to it (once)."""
	if override_url() or os.environ.get("IRONCLAD_SYNC_REEXEC") == "1":
		return
	pinned = locked_frappe_nix_version(runner)
	if pinned is None or pinned == ironclad.__version__:
		return
	runner.say(
		f"ironclad sync: flake.lock pins frappe-nix {pinned}; this is {ironclad.__version__}: re-running from the lock"
	)
	handover(
		runner,
		["nix", "run", "--no-pure-eval", ".#frappe-init", "--", "--sync", *argv],
		{"IRONCLAD_SYNC_REEXEC": "1", **lock_changed_env(lock_changed)},
	)


def ensure_tools(runner: Runner, argv: list[str], lock_changed: bool = False) -> None:
	"""Phase B needs ``uv`` and ``yarn``; without them, re-enter through the app's dev shell (once)."""
	if runner.offline or runner.dry_run or (shutil.which("uv") and shutil.which("yarn")):
		return
	if os.environ.get("IRONCLAD_SYNC_REENTERED") == "1":
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
			"ironclad",
			"sync",
			"--write",
			"--phase",
			"b",
			*argv,
		],
		{"FRAPPE_NIX_CI": "1", "IRONCLAD_SYNC_REENTERED": "1", **lock_changed_env(lock_changed)},
	)


def seed_node_locks(root: Path, major: int, siblings: list[str], *, dry_run: bool) -> list[str]:
	"""Step 10: copy ``ironclad/data/node-locks/version-<major>/<key>`` for each present app
	whose ``nix/node-locks/<key>`` has no ``yarn.lock`` yet. Returns the paths written."""
	written = []
	for app in ["frappe", *siblings]:
		for key in NODE_LOCKS.get(app, ()):
			dest = root / "nix" / "node-locks" / key
			if (dest / "yarn.lock").exists():
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
