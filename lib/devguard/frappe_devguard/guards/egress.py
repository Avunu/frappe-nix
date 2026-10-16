"""Egress guard — the catch-all.

Refuses every ``connect()`` this process makes to a non-loopback address unless
the destination is allowlisted (``devguard.egress.allowHosts`` /
``FRAPPE_DEVGUARD_EGRESS_ALLOW_HOSTS``). The other guards each know one
integration, and a bench's outbound paths are too many to enumerate — requests
(gocardless_pro, stripe, razorpay), httpx, aiohttp, urllib, botocore, smtplib,
and whatever the next app imports — but they all end in
``socket.socket.connect``. An integration nobody has written a guard for is
refused here before a packet leaves.

What passes untouched:

* ``AF_UNIX`` — the bench's MariaDB, Redis, Socket.IO and web sockets
* loopback and the unspecified address: MariaDB and Redis over TCP, Mailpit,
  the bench calling its own web server
* the mail guard's catcher, wherever it listens
* anything on ``egress.allow_hosts`` or ``integrations.allow_hosts``: a
  hostname (``*.example.com`` for a domain and its subdomains), an IP literal,
  or a CIDR
* S3 calls the objectstore guard has vetted in its ``push`` mode (see
  :func:`passage`)

Hostname entries are matched against the name a connection was *resolved
from*. Clients resolve first and connect by address — ``create_connection``,
urllib3, asyncio — so ``connect()`` only ever sees an IP. ``getaddrinfo`` is
wrapped to remember which names produced which addresses, and an address passes
if a name that resolved to it is allowlisted. One resolved some other way
(c-ares via aiodns, gevent's resolver, a hard-coded IP) is not remembered, and
is refused unless its address is allowlisted: the gap fails closed.

What this does not see: anything that is not Python opening a socket. A
subprocess (git, mc, curl, chromium, wkhtmltopdf, node), a C extension that
connects on its own (mysqlclient's libmariadb, grpc), and UDP sent without
``connect()`` are all outside it. Not a firewall for the machine, only for this
process's Python.
"""

import contextlib
import contextvars
import ipaddress
import socket
import threading

from .._hook import on_import
from .._patch import STATUS, announce, block, require
from .._settings import DevGuardBlocked, settings

NAME = "egress"

_INSTALLED = False

#: Warn once per destination: a retry loop would otherwise bury the log.
_WARNED = set()

#: address -> the names getaddrinfo resolved to it, in this process.
_ORIGINS = {}
_ORIGINS_LOCK = threading.Lock()

#: A bound, not a cache policy: past it the map starts over. Clients resolve
#: right before they connect, so starting over loses nothing in use.
_ORIGINS_MAX = 4096

#: (guard, thread id) while a guard's vetted operation runs; see passage().
_PASSAGE: contextvars.ContextVar[tuple[str, int] | None] = contextvars.ContextVar(
	"frappe_devguard_egress_passage", default=None
)

#: The real resolver. A module global looked up at call time, so the unit test
#: can stand DNS in without a network.
_getaddrinfo = socket.getaddrinfo


def enabled():
	return settings().guard_enabled(NAME)


def _normal(name):
	return name.strip().lower().rstrip(".")


def _ip(host):
	"""HOST as an address, or None for a name. IPv4-mapped IPv6 is unwrapped."""
	if not isinstance(host, str):
		return None
	try:
		addr = ipaddress.ip_address(host.split("%", 1)[0])
	except ValueError:
		return None
	mapped = getattr(addr, "ipv4_mapped", None)
	return mapped if mapped is not None else addr


def _is_local(addr):
	return addr.is_loopback or addr.is_unspecified


def _local_name(host):
	# RFC 6761: localhost and everything under it resolve to loopback.
	host = _normal(host)
	return host == "localhost" or host.endswith(".localhost")


def _entries():
	"""Every allowlist entry: both lists, and the mail catcher's host."""
	st = settings()
	entries = st.items(NAME, "allow_hosts") + st.items("integrations", "allow_hosts")
	if st.guard_enabled("mail"):
		entries.append(st.mail_host)
	return entries


def _parse(entries):
	"""Split allowlist entries into (names, networks)."""
	names, networks = [], []
	for entry in entries:
		entry = _normal(entry)
		if not entry:
			continue
		try:
			networks.append(ipaddress.ip_network(entry, strict=False))
		except ValueError:
			names.append(entry)
	return names, networks


def _name_matches(host, names):
	host = _normal(host)
	for pattern in names:
		if pattern.startswith("*."):
			if host == pattern[2:] or host.endswith(pattern[1:]):
				return True
		elif host == pattern:
			return True
	return False


def _in(addr, networks):
	return any(addr in net for net in networks)


def _origins(addr):
	with _ORIGINS_LOCK:
		return set(_ORIGINS.get(addr, ()))


def _remember(host, infos):
	"""Record the addresses a name resolved to. Never fails a resolution."""
	if isinstance(host, bytes):
		try:
			host = host.decode("idna")
		except UnicodeError:
			return
	if not isinstance(host, str) or _ip(host) is not None or not _normal(host):
		return
	addrs = set()
	for info in infos:
		sockaddr = info[4] if len(info) > 4 else None
		if isinstance(sockaddr, tuple) and sockaddr:
			addr = _ip(sockaddr[0])
			if addr is not None:
				addrs.add(addr)
	with _ORIGINS_LOCK:
		if len(_ORIGINS) >= _ORIGINS_MAX:
			_ORIGINS.clear()
		for addr in addrs:
			_ORIGINS.setdefault(addr, set()).add(_normal(host))


