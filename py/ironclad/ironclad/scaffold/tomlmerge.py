"""``pyproject.toml``: the ``toml-merge`` strategy (spec §2.12).

tomlkit edits keys in place, so comments, order and every key sync does not own stay byte
for byte. A managed table the file lacks is written as text after the last existing
``[tool.*]`` table (tomlkit would put it wherever its container ends, without the blank
lines around it).
"""

import re
from typing import Any

import tomlkit
import tomlkit.items

from ironclad.common import known_apps
from ironclad.common.report import DRIFT, INVALID
from ironclad.scaffold import globs, hooks

FRAPPE_APPS = ("frappe", "erpnext", "hrms", "payments")
RUFF_SELECT = ["F", "E", "W", "I", "UP", "B", "RUF", "SIM", "C4", "PIE", "PERF", "T20"]
RUFF_IGNORE = ["E501", "W191"]
COVERAGE_OMIT = ["*/tests/*", "*/test_*.py", "*/patches/*"]
VULTURE_EXCLUDE = [".venv/", "node_modules/", ".frappe-nix/", ".dev-dist/"]

_HEADER = re.compile(r"^\s*\[\[?\s*(?P<name>[^\]\[]+?)\s*\]\]?\s*(#.*)?$")


def managed(ctx: Any) -> list[tuple[tuple[str, ...], Any]]:
	"""Every managed key with its exact value, as ``((table, …, key), value)``, in file order."""
	py = ctx.frappe.python
	deps = {"frappe": ctx.frappe.range}
	for spelling in ctx.required_apps:
		name = hooks.bare(spelling)
		if name == "frappe":
			continue
		deps[name] = known_apps.resolve(spelling, ctx.frappe.major).range
	omit = list(COVERAGE_OMIT) + [o["glob"] for o in ctx.cfg.get("coverage-omit", [])]
	return [
		(("project", "requires-python"), f">={py}"),
		(("build-system", "requires"), ["flit_core >=3.4,<4"]),
		(("build-system", "build-backend"), "flit_core.buildapi"),
		(("tool", "bench", "frappe-dependencies"), deps),
		(("tool", "ruff", "line-length"), 110),
		(("tool", "ruff", "target-version"), "py" + py.replace(".", "")),
		(("tool", "ruff", "lint", "select"), RUFF_SELECT),
		(("tool", "ruff", "lint", "ignore"), RUFF_IGNORE),
		(("tool", "ruff", "lint", "typing-modules"), ["frappe.types.DF"]),
		(("tool", "ruff", "format", "quote-style"), "double"),
		(("tool", "ruff", "format", "indent-style"), "tab"),
		(("tool", "ruff", "format", "docstring-code-format"), True),
		(("tool", "ty", "environment", "python-version"), py),
		(("tool", "ty", "src", "include"), [ctx.app]),
		(("tool", "ty", "terminal", "error-on-warning"), True),
		(("tool", "coverage", "run", "omit"), omit),
		(("tool", "coverage", "report", "show_missing"), True),
		(("tool", "coverage", "report", "skip_covered"), True),
		(("tool", "coverage", "report", "precision"), 1),
		(
			("tool", "coverage", "report", "exclude_also"),
			["if TYPE_CHECKING:", "raise NotImplementedError", "@(abc\\.)?abstractmethod"],
		),
		# test_utils' static_analysis runs vulture over the whole checkout, which reads this
		# table: without it, tools/.venv (uv's environment for tools/) and the generated
		# bench are scanned as the app's own dead code.
		(("tool", "vulture", "exclude"), VULTURE_EXCLUDE),
	]


def _get(doc: Any, path: tuple[str, ...]) -> Any:
	cur = doc
	for part in path:
		if not isinstance(cur, dict) or part not in cur:
			return None
		cur = cur[part]
	return cur


def _plain(value: Any) -> Any:
	return value.unwrap() if hasattr(value, "unwrap") else value


