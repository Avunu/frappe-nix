"""Exit codes, errors and the drift report format the commands share (spec §3.3).

Every command exits with one of these codes, and when several apply the highest wins:

====  =================================================================================
0     clean: nothing to write, or everything written
1     drift: a managed file is missing, differs or should not exist (``--check`` only)
2     invalid configuration that sync cannot fix (schema, unknown sibling, bad argument)
3     environment error: not an app, not a git repository, unreadable lock, version skew
====  =================================================================================
"""

import json
import secrets
from dataclasses import asdict, dataclass

CLEAN = 0
DRIFT = 1
INVALID = 2
ENVIRONMENT = 3

STATUS = {CLEAN: "clean", DRIFT: "drift", INVALID: "invalid", ENVIRONMENT: "error"}


class FrappeNixError(Exception):
	"""An error a command reports as one line on stderr and its exit code."""

	code = ENVIRONMENT


class ConfigError(FrappeNixError):
	"""Exit 2: the configuration or an argument is invalid."""

	code = INVALID


class EnvError(FrappeNixError):
	"""Exit 3: the environment is not what the command needs."""

	code = ENVIRONMENT


@dataclass(frozen=True)
class Finding:
	"""One file's problem, as ``sync --check`` and the tools built on it report it."""

	path: str
	strategy: str
	problem: str
	diff: str = ""


def worst(*codes: int) -> int:
	"""The exit code when several apply: the highest."""
	return max((CLEAN, *codes))


def _escape_data(text: str) -> str:
	"""A workflow command's message: GitHub reads ``%``, CR and LF as escapes and line ends."""
	return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
	"""A workflow command's property value, where ``:`` and ``,`` also end the value."""
	return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(f: Finding) -> str:
	"""The ``::error`` workflow command for one finding."""
	return f"::error file={_escape_property(f.path)}::{_escape_data(f.problem)}"


def render(findings: list[Finding], fmt: str, code: int, frappe_nix: dict | None = None) -> str:
	"""Format ``findings`` as ``text``, ``json`` or ``github`` (text plus ``::error`` annotations).

	In ``github`` the text part (paths, problems and diffs, which hold file contents) is
	wrapped in ``::stop-commands::``, so no line of it runs as a workflow command, and the
	annotations follow it, escaped.
	"""
	if fmt == "json":
		doc = {
			"status": STATUS.get(code, "error"),
			"frappe_nix": frappe_nix or {},
			"files": [asdict(f) for f in findings],
		}
		return json.dumps(doc, indent=2, sort_keys=False) + "\n"
	if fmt not in ("text", "github"):
		raise ConfigError(f"unknown report format {fmt!r} (text, json or github)")
	text = []
	for f in findings:
		text.append(f"{f.path} ({f.strategy}): {f.problem}")
		if f.diff:
			text.append(f.diff.rstrip("\n"))
	out = []
	if fmt == "github" and text:
		token = secrets.token_hex(16)
		out += [f"::stop-commands::{token}", *text, f"::{token}::"]
		out += [annotation(f) for f in findings]
	else:
		out += text
	drifted = len(findings)
	if drifted:
		out.append(f"frappe-nix: {drifted} file(s) drifted — run `frappe-init --sync`")
	else:
		out.append("frappe-nix: clean")
	return "\n".join(out) + "\n"
