"""What frappe-test, frappe-demo and frappe-shots share: running commands in the
dev shell, the bench's processes, and the guard on dropping an existing site.

Process semantics follow the bash these replace (PLAN.md, Track D): output that
is not captured streams through unchanged; a step's failure is the caller's to
handle, as the bash checked each one; Ctrl-C reaches the child from the terminal
along with this process, which waits for the child to finish with it, then stops
with exit 130 (running the caller's cleanup on the way, as a bash EXIT trap did).
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

#: The bench's app processes. Provisioning runs with them stopped: each start of
#: the runtime (or of `web`, without it) re-runs the frappe:apps-reconcile task,
#: and once new-site has made the site's directory that task installs the same
#: apps alongside provision-site, which then fails on a duplicate Module Def or
#: Role. Started again afterwards, the task finds every app installed.
APP_PROCESSES = ("runtime", "web", "worker", "scheduler", "socketio")

#: Counted, not timed: 150 tries two seconds apart.
TRIES = 150


class Stop(Exception):
	"""Stop the tool with CODE; whatever had to be said has been said."""

	def __init__(self, code: int) -> None:
		super().__init__(code)
		self.code = code


def flush() -> None:
	sys.stdout.flush()
	sys.stderr.flush()


def say(text: str) -> None:
	print(text, flush=True)


def warn(text: str) -> None:
	sys.stdout.flush()
	print(text, file=sys.stderr, flush=True)


def _wait(proc: subprocess.Popen) -> int:
	try:
		return proc.wait()
	except KeyboardInterrupt:
		try:
			proc.wait()
		except KeyboardInterrupt:
			proc.kill()
			proc.wait()
		raise Stop(130) from None


def call(
	cmd: Sequence[str],
	*,
	cwd: str | os.PathLike[str] | None = None,
	env: Mapping[str, str] | None = None,
	stdin_text: str | None = None,
	quiet: bool = False,
	no_stdout: bool = False,
) -> int:
	"""Run CMD, its output streaming through (none with QUIET, no stdout with
	NO_STDOUT); its exit status, 127 when it cannot be started."""
	flush()
	try:
		proc = subprocess.Popen(
			list(cmd),
			cwd=cwd,
			env=None if env is None else dict(env),
			stdin=subprocess.PIPE if stdin_text is not None else None,
			stdout=subprocess.DEVNULL if quiet or no_stdout else None,
			stderr=subprocess.DEVNULL if quiet else None,
			text=True,
		)
	except OSError as e:
		if not quiet:
			warn(f"{cmd[0]}: {e.strerror}")
		return 127
	if stdin_text is not None and proc.stdin is not None:
		try:
			proc.stdin.write(stdin_text)
			proc.stdin.close()
		except BrokenPipeError:
			pass
	return _wait(proc)


def ok(cmd: Sequence[str], *, cwd: str | os.PathLike[str] | None = None) -> bool:
	"""CMD succeeds, its output discarded: a question, not a step."""
	return call(cmd, cwd=cwd, quiet=True) == 0


def capture(
	cmd: Sequence[str],
	*,
	cwd: str | os.PathLike[str] | None = None,
	env: Mapping[str, str] | None = None,
) -> tuple[int, str]:
	"""CMD's exit status and its stdout without the trailing newlines, as
	``$(…)`` has it; its stderr streams through."""
	flush()
	try:
		proc = subprocess.Popen(
			list(cmd),
			cwd=cwd,
			env=None if env is None else dict(env),
			stdout=subprocess.PIPE,
			text=True,
		)
	except OSError as e:
		warn(f"{cmd[0]}: {e.strerror}")
		return 127, ""
	try:
		out, _ = proc.communicate()
	except KeyboardInterrupt:
		_wait(proc)
		raise Stop(130) from None
	return proc.returncode, out.rstrip("\n")


def jq_raw(value: Any) -> str:
	"""VALUE as ``jq -r`` prints a scalar."""
	if isinstance(value, str):
		return value
	return json.dumps(value)


def alt(value: Any, fallback: Any) -> Any:
	"""jq's ``VALUE // FALLBACK``: FALLBACK when VALUE is null or false."""
	return fallback if value is None or value is False else value


def truthy(value: Any) -> bool:
	"""jq's truth: everything but null and false."""
	return value is not None and value is not False


