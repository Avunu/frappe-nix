"""``pyproject.toml`` and its ``[tool.frappe-nix]`` table: loading them, and whether an app opted in.

Resolving the table into the configuration the tools act on (profiles, defaults, module
switches) is ``frappe_nix_tools.common.config``; this module only reads.
"""

import tomllib
from pathlib import Path

from frappe_nix_tools.common.report import ConfigError, EnvError

TABLE = "frappe-nix"
OPT_IN_HINT = (
	"this app has not opted in to frappe-nix app standards; run "
	"frappe-init --sync --standards minimal|recommended|github:<owner>/<repo>"
)


def load(path: Path) -> dict:
	"""The parsed ``pyproject.toml``; missing is an ``EnvError``, malformed a ``ConfigError``."""
	try:
		return tomllib.loads(path.read_text())
	except FileNotFoundError as e:
		raise EnvError(f"{path} does not exist") from e
	except (OSError, UnicodeDecodeError) as e:
		raise EnvError(f"{path} is unreadable: {e}") from e
	except tomllib.TOMLDecodeError as e:
		raise ConfigError(f"{path}: {e}") from e


def tool_frappe_nix(doc: dict) -> dict | None:
	"""The raw ``[tool.frappe-nix]`` table, or ``None`` when there is none (not opted in, S35)."""
	tool = doc.get("tool", {})
	table = tool.get(TABLE) if isinstance(tool, dict) else None
	if table is not None and not isinstance(table, dict):
		raise ConfigError("[tool.frappe-nix] must be a table")
	return table


def opted_in(doc: dict) -> bool:
	"""Whether the app opted in: its ``pyproject.toml`` has a ``[tool.frappe-nix]`` table (S35)."""
	return tool_frappe_nix(doc) is not None


def project_name(doc: dict) -> str | None:
	"""``[project].name``, or ``None``."""
	project = doc.get("project")
	name = project.get("name") if isinstance(project, dict) else None
	return name if isinstance(name, str) and name else None
