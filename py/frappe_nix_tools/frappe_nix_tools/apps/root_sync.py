"""``frappe-nix-root-sync <bench-root>``: keep a bench's workspace root —
pyproject.toml, and uv.lock when the root moved — in step with what this
frappe-nix expects of one. Run from the dev shell's enterShell. Was bash in
lib/root-sync.nix.

A frappe-nix bump that asks something new of a bench is answered on the next
shell entry rather than by a command run from outside it. The case it was
written for: frappe-runtime became a required workspace dependency, and the
check that noticed lived at evaluation, where `nix develop` refused to open and
the fix it named had to be run from outside the shell it blocked. Evaluation
now degrades instead (modules/devenv.nix runs the split processes for that one
session) and this does the reconciling.

It is `frappe-nix-workspace ensure-root` — the call `frappe-init` makes, which
adds only what is missing and changes nothing already correct — followed by
`uv lock` when, and only when, that changed the file. Not the rest of the
reconciler: classifying apps, registering submodules and `git add -A` are a
migration's business, not a shell hook's.

Anything but a completed run — a failed lock, or a ^C in the middle of one —
puts both files back, mtimes included, so git and anything that stamps on mtime
see no change. A pyproject.toml declaring what uv.lock does not carry fails the
next evaluation (the lock audit in lib/python.nix), which is the shape this
exists to remove. The shell stays openable, this says why it could not finish,
and the next entry tries again.

uv is taken from PATH on purpose: the dev shell carries its own, and the lock
should be written by that one.

It runs on every shell entry, so it costs one interpreter, not two: the
ensure-root step runs frappe-nix-workspace's script (lib/frappe-workspace.py,
tomlkit is in this environment too) in this process, and nothing heavier than
json is imported until a lock is needed. The configuration is read by hand for
the same reason, as strictly as frappe_nix_tools.cli_config would.
"""

import json
import os
import signal
import sys
from collections.abc import Sequence

NAME = "frappe-nix-root-sync"
ENV = "FRAPPE_NIX_CLI_CONFIG"

#: The configuration lib/root-sync.nix writes: key → whether null is allowed.
KEYS = {
	# frappe-nix-workspace, whose `ensure-root` does the reconciling.
	"workspace": False,
	# templates/bench/pyproject.toml, unrendered.
	"template": False,
	# lib/build-deps.nix's sync, after the lock; null runs nothing.
	"buildDepsSync": True,
}


class ConfigError(Exception):
	pass


def parse_config(data: object) -> dict[str, str | None]:
	"""DATA as the configuration: every key, nothing else, strings (or null where
	allowed)."""
	if not isinstance(data, dict):
		raise ConfigError(f"config: expected an object, got {type(data).__name__}")
	unknown = sorted(set(data) - set(KEYS))
	missing = sorted(set(KEYS) - set(data))
	if unknown:
		raise ConfigError(f"config: unknown key(s): {', '.join(unknown)}")
	if missing:
		raise ConfigError(f"config: missing key(s): {', '.join(missing)}")
	for key, nullable in KEYS.items():
		value = data[key]
		if not (isinstance(value, str) or (nullable and value is None)):
			raise ConfigError(f"config.{key}: expected a string{' or null' if nullable else ''}")
	return data


def load_config() -> dict[str, str | None]:
	where = os.environ.get(ENV)
	if not where:
		raise ConfigError(f"{ENV} is not set; run frappe-nix-root-sync from the dev shell")
	try:
		with open(where) as f:
			return parse_config(json.load(f))
	except (OSError, ValueError) as e:
		raise ConfigError(f"cannot read {where}: {e}") from e


def _warn(*lines: str) -> None:
	sys.stdout.flush()
	for line in lines:
		print(line, file=sys.stderr, flush=True)


def _say(*lines: str) -> None:
	for line in lines:
		print(line, flush=True)


def _read(path: str) -> tuple[bytes, os.stat_result] | None:
	try:
		with open(path, "rb") as f:
			return f.read(), os.fstat(f.fileno())
	except FileNotFoundError:
		return None


