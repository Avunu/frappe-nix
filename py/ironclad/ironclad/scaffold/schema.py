"""Validating ``[tool.ironclad]`` against ``ironclad/data/schema/tool-ironclad.schema.json``.

The package carries no JSON Schema library (its dependencies are jinja2, tomlkit and
packaging), so this implements the keywords that schema uses: ``type``, ``const``,
``enum``, ``minimum``, ``maximum``, ``pattern``, ``minLength``, ``minItems``,
``uniqueItems``, ``required``, ``properties``, ``additionalProperties`` (``false`` or a
schema) and ``items``. A keyword it does not know is a bug in the schema, so it raises.
"""

import json
import re
from typing import Any

KNOWN = {
	"$schema",
	"$id",
	"title",
	"description",
	"default",
	"type",
	"const",
	"enum",
	"minimum",
	"maximum",
	"pattern",
	"minLength",
	"minItems",
	"uniqueItems",
	"required",
	"properties",
	"additionalProperties",
	"items",
}

_TYPES = {
	"object": lambda v: isinstance(v, dict),
	"array": lambda v: isinstance(v, list),
	"string": lambda v: isinstance(v, str),
	"integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
	"number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
	"boolean": lambda v: isinstance(v, bool),
}


def _ecma(pattern: str) -> str:
	"""A JSON Schema (ECMA-262) ``pattern`` for Python's ``re.search``: ``$`` is the end of the
	value, where Python's would also match before a final newline (which would then land in
	a rendered ``flake.nix``)."""
	return re.sub(r"(?<!\\)\$", r"\\Z", pattern)


def _where(path: str) -> str:
	return f"[tool.ironclad]{path}" if path else "[tool.ironclad]"


def errors(value: Any, schema: dict, path: str = "") -> list[str]:
	"""Every way ``value`` breaks ``schema``, as ``<where>: <what>`` lines (empty when valid)."""
	unknown = set(schema) - KNOWN
	if unknown:
		raise RuntimeError(f"schema keyword(s) not supported: {sorted(unknown)}")
	where = _where(path)
	out: list[str] = []
	if "type" in schema and not _TYPES[schema["type"]](value):
		return [f"{where}: expected {schema['type']}, got {json.dumps(value, default=str)}"]
	if "const" in schema and value != schema["const"]:
		out.append(f"{where}: must be {json.dumps(schema['const'])}")
	if "enum" in schema and value not in schema["enum"]:
		out.append(f"{where}: must be one of {json.dumps(schema['enum'])}")
	if isinstance(value, int | float) and not isinstance(value, bool):
		if "minimum" in schema and value < schema["minimum"]:
			out.append(f"{where}: must be at least {schema['minimum']}")
		if "maximum" in schema and value > schema["maximum"]:
			out.append(f"{where}: must be at most {schema['maximum']}")
	if isinstance(value, str):
		if "minLength" in schema and len(value) < schema["minLength"]:
			out.append(f"{where}: must be at least {schema['minLength']} characters")
		if "pattern" in schema and not re.search(_ecma(schema["pattern"]), value):
			out.append(f"{where}: {json.dumps(value)} does not match {schema['pattern']}")
	if isinstance(value, list):
		if "minItems" in schema and len(value) < schema["minItems"]:
			out.append(f"{where}: needs at least {schema['minItems']} item(s)")
		if schema.get("uniqueItems"):
			seen = [json.dumps(v, sort_keys=True) for v in value]
			if len(set(seen)) != len(seen):
				out.append(f"{where}: items must be unique")
		if "items" in schema:
			for i, item in enumerate(value):
				out += errors(item, schema["items"], f"{path}[{i}]")
	if isinstance(value, dict):
		props = schema.get("properties", {})
		for key in schema.get("required", []):
			if key not in value:
				out.append(f"{where}: {key} is required")
		extra = schema.get("additionalProperties", True)
		for key, item in value.items():
			sub = f"{path}.{key}"
			if key in props:
				out += errors(item, props[key], sub)
			elif extra is False:
				out.append(f"{_where(sub)}: unknown key")
			elif isinstance(extra, dict):
				out += errors(item, extra, sub)
	return out
