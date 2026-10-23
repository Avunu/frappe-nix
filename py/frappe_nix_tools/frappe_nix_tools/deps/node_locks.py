"""``frappe-nix-node-locks``: write node-locks/<target>/yarn.lock, the fallback
lock for a node target that ships no yarn.lock of its own. Was bash in
lib/node-locks.nix; tests/node-locks.sh is its
acceptance suite.

The rule is that an app's node_modules is built from the app's yarn.lock
(lib/yarn-lock.nix, lib/bench.nix), and nothing about it is committed to the
bench. This tool exists for the exceptions. An app whose upstream never
committed a lock gets one resolved here from its package.json — ``yarn
install`` in a scratch directory holding nothing but the manifests, and the
yarn.lock it writes is the fallback — so that the build has something to pin
to. An app whose upstream lock is broken (a dependency bump that never
regenerated the transitive entries, leaving a range no entry satisfies) can be
*forced*: named explicitly, it gets a fallback resolved with its own yarn.lock
as the seed, so upstream's pins stay and only the gap is filled, and the stamp
records that the override was deliberate. The build then takes the fallback
over the upstream lock for that target, and only that one.

Committed, because the sandbox has no network and a bench's apps are
submodules whose trees are pinned. The stamp (source.json) is what keeps
regeneration cheap and honest: a target whose manifests have not moved costs
nothing, one whose manifests have is re-resolved, and the previous fallback is
the seed so only what has to move does. Its bytes are jq -S's pretty print, as
the bash wrote it, so a bench's committed stamps do not churn.

yarn is taken from PATH on purpose: the dev shell pins its own nodejs and yarn
(``frappe-nix.nodejs``), and ``relock`` puts the same on PATH, so the lock is
resolved by the yarn that will install it.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

NAME = "frappe-nix-node-locks"
DEFAULT_COMMAND = "bench-update --node-locks"

USAGE = (
	f"usage: {NAME} [--exclude=<app/subdir>]... [--command=<how to invoke me>] <bench-root> <locks-dir> [<target>...]",
	"  no targets: every app and nested frontend under <bench-root>/apps without a yarn.lock",
	"  of its own (plus the forced locks already in <locks-dir>), and <locks-dir> is pruned",
	"  to the targets that exist; with targets: exactly those, and one that ships a yarn.lock",
	"  gets a forced lock that overrides it",
)

#: The same script, engine and platform flags as the sandbox install, so what
#: resolves here is what installs there; devDependencies included, which is
#: where every vite frontend keeps its build tooling.
YARN = (
	"yarn",
	"install",
	"--ignore-scripts",
	"--ignore-engines",
	"--ignore-platform",
	"--production=false",
	"--non-interactive",
	"--no-progress",
)

_SUBMODULE_PATH = re.compile(r"^\s*path\s*=\s*(\S+)\s*$")
_KEY = re.compile(r"^[^ \t#].*:[ \t]*$")
_GITISH = re.compile(r"^(git\+|git:|ssh:)")
_DOT_GIT = re.compile(r"\.git(#.*)?$")
_CODELOAD = re.compile(r"^https://codeload\.github\.com/")
_PINNED = re.compile(r"#[0-9a-f]+$")
_CODELOAD_PINNED = re.compile(r"codeload\.github\.com/[^/]+/[^/]+/tar\.gz/[0-9a-f]+$")


class Usage(Exception):
	pass


def _out(*lines: str) -> None:
	for line in lines:
		print(line, flush=True)


def _err(*lines: str) -> None:
	sys.stdout.flush()
	for line in lines:
		print(line, file=sys.stderr, flush=True)


@dataclass
class Args:
	bench: str
	locks: str
	targets: list[str]
	excludes: list[str] = field(default_factory=list)
	command: str = DEFAULT_COMMAND


def parse(argv: Sequence[str]) -> Args:
	"""Flags first, then <bench-root> <locks-dir> [<target>...]: a word after the
	first positional is a target whatever it looks like, as the bash had it."""
	excludes: list[str] = []
	command = DEFAULT_COMMAND
	words = list(argv)
	while words:
		word = words[0]
		if word.startswith("--exclude="):
			excludes.append(word.removeprefix("--exclude="))
			words.pop(0)
		elif word == "--exclude":
			if len(words) < 2:
				raise Usage
			excludes.append(words[1])
			del words[:2]
		elif word.startswith("--command="):
			command = word.removeprefix("--command=")
			words.pop(0)
		elif word in ("-h", "--help"):
			raise Usage
		elif word == "--":
			words.pop(0)
			break
		elif word.startswith("-"):
			raise Usage(f"{NAME}: unknown flag {word}")
		else:
			break
	if len(words) < 2:
		raise Usage
	return Args(words[0], words[1], words[2:], excludes, command)


def unbuildable(lock: str) -> list[str]:
	"""One line per entry of LOCK the Nix build could not fetch: the rules
	lib/yarn-lock.nix throws on, checked here where the fix is cheap."""
	bad: list[str] = []
	key = ""
	resolved = ""
	integ = False

	def flush() -> None:
		if not key:
			return
		if re.search(r"@(file|link):", key):
			bad.append(f"{key}: a file:/link: dependency, which the Nix build cannot fetch")
		elif not resolved:
			bad.append(f"{key}: no resolved URL")
		elif _GITISH.search(resolved) or _DOT_GIT.search(resolved) or _CODELOAD.search(resolved):
			if not _PINNED.search(resolved) and not _CODELOAD_PINNED.search(resolved):
				bad.append(f"{key}: a git dependency without a commit: {resolved}")
		elif resolved.startswith("https://") and not integ and "#" not in resolved:
			bad.append(f"{key}: no integrity and no #hash on {resolved}")

	for line in lock.split("\n"):
		if _KEY.match(line):
			flush()
			key = re.sub(r":[ \t]*$", "", line)
			resolved = ""
			integ = False
		elif line.startswith("  resolved "):
			fields = line.split()
			resolved = fields[1].replace('"', "") if len(fields) > 1 else ""
		elif line.startswith("  integrity "):
			integ = True
	flush()
	return bad


def _sha256(path: str) -> str:
	with open(path, "rb") as f:
		return hashlib.sha256(f.read()).hexdigest()


def stamp(src: str, forced: bool) -> dict[str, object]:
	"""What a fallback is resolved from, hashed, and whether it was forced over
	an upstream lock. lib/bench.nix reads the same keys to warn about a fallback
	older than its manifests."""
	want: dict[str, object] = {"package.json": _sha256(f"{src}/package.json")}
	if os.path.isfile(f"{src}/yarn.lock"):
		want["yarn.lock"] = _sha256(f"{src}/yarn.lock")
	if forced:
		want["forced"] = True
	return want


def stamp_text(want: dict[str, object]) -> str:
	"""`jq -S .`: two-space indent, sorted keys, a final newline."""
	return json.dumps(want, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _read_stamp(path: str) -> object:
	try:
		with open(path) as f:
			return json.load(f)
	except (OSError, ValueError):
		return None


def _wait(proc: subprocess.Popen) -> int:
	try:
		return proc.wait()
	except KeyboardInterrupt:
		import signal

		if proc.poll() is None:
			proc.send_signal(signal.SIGINT)
		proc.wait()
		raise


def _listdir(path: str) -> list[str]:
	"""A bash glob's ``path/*``: hidden names left out, in order."""
	try:
		return sorted(n for n in os.listdir(path) if not n.startswith("."))
	except OSError:
		return []


