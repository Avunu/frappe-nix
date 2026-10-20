#!/usr/bin/env python3
"""Lease a development bench's TCP ports for as long as the bench runs.

Each port the dev shell evaluates (nginx's, Mailpit's) is where the walk
starts, not the port itself: devenv's port allocator is the identity under its
flake integration, so a port fixed at evaluation stays taken when another bench
holds it. Two checkouts of one app hash to the same ports, and on a CI host that
runs several jobs at once they did (five failed jobs in an evening, every one
"already in use: nginx=127.0.0.1:8234").

`hold` runs as the bench's first process, frappe-ports, and for each port walks
from the preferred one to preferred + span - 1:

  1. Claim it host-wide. On Linux that is a listening unix socket in the
     abstract namespace, `@frappe-nix/port/<port>`: it lives in the network
     namespace, so it is shared by every bench on the host, the systemd
     sandboxes of CI runners included (DynamicUser, PrivateTmp and ProtectHome
     leave them no directory in common, but the same loopback). EADDRINUSE
     means another bench holds the port. The kernel drops the claim when this
     process exits, however it exits, so nothing is ever left stale. Elsewhere
     (darwin) it is an flock on /tmp/frappe-nix-ports/<port>.lock, which ends
     the same way.
  2. Check that nothing outside frappe-nix has it: a TCP bind with
     SO_REUSEADDR (nginx and Go set it too, so TIME_WAIT is no obstacle).
  3. Keep the claim for as long as the bench runs, and answer on it: a connect
     to the claim socket reads one JSON line naming the bench that holds it
     (`who`), which nothing else can say across those sandboxes.

Then it renders the templates it was given (nginx.conf: @WEB_PORT@ and
@BENCH_ID@), writes <runtime>/frappe-nix/ports.json in one rename, and waits.
nginx, Mailpit and the frappe:config task start after it and read their ports
from that file. Between the TCP check and nginx's own bind only a process
outside frappe-nix can take the port; between two benches there is no window
at all.

With --strict (or --pin KEY for one port) only the preferred port is tried,
and a taken one stops the bench with the holder named.

	port-lease.py hold --runtime DIR --bench-id ID --name NAME --root PATH
	              [--strict] [--pin KEY] [--span N] [--offset N] [--base N]
	              [--render TEMPLATE=OUT]... [--dry-run] KEY[/LABEL]=HOST:PORT...
	port-lease.py ready --runtime DIR
	port-lease.py show --runtime DIR [--preferred KEY=PORT]... [--json | KEY]
	port-lease.py who PORT [--host HOST]

`show` exits 3 when the bench is not up, after printing the preferred ports.
"""

import argparse
import contextlib
import errno
import fcntl
import json
import os
import selectors
import signal
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The walk's length: devenv's own allocator tries as many (MAX_ATTEMPTS).
SPAN = 100

#: Not running: `show`'s exit status, as frappe-nix-ports documents it.
NOT_UP = 3


def say(msg: str) -> None:
	print(f"frappe-nix: {msg}", file=sys.stderr, flush=True)


def backend() -> str:
	"""abstract (Linux) or flock. FRAPPE_NIX_PORT_LEASE_BACKEND=flock forces the
	second, so its tests run on Linux too."""
	forced = os.environ.get("FRAPPE_NIX_PORT_LEASE_BACKEND", "")
	if forced in ("abstract", "flock"):
		return forced
	return "abstract" if sys.platform.startswith("linux") else "flock"


def claim_name(port: int) -> str:
	# The port alone, not host:port: two benches on one port collide whatever
	# loopback address each binds.
	return f"\0frappe-nix/port/{port}"


def lock_dir() -> Path:
	return Path(os.environ.get("FRAPPE_NIX_PORT_LEASE_DIR") or "/tmp/frappe-nix-ports")


class Claim:
	"""One port, claimed host-wide until release() or this process ends."""

	def __init__(self, port: int, sock: socket.socket | None = None, fd: int | None = None) -> None:
		self.port = port
		self.sock = sock
		self.fd = fd

	def release(self) -> None:
		if self.sock is not None:
			self.sock.close()
			self.sock = None
		if self.fd is not None:
			os.close(self.fd)
			self.fd = None