def _put_back(path: str, kept: tuple[bytes, os.stat_result] | None) -> None:
	"""PATH as it was (`cp -p`: the content, the mode and the times), or gone."""
	if kept is None:
		try:
			os.unlink(path)
		except FileNotFoundError:
			pass
		return
	content, st = kept
	with open(path, "wb") as f:
		f.write(content)
	os.chmod(path, st.st_mode & 0o7777)
	os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def ensure_root(workspace: str, template: str) -> int:
	"""`frappe-nix-workspace ensure-root --pyproject pyproject.toml --template
	TEMPLATE`, in this process; its exit status. The template unrendered: its
	tokens are all inside strings, so it parses, and ensure-root reads only the
	token-free tables from it."""
	import runpy

	argv = sys.argv
	sys.argv = [workspace, "ensure-root", "--pyproject", "pyproject.toml", "--template", template]
	try:
		runpy.run_path(workspace, run_name="__main__")
	except SystemExit as e:
		code = e.code
		if code is None:
			return 0
		if isinstance(code, int):
			return code
		print(code, file=sys.stderr)
		return 1
	except Exception as e:
		_warn(f"{workspace}: {type(e).__name__}: {e}")
		return 1
	finally:
		sys.argv = argv
		sys.stdout.flush()
	return 0


def _run(cmd: Sequence[str]) -> int:
	import subprocess

	sys.stdout.flush()
	sys.stderr.flush()
	try:
		return subprocess.call(list(cmd))
	except FileNotFoundError:
		return 127


def _which(name: str) -> bool:
	return any(
		os.access(os.path.join(d or ".", name), os.X_OK) and os.path.isfile(os.path.join(d or ".", name))
		for d in os.environ.get("PATH", "").split(os.pathsep)
	)


def _terminated(_signum: int, _frame: object) -> None:
	raise KeyboardInterrupt


def sync(config: dict[str, str | None]) -> int:
	"""The root sync in the current directory, the bench root."""
	if not os.path.isfile("pyproject.toml"):
		_warn(f"{NAME}: no pyproject.toml in {os.getcwd()}")
		return 2
	kept_pyproject = _read("pyproject.toml")
	kept_lock = _read("uv.lock")
	assert kept_pyproject is not None

	completed = False
	previous = signal.signal(signal.SIGTERM, _terminated)
	try:
		if ensure_root(str(config["workspace"]), str(config["template"])) != 0:
			_warn(f"{NAME}: could not reconcile pyproject.toml — left as it was")
			return 1
		now = _read("pyproject.toml")
		if now is not None and now[0] == kept_pyproject[0]:
			completed = True
			return 0

		_say("pyproject.toml reconciled with frappe-nix (see above) — re-locking the Python workspace…")
		if not _which("uv"):
			_warn(f"{NAME}: uv is not on PATH; pyproject.toml restored")
			return 1
		# The same lock bench update and relock run, refreshing frappe-runtime's
		# git pin (frappe_nix_tools.deps.update.UV_LOCK) to the revision
		# flake.lock pins; imported only now, when a lock is needed.
		from frappe_nix_tools.deps.runtime_pin import runtime_at_pin
		from frappe_nix_tools.deps.update import UV_LOCK

		with runtime_at_pin():
			status = _run(UV_LOCK)
		if status != 0:
			_warn(
				"",
				f"{NAME}: uv lock failed — pyproject.toml and uv.lock restored, so the",
				"  shell keeps opening. Re-entering the shell retries. To resolve it by hand, make the",
				"  additions listed above in pyproject.toml and run 'uv lock' here (uv is on PATH).",
			)
			return 1
		sync_deps = config["buildDepsSync"]
		if sync_deps and _run([sync_deps]) != 0:
			return 1
		completed = True
	except KeyboardInterrupt:
		return 130
	finally:
		signal.signal(signal.SIGTERM, previous)
		if not completed:
			_put_back("pyproject.toml", kept_pyproject)
			_put_back("uv.lock", kept_lock)

	_say(
		"",
		"frappe-nix: pyproject.toml and uv.lock updated — commit them, then re-enter the shell",
		"  (direnv reload, or exit and nix develop): this one was built from the previous uv.lock.",
	)
	return 0


def main(argv: Sequence[str] | None = None) -> int:
	args = list(sys.argv[1:] if argv is None else argv)
	if len(args) != 1:
		_warn(f"usage: {NAME} <bench-root>")
		return 2
	try:
		config = load_config()
	except ConfigError as e:
		_warn(f"{NAME}: {e}")
		return 2
	try:
		os.chdir(args[0])
	except OSError as e:
		_warn(f"{NAME}: {args[0]}: {e.strerror}")
		return 2
	return sync(config)


if __name__ == "__main__":
	sys.exit(main())
