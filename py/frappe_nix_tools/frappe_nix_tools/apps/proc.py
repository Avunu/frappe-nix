"""Running git, uv, bench and frappe-nix-workspace for the apps commands.

The bash they replace ran under ``set -euo pipefail``; here that is spelled out
per call (PLAN.md, Track D, process semantics):

-   ``run`` is a step whose failure stops the command with the step's own exit
    status (``set -e``); the step says why on stderr itself;
-   ``attempt`` is a step whose failure the caller handles: its status;
-   ``output`` and ``probe`` ask a question with stdout captured.

Output that is not captured streams through unchanged. Ctrl-C reaches the child
from the terminal along with this process: the command waits for the child to
finish with it, then exits 130.
"""

import os
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from frappe_nix_tools.cli_config import ConfigError

if TYPE_CHECKING:
	from frappe_nix_tools.apps.config import AppsConfig


class Abort(Exception):
	"""Stop with these lines on stderr (none: the step said why) and this status."""

	def __init__(self, status: int, *lines: str) -> None:
		super().__init__(lines[0] if lines else f"exit {status}")
		self.status = status
		self.lines = lines


def fail(*lines: str, status: int = 1) -> NoReturn:
	raise Abort(status, *lines)


def say(*lines: str) -> None:
	"""LINES on stdout, flushed ahead of whatever a child prints next."""
	for line in lines:
		print(line, flush=True)


def warn(*lines: str) -> None:
	"""LINES on stderr."""
	sys.stdout.flush()
	for line in lines:
		print(line, file=sys.stderr, flush=True)


def _wait(proc: subprocess.Popen) -> tuple[str | None, int]:
	try:
		out, _ = proc.communicate()
	except KeyboardInterrupt:
		# The terminal sent SIGINT to the whole foreground group; if it came
		# from elsewhere, pass it on. Let the child finish with it either way.
		if proc.poll() is None:
			proc.send_signal(signal.SIGINT)
		proc.wait()
		raise Abort(130) from None
	return out, proc.returncode


def attempt(
	cmd: Sequence[str],
	*,
	cwd: str | os.PathLike[str] | None = None,
	env: Mapping[str, str] | None = None,
	quiet: bool = False,
	stdin: int | None = None,
) -> int:
	"""Run CMD with its output streaming through (none with QUIET); its status.
	STDIN is the child's (subprocess.DEVNULL: none), the terminal by default."""
	sys.stdout.flush()
	sys.stderr.flush()
	sink = subprocess.DEVNULL if quiet else None
	try:
		proc = subprocess.Popen(
			list(cmd),
			cwd=cwd,
			env=dict(env) if env is not None else None,
			stdin=stdin,
			stdout=sink,
			stderr=sink,
		)
	except FileNotFoundError:
		warn(f"{cmd[0]}: command not found")
		return 127
	return _wait(proc)[1]


def run(
	cmd: Sequence[str],
	*,
	cwd: str | os.PathLike[str] | None = None,
	env: Mapping[str, str] | None = None,
) -> None:
	"""Run CMD; stop the command with its status when it fails."""
	status = attempt(cmd, cwd=cwd, env=env)
	if status != 0:
		raise Abort(status)


def probe(
	cmd: Sequence[str],
	*,
	cwd: str | os.PathLike[str] | None = None,
) -> tuple[int, str]:
	"""CMD's status and stdout, stderr discarded; never fails."""
	sys.stdout.flush()
	try:
		proc = subprocess.Popen(
			list(cmd),
			cwd=cwd,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.PIPE,
			stderr=subprocess.DEVNULL,
			text=True,
		)
	except OSError:
		return 127, ""
	out, status = _wait(proc)
	return status, out or ""


def output(
	cmd: Sequence[str],
	*,
	cwd: str | os.PathLike[str] | None = None,
) -> str:
	"""CMD's stdout (stderr streams through); stop with its status when it fails."""
	sys.stdout.flush()
	sys.stderr.flush()
	try:
		proc = subprocess.Popen(list(cmd), cwd=cwd, stdout=subprocess.PIPE, text=True)
	except FileNotFoundError:
		fail(f"{cmd[0]}: command not found", status=127)
	out, status = _wait(proc)
	if status != 0:
		raise Abort(status)
	return out or ""


def which(name: str) -> str | None:
	"""``command -v NAME``: NAME's path on PATH, or None."""
	for directory in os.environ.get("PATH", "").split(os.pathsep):
		candidate = Path(directory or ".", name)
		if candidate.is_file() and os.access(candidate, os.X_OK):
			return str(candidate)
	return None


def bench_root() -> Path:
	"""$FRAPPE_BENCH_ROOT, which every bench command runs from (lib/scripts.nix,
	atBench: the bench CLI finds its bench by walking up from cwd)."""
	root = os.environ.get("FRAPPE_BENCH_ROOT")
	if not root:
		fail("FRAPPE_BENCH_ROOT is not set: run this from the bench's dev shell")
	return Path(root)


def apps_config() -> "AppsConfig":
	"""The apps section of the file FRAPPE_NIX_CLI_CONFIG names."""
	from frappe_nix_tools.bench_config import load

	return load().apps


def main_guard(body: Callable[[], int]) -> int:
	"""Run BODY; an Abort is its lines on stderr and its status, and a bad
	configuration (a mismatch between frappe-nix's Nix and Python) exit 1."""
	try:
		return body()
	except ConfigError as e:
		warn(f"frappe-nix: {e}")
		return 1
	except Abort as e:
		if e.lines:
			warn(*e.lines)
		return e.status
	except KeyboardInterrupt:
		return 130
	finally:
		sys.stdout.flush()
