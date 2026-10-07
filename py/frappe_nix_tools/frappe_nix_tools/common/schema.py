"""Validating a TOML document against a schema in ``frappe_nix_tools/data/schema/``.

The package carries no JSON Schema library (its dependencies are jinja2, tomlkit and
packaging), so this implements the keywords the shipped schemas use: ``$ref`` (within a
file, ``#/…``, and to a sibling file, ``<file>.json#/…``), ``type`` (a name or a list),
``const``, ``enum``, ``minimum``, ``maximum``, ``pattern``, ``minLength``, ``minItems``,
``uniqueItems``, ``required``, ``properties``, ``additionalProperties`` (``false`` or a
schema), ``propertyNames``, ``items`` and ``anyOf``. Annotations (``title``,
``description``, ``default``, ``$id``, ``$schema``, ``$defs``, ``$comment``, ``examples``)
are ignored. A keyword it does not know is a bug in the schema, so it raises.

One keyword is frappe-nix's own: ``"x-app-only": true`` marks a module parameter only an
app may set (spec §8.1). Validated as a profile (``app_only_allowed=False``), a document
that sets one gets an error naming it.

Defaults (``defaults``) are read from the same schemas: every ``default`` reached through
``properties`` and ``$ref``.
"""

import copy
import json
import re
from functools import cache
from typing import Any

from frappe_nix_tools.common import data_path

ANNOTATIONS = {
	"$schema",
	"$id",
	"$defs",
	"$comment",
	"title",
	"description",
	"default",
	"examples",
	"x-app-only",
}
KEYWORDS = {
	"$ref",
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
	"propertyNames",
	"items",
	"anyOf",
}

_TYPES = {
	"object": lambda v: isinstance(v, dict),
	"array": lambda v: isinstance(v, list),
	"string": lambda v: isinstance(v, str),
	"integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
	"number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
	"boolean": lambda v: isinstance(v, bool),
	"null": lambda v: v is None,
}


@cache
def load(name: str) -> dict:
	"""The schema file ``schema/<name>`` (``profile.schema.json``)."""
	return json.loads(data_path(f"schema/{name}").read_text())


def _resolve(ref: str, base: str) -> tuple[dict, str]:
	"""The schema a ``$ref`` names, and the file further refs inside it are relative to."""
	file, _, pointer = ref.partition("#")
	file = file or base
	node: Any = load(file)
	for part in [p for p in pointer.split("/") if p]:
		part = part.replace("~1", "/").replace("~0", "~")
		if not isinstance(node, dict) or part not in node:
			raise RuntimeError(f"schema {base}: $ref {ref!r} names nothing")
		node = node[part]
	return node, file


def deref(node: dict, base: str) -> tuple[dict, str]:
	"""Follow ``$ref`` until a schema without one."""
	seen = 0
	while "$ref" in node:
		node, base = _resolve(node["$ref"], base)
		seen += 1
		if seen > 32:
			raise RuntimeError(f"schema {base}: $ref cycle")
	return node, base


def _ecma(pattern: str) -> str:
	"""A JSON Schema (ECMA-262) ``pattern`` for Python's ``re.search``: ``$`` is the end of
	the value, where Python's would also match before a final newline."""
	return re.sub(r"(?<!\\)\$", r"\\Z", pattern)


def _show(value: Any) -> str:
	return json.dumps(value, default=str)


