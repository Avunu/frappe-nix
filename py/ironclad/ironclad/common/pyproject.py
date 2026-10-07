"""``[tool.ironclad]``: loading it, its defaults, and looking up one key.

The defaults are the schema's own ``default`` values
(``ironclad/data/schema/tool-ironclad.schema.json``), so the schema is the one place a
default is written down. Validation against the schema is sync's (N3); this module only
reads.
"""

import copy
import json
import tomllib
from functools import cache
from pathlib import Path
from typing import Any

from ironclad.common import data_path
from ironclad.common.report import ConfigError, EnvError

SCHEMA = "schema/tool-ironclad.schema.json"


def load(path: Path) -> dict:
	"""The parsed ``pyproject.toml``; missing is an ``EnvError``, malformed a ``ConfigError``."""
	try:
		return tomllib.loads(path.read_text())
	except FileNotFoundError as e:
		raise EnvError(f"{path} does not exist") from e
	except tomllib.TOMLDecodeError as e:
		raise ConfigError(f"{path}: {e}") from e


@cache
def schema() -> dict:
	"""The ``[tool.ironclad]`` JSON Schema."""
	return json.loads(data_path(SCHEMA).read_text())


def _defaults(node: dict) -> Any:
	if node.get("type") == "object" and "properties" in node:
		out = {}
		for key, sub in node["properties"].items():
			value = _defaults(sub)
			if value is not None:
				out[key] = value
		return out if out or "default" in node else None
	return copy.deepcopy(node.get("default"))


def defaults() -> dict:
	"""Every key that has a default, with it; nested tables included."""
	return _defaults(schema())


def _merge(base: Any, over: Any) -> Any:
	if isinstance(base, dict) and isinstance(over, dict):
		out = dict(base)
		for key, value in over.items():
			out[key] = _merge(base.get(key), value)
		return out
	return over


def tool_ironclad(doc: dict) -> dict | None:
	"""The raw ``[tool.ironclad]`` table, or ``None`` when there is none."""
	table = doc.get("tool", {}).get("ironclad")
	if table is not None and not isinstance(table, dict):
		raise ConfigError("[tool.ironclad] must be a table")
	return table


def config(doc: dict) -> dict:
	"""``[tool.ironclad]`` with the defaults applied (``cfg`` in the template context)."""
	return _merge(defaults(), tool_ironclad(doc) or {})


def schema_node(key: str) -> dict:
	"""The schema of the dotted ``key`` (``test.setup``); an unknown key is a ``ConfigError``."""
	node = schema()
	for part in key.split("."):
		props = node.get("properties", {})
		if part not in props:
			raise ConfigError(f"[tool.ironclad] has no key {key!r}")
		node = props[part]
	return node


def get(cfg: dict, key: str) -> Any:
	"""The value of the dotted ``key`` in ``cfg``, or ``None`` when unset and without a default."""
	schema_node(key)
	value: Any = cfg
	for part in key.split("."):
		if not isinstance(value, dict) or part not in value:
			return None
		value = value[part]
	return value