#: The Administrator password the tools give a site, and its name before CONVENTIONS.md's
#: FRAPPE_NIX_* rule (python-devops-nix, Tool-owned variables), still read with a warning
#: through 1.x, until frappe-nix 2.0.0.
ENV_ADMIN_PASSWORD = "FRAPPE_NIX_TEST_ADMIN_PASSWORD"
OLD_ENV_ADMIN_PASSWORD = "FRAPPE_TEST_ADMIN_PASSWORD"


def admin_password() -> str:
	value = os.environ.get(ENV_ADMIN_PASSWORD)
	if value is None and OLD_ENV_ADMIN_PASSWORD in os.environ:
		print(
			f"{OLD_ENV_ADMIN_PASSWORD} is deprecated; set {ENV_ADMIN_PASSWORD} instead"
			" (the old name goes in frappe-nix 2.0.0)",
			file=sys.stderr,
		)
		value = os.environ[OLD_ENV_ADMIN_PASSWORD]
	return value or "admin"


def repo_root() -> str | None:
	"""The app's repository: $DEVENV_ROOT, else the git work tree around the
	working directory; None when there is neither."""
	root = os.environ.get("DEVENV_ROOT", "")
	if root:
		return root
	rc, out = capture(["git", "rev-parse", "--show-toplevel"])
	return out if rc == 0 and out else None


class Compose:
	"""The dev shell's process-compose, over its socket (which may be unset)."""

	def __init__(self, socket: str) -> None:
		self.socket = socket

	@classmethod
	def from_env(cls) -> "Compose":
		return cls(os.environ.get("PC_SOCKET_PATH", ""))

	def cmd(self, *args: str) -> list[str]:
		return ["process-compose", "-U", "-u", self.socket, *args]

	def up(self) -> bool:
		"""The bench is up: process-compose answers on its socket."""
		return bool(self.socket) and ok(self.cmd("process", "list"))

	def down(self) -> None:
		call(self.cmd("down"), quiet=True)

	def stop_app_processes(self) -> list[str]:
		"""Stop those of APP_PROCESSES that exist (one between restarts is
		present, but `stop` refuses it); the names it stopped."""
		stopped: list[str] = []
		if not self.socket:
			return stopped
		for proc in APP_PROCESSES:
			if ok(self.cmd("process", "get", proc)):
				call(self.cmd("process", "stop", proc), quiet=True)
				stopped.append(proc)
		return stopped

	def start(self, procs: Sequence[str]) -> None:
		for proc in procs:
			call(self.cmd("process", "start", proc), quiet=True)


def devenv_up(*, cwd: str | None = None) -> int:
	"""``devenv up -D``, detached, without the TUI."""
	env = {**os.environ, "DEVENV_IN_DIRENV_SHELL": "true", "PC_TUI_ENABLED": "0"}
	return call(["devenv", "up", "-D"], cwd=cwd, env=env)


def db_answers() -> bool:
	return ok(
		[
			"mariadb-admin",
			f"--socket={os.environ.get('FRAPPE_DB_SOCKET', '')}",
			"--connect-timeout=3",
			"ping",
		]
	)


def ports_file() -> Path | None:
	"""Where `devenv up` records the ports it leased and the bench's identity
	($FRAPPE_NIX_PORTS_FILE); None in a shell that does not lease them."""
	path = os.environ.get("FRAPPE_NIX_PORTS_FILE", "")
	return Path(path) if path else None


