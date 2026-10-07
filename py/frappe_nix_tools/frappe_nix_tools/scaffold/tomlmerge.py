"""``pyproject.toml``: the ``toml-merge`` strategy (spec §2.12).

tomlkit edits keys in place, so comments, order and every key sync does not own stay byte
for byte. A managed table the file lacks is written as text in its place among the managed
tables the file has (``CANON_TABLES``), else after the last existing ``[tool.*]`` table
(tomlkit would put it wherever its container ends, without the blank lines around it); a
managed key a table lacks goes before the first later one it has. So turning a module off
and on again restores the file byte for byte.

Each managed key belongs to one module's group and is managed only while that module is
on: ``[project]`` ``requires-python``/``dynamic``, ``[build-system]`` (with
``metadata.build-backend = "flit"`` only) and ``[tool.bench.frappe-dependencies]`` are
``metadata``'s, ``[tool.ruff*]`` ``python-lint``'s, ``[tool.ty*]`` ``python-types``',
``[tool.coverage*]`` ``tests``' (with ``tests.coverage.enable``), and ``[tool.vulture]`` and
``[tool.test_utils.*]`` ``test-utils``'. When a module turns off, each of its keys that
still holds the rendered value is removed; any other value is left as the app's, with a
warning on the run that turns it off (§3.3 step 6). "Was on" means on in any
``[tool.frappe-nix]`` table the app has committed since it opted in (``ctx.history``,
which the engine reads from git): a module that was never on owns nothing yet, so opting
in with ``minimal`` never removes the ruff or coverage settings an app already had, while
a module turned off in a commit made before syncing is retracted all the same.

A key that holds what ``bench new-app`` writes for the app's Frappe major (``BENCH_NEW_APP``:
the flit ``[build-system]``, ruff's ``line-length = 110``, ``requires-python`` and so on), and
a ``[tool.bench.frappe-dependencies]`` equal to the rendered one, is the app's baseline, not a
leftover: it is never retracted, so the history is never asked
about it. Most apps keep those keys, and a module that never was on must not make a shallow
clone (CI's default ``fetch-depth: 1``, ``repo audit``) fail ``--check``.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import tomlkit
import tomlkit.items

from frappe_nix_tools.common import known_apps
from frappe_nix_tools.common.report import DRIFT, INVALID
from frappe_nix_tools.scaffold import context, hooks

FRAPPE_APPS = ("frappe", "erpnext", "hrms", "payments")
COVERAGE_OMIT = ["*/tests/*", "*/test_*.py", "*/patches/*"]
VULTURE_EXCLUDE = [".venv/", "node_modules/", ".frappe-nix/", ".dev-dist/"]

# test_utils' static_analysis resolves every dotted path an app calls (frappe.call, a
# whitelisted method in hooks.py) against the app and the apps beside it in a bench. Outside
# one (prek at the repo root, CI's lint job) it has no frappe or sibling sources, so their
# namespaces are whitelisted; the app may list more ([tool.test_utils.static-analysis]).
STATIC_ANALYSIS = ("tool", "test_utils", "static-analysis")


# What ``bench new-app`` writes into pyproject.toml (frappe/utils/boilerplate.py), by Frappe
# major: an app's baseline. A managed key holding one of these values is never retracted.
_BENCH_RUFF = {
	("build-system", "requires"): ["flit_core >=3.4,<4"],
	("build-system", "build-backend"): "flit_core.buildapi",
	("tool", "ruff", "line-length"): 110,
	("tool", "ruff", "lint", "select"): ["F", "E", "W", "I", "UP", "B", "RUF"],
	("tool", "ruff", "lint", "typing-modules"): ["frappe.types.DF"],
	("tool", "ruff", "format", "quote-style"): "double",
	("tool", "ruff", "format", "indent-style"): "tab",
	("tool", "ruff", "format", "docstring-code-format"): True,
}
_BENCH_IGNORE_15 = [
	*("B017", "B018", "B023", "B904", "E101", "E402", "E501", "E741"),
	*("F401", "F403", "F405", "F722", "W191"),
]
BENCH_NEW_APP: dict[int, dict[tuple[str, ...], Any]] = {
	15: {
		**_BENCH_RUFF,
		("project", "requires-python"): ">=3.10",
		("tool", "ruff", "target-version"): "py310",
		("tool", "ruff", "lint", "ignore"): _BENCH_IGNORE_15,
	},
	16: {
		**_BENCH_RUFF,
		("project", "requires-python"): ">=3.14",
		("tool", "ruff", "target-version"): "py314",
		("tool", "ruff", "lint", "ignore"): [*_BENCH_IGNORE_15, "UP030", "UP031", "UP032", "UP037", "UP040"],
	},
}


BENCH_DEPENDENCIES = ("tool", "bench", "frappe-dependencies")


def bench_baseline(ctx: Any, path: tuple[str, ...], have: Any, rendered: Any) -> bool:
	"""Whether ``have`` at ``path`` is the app's baseline: what ``bench new-app`` writes for the
	app's Frappe major (a later major reads as the newest one known), or a
	``[tool.bench.frappe-dependencies]`` that names exactly the app's bench apps and their
	ranges, which bench reads to install the app."""
	if path == BENCH_DEPENDENCIES:
		return have == rendered
	table = BENCH_NEW_APP[15 if ctx.frappe.major <= 15 else 16]
	return path in table and have == table[path]


@dataclass
class Merged:
	"""The merged text, and the keys sync left as the app's although their module is off."""

	text: str
	warnings: list[str] = field(default_factory=list)


