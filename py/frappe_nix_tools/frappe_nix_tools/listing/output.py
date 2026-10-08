"""How ``frappe-listing check`` reports (spec §5.2): text, JSON or GitHub annotations.

In ``github`` the text (paths and messages, which carry the app's content) is fenced in
``::stop-commands::``, so no line of it runs as a workflow command, and the annotations follow,
escaped, as ``sync --check --format github`` does (§3.3).
"""

import json
import secrets
from typing import Any

from frappe_nix_tools.listing.rules import Result


def escape_data(text: str) -> str:
	"""A workflow command's message: GitHub reads ``%``, CR and LF as escapes and line ends."""
	return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
	"""A workflow command's property value, where ``:`` and ``,`` also end the value."""
	return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def document(results: list[Result], code: int, extra: dict[str, Any]) -> dict[str, Any]:
	return {
		"status": "pass" if code == 0 else "fail",
		"errors": sum(r.level == "error" for r in results),
		"warnings": sum(r.level == "warning" for r in results),
		"results": [
			{"rule": r.rule, "level": r.level, "path": r.path, "message": r.message} for r in results
		],
		**extra,
	}


def render(results: list[Result], fmt: str, code: int, extra: dict[str, Any]) -> str:
	if fmt == "json":
		return json.dumps(document(results, code, extra), indent=2) + "\n"
	lines = [f"{r.rule} {r.level}: {r.path}: {r.message}" for r in results]
	out: list[str] = []
	if fmt == "github" and lines:
		token = secrets.token_hex(16)
		out += [f"::stop-commands::{token}", *lines, f"::{token}::"]
		# Notes (pilot checks that pass, advisory semgrep findings) stay in the text and the report.
		for r in results:
			if r.level in ("error", "warning"):
				out.append(
					f"::{r.level} file={escape_property(r.path)},title={r.rule}::{escape_data(r.message)}"
				)
	else:
		out += lines
	errors = sum(r.level == "error" for r in results)
	warnings = sum(r.level == "warning" for r in results)
	out.append(
		f"frappe-listing: {errors} error(s), {warnings} warning(s)"
		if errors or warnings
		else "frappe-listing: every check passes"
	)
	return "\n".join(out) + "\n"