def claim(port: int, identity: dict[str, Any]) -> Claim | None:
	"""The claim on PORT, or None when another bench holds it."""
	if backend() == "abstract":
		sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		try:
			sock.bind(claim_name(port))
		except OSError as e:
			sock.close()
			if e.errno == errno.EADDRINUSE:
				return None
			raise
		sock.listen(16)
		sock.setblocking(False)
		return Claim(port, sock=sock)
	d = lock_dir()
	d.mkdir(parents=True, exist_ok=True)
	with contextlib.suppress(OSError):
		d.chmod(0o1777)
	fd = os.open(d / f"{port}.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o666)
	try:
		fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
	except BlockingIOError:
		os.close(fd)
		return None
	with contextlib.suppress(OSError):
		os.fchmod(fd, 0o666)
	os.ftruncate(fd, 0)
	os.pwrite(fd, (json.dumps(identity) + "\n").encode(), 0)
	return Claim(port, fd=fd)


def holder(port: int) -> dict[str, Any] | None:
	"""The identity of the bench holding PORT's claim, or None when none does."""
	if backend() == "abstract":
		sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		sock.settimeout(2)
		try:
			sock.connect(claim_name(port))
			data = b""
			while b"\n" not in data:
				chunk = sock.recv(4096)
				if not chunk:
					break
				data += chunk
		except OSError:
			return None
		finally:
			sock.close()
		try:
			value = json.loads(data.decode() or "null")
		except ValueError:
			return {}
		return value if isinstance(value, dict) else {}
	path = lock_dir() / f"{port}.lock"
	try:
		fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
	except OSError:
		return None
	try:
		try:
			fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
		except BlockingIOError:
			text = os.pread(fd, 65536, 0).decode(errors="replace")
			try:
				value = json.loads(text)
			except ValueError:
				return {}
			return value if isinstance(value, dict) else {}
		return None
	finally:
		os.close(fd)


def tcp_free(host: str, port: int) -> bool:
	"""Nothing has HOST:PORT bound. SO_REUSEADDR, as nginx and Mailpit bind it,
	so a port with only TIME_WAIT connections left counts as free."""
	try:
		infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
	except OSError:
		return False
	family, _, _, _, addr = infos[0]
	sock = socket.socket(family, socket.SOCK_STREAM)
	try:
		sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		sock.bind(addr)
	except OSError:
		return False
	finally:
		sock.close()
	return True


def describe(port: int, host: str = "127.0.0.1") -> str:
	"""Who has PORT, for a message."""
	who = holder(port)
	if who is not None:
		return f"held by bench {who.get('bench_id', '?')} (pid {who.get('pid', '?')}, {who.get('root', '?')})"
	if not tcp_free(host, port):
		return "in use by a process outside frappe-nix"
	return "free"


@dataclass(frozen=True)
class Want:
	key: str
	label: str
	host: str
	port: int


def parse_want(text: str) -> Want:
	"""KEY[/LABEL]=HOST:PORT."""
	name, sep, addr = text.partition("=")
	host, colon, port = addr.rpartition(":")
	if not (sep and colon and host and port.isdigit() and name):
		raise argparse.ArgumentTypeError(f"not KEY[/LABEL]=HOST:PORT: {text!r}")
	key, _, label = name.partition("/")
	return Want(key, label or key, host, int(port))


def ports_file(runtime: str) -> Path:
	return Path(runtime) / "frappe-nix" / "ports.json"


def write_atomic(path: Path, text: str) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	tmp = path.with_name(f".{path.name}.{os.getpid()}")
	tmp.write_text(text)
	os.replace(tmp, path)


def alive(pid: Any) -> bool:
	if not isinstance(pid, int) or pid <= 0:
		return False
	try:
		os.kill(pid, 0)
	except ProcessLookupError:
		return False
	except PermissionError:
		return True
	return True


def live(runtime: str) -> dict[str, Any] | None:
	"""ports.json, when the holder that wrote it is still running."""
	try:
		data = json.loads(ports_file(runtime).read_text())
	except (OSError, ValueError):
		return None
	if not isinstance(data, dict) or not alive(data.get("pid")):
		return None
	return data


def serve(runtime: str, claims: list[Claim], identity: dict[str, Any]) -> int:
	"""Answer on the claims until a signal ends the bench; then drop ports.json."""
	line = (json.dumps(identity) + "\n").encode()

	def stop(signum: int, _frame: Any) -> None:
		# Said, so a lease that ends early can be traced in the process's log.
		say(f"frappe-ports: {signal.Signals(signum).name}, releasing the leases")
		raise SystemExit(0)

	for signum in (signal.SIGTERM, signal.SIGINT):
		signal.signal(signum, stop)
	# Like nginx and MariaDB, outlive a hangup: the manager stops the bench with
	# SIGTERM, and a lease that ended under a running nginx would let another
	# bench take its port.
	signal.signal(signal.SIGHUP, signal.SIG_IGN)
	sel = selectors.DefaultSelector()
	try:
		for c in claims:
			if c.sock is not None:
				sel.register(c.sock, selectors.EVENT_READ)
		while True:
			if not sel.get_map():
				signal.pause()
				continue
			for key, _ in sel.select():
				sock = key.fileobj
				assert isinstance(sock, socket.socket)
				try:
					conn, _ = sock.accept()
				except OSError:
					continue
				with contextlib.suppress(OSError):
					conn.settimeout(1)
					conn.sendall(line)
				conn.close()
	finally:
		path = ports_file(runtime)
		with contextlib.suppress(OSError, ValueError):
			if json.loads(path.read_text()).get("pid") == os.getpid():
				path.unlink()
		for c in claims:
			c.release()


def hold(args: argparse.Namespace) -> int:
	identity = {
		"bench_id": args.bench_id,
		"name": args.name,
		"root": args.root,
		"pid": os.getpid(),
	}
	pinned = set(args.pin)
	claims: list[Claim] = []
	got: dict[str, int] = {}
	moved: dict[str, int] = {}
	taken: list[tuple[Want, str]] = []
	for want in args.want:
		strict = args.strict or want.key in pinned
		last = want.port if strict else min(want.port + args.span - 1, 65535)
		first_holder = ""
		for port in range(want.port, last + 1):
			if port in got.values():
				continue
			reason = ""
			c = claim(port, identity)
			if c is not None and not tcp_free(want.host, port):
				c.release()
				c = None
				reason = "in use by a process outside frappe-nix"
			elif c is None:
				reason = describe(port, want.host)
			if c is None:
				first_holder = first_holder or reason
				continue
			claims.append(c)
			got[want.key] = port
			if port != want.port:
				moved[want.key] = want.port
				if not args.dry_run:
					say(f"{want.label} moved to {want.host}:{port} ({want.port} is {first_holder})")
			break
		else:
			taken.append((want, first_holder))

	if taken:
		for c in claims:
			c.release()
		refused = [(w, r) for w, r in taken if args.strict or w.key in pinned]
		if refused:
			listing = " ".join(f"{w.label}={w.host}:{w.port}" for w, _ in refused)
			say(f"cannot start {args.name}: already in use: {listing}")
			for w, reason in refused:
				print(f"  {w.label}={w.host}:{w.port} is {reason}.", file=sys.stderr)
			print(
				"  Another bench on the same ports, or this bench already up in another shell.",
				file=sys.stderr,
			)
			if args.base is not None and any(w.key in pinned for w, _ in refused):
				print(
					f"  The web port is frappe-nix.ports.base ({args.base}), which FRAPPE_NIX_PORT_OFFSET",
					file=sys.stderr,
				)
				print("  does not move: change ports.base in flake.nix for that one.", file=sys.stderr)
			if args.offset is not None:
				print(
					f"  Pick other ports for this shell (offset now {args.offset}, 0 to 899):",
					file=sys.stderr,
				)
				print(
					"    FRAPPE_NIX_PORT_OFFSET=<n> nix develop --no-pure-eval   (or direnv reload)",
					file=sys.stderr,
				)
			elif not (args.base is not None and any(w.key in pinned for w, _ in refused)):
				# Bench mode: its offset is committed, and the variable ignored.
				print(
					"  Pick other ports for this bench: change ports.offset (or ports.base) in flake.nix.",
					file=sys.stderr,
				)
		for w, reason in taken:
			if (w, reason) not in refused:
				say(
					f"cannot start {args.name}: no free port for {w.label} in {w.port}-"
					f"{min(w.port + args.span - 1, 65535)} (the first is {reason})"
				)
		return 1

	if args.dry_run:
		for c in claims:
			c.release()
		return 0

	runtime_dir = ports_file(args.runtime).parent
	web = got.get("web")
	for spec in args.render:
		template, _, out = spec.partition("=")
		text = Path(template).read_text()
		text = text.replace("@BENCH_ID@", args.bench_id)
		if web is not None:
			text = text.replace("@WEB_PORT@", str(web))
		write_atomic(Path(out) if out else runtime_dir / Path(template).name, text)
	record = {
		**identity,
		"runtime": args.runtime,
		**got,
		"preferred": {w.key: w.port for w in args.want},
		"moved": moved,
		# Whether the web port answers /__frappe_nix/bench: only the nginx
		# config rendered here does, so a bench without sockets says false.
		"identity": bool(args.render),
	}
	write_atomic(ports_file(args.runtime), json.dumps(record, indent=2) + "\n")
	return serve(args.runtime, claims, identity)


def ready(args: argparse.Namespace) -> int:
	"""ports.json is there and its holder is the one answering on the claims: a
	file left by a holder that was killed never counts."""
	data = live(args.runtime)
	if data is None:
		return 1
	ports = [data[k] for k in data.get("preferred", {}) if isinstance(data.get(k), int)]
	if ports and backend() == "abstract":
		who = holder(ports[0])
		return 0 if who is not None and who.get("pid") == data["pid"] else 1
	return 0


def show(args: argparse.Namespace) -> int:
	data = live(args.runtime)
	preferred = dict(p.split("=", 1) for p in args.preferred)
	if data is None:
		print(
			"frappe-nix: the bench is not up; these are the preferred ports, leased at devenv up",
			file=sys.stderr,
		)
		if args.key:
			value = preferred.get(args.key)
			if value is not None:
				print(value)
		elif args.json:
			print(json.dumps({k: int(v) for k, v in preferred.items()}, indent=2))
		else:
			for k, v in preferred.items():
				print(f"{k} {v}")
		return NOT_UP
	if args.json:
		print(json.dumps(data, indent=2))
	elif args.key:
		if args.key not in data:
			return 1
		value = data[args.key]
		print(value if isinstance(value, str) else json.dumps(value))
	else:
		print(f"bench_id {data['bench_id']}")
		for k in data.get("preferred", {}):
			note = f" (moved from {data['moved'][k]})" if k in data.get("moved", {}) else ""
			print(f"{k} {data[k]}{note}")
	return 0


def answering(host: str, port: int) -> str:
	"""Which bench's nginx answers on HOST:PORT (its /__frappe_nix/bench), as a
	diagnosis can say it: the claim names who leased the port, this who serves
	on it, and the two differ when something else bound it meanwhile."""
	try:
		with socket.create_connection((host, port), timeout=2) as sock:
			sock.sendall(f"GET /__frappe_nix/bench HTTP/1.0\r\nHost: {host}:{port}\r\n\r\n".encode())
			data = b""
			while chunk := sock.recv(4096):
				data += chunk
	except OSError as e:
		return f"nothing ({e.strerror or e})"
	head, _, body = data.partition(b"\r\n\r\n")
	status = head.split(b"\r\n", 1)[0].decode(errors="replace")
	return body.decode(errors="replace").strip() if " 200 " in status else f"not a bench ({status})"


def who(args: argparse.Namespace) -> int:
	found = holder(args.port)
	if found is not None:
		print(json.dumps({**found, "answering": answering(args.host, args.port)}))
	elif not tcp_free(args.host, args.port):
		print(f"{args.port}: in use by a process outside frappe-nix")
	else:
		print(f"{args.port}: free")
	return 0


def main(argv: list[str] | None = None) -> int:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	sub = ap.add_subparsers(dest="cmd", required=True)

	h = sub.add_parser("hold", help="lease the ports and keep them while the bench runs")
	h.add_argument("--runtime", required=True)
	h.add_argument("--bench-id", required=True)
	h.add_argument("--name", required=True)
	h.add_argument("--root", required=True)
	h.add_argument("--strict", action="store_true", help="try only each preferred port")
	h.add_argument("--pin", action="append", default=[], metavar="KEY", help="try only KEY's preferred port")
	h.add_argument("--span", type=int, default=SPAN)
	h.add_argument("--offset", type=int, help="the offset in force, for the strict message")
	h.add_argument("--base", type=int, help="ports.base, for the strict message")
	h.add_argument("--render", action="append", default=[], metavar="TEMPLATE=OUT")
	h.add_argument("--dry-run", action="store_true", help="check, report a refusal, hold nothing")
	h.add_argument("want", nargs="+", type=parse_want, metavar="KEY[/LABEL]=HOST:PORT")

	r = sub.add_parser("ready", help="exit 0 when this bench's lease is up")
	r.add_argument("--runtime", required=True)

	s = sub.add_parser("show", help="print the leased ports (exit 3: not up)")
	s.add_argument("--runtime", required=True)
	s.add_argument("--preferred", action="append", default=[], metavar="KEY=PORT")
	s.add_argument("--json", action="store_true")
	s.add_argument("key", nargs="?")

	w = sub.add_parser("who", help="which bench holds a port")
	w.add_argument("port", type=int)
	w.add_argument("--host", default="127.0.0.1")

	args = ap.parse_args(argv)
	if args.cmd == "hold" and args.span < 1:
		ap.error("--span must be at least 1")
	return {"hold": hold, "ready": ready, "show": show, "who": who}[args.cmd](args)


if __name__ == "__main__":
	sys.exit(main())