def static_whitelist(ctx: Any) -> list[str]:
	"""The ``whitelist`` entries ``[tool.test_utils.static-analysis]`` must hold."""
	names = ["frappe", *(s.name for s in ctx.siblings if s.name != "frappe")]
	return [f"{name}.*" for name in dict.fromkeys(names)]


def _on(ctx: Any, module: str) -> bool:
	if module == "tests":
		return bool(ctx.modules.get("tests")) and bool(ctx.cfg["tests"]["coverage"].get("enable", True))
	if module == "build-system":
		return bool(ctx.modules.get("metadata")) and ctx.cfg["metadata"].get("build-backend") == "flit"
	return bool(ctx.modules.get(module))


def _was_on(ctx: Any, module: str) -> bool:
	"""Whether ``module``'s group was on in any ``[tool.frappe-nix]`` table since the app opted
	in (``ctx.history``): its keys are then sync's to retract."""
	return context.ever(ctx.get("history"), lambda h: _on(h, module))


def _turns_off(ctx: Any, module: str) -> bool:
	"""Whether ``module``'s group was on at ``HEAD``: the run that turns it off says once what
	it leaves to the app."""
	previous = ctx.get("previous")
	return bool(previous) and _on(previous, module)


def groups(ctx: Any) -> dict[str, list[tuple[tuple[str, ...], Any]]]:
	"""Every managed key with its exact value, as ``((table, …, key), value)`` in file order,
	by the module that owns it (``build-system`` is ``metadata`` with flit)."""
	py = ctx.frappe.python
	apps = known_apps.merged(ctx.cfg.get("known-apps"))
	deps = {"frappe": ctx.frappe.range}
	for spelling in ctx.required_apps:
		name = hooks.bare(spelling)
		if name == "frappe":
			continue
		sib = next((s for s in ctx.siblings if s.name == name), None)
		deps[name] = sib.range if sib else known_apps.resolve(spelling, ctx.frappe.major, apps).range
	omit = list(COVERAGE_OMIT) + [o["glob"] for o in ctx.cfg.get("coverage-omit", [])]
	lint = ctx.cfg["python-lint"]
	return {
		"metadata": [
			(("project", "requires-python"), f">={py}"),
			(("tool", "bench", "frappe-dependencies"), deps),
		],
		"build-system": [
			(("build-system", "requires"), ["flit_core >=3.4,<4"]),
			(("build-system", "build-backend"), "flit_core.buildapi"),
		],
		"python-lint": [
			(("tool", "ruff", "line-length"), lint["line-length"]),
			(("tool", "ruff", "target-version"), "py" + py.replace(".", "")),
			(("tool", "ruff", "lint", "select"), list(lint["select"])),
			(("tool", "ruff", "lint", "ignore"), list(lint["ignore"])),
			(("tool", "ruff", "lint", "typing-modules"), list(lint["typing-modules"])),
			(("tool", "ruff", "format", "quote-style"), lint["quote-style"]),
			(("tool", "ruff", "format", "indent-style"), lint["indent-style"]),
			(("tool", "ruff", "format", "docstring-code-format"), True),
		],
		"python-types": [
			(("tool", "ty", "environment", "python-version"), py),
			(("tool", "ty", "src", "include"), [ctx.app]),
			(
				("tool", "ty", "terminal", "error-on-warning"),
				bool(ctx.cfg["python-types"]["error-on-warning"]),
			),
		],
		"tests": [
			(("tool", "coverage", "run", "omit"), omit),
			(("tool", "coverage", "report", "show_missing"), True),
			(("tool", "coverage", "report", "skip_covered"), True),
			(("tool", "coverage", "report", "precision"), 1),
			(
				("tool", "coverage", "report", "exclude_also"),
				["if TYPE_CHECKING:", "raise NotImplementedError", "@(abc\\.)?abstractmethod"],
			),
		],
		# test_utils' static_analysis runs vulture over the whole checkout, which reads this
		# table: without it, tools/.venv (uv's environment for tools/) and the generated
		# bench are scanned as the app's own dead code.
		"test-utils": [(("tool", "vulture", "exclude"), VULTURE_EXCLUDE)],
	}


