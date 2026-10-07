"""``pyproject.toml`` and its ``[tool.frappe-nix]`` table: loading them, and whether an app opted in.

Resolving the table into the configuration the tools act on (profiles, defaults, module
switches) is ``frappe_nix_tools.common.config``; this module only reads.
"""

import re
import tomllib
from pathlib import Path

from frappe_nix_tools.common.report import ConfigError, EnvError

TABLE = "frappe-nix"
OPT_IN_HINT = (
	"this app has not opted in to frappe-nix app standards; run "
	"frappe-init --sync --standards minimal|recommended|github:<owner>/<repo>"
)


# The dev shell's opt-in test (lib/standards/shell.nix, S35), the same pattern on the same
# lines: one that opens [tool.frappe-nix], a subtable of it or an array of tables under it.
# Nix's [[:space:]] is these six characters.
_OPT_IN_LINE = re.compile(r"[ \t\n\v\f\r]*\[{1,2}tool\.frappe-nix[].].*", re.DOTALL)


def read(path: Path) -> str:
	"""The text of ``pyproject.toml``; missing or unreadable is an ``EnvError``."""
	try:
		return path.read_text()
	except FileNotFoundError as e:
		raise EnvError(f"{path} does not exist") from e
	except (OSError, UnicodeDecodeError) as e:
		raise EnvError(f"{path} is unreadable: {e}") from e


def parse(text: str, path: Path) -> dict:
	"""``text`` parsed as TOML; malformed is a ``ConfigError``."""
	try:
		return tomllib.loads(text)
	except tomllib.TOMLDecodeError as e:
		raise ConfigError(f"{path}: {e}") from e


def load(path: Path) -> dict:
	"""The parsed ``pyproject.toml``; missing is an ``EnvError``, malformed a ``ConfigError``."""
	return parse(read(path), path)


def opt_in_line(text: str) -> bool:
	"""Whether the dev shell sees ``text`` as opted in: it has a ``[tool.frappe-nix…]`` header line.

	The shell can't parse TOML (S35), so this is what decides its tools and apps.
	"""
	return any(_OPT_IN_LINE.fullmatch(line) for line in text.split("\n"))


def tool_frappe_nix(doc: dict) -> dict | None:
	"""The raw ``[tool.frappe-nix]`` table, or ``None`` when there is none (not opted in, S35)."""
	tool = doc.get("tool", {})
	table = tool.get(TABLE) if isinstance(tool, dict) else None
	if table is not None and not isinstance(table, dict):
		raise ConfigError("[tool.frappe-nix] must be a table")
	return table


def check_opt_in_spelling(text: str, doc: dict) -> None:
	"""Refuse (exit 2) a ``pyproject.toml`` the dev shell and the tools disagree about.

	The tools find the table by parsing TOML, the shell by a line match, and TOML has
	spellings only one of them sees: ``[tool."frappe-nix"]``, ``[ tool.frappe-nix ]`` and
	``[tool]`` with ``frappe-nix.<key> = …`` create the table without the line, and a
	``[tool.frappe-nix]`` line inside a multi-line string is the line without the table.
	"""
	table = tool_frappe_nix(doc) is not None
	line = opt_in_line(text)
	if table and not line:
		raise ConfigError(
			"write the table as a [tool.frappe-nix] header (or [tool.frappe-nix.<name>] / "
			"[[tool.frappe-nix.<name>]]): the dev shell finds it by that line, not by parsing "
			"TOML, so a quoted key, spaces inside the brackets or dotted keys under [tool] "
			"leave it without frappe-nix's tools"
		)
	if line and not table:
		raise ConfigError(
			"a line opens [tool.frappe-nix] but TOML sees no such table (is it inside a "
			"multi-line string?): the dev shell takes that line as the opt-in, so reword it "
			"or add the table"
		)


def opted_in(doc: dict) -> bool:
	"""Whether the app opted in: its ``pyproject.toml`` has a ``[tool.frappe-nix]`` table (S35)."""
	return tool_frappe_nix(doc) is not None


def project_name(doc: dict) -> str | None:
	"""``[project].name``, or ``None``."""
	project = doc.get("project")
	name = project.get("name") if isinstance(project, dict) else None
	return name if isinstance(name, str) and name else None