def _remove(path: str) -> None:
	try:
		os.remove(path)
	except FileNotFoundError:
		pass


class Generator:
	def __init__(self, args: Args) -> None:
		self.args = args
		# A relative locks dir is relative to the bench root, like everything
		# else here; the working directory is the bench root.
		self.locks = os.path.join(os.getcwd(), args.locks)
		self.label = os.path.basename(self.locks.rstrip("/"))
		self.failed: list[str] = []

	def _submodule_paths(self, app: str) -> set[str]:
		"""``path = <subdir>`` lines of the app's own .gitmodules: a subdirectory
		the app tracks as a submodule is a project of its own, not its
		frontend."""
		try:
			with open(f"apps/{app}/.gitmodules") as f:
				return {m.group(1) for line in f if (m := _SUBMODULE_PATH.match(line.rstrip("\n")))}
		except OSError:
			return set()

	def discover(self) -> Iterator[str]:
		"""The same rule as lib/node-targets.nix, over the tree as it is on disk."""
		for app in _listdir("apps"):
			if not os.path.exists(f"apps/{app}/package.json"):
				continue
			yield app
			subs = None
			for sub in _listdir(f"apps/{app}"):
				if sub == "node_modules" or not os.path.exists(f"apps/{app}/{sub}/package.json"):
					continue
				if subs is None:
					subs = self._submodule_paths(app)
				if sub in subs or f"{app}/{sub}" in self.args.excludes:
					continue
				yield f"{app}/{sub}"

	def is_forced(self, key: str) -> bool:
		"""The committed stamp's word."""
		data = _read_stamp(f"{self.locks}/{key}/source.json")
		return isinstance(data, dict) and data.get("forced") in (True, "true")

	def _fail(self, key: str, *lines: str) -> None:
		_err(*lines)
		self.failed.append(key)

	def _resolve(
		self, key: str, src: str, dst: str, forced: bool, want: dict[str, object], work: str
	) -> None:
		# Followed: in app mode the apps are symlink mirrors of their flake inputs.
		# Contents only, not modes: a store path's files are 0444, and yarn has to
		# rewrite the seed it is handed.
		shutil.copyfile(f"{src}/package.json", f"{work}/package.json")
		for name in (".yarnrc", ".npmrc"):
			if os.path.isfile(f"{src}/{name}"):
				shutil.copyfile(f"{src}/{name}", f"{work}/{name}")
		# The seed: upstream's own lock when forcing (its pins stay, the gap is
		# what gets resolved), else the previous fallback (only what must move
		# does). yarn keeps every entry the manifest still wants.
		if forced and os.path.isfile(f"{src}/yarn.lock"):
			shutil.copyfile(f"{src}/yarn.lock", f"{work}/yarn.lock")
			_out(
				f"  resolving {key} (forced: seeded from apps/{key}/yarn.lock, gaps filled from the registry)…"
			)
		elif os.path.isfile(f"{dst}/yarn.lock"):
			shutil.copyfile(f"{dst}/yarn.lock", f"{work}/yarn.lock")
			_out(
				f"  resolving {key} (from package.json, seeded from the previous {self.label}/{key}/yarn.lock)…"
			)
		else:
			_out(f"  resolving {key} (from package.json — no yarn.lock upstream)…")

		# Everything installs into the scratch directory and is thrown away; the
		# lock is the product. NODE_ENV=production would drop devDependencies.
		env = {k: v for k, v in os.environ.items() if k != "NODE_ENV"}
		log_path = f"{work}/yarn.log"
		with open(log_path, "wb") as log:
			try:
				status = _wait(
					subprocess.Popen(
						YARN, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log
					)
				)
			except OSError as e:
				log.write(f"yarn: {e.strerror}\n".encode())
				status = 127
		if status != 0:
			with open(log_path, errors="replace") as f:
				said = [line.rstrip("\n") for line in f if re.match(r"^(error|warning)", line)]
			self._fail(key, f"  ✗ {key}: yarn could not resolve a lock:", *said[-20:])
			return
		if not os.path.isfile(f"{work}/yarn.lock"):
			self._fail(key, f"  ✗ {key}: yarn install wrote no yarn.lock")
			return

		with open(f"{work}/yarn.lock", errors="surrogateescape") as f:
			bad = unbuildable(f.read())
		if bad:
			self._fail(key, f"  ✗ {key}: entries the Nix build cannot fetch:", *(f"     {b}" for b in bad))
			return

		os.makedirs(dst, exist_ok=True)
		# Leftovers of the npm-based scheme this replaced.
		for name in ("package-lock.json", "package.json"):
			_remove(f"{dst}/{name}")
		shutil.copyfile(f"{work}/yarn.lock", f"{dst}/yarn.lock")
		os.chmod(f"{dst}/yarn.lock", 0o644)
		# Last: an interrupted run is retried next time, not remembered as done.
		with open(f"{dst}/source.json", "w") as f:
			f.write(stamp_text(want))
		if forced:
			_out(f"  + {self.label}/{key} (forced over apps/{key}/yarn.lock)")
		else:
			_out(f"  + {self.label}/{key}")

	def generate(self, key: str, forced: bool) -> None:
		src = f"apps/{key}"
		dst = f"{self.locks}/{key}"
		if not os.path.isfile(f"{src}/package.json"):
			self._fail(key, f"  ✗ {key}: no apps/{key}/package.json")
			return

		want = stamp(src, forced)
		if os.path.isfile(f"{dst}/yarn.lock") and _read_stamp(f"{dst}/source.json") == want:
			return

		work = tempfile.mkdtemp()
		try:
			self._resolve(key, src, dst, forced, want, work)
		finally:
			shutil.rmtree(work, ignore_errors=True)

	def named(self, targets: Sequence[str]) -> None:
		"""Resolve each, over the app's own lock if it has one: the only way a
		forced lock comes to be."""
		for key in targets:
			self.generate(key, os.path.isfile(f"apps/{key}/yarn.lock"))

	def everything(self) -> None:
		targets = list(self.discover())
		for key in targets:
			dst = f"{self.locks}/{key}"
			if not os.path.isfile(f"apps/{key}/yarn.lock"):
				self.generate(key, False)
			elif self.is_forced(key):
				self.generate(key, True)
			elif os.path.isfile(f"{dst}/yarn.lock"):
				_out(
					f"  ~ {self.label}/{key} is unused — apps/{key} ships a yarn.lock and builds from it;"
					f" git rm -r {self.label}/{key}, or force it: {self.args.command} {key}"
				)
			elif any(
				os.path.isfile(f"{dst}/{n}") for n in ("package-lock.json", "package.json", "source.json")
			):
				for name in ("package-lock.json", "package.json", "source.json"):
					_remove(f"{dst}/{name}")
				_out(f"  - {self.label}/{key} (npm-based lock; apps/{key} builds from its own yarn.lock now)")
		self.prune(set(targets))

	def prune(self, targets: set[str]) -> None:
		if not os.path.isdir(self.locks):
			return
		holders = sorted(
			{
				os.path.relpath(parent, self.locks)
				for parent, _dirs, files in os.walk(self.locks)
				if parent != self.locks and ({"yarn.lock", "package-lock.json"} & set(files))
			}
		)
		for rel in holders:
			if rel not in targets:
				shutil.rmtree(f"{self.locks}/{rel}", ignore_errors=True)
				_out(f"  - {self.label}/{rel} (no longer a target)")
		# Bottom up, so a directory whose only children were empty goes too.
		for parent, _dirs, _files in os.walk(self.locks, topdown=False):
			if parent != self.locks and not os.listdir(parent):
				os.rmdir(parent)


def main(argv: Sequence[str] | None = None) -> int:
	try:
		args = parse(sys.argv[1:] if argv is None else argv)
	except Usage as e:
		if e.args:
			_err(str(e))
		_err(*USAGE)
		return 2
	try:
		os.chdir(args.bench)
	except OSError as e:
		_err(f"{NAME}: {args.bench}: {e.strerror}")
		return 1
	gen = Generator(args)
	try:
		if args.targets:
			gen.named(args.targets)
		else:
			gen.everything()
	except KeyboardInterrupt:
		return 130
	if gen.failed:
		_err(f"node locks NOT regenerated for: {' '.join(gen.failed)}")
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