def managed(ctx: Any) -> list[tuple[tuple[str, ...], Any]]:
	"""The managed keys of the modules that are on, in file order."""
	return [kv for module, keys in groups(ctx).items() if _on(ctx, module) for kv in keys]


_HEADER = re.compile(r"^\s*\[\[?\s*(?P<name>[^\]\[]+?)\s*\]\]?\s*(#.*)?$")


def _get(doc: Any, path: tuple[str, ...]) -> Any:
	cur = doc
	for part in path:
		if not isinstance(cur, dict) or part not in cur:
			return None
		cur = cur[part]
	return cur


def _plain(value: Any) -> Any:
	return value.unwrap() if hasattr(value, "unwrap") else value


def _hides(codes: object, rules: tuple[str, ...]) -> list[str]:
	"""The ``rules`` a ruff selector list turns off: an exact code, a prefix of one, or ``ALL``."""
	if not isinstance(codes, list):
		return []
	sel = [c for c in codes if isinstance(c, str)]
	return [r for r in rules if any(c == "ALL" or (c and r.startswith(c)) for c in sel)]


def _ruff_glob(glob: str) -> re.Pattern[str]:
	"""A ruff ``per-file-ignores`` glob as a regex, with globset's defaults: unlike ``globs``,
	``*`` and ``?`` cross ``/``; ``[…]`` and ``{a,b}`` are classes and alternations."""
	out: list[str] = []
	i, depth = 0, 0
	while i < len(glob):
		c = glob[i]
		if glob.startswith("**/", i):
			out.append("(?:.*/)?")
			i += 3
			continue
		if c == "*":
			out.append(".*")
			i += 2 if glob.startswith("**", i) else 1
			continue
		if c == "?":
			out.append(".")
		elif c == "[" and (end := glob.find("]", i + 2)) != -1:
			body = glob[i + 1 : end]
			if body.startswith("!"):
				body = "^" + body[1:]
			out.append("[" + body.replace("\\", "\\\\") + "]")
			i = end + 1
			continue
		elif c == "{":
			out.append("(?:")
			depth += 1
		elif c == "}" and depth:
			out.append(")")
			depth -= 1
		elif c == "," and depth:
			out.append("|")
		else:
			out.append(re.escape(c))
		i += 1
	return re.compile("".join(out) + ")" * depth + r"\Z")


def _package_wide(glob: str, app: str) -> bool:
	"""Whether ruff applies a ``per-file-ignores`` entry to every module of the package.

	ruff matches a pattern against the path relative to the project root and also against
	the file's basename (so ``"*.py"`` is every file), and a leading ``!`` negates it."""
	negated = glob.startswith("!")
	pattern = glob.removeprefix("!").removeprefix("./")
	try:
		rx = _ruff_glob(pattern)
	except re.error:
		return False
	probes = (f"{app}/testmap_probe.py", f"{app}/a/b/testmap_probe.py")
	hits = [bool(rx.match(p) or rx.match(p.rsplit("/", 1)[-1])) for p in probes]
	return not any(hits) if negated else all(hits)


