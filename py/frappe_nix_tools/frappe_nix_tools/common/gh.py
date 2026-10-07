"""A thin wrapper over ``gh api``: the GitHub CLI already holds the user's (or CI's) token."""

import json
import subprocess
from typing import Any

from frappe_nix_tools.common.report import EnvError


def api(
	endpoint: str,
	*,
	method: str = "GET",
	fields: dict[str, str] | None = None,
	body: Any = None,
	paginate: bool = False,
) -> Any:
	"""Call ``gh api`` and return the decoded JSON (``None`` for an empty response).

	``fields`` become ``-f key=value``; ``body`` is sent as the JSON request body. With
	``paginate``, every page is fetched and the lists are concatenated.
	"""
	cmd = ["gh", "api", "--method", method, endpoint]
	for key, value in (fields or {}).items():
		cmd += ["-f", f"{key}={value}"]
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
		raise EnvError(f"gh api {method} {endpoint}: {proc.stderr.strip() or proc.returncode}")
	if not proc.stdout.strip():
		return None
	try:
		data = json.loads(proc.stdout)
	except json.JSONDecodeError as e:
		raise EnvError(f"gh api {method} {endpoint}: the response is not JSON: {e}") from e
	if paginate and isinstance(data, list) and all(isinstance(page, list) for page in data):
		return [item for page in data for item in page]
	return data