def getaddrinfo(host, port, *args, **kwargs):
	"""``socket.getaddrinfo``, remembering what each name resolved to."""
	infos = _getaddrinfo(host, port, *args, **kwargs)
	_remember(host, infos)
	return infos


def _resolve(host):
	"""Addresses a bare name maps to."""
	try:
		infos = _getaddrinfo(host, None)
	except (OSError, UnicodeError):
		return ()
	return tuple({addr for addr in (_ip(info[4][0]) for info in infos) if addr is not None})


def listed(host):
	"""Whether HOST, a name or an address, is local or allowlisted. No DNS.

	The integrations guard asks this too, so one list opens a host to both.
	"""
	addr = _ip(host)
	if addr is not None and _is_local(addr):
		return True
	names, networks = _parse(_entries())
	if addr is not None:
		return _in(addr, networks)
	return _local_name(host) or _name_matches(host, names)


def _allowed(host):
	"""Whether a connection to HOST (a name or an address) may proceed."""
	addr = _ip(host)
	if addr is not None and _is_local(addr):
		return True
	names, networks = _parse(_entries())
	if addr is not None:
		# Resolved first, as nearly every client does: allowed if a name that
		# produced this address is.
		return _in(addr, networks) or any(_name_matches(name, names) for name in _origins(addr))
	if _local_name(host) or _name_matches(host, names):
		return True
	# connect() was handed a name and resolves it itself. One that is not
	# listed may still resolve only somewhere that is (an /etc/hosts alias for
	# 127.0.0.1, a CIDR-allowlisted internal name).
	resolved = _resolve(host)
	return bool(resolved) and all(_is_local(a) or _in(a, networks) for a in resolved)


@contextlib.contextmanager
def passage(guard):
	"""Let the connections made inside this block through.

	For a guard that has vetted an operation itself and is about to send it:
	objectstore's push mode, whose S3 calls are additive-only by the time they
	reach the network. Scoped to the calling thread, so a connection another
	thread makes in the meantime is still checked — including one started
	inside the block, which a free-threaded build hands a copy of the context.
	"""
	token = _PASSAGE.set((guard, threading.get_ident()))
	try:
		yield
	finally:
		_PASSAGE.reset(token)


def in_passage():
	held = _PASSAGE.get()
	return held is not None and held[1] == threading.get_ident()


def _check(sock, address):
	"""Raise DevGuardBlocked unless connecting SOCK to ADDRESS is allowed."""
	if in_passage() or not enabled():
		return
	if sock.family not in (socket.AF_INET, socket.AF_INET6):
		return  # AF_UNIX and friends
	if not isinstance(address, tuple) or not address:
		return
	host = address[0]
	if isinstance(host, bytes):
		host = host.decode("idna")
	if host == "":
		return  # "" is INADDR_ANY for connect: the local host
	if _allowed(host):
		return

	port = address[1] if len(address) > 1 else "?"
	announce(
		NAME,
		"outbound connections from this process are refused (loopback only). Allow a "
		"destination with devguard.egress.allowHosts, or for one command with "
		"FRAPPE_DEVGUARD_EGRESS_ALLOW_HOSTS=host[,host...]; FRAPPE_DEVGUARD_DISABLE=egress "
		"turns the guard off",
	)
	addr = _ip(host)
	names = sorted(_origins(addr)) if addr is not None else []
	message = f"blocked connection to {host}:{port}" + (f" ({', '.join(names)})" if names else "")
	key = (host, port)
	if key in _WARNED:
		raise DevGuardBlocked(f"frappe_devguard[{NAME}]: {message}")
	_WARNED.add(key)
	block(NAME, message)


def _wrap(cls, original_connect, original_connect_ex):
	def connect(self, address):
		_check(self, address)
		return original_connect(self, address)

	def connect_ex(self, address):
		_check(self, address)
		return original_connect_ex(self, address)

	cls.connect = connect
	cls.connect_ex = connect_ex


def _patch_stdlib():
	cls = socket.socket
	_wrap(cls, cls.connect, cls.connect_ex)
	STATUS["socket.socket.connect"] = "patched"
	# socket.create_connection, urllib3 and asyncio look this up on the module
	# at call time, so rebinding it reaches them all. The wrapper passes its
	# arguments through rather than restating typeshed's signature.
	socket.getaddrinfo = getaddrinfo  # ty: ignore[invalid-assignment]
	STATUS["socket.getaddrinfo"] = "patched"


def _patch_gevent(module):
	# gevent.monkey.patch_all() swaps socket.socket for gevent's own class,
	# which carries its own connect and would otherwise be unguarded. No
	# Frappe process uses gevent; a third-party app might.
	cls = require(module, "socket")
	_wrap(cls, require(cls, "connect"), require(cls, "connect_ex"))


def install():
	global _INSTALLED
	if _INSTALLED:
		return
	_INSTALLED = True

	_patch_stdlib()
	on_import("gevent._socket3", _patch_gevent)