def problems(doc: dict, ctx: Any) -> list[tuple[int, str]]:
	"""The app-owned keys that break a rule, each only while its module is on: exit 1 for
	what only the app can fix, 2 for the forbidden (§2.12)."""
	out: list[tuple[int, str]] = []
	modules = ctx.modules
	if modules.get("metadata"):
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
			out.append(
				(DRIFT, f"[project].dependencies must not name {', '.join(named)} (they are bench apps)")
			)
		license = _get(doc, ("project", "license"))
		want = ctx.org.get("license")
		if want and license is not None:
			text = license if isinstance(license, str) else (license or {}).get("text")
			if text != want:
				out.append(
					(DRIFT, f"[project].license is {text!r}, but the profile's org.license is {want!r}")
				)
		if _on(ctx, "build-system") and _get(doc, ("tool", "poetry")) is not None:
			out.append((INVALID, "[tool.poetry] is forbidden while metadata.build-backend is flit (D6)"))
	if _on(ctx, "tests"):
		fail = _get(doc, ("tool", "coverage", "report", "fail_under"))
		if fail is not None and (
			isinstance(fail, bool) or not isinstance(fail, int | float) or not 0 <= fail <= 100
		):
			out.append((INVALID, "[tool.coverage.report].fail_under must be a number from 0 to 100"))
		run = _get(doc, ("tool", "coverage", "run")) or {}
		for key in ("source", "relative_files"):
			if key in run:
				out.append((INVALID, f"[tool.coverage.run].{key} is forbidden (frappe-test sets it)"))
	if modules.get("python-lint"):
		allowed = list(ctx.cfg["python-lint"]["ignore"])
		top = _get(doc, ("tool", "ruff")) or {}
		lint = _get(doc, ("tool", "ruff", "lint")) or {}
		# ruff still reads the pre-0.2 spellings at the top of [tool.ruff], so both tables count.
		for table, keys in (("[tool.ruff]", top), ("[tool.ruff.lint]", lint)):
			for key in ("extend-select", "extend-ignore", "unfixable", "isort"):
				if key in keys:
					out.append(
						(
							INVALID,
							f"{table}.{key} is forbidden: change the rule set through [tool.frappe-nix.python-lint]",
						)
					)
			# [tool.ruff.lint].ignore is a managed exact key: the merge sets it, so a longer list
			# there (bench new-app writes 13 codes) is drift that --sync fixes. The top-level
			# spelling is not managed, and sync never touches it.
			extra = [c for c in (keys.get("ignore") or []) if c not in allowed]
			if extra and table == "[tool.ruff]":
				out.append(
					(
						INVALID,
						f"{table}.ignore may hold only python-lint.ignore ({', '.join(allowed) or 'nothing'}), not {', '.join(extra)}",
					)
				)
			for key in ("per-file-ignores", "extend-per-file-ignores"):
				for glob, codes in (keys.get(key) or {}).items():
					bad = _hides(codes, ("F401", "E402"))
					if bad and _package_wide(glob, ctx.app):
						out.append(
							(
								INVALID,
								f"{table}.{key} {glob!r} may not ignore {', '.join(bad)} package-wide",
							)
						)
	if modules.get("python-types"):
		ty = _get(doc, ("tool", "ty")) or {}
		for rule, level in (ty.get("rules") or {}).items():
			if level != "error":
				out.append((INVALID, f'[tool.ty.rules].{rule} = {level!r}: only "error" is allowed'))
		if "overrides" in ty:
			out.append((INVALID, "[tool.ty.overrides] is forbidden"))
	return out


def _table_text(path: tuple[str, ...], values: dict[str, Any]) -> str:
	lines = [f"[{'.'.join(path)}]"]
	for key, value in values.items():
		lines.append(f"{tomlkit.key(key).as_string()} = {tomlkit.item(value).as_string()}")
	return "\n".join(lines) + "\n"


# The managed tables in the order sync lays them out. A table sync adds goes where this order
# puts it among those the file has (``_table_position``), so turning a module off and on again
# restores ``pyproject.toml`` byte for byte.
HEAD_TABLES = ["project", "build-system", "tool.bench.frappe-dependencies"]
CANON_TABLES = [
	"build-system",
	"tool.bench.frappe-dependencies",
	"tool.ruff",
	"tool.ruff.lint",
	"tool.ruff.format",
	"tool.ty.environment",
	"tool.ty.src",
	"tool.ty.terminal",
	"tool.coverage.run",
	"tool.coverage.report",
	"tool.vulture",
	"tool.test_utils.static-analysis",
]
# Keys sync adds to a table the app owns, in their place: before the first later one there.
KEY_ORDER = {("project",): ["requires-python", "dynamic", "dependencies"]}


def _is_tool(name: str) -> bool:
	return name == "tool" or name.startswith("tool.")


def _after_tools(headers: list[tuple[int, str]], end: int) -> int:
	"""The line after the last ``[tool.*]`` table: where a table goes that has no place."""
	tool = [i for i, name in headers if _is_tool(name)]
	if not tool:
		return end
	after = [i for i, _ in headers if i > tool[-1]]
	return after[0] if after else end