def problems(doc: dict, ctx: Any) -> list[tuple[int, str]]:
	"""The app-owned keys that break a rule: exit 1 for what only the app can fix, 2 for the forbidden."""
	out: list[tuple[int, str]] = []
	deps = _get(doc, ("project", "dependencies")) or []
	named = sorted(
		{
			app
			for d in deps
			if isinstance(d, str)
			for app in FRAPPE_APPS
			if re.match(rf"^{app}(\s|[<>=!~;\[(]|$)", d.strip(), re.I)
		}
	)
	if named:
		out.append((DRIFT, f"[project].dependencies must not name {', '.join(named)} (they are bench apps)"))
	fail = _get(doc, ("tool", "coverage", "report", "fail_under"))
	if fail is not None and (
		isinstance(fail, bool) or not isinstance(fail, int | float) or not 0 <= fail <= 100
	):
		out.append((INVALID, "[tool.coverage.report].fail_under must be a number from 0 to 100"))
	lint = _get(doc, ("tool", "ruff", "lint")) or {}
	for key in ("extend-select", "extend-ignore", "unfixable", "isort"):
		if key in lint:
			out.append((INVALID, f"[tool.ruff.lint].{key} is forbidden (the ruff profile is fixed, S27)"))
	extra = [c for c in (lint.get("ignore") or []) if c not in RUFF_IGNORE]
	if extra:
		out.append(
			(
				INVALID,
				f"[tool.ruff.lint].ignore may hold only {', '.join(RUFF_IGNORE)}, not {', '.join(extra)}",
			)
		)
	app = ctx.app
	for glob, codes in (lint.get("per-file-ignores") or {}).items():
		broad = globs.match(glob, f"{app}/ironclad_probe.py") and globs.match(
			glob, f"{app}/a/b/ironclad_probe.py"
		)
		bad = [c for c in codes if c in ("F401", "E402")]
		if broad and bad:
			out.append(
				(
					INVALID,
					f"[tool.ruff.lint.per-file-ignores] {glob!r} may not ignore {', '.join(bad)} package-wide",
				)
			)
	ty = _get(doc, ("tool", "ty")) or {}
	for rule, level in (ty.get("rules") or {}).items():
		if level != "error":
			out.append((INVALID, f'[tool.ty.rules].{rule} = {level!r}: only "error" is allowed'))
	if "overrides" in ty:
		out.append((INVALID, "[tool.ty.overrides] is forbidden"))
	run = _get(doc, ("tool", "coverage", "run")) or {}
	for key in ("source", "relative_files"):
		if key in run:
			out.append((INVALID, f"[tool.coverage.run].{key} is forbidden (frappe-test sets it)"))
	if _get(doc, ("tool", "poetry")) is not None:
		out.append((INVALID, "[tool.poetry] is forbidden (apps build with flit, D6)"))
	return out


def _table_text(path: tuple[str, ...], values: dict[str, Any]) -> str:
	lines = [f"[{'.'.join(path)}]"]
	for key, value in values.items():
		lines.append(f"{tomlkit.key(key).as_string()} = {tomlkit.item(value).as_string()}")
	return "\n".join(lines) + "\n"


def _insert_tables(text: str, blocks: list[str]) -> str:
	"""``text`` with ``blocks`` (TOML tables) placed after the last ``[tool.*]`` table."""
	if not blocks:
		return text
	lines = text.splitlines(keepends=True)
	headers = [(i, m["name"].strip()) for i, line in enumerate(lines) if (m := _HEADER.match(line))]
	tool = [i for i, name in headers if name == "tool" or name.startswith("tool.")]
	if tool:
		after = [i for i, _ in headers if i > tool[-1]]
		at = after[0] if after else len(lines)
	else:
		at = len(lines)
	before, rest = lines[:at], lines[at:]
	while before and not before[-1].strip():
		rest.insert(0, before.pop())
	head = "".join(before)
	if head and not head.endswith("\n"):
		head += "\n"
	middle = "\n".join(blocks)
	tail = "".join(rest).lstrip("\n")
	return head + ("\n" if head else "") + middle + ("\n" + tail if tail else "")


def merge(text: str, ctx: Any, *, seed_fail_under: bool = True) -> str:
	"""``text`` with every managed key set (``dynamic`` gains ``version``; a missing
	``fail_under`` is seeded as 0, and is the app's from then on)."""
	doc = tomlkit.parse(text)
	missing: dict[tuple[str, ...], dict[str, Any]] = {}

	def table_at(path: tuple[str, ...]) -> Any:
		cur: Any = doc
		for part in path:
			if not isinstance(cur, dict) or part not in cur:
				return None
			cur = cur[part]
		return cur if isinstance(cur, dict) else None

	wanted = managed(ctx)
	if seed_fail_under and _get(doc.unwrap(), ("tool", "coverage", "report", "fail_under")) is None:
		wanted.append((("tool", "coverage", "report", "fail_under"), 0))
	for path, value in wanted:
		table_path, key = path[:-1], path[-1]
		if isinstance(value, dict):
			# An exact table (frappe-dependencies): its keys are exactly these.
			table = table_at(path)
			if table is None:
				missing.setdefault(path, {}).update(value)
				continue
			for k in [k for k in table if k not in value]:
				del table[k]
			for k, v in value.items():
				if _plain(table.get(k)) != v:
					table[k] = v
			continue
		table = table_at(table_path)
		if table is None or isinstance(table, tomlkit.items.AoT):
			missing.setdefault(table_path, {})[key] = value
		elif _plain(table.get(key)) != value:
			table[key] = value
	project = table_at(("project",))
	if project is not None:
		dynamic = project.get("dynamic")
		if dynamic is None:
			project["dynamic"] = ["version"]
		elif "version" not in _plain(dynamic):
			dynamic.append("version")
	out = tomlkit.dumps(doc)
	blocks = [_table_text(path, values) for path, values in missing.items()]
	return _insert_tables(out, blocks)


def add_tool_ironclad(text: str, values: dict[str, Any]) -> str:
	"""``text`` with a new ``[tool.ironclad]`` table (sync creates it once, §2.1)."""
	return _insert_tables(text, [_table_text(("tool", "ironclad"), values)])


def managed_view(doc: dict, ctx: Any) -> dict:
	"""The managed keys' current values, for the semantic comparison ``--check`` makes."""
	view = {".".join(path): _get(doc, path) for path, _ in managed(ctx)}
	view["project.dynamic has version"] = "version" in (_get(doc, ("project", "dynamic")) or [])
	view["fail_under present"] = _get(doc, ("tool", "coverage", "report", "fail_under")) is not None
	return view
