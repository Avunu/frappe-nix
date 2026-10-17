"""GitHub through the ``gh`` CLI, with HTTP status codes and one seam tests replace (spec §4, §5.6).

``common/gh.py`` (``gh.api``) turns any failure into an ``EnvError``. The CI steps and the
``repo`` group also need to tell a 403 from a 404 from a 422 (rulesets on a private repo,
a missing ref, a refused bypass actor), and to run the ``gh`` subcommands that are not
``gh api`` (``gh pr merge --auto``, ``gh workflow run``, ``gh label create``).

Everything goes through two module-level functions, ``request`` and ``cli``. Tests swap
``Transport`` for a fake (``use(fake)``), so no test needs the network or ``gh``.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from frappe_nix_tools.common.report import EnvError

# gh prints "gh: Not Found (HTTP 404)" on stderr for a failed API call.
_STATUS = re.compile(r"\(HTTP (\d{3})\)")

# GitHub Actions' App id: the `integration_id` of every required `ci / <gate>` context and
# the bypass actor of the release-branch ruleset (spec S3, §4.8).
ACTIONS_APP_ID = 15368


class GitHubError(EnvError):
	"""A GitHub call that failed; ``status`` is the HTTP status when gh reported one."""

	def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
		super().__init__(message)
		self.status = status
		self.body = body


@dataclass
class Result:
	"""A finished ``gh`` subcommand."""

	code: int
	stdout: str = ""
	stderr: str = ""


class Transport:
	"""The real transport: ``gh`` on PATH, with the token it already holds (``GH_TOKEN``)."""

	def request(
		self, method: str, endpoint: str, body: Any = None, paginate: bool = False
	) -> tuple[int, Any]:
		cmd = ["gh", "api", "--method", method, endpoint]
		if paginate:
			cmd += ["--paginate", "--slurp"]
		stdin = None
		if body is not None:
			cmd += ["--input", "-"]
			stdin = json.dumps(body)
		try:
			proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=False)
		except FileNotFoundError as e:
			raise EnvError("gh is not on PATH") from e
		if proc.returncode != 0:
			m = _STATUS.search(proc.stderr)
			status = int(m[1]) if m else None
			raise GitHubError(
				f"gh api {method} {endpoint}: {proc.stderr.strip() or proc.returncode}",
				status,
				proc.stdout,
			)
		if not proc.stdout.strip():
			return 204, None
		try:
			data = json.loads(proc.stdout)
		except json.JSONDecodeError as e:
			raise GitHubError(f"gh api {method} {endpoint}: the response is not JSON: {e}") from e
		if paginate and isinstance(data, list) and all(isinstance(page, list) for page in data):
			data = [item for page in data for item in page]
		return 200, data

	def cli(self, *args: str) -> Result:
		try:
			proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
		except FileNotFoundError as e:
			raise EnvError("gh is not on PATH") from e
		return Result(proc.returncode, proc.stdout, proc.stderr)


_transport: Transport = Transport()


def use(transport: Transport) -> Transport:
	"""Replace the transport (tests); returns the previous one, to restore."""
	global _transport
	previous, _transport = _transport, transport
	return previous


def request(method: str, endpoint: str, body: Any = None, *, paginate: bool = False) -> Any:
	"""The decoded JSON of a REST call (``None`` when empty); a failure is a ``GitHubError``."""
	return _transport.request(method, endpoint, body, paginate)[1]


def get(endpoint: str, *, paginate: bool = False) -> Any:
	return request("GET", endpoint, paginate=paginate)


def get_or_none(endpoint: str) -> Any:
	"""``GET endpoint``, or ``None`` on a 404."""
	try:
		return get(endpoint)
	except GitHubError as e:
		if e.status == 404:
			return None
		raise


def cli(*args: str) -> Result:
	"""A ``gh`` subcommand (``pr merge``, ``workflow run``, ``label create``); never raises on its exit."""
	return _transport.cli(*args)


def required_contexts(repo: str, branch: str) -> tuple[set[str], bool]:
	"""The status-check contexts required on ``branch``, from its rulesets and its classic protection.

	Returns ``(contexts, known)``. ``GET /repos/{r}/rules/branches/{b}`` (the rules that apply
	to the branch, every ruleset merged) is readable with ``contents: read``; so is the
	``protection`` summary of ``GET /repos/{r}/branches/{b}``. ``known`` is false when neither
	could be read, which the callers treat as "not protected" (spec §4.5)."""
	contexts: set[str] = set()
	known = False
	try:
		rules = get(f"repos/{repo}/rules/branches/{branch}", paginate=True) or []
		known = True
		for rule in rules:
			if rule.get("type") == "required_status_checks":
				for check in (rule.get("parameters") or {}).get("required_status_checks", []):
					if check.get("context"):
						contexts.add(check["context"])
	except GitHubError:
		pass
	try:
		info = get(f"repos/{repo}/branches/{branch}") or {}
		known = True
		checks = (info.get("protection") or {}).get("required_status_checks") or {}
		contexts.update(c for c in checks.get("contexts", []) if c)
		contexts.update(c.get("context") for c in checks.get("checks", []) if c.get("context"))
	except GitHubError:
		pass
	return contexts, known


def gate_contexts(gates: list[str] | tuple[str, ...]) -> list[str]:
	"""``ci / <gate>`` for each gate (S3)."""
	return [f"ci / {g}" for g in gates]


def gates_protected(repo: str, branch: str, gates: list[str] | tuple[str, ...]) -> bool:
	"""Whether every enabled gate's context is required on ``branch`` (the auto-merge safety rule, §4.5)."""
	contexts, known = required_contexts(repo, branch)
	wanted = set(gate_contexts(gates))
	return known and bool(wanted) and wanted <= contexts