def _table_position(headers: list[tuple[int, str]], name: str, end: int) -> int:
	"""The line a new managed table ``name`` goes before.

	``[build-system]`` and ``[tool.bench.frappe-dependencies]`` follow ``[project]`` (and each
	other); a managed ``[tool.*]`` table goes before the first managed table ranked after it
	that follows the app's own ``[tool.*]`` tables (``[tool.frappe-nix]`` among them), else
	after the last ``[tool.*]`` table."""
	if name in HEAD_TABLES[1:]:
		for anchor in reversed(HEAD_TABLES[: HEAD_TABLES.index(name)]):
			found = [j for j, (_, n) in enumerate(headers) if n == anchor]
			if found:
				j = found[-1] + 1
				while j < len(headers) and headers[j][1].startswith(anchor + "."):
					j += 1
				return headers[j][0] if j < len(headers) else end
		return _after_tools(headers, end)
	rank = CANON_TABLES.index(name) if name in CANON_TABLES else len(CANON_TABLES)
	own = [j for j, (_, n) in enumerate(headers) if _is_tool(n) and n not in CANON_TABLES]
	for i, n in headers[own[-1] + 1 if own else 0 :]:
		if n in CANON_TABLES and CANON_TABLES.index(n) > rank:
			return i
	return _after_tools(headers, end)


def _headers(lines: list[str]) -> list[tuple[int, str]]:
	return [(i, m["name"].strip()) for i, line in enumerate(lines) if (m := _HEADER.match(line))]


def _insert_at(text: str, at: int, blocks: list[str]) -> str:
	"""``text`` with ``blocks`` (TOML tables) before line ``at``, a blank line around them."""
	lines = text.splitlines(keepends=True)
	before, rest = lines[:at], lines[at:]
	while before and not before[-1].strip():
		rest.insert(0, before.pop())
	head = "".join(before)
	if head and not head.endswith("\n"):
		head += "\n"
	middle = "\n".join(blocks)
	tail = "".join(rest).lstrip("\n")
	return head + ("\n" if head else "") + middle + ("\n" + tail if tail else "")


def _place_tables(text: str, missing: dict[tuple[str, ...], dict[str, Any]]) -> str:
	"""``text`` with each missing managed table written in its place (``_table_position``)."""

	def rank(path: tuple[str, ...]) -> int:
		name = ".".join(path)
		return CANON_TABLES.index(name) if name in CANON_TABLES else len(CANON_TABLES)

	for path in sorted(missing, key=rank):
		lines = text.splitlines(keepends=True)
		at = _table_position(_headers(lines), ".".join(path), len(lines))
		text = _insert_at(text, at, [_table_text(path, missing[path])])
	return text


def _put_key(table: Any, key: str, value: Any, order: list[str]) -> None:
	"""Set a key the table lacks before the first later key of ``order`` it has, else at the end."""
	later = order[order.index(key) + 1 :] if key in order else []
	container = getattr(table, "value", None)
	body = getattr(container, "body", None)
	at = next(
		(i for i, (k, _) in enumerate(body or []) if k is not None and getattr(k, "key", None) in later),
		None,
	)
	if at is None or not hasattr(container, "_insert_at"):
		table[key] = value
		return
	container._insert_at(at, key, value)  # tomlkit has no public insert-before


def _insert_tables(text: str, blocks: list[str]) -> str:
	"""``text`` with ``blocks`` (TOML tables) placed after the last ``[tool.*]`` table."""
	if not blocks:
		return text
	lines = text.splitlines(keepends=True)
	return _insert_at(text, _after_tools(_headers(lines), len(lines)), blocks)