def leased(path: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
	"""ports.json while the holder that wrote it runs; None before `devenv up`,
	after the bench stops, or when a killed holder left it behind."""
	path = path if path is not None else ports_file()
	if path is None:
		return None
	try:
		data = json.loads(Path(path).read_text())
	except (OSError, ValueError):
		return None
	if not isinstance(data, dict):
		return None
	pid = data.get("pid")
	if not isinstance(pid, int) or pid <= 0:
		return None
	try:
		os.kill(pid, 0)
	except ProcessLookupError:
		return None
	except PermissionError:
		pass
	return data


def bench_id() -> str | None:
	"""This bench's identity, as its nginx answers it at /__frappe_nix/bench;
	None in a shell that does not lease ports, before the lease is up, or when
	nothing on the web port serves it (sockets off: no nginx, ports.json's
	identity false)."""
	data = leased()
	value = data.get("bench_id") if data and data.get("identity", True) else None
	return value if isinstance(value, str) and value else None


def webserver_port(bench: str | os.PathLike[str]) -> str:
	"""The port the bench serves on: the leased one in a shell that leases
	ports ("" until `devenv up` has), else ``.webserver_port // empty`` of the
	bench's common_site_config.json, "" when that is missing or unreadable."""
	if ports_file() is not None:
		data = leased()
		value = data.get("web") if data else None
		return str(value) if isinstance(value, int) else ""
	try:
		data = json.loads((Path(bench) / "sites" / "common_site_config.json").read_text())
	except (OSError, ValueError):
		return ""
	value = data.get("webserver_port") if isinstance(data, dict) else None
	return "" if value is None or value is False else jq_raw(value)


def web_answers(port: str, expect: str | None = None) -> bool:
	"""Any HTTP answer on the port is "up": nginx says 404 until the site exists.
	With EXPECT, the answer has to come from that bench: its nginx serves its
	identity at /__frappe_nix/bench, so a neighbour's on the same port does not
	count. Straight to loopback, past any proxy the environment names."""
	opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
	if expect is not None:
		try:
			with opener.open(f"http://127.0.0.1:{port}/__frappe_nix/bench", timeout=3) as resp:
				return resp.read().decode(errors="replace").strip() == expect
		except (OSError, ValueError):
			return False
	try:
		with opener.open(f"http://127.0.0.1:{port}/", timeout=3):
			return True
	except urllib.error.HTTPError:
		return True
	except (OSError, ValueError):
		return False


def who(port: str) -> str:
	"""`frappe-nix-ports who PORT`: the bench, or the kind of process, holding
	PORT, for an environment failure's message; "" without the command."""
	if not port or shutil.which("frappe-nix-ports") is None:
		return ""
	rc, out = capture(["frappe-nix-ports", "who", port])
	return out if rc == 0 else ""


def provision(site: str, compose: Compose) -> bool:
	"""``provision-site`` for SITE, with the app processes stopped meanwhile.

	bench new-site asks for the MariaDB root password through getpass, which
	reads a line from stdin without a tty; the dev bench's root has none.
	provision-site creates $FRAPPE_SITE: it is pointed at SITE.
	"""
	stopped = compose.stop_app_processes()
	rc = call(
		["provision-site", admin_password()],
		env={**os.environ, "FRAPPE_SITE": site},
		stdin_text="\n",
	)
	compose.start(stopped)
	return rc == 0


def has_terminal() -> bool:
	"""A terminal to ask on: stdin to read the answer, stderr to ask."""
	return sys.stdin.isatty() and sys.stderr.isatty()


def ask_recreate(question: str) -> bool:
	"""QUESTION, then `  Recreate it? [y/N] ` on stderr; whether the answer is yes.
	As `read -r` read it: one line, the blanks around it dropped."""
	sys.stdout.flush()
	sys.stderr.write(f"{question}\n  Recreate it? [y/N] ")
	sys.stderr.flush()
	try:
		answer = sys.stdin.readline()
	except KeyboardInterrupt:
		raise Stop(130) from None
	return answer.strip(" \t\n") in ("y", "Y", "yes", "YES")


def faked_env(env: Mapping[str, str]) -> dict[str, str]:
	"""ENV, on frappe-shots' fake clock when it set one (FRAPPE_NIX_SHOTS_FAKETIME).

	Without libfaketime's shared-memory clock, whose process-shared semaphore can
	deadlock a process. libfaketime reads FAKETIME in the process's zone, so
	TZ=UTC makes it the same instant on every host.
	"""
	out = dict(env)
	faketime = env.get("FRAPPE_NIX_SHOTS_FAKETIME", "")
	if faketime:
		preload = env.get("FRAPPE_NIX_SHOTS_PRELOAD", "")
		if env.get("LD_PRELOAD"):
			preload = f"{preload}:{env['LD_PRELOAD']}"
		out.update(
			LD_PRELOAD=preload,
			FAKETIME=faketime,
			FAKETIME_DONT_FAKE_MONOTONIC="1",
			FAKETIME_DISABLE_SHM="1",
			TZ="UTC",
		)
	return out


def node_json(script: str, path: str) -> Any:
	"""What the ES module SCRIPT prints for PATH (its argv[1]), as JSON; None when
	node fails or prints something else."""
	rc, out = capture(["node", "--input-type=module", "-e", script, path])
	if rc != 0:
		return None
	try:
		return json.loads(out)
	except ValueError:
		return None
