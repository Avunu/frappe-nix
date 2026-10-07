"""Locked trees on disk: ``frappe-nix pin-path <input>`` (spec §4.2, §5.2, §8.3).

Given an input of the app's ``flake.lock`` (its own, such as ``frappe`` or
``standards-profile``, or one frappe-nix pins for it, such as ``frappe-semgrep-rules``),
produce a directory holding exactly the locked tree:

1. In a dev shell the tree is usually in the Nix store already: when the store path its
   ``narHash`` implies exists, that is the answer.
2. Otherwise ``.dev-dist/pins/<name>-<rev>/`` beside the lock, fetched and verified
   against the ``narHash`` with ``frappe_nix_tools.common.nar``. A mismatch is an
   ``EnvError`` (exit 3) and leaves nothing behind. A tree already there is hashed again
   before it is reused, and refetched unless it matches: it lives in the app checkout, so
   a commit or a stray edit can change it.

The fetch follows the lock node's type: ``github`` from codeload's tarball (a GitHub
Enterprise host's API tarball), ``gitlab`` from the GitLab API's archive, ``git`` with
``git clone --filter=blob:none`` and a checkout of the locked rev.
``FRAPPE_NIX_FETCH_TOKEN``, when set, authenticates each one (``Authorization: Bearer``;
for git through ``http.extraHeader``, passed in git's environment so it never shows in a
process listing), so private profiles, forks and siblings work (S43). Without it, a 401,
403 or 404 says to set it. The token goes only over https (or plain http to a loopback
address), never with a redirect to another scheme, host or port, and a redirect from
https to http is refused.

``FRAPPE_NIX_PIN_URL`` replaces a tarball URL (``{host}``, ``{owner}``, ``{repo}`` and
``{rev}`` are filled in), for mirrors and for the tests, which serve a tarball from
``file://`` or a local server.
"""

import http.client
import ipaddress
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from frappe_nix_tools.common import flakelock, nar
from frappe_nix_tools.common.report import ConfigError, EnvError

PINS_DIR = ".dev-dist/pins"
TOKEN = "FRAPPE_NIX_FETCH_TOKEN"


def _token() -> str:
	return os.environ.get(TOKEN, "")


def _hint(pin: flakelock.Pin) -> str:
	return f"set {TOKEN} (Contents read on {pin.label})"


def _url(pin: flakelock.Pin) -> str:
	template = os.environ.get("FRAPPE_NIX_PIN_URL")
	if not template:
		return pin.tarball_url
	try:
		return template.format(host=pin.host, owner=pin.owner, repo=pin.repo, rev=pin.rev)
	except (KeyError, IndexError, ValueError) as e:
		raise ConfigError(
			f"FRAPPE_NIX_PIN_URL={template!r} is not a URL template with {{host}}, {{owner}}, {{repo}} and {{rev}}: {e!r}"
		) from e


def _may_carry_token(url: str) -> bool:
	"""Whether ``url`` may be sent the token: https, or plain http to a loopback address."""
	parts = urllib.parse.urlsplit(url)
	if parts.scheme == "https":
		return True
	if parts.scheme != "http" or not parts.hostname:
		return False
	if parts.hostname == "localhost":
		return True
	try:
		return ipaddress.ip_address(parts.hostname).is_loopback
	except ValueError:
		return False


def _origin(url: str) -> tuple[str, str]:
	parts = urllib.parse.urlsplit(url)
	return parts.scheme.lower(), parts.netloc.lower()


class _Redirects(urllib.request.HTTPRedirectHandler):
	"""Follows a redirect without the token unless it stays on the same scheme, host and port.

	urllib's own handler copies every ordinary header, ``Authorization`` included, to
	wherever a redirect points, and follows https to http. A GitLab that redirects archive
	downloads to object storage, or a mirror, would otherwise be handed the token.
	"""

	def redirect_request(self, req, fp, code, msg, headers, newurl):
		old, new = _origin(req.full_url), _origin(newurl)
		if old[0] == "https" and new[0] != "https":
			raise EnvError(f"cannot fetch {req.full_url}: it redirects to {newurl}, which is not https")
		out = super().redirect_request(req, fp, code, msg, headers, newurl)
		if out is not None and (old != new or not _may_carry_token(newurl)):
			out.remove_header("Authorization")
		return out


_OPENER = urllib.request.build_opener(_Redirects)


def _fetch(pin: flakelock.Pin, url: str, dest: Path) -> None:
	token = _token()
	try:
		request = urllib.request.Request(url)
		if token and _may_carry_token(url):
			request.add_header("Authorization", f"Bearer {token}")
		with _OPENER.open(request, timeout=120) as response, dest.open("wb") as out:
			shutil.copyfileobj(response, out)
	except urllib.error.HTTPError as e:
		if e.code in (401, 403, 404) and not token:
			raise EnvError(
				f"cannot fetch {url}: HTTP {e.code}; if the repository is private, {_hint(pin)}"
			) from e
		if e.code in (401, 403, 404) and not _may_carry_token(url):
			raise EnvError(f"cannot fetch {url}: HTTP {e.code}; {TOKEN} is sent only over https") from e
		raise EnvError(f"cannot fetch {url}: HTTP {e.code}") from e
	except (OSError, ValueError, http.client.HTTPException) as e:
		raise EnvError(f"cannot fetch {url}: {e}") from e


