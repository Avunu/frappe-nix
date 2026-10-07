"""The literal top-level values of an app's ``hooks.py``, read without importing it.

Sync needs ``app_title``, ``app_description`` and ``required_apps``; importing the module
would need frappe. Only plain ``name = <literal>`` assignments at module level count; any
other form leaves the name unset.
"""

import ast
from pathlib import Path
from typing import Any

from frappe_nix_tools.common.report import ConfigError


def read(path: Path) -> dict[str, Any]:
	"""Every top-level ``name = <literal>`` in ``path``; a missing file reads as empty."""
	try:
		tree = ast.parse(path.read_text(), filename=str(path))
	except FileNotFoundError:
		return {}
	except (OSError, UnicodeDecodeError, SyntaxError) as e:
		raise ConfigError(f"{path}: {e}") from e
	out: dict[str, Any] = {}
	for node in tree.body:
		if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
			try:
				out[node.targets[0].id] = ast.literal_eval(node.value)
			except ValueError:
				continue
	return out


def required_apps(values: dict[str, Any]) -> list[str]:
	"""``required_apps`` as written (``"erpnext"``, ``"example/<repo>"``); not a list of strings is exit 2."""
	apps = values.get("required_apps", [])
	if not isinstance(apps, list | tuple) or not all(isinstance(a, str) for a in apps):
		raise ConfigError("hooks.py: required_apps must be a list of strings")
	return list(apps)


def bare(spelling: str) -> str:
	"""The app name a ``required_apps`` entry names: ``"example/shared_lib"`` → ``"shared_lib"``."""
	return spelling.rsplit("/", 1)[-1]
