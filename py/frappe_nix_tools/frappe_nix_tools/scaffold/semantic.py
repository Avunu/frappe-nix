"""Comparing a ``whole`` file as data rather than bytes (spec §2.10, §3.2).

While ``js.tool`` isn't ``"oxc"``, an app may format the managed YAML, JSON, JSONC and TOML
files with its own formatter (prettier, biome). Such a file is not drift when its first
line, the managed header, is unchanged and it parses to the same data as the rendered one;
sync then leaves it as it is. Any other kind of file, or one that does not parse, is
compared byte for byte.
"""

import json
import re
import tomllib
from typing import Any

import yaml

# Comments and trailing commas, outside strings: what JSONC adds to JSON.
_JSONC = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|,(?=\s*[}\]])', re.S)


def kind(path: str) -> str | None:
	"""The data format of ``path``, when it is one sync compares semantically."""
	name = path.rsplit("/", 1)[-1]
	if name.endswith((".yml", ".yaml")):
		return "yaml"
	if name.endswith(".jsonc") or re.fullmatch(r"tsconfig[^/]*\.json", name):
		return "jsonc"
	if name.endswith(".json"):
		return "json"
	if name.endswith(".toml"):
		return "toml"
	return None


def _jsonc(text: str) -> Any:
	return json.loads(_JSONC.sub(lambda m: m[0] if m[0].startswith('"') else "", text))


def parse(text: str, fmt: str) -> Any:
	"""``text`` as data; raises ``ValueError`` when it does not parse."""
	try:
		if fmt == "yaml":
			return yaml.safe_load(text)
		if fmt == "json":
			return json.loads(text)
		if fmt == "jsonc":
			return _jsonc(text)
		if fmt == "toml":
			return tomllib.loads(text)
	except (yaml.YAMLError, json.JSONDecodeError, tomllib.TOMLDecodeError) as e:
		raise ValueError(str(e)) from e
	raise ValueError(f"not a data format: {fmt}")


def _first_line(text: str) -> str:
	return text.split("\n", 1)[0].strip()


def equal(path: str, current: str | None, rendered: str) -> bool:
	"""Whether ``current`` is ``rendered`` up to formatting: the same header, the same data."""
	fmt = kind(path)
	if current is None or fmt is None:
		return False
	if _first_line(current) != _first_line(rendered):
		return False
	try:
		return parse(current, fmt) == parse(rendered, fmt)
	except ValueError:
		return False