def _unpack(tarball: Path, into: Path) -> Path:
	"""Unpack and return the tarball's single top-level directory (GitHub's ``<repo>-<rev>/``)."""
	try:
		with tarfile.open(tarball) as tar:
			tar.extractall(into, filter="tar")
	except (tarfile.TarError, OSError) as e:
		raise EnvError(f"cannot unpack {tarball}: {e}") from e
	entries = list(into.iterdir())
	if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
		raise EnvError(f"{tarball} does not hold exactly one top-level directory")
	return entries[0]


def _git_env(pin: flakelock.Pin) -> dict[str, str]:
	"""git's environment: no prompts, and the token as ``http.extraHeader`` when it may go.

	Through ``GIT_CONFIG_COUNT``/``_KEY_<n>``/``_VALUE_<n>`` rather than ``git -c``, which
	would put the token in the command line, readable by every user of the machine.
	"""
	env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
	token = _token()
	if token and _may_carry_token(pin.url):
		try:
			n = int(env.get("GIT_CONFIG_COUNT") or 0)
		except ValueError:
			n = 0
		env[f"GIT_CONFIG_KEY_{n}"] = "http.extraHeader"
		env[f"GIT_CONFIG_VALUE_{n}"] = f"Authorization: Bearer {token}"
		env["GIT_CONFIG_COUNT"] = str(n + 1)
	return env


def _git(pin: flakelock.Pin, *args: str, cwd: Path | None = None) -> None:
	token = _token()
	try:
		subprocess.run(["git", *args], cwd=cwd, env=_git_env(pin), check=True, capture_output=True, text=True)
	except FileNotFoundError as e:
		raise EnvError("git is not on PATH") from e
	except subprocess.CalledProcessError as e:
		detail = (e.stderr or "").strip().splitlines()
		message = f"cannot fetch {pin.url}: git {args[0]}: {detail[-1] if detail else e.returncode}"
		if args[0] == "clone" and not token:
			message += f"; if the repository is private, {_hint(pin)}"
		elif args[0] == "clone" and not _may_carry_token(pin.url):
			message += f"; {TOKEN} is sent only over https"
		raise EnvError(message) from e


def _clone(pin: flakelock.Pin, into: Path) -> Path:
	"""Clone a ``git`` pin and check out its rev; the tree without ``.git``."""
	tree = into / pin.name
	_git(pin, "clone", "--quiet", "--filter=blob:none", "--no-checkout", "--", pin.url, str(tree))
	_git(pin, "-c", "advice.detachedHead=false", "checkout", "--quiet", pin.rev, cwd=tree)
	shutil.rmtree(tree / ".git")
	return tree


def _holds(dest: Path, nar_hash: str) -> bool:
	"""Whether ``dest`` is a real directory whose NAR hash is ``nar_hash``.

	Never trusts a marker: the pins directory sits in the app checkout, where a commit
	can put any tree (and any stamp) under the expected name.
	"""
	if dest.is_symlink() or not dest.is_dir():
		return False
	try:
		return nar.nar_hash(dest) == nar_hash
	except (OSError, ValueError):
		return False


def pin_path(name: str, lock_path: Path, store_dir: str = "/nix/store", *, lock: dict | None = None) -> Path:
	"""The directory holding input ``name`` as ``lock_path`` locks it.

	``lock`` is a parsed lock read elsewhere (a committed one: ``git show <sha>:flake.lock``)
	to use instead of ``lock_path``'s, which then only places the pins directory."""
	pin = flakelock.locked_pin(lock if lock is not None else flakelock.load(lock_path), name)

	in_store = Path(nar.store_path(pin.nar_hash, store_dir=store_dir))
	if in_store.is_dir():
		return in_store

	pins = lock_path.parent / PINS_DIR
	dest = pins / f"{pin.name}-{pin.rev}"
	# flakelock.locked_pin already refuses names that leave the pins directory; this keeps
	# it true whatever a future Pin is built from.
	if dest.parent != pins or pin.name.startswith("."):
		raise ConfigError(f"{pin.name}-{pin.rev} is not a directory name under {PINS_DIR}")
	if _holds(dest, pin.nar_hash):
		return dest

	pins.mkdir(parents=True, exist_ok=True)
	with tempfile.TemporaryDirectory(dir=pins, prefix=".fetch-") as tmp:
		work = Path(tmp)
		if pin.type == "git":
			tree = _clone(pin, work)
		else:
			tarball = work / "src.tar.gz"
			_fetch(pin, _url(pin), tarball)
			tree = _unpack(tarball, work / "unpacked")
		got = nar.nar_hash(tree)
		if got != pin.nar_hash:
			raise EnvError(
				f"{pin.label}@{pin.rev} does not match flake.lock: narHash {got}, locked {pin.nar_hash}"
			)
		if dest.is_symlink() or dest.is_file():
			dest.unlink()
		elif dest.exists():
			shutil.rmtree(dest)
		tree.rename(dest)
	return dest
