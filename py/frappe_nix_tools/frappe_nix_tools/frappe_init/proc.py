"""Running git, uv, frappe-nix-workspace and frappe-nix.

The bash ran under ``set -euo pipefail``; here that is spelled out per call:

-   ``run`` is a step whose failure stops frappe-init with the step's own exit
    status, after the step has said why on stderr (ui.Exit);
-   ``attempt`` is a step whose failure the caller handles, and returns its
    status;
-   ``probe`` and ``probe_ok`` ask a question (stdout captured, stderr
    discarded) and never fail.

Output that is not captured streams through unchanged. Ctrl-C reaches the
child from the terminal along with this process: frappe-init waits for the
child to finish with it, then exits 130.
"""

import signal
import subprocess
import sys
from collections.abc import Sequence

from frappe_nix_tools.frappe_init.ui import Exit


def _wait(proc: subprocess.Popen) -> int:
	try:
		return proc.wait()
	except KeyboardInterrupt:
		# The terminal sent SIGINT to the whole foreground group; let the child
		# act on it, then stop with the conventional status.
		try:
			proc.wait()
		except KeyboardInterrupt:
			proc.send_signal(signal.SIGKILL)
			proc.wait()
		raise Exit(130) from None


def attempt(cmd: Sequence[str], *, quiet_stdout: bool = False) -> int:
	"""Run CMD with its output streaming through; its exit status."""
	sys.stdout.flush()
	sys.stderr.flush()
	try:
		proc = subprocess.Popen(list(cmd), stdout=subprocess.DEVNULL if quiet_stdout else None)
	except FileNotFoundError:
		print(f"frappe-init: {cmd[0]}: command not found", file=sys.stderr, flush=True)
		return 127
	return _wait(proc)


def run(cmd: Sequence[str], *, quiet_stdout: bool = False) -> None:
	"""Run CMD; stop frappe-init with its status when it fails."""
	rc = attempt(cmd, quiet_stdout=quiet_stdout)
	if rc != 0:
		raise Exit(rc)


def output(cmd: Sequence[str]) -> str:
	"""CMD's stdout; stop frappe-init with its status when it fails (stderr streams)."""
	sys.stdout.flush()
	try:
		proc = subprocess.run(list(cmd), stdout=subprocess.PIPE, text=True, check=False)
	except FileNotFoundError:
		print(f"frappe-init: {cmd[0]}: command not found", file=sys.stderr, flush=True)
		raise Exit(127) from None
	if proc.returncode != 0:
		raise Exit(proc.returncode)
	return proc.stdout


def probe(cmd: Sequence[str]) -> str | None:
	"""CMD's stdout without its trailing newlines, or None when it fails."""
	try:
		proc = subprocess.run(list(cmd), capture_output=True, text=True, check=False)
	except OSError:
		return None
	if proc.returncode != 0:
		return None
	return proc.stdout.rstrip("\n")


def probe_ok(cmd: Sequence[str]) -> bool:
	try:
		return subprocess.run(list(cmd), capture_output=True, check=False).returncode == 0
	except OSError:
		return False


def git(*args: str, cwd: str | None = None) -> list[str]:
	"""A git command line, run in CWD (``git -C``) when given."""
	return ["git", *(["-C", cwd] if cwd else []), *args]