class Validator:
	"""Validates against one schema file; ``where`` prefixes every message."""

	def __init__(self, name: str, where: str, *, app_only_allowed: bool = True):
		self.name = name
		self.where = where
		self.app_only_allowed = app_only_allowed

	def _at(self, path: str) -> str:
		return f"{self.where}{path}" if path else self.where

	def _object(self, value: dict, schema: dict, path: str, base: str) -> list[str]:
		out: list[str] = []
		props = schema.get("properties", {})
		for key in schema.get("required", []):
			if key not in value:
				out.append(f"{self._at(path)}: {key} is required")
		extra = schema.get("additionalProperties", True)
		names = schema.get("propertyNames")
		for key, item in value.items():
			sub = f"{path}.{key}"
			if names is not None:
				out += [
					e.replace(self._at(sub), f"{self._at(sub)} (key)")
					for e in self.errors(key, names, sub, base)
				]
			if key in props:
				child, child_base = deref(props[key], base)
				if child.get("x-app-only") and not self.app_only_allowed:
					out.append(f"{self._at(sub)}: an app-only key; set it in the app's [tool.frappe-nix]")
					continue
				out += self.errors(item, child, sub, child_base)
			elif extra is False:
				out.append(f"{self._at(sub)}: unknown key")
			elif isinstance(extra, dict):
				out += self.errors(item, extra, sub, base)
		return out

	def errors(
		self, value: Any, schema: dict | None = None, path: str = "", base: str | None = None
	) -> list[str]:
		"""Every way ``value`` breaks the schema, as ``<where>: <what>`` lines (empty when valid)."""
		if schema is None:
			schema = load(self.name)
		base = base or self.name
		schema, base = deref(schema, base)
		unknown = set(schema) - KEYWORDS - ANNOTATIONS
		if unknown:
			raise RuntimeError(f"schema {base}: keyword(s) not supported: {sorted(unknown)}")
		where = self._at(path)
		if "type" in schema:
			types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
			if not any(_TYPES[t](value) for t in types):
				return [f"{where}: expected {' or '.join(types)}, got {_show(value)}"]
		if "anyOf" in schema:
			branches = [self.errors(value, sub, path, base) for sub in schema["anyOf"]]
			if all(branches):
				# The branch whose type matched explains best; else the first.
				best = min(branches, key=lambda errs: (any("expected" in e for e in errs), len(errs)))
				return best
		out: list[str] = []
		if "const" in schema and value != schema["const"]:
			out.append(f"{where}: must be {_show(schema['const'])}")
		if "enum" in schema and value not in schema["enum"]:
			out.append(f"{where}: must be one of {_show(schema['enum'])}")
		if isinstance(value, int | float) and not isinstance(value, bool):
			if "minimum" in schema and value < schema["minimum"]:
				out.append(f"{where}: must be at least {schema['minimum']}")
			if "maximum" in schema and value > schema["maximum"]:
				out.append(f"{where}: must be at most {schema['maximum']}")
		if isinstance(value, str):
			if "minLength" in schema and len(value) < schema["minLength"]:
				out.append(f"{where}: must be at least {schema['minLength']} characters")
			if "pattern" in schema and not re.search(_ecma(schema["pattern"]), value):
				out.append(f"{where}: {_show(value)} does not match {schema['pattern']}")
		if isinstance(value, list):
			if "minItems" in schema and len(value) < schema["minItems"]:
				out.append(f"{where}: needs at least {schema['minItems']} item(s)")
			if schema.get("uniqueItems"):
				seen = [json.dumps(v, sort_keys=True, default=str) for v in value]
				if len(set(seen)) != len(seen):
					out.append(f"{where}: items must be unique")
			if "items" in schema:
				for i, item in enumerate(value):
					out += self.errors(item, schema["items"], f"{path}[{i}]", base)
		if isinstance(value, dict):
			out += self._object(value, schema, path, base)
		return out


def _defaults(node: dict, base: str) -> Any:
	node, base = deref(node, base)
	if "properties" in node:
		out = {}
		for key, sub in node["properties"].items():
			value = _defaults(sub, base)
			if value is not None:
				out[key] = value
		if "default" in node and isinstance(node["default"], dict):
			out = {**copy.deepcopy(node["default"]), **out}
		return out if out or "default" in node else None
	return copy.deepcopy(node.get("default"))


def defaults(name: str) -> dict:
	"""Every key of the schema file ``name`` that has a ``default``, with it; nested tables included."""
	return _defaults(load(name), name) or {}


def node_at(name: str, key: str) -> dict | None:
	"""The schema of the dotted ``key`` in schema file ``name``; ``None`` when it has none."""
	node, base = deref(load(name), name)
	for part in key.split("."):
		props = node.get("properties", {})
		if part in props:
			node, base = deref(props[part], base)
		elif isinstance(node.get("additionalProperties"), dict):
			node, base = deref(node["additionalProperties"], base)
		else:
			return None
	return node