def merge(text: str, ctx: Any, *, seed_fail_under: bool = True) -> Merged:
	"""``text`` with every managed key of an enabled module set, and every managed key of a
	disabled one that still holds its rendered value removed. ``dynamic`` gains
	``version`` (with ``metadata``); a missing ``fail_under`` is seeded as
	``tests.coverage.initial-floor`` (with ``tests``), and is the app's from then on."""
	doc = tomlkit.parse(text)
	missing: dict[tuple[str, ...], dict[str, Any]] = {}
	warnings: list[str] = []

	def table_at(path: tuple[str, ...]) -> Any:
		cur: Any = doc
		for part in path:
			if not isinstance(cur, dict) or part not in cur:
				return None
			cur = cur[part]
		return cur if isinstance(cur, dict) else None

	def prune(path: tuple[str, ...]) -> None:
		"""Remove the tables along ``path`` that the retraction left empty."""
		for n in range(len(path), 0, -1):
			table, parent = table_at(path[:n]), table_at(path[: n - 1]) if n > 1 else doc
			if table is None or len(table) or parent is None:
				return
			del parent[path[n - 1]]

	for module, keys in groups(ctx).items():
		on = _on(ctx, module)
		wanted = list(keys)
		if (
			on
			and module == "tests"
			and seed_fail_under
			and _get(doc.unwrap(), ("tool", "coverage", "report", "fail_under")) is None
		):
			wanted.append(
				(("tool", "coverage", "report", "fail_under"), ctx.cfg["tests"]["coverage"]["initial-floor"])
			)
		for path, value in wanted:
			table_path, key = path[:-1], path[-1]
			if not on:
				# The history is asked only about a key that holds the rendered value and is
				# not bench new-app's (a shallow clone may not know the answer: context.ever).
				have = _plain(_get(doc, path))
				if have is None or bench_baseline(ctx, path, have, value):
					continue
				if have == value:
					if _was_on(ctx, module):
						del table_at(table_path)[key]
						prune(table_path)
				elif _turns_off(ctx, module):
					warnings.append(
						f"pyproject.toml {'.'.join(path)} is the app's now that {module} is off (it differs from what sync wrote)"
					)
				continue
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
			elif key not in table:
				order = KEY_ORDER.get(table_path) or [p[-1] for p, _ in keys if p[:-1] == table_path]
				_put_key(table, key, value, order)
			elif _plain(table.get(key)) != value:
				table[key] = value
	if _on(ctx, "test-utils"):
		required = static_whitelist(ctx)
		table = table_at(STATIC_ANALYSIS)
		if table is None:
			missing.setdefault(STATIC_ANALYSIS, {})["whitelist"] = required
		elif not isinstance(table.get("whitelist"), list):
			table["whitelist"] = required
		else:
			have = _plain(table["whitelist"])
			for entry in required:
				if entry not in have:
					table["whitelist"].append(entry)
	project = table_at(("project",))
	if project is not None and _on(ctx, "metadata"):
		dynamic = project.get("dynamic")
		if dynamic is None:
			_put_key(project, "dynamic", ["version"], KEY_ORDER[("project",)])
		elif "version" not in _plain(dynamic):
			dynamic.append("version")
	return Merged(_place_tables(tomlkit.dumps(doc), missing), warnings)


def _value_text(value: Any) -> str:
	"""A TOML value on one line: arrays and tables inline (``siblings`` may hold objects)."""
	if isinstance(value, dict):
		inner = ", ".join(f"{tomlkit.key(k).as_string()} = {_value_text(v)}" for k, v in value.items())
		return "{ " + inner + " }" if inner else "{}"
	if isinstance(value, list):
		return "[" + ", ".join(_value_text(v) for v in value) + "]"
	return tomlkit.item(value).as_string()


def add_tool_frappe_nix(text: str, values: dict[str, Any]) -> str:
	"""``text`` with a new ``[tool.frappe-nix]`` table (sync creates it once, §2.1)."""
	lines = [
		"[tool.frappe-nix]",
		*(f"{tomlkit.key(k).as_string()} = {_value_text(v)}" for k, v in values.items()),
	]
	return _insert_tables(text, ["\n".join(lines) + "\n"])


def managed_view(doc: dict, ctx: Any) -> dict:
	"""The managed keys' current values, for the semantic comparison ``--check`` makes:
	the enabled modules' keys, and whether each disabled module's rendered keys are gone."""
	view = {".".join(path): _get(doc, path) for path, _ in managed(ctx)}
	for module, keys in groups(ctx).items():
		if _on(ctx, module):
			continue
		left = {
			f"{'.'.join(path)} (off)": _get(doc, path) == value
			for path, value in keys
			if not bench_baseline(ctx, path, _plain(_get(doc, path)), value)
		}
		# None left reads the same either way, so the history is asked only when one is.
		if any(left.values()) and _was_on(ctx, module):
			view.update(left)
	if _on(ctx, "metadata"):
		view["project.dynamic has version"] = "version" in (_get(doc, ("project", "dynamic")) or [])
	if _on(ctx, "test-utils"):
		have = _get(doc, (*STATIC_ANALYSIS, "whitelist"))
		view["static-analysis whitelist"] = isinstance(have, list) and all(
			e in have for e in static_whitelist(ctx)
		)
	if _on(ctx, "tests"):
		view["fail_under present"] = _get(doc, ("tool", "coverage", "report", "fail_under")) is not None
	return view
