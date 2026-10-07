"""``pyproject.toml``: the ``toml-merge`` strategy (spec §2.12).

tomlkit edits keys in place, so comments, order and every key sync does not own stay byte
for byte. A managed table the file lacks is written as text after the last existing
``[tool.*]`` table (tomlkit would put it wherever its container ends, without the blank
lines around it).

Each managed key belongs to one module's group and is managed only while that module is
on: ``[project]`` ``requires-python``/``dynamic``, ``[build-system]`` (with
``metadata.build-backend = "flit"`` only) and ``[tool.bench.frappe-dependencies]`` are
``metadata``'s, ``[tool.ruff*]`` ``python-lint``'s, ``[tool.ty*]`` ``python-types``',
``[tool.coverage*]`` ``tests``' (with ``tests.coverage.enable``), and ``[tool.vulture]`` and
``[tool.test_utils.*]`` ``test-utils``'. When a module turns off, each of its keys that
still holds the rendered value is removed; any other value is left as the app's, with a
warning (§3.3 step 6). "Turns off" means it was on in ``HEAD``'s configuration
(``ctx.previous``): a module that was never on owns nothing yet, so opting in with
``minimal`` never removes the ruff or coverage settings an app already had.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import tomlkit
import tomlkit.items

from frappe_nix_tools.common import known_apps
from frappe_nix_tools.common.report import DRIFT, INVALID
from frappe_nix_tools.scaffold import hooks

FRAPPE_APPS = ("frappe", "erpnext", "hrms", "payments")
COVERAGE_OMIT = ["*/tests/*", "*/test_*.py", "*/patches/*"]
VULTURE_EXCLUDE = [".venv/", "node_modules/", ".frappe-nix/", ".dev-dist/"]

# test_utils' static_analysis resolves every dotted path an app calls (frappe.call, a
# whitelisted method in hooks.py) against the app and the apps beside it in a bench. Outside
# one (prek at the repo root, CI's lint job) it has no frappe or sibling sources, so their
# namespaces are whitelisted; the app may list more ([tool.test_utils.static-analysis]).
STATIC_ANALYSIS = ("tool", "test_utils", "static-analysis")


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
	"""Whether ``module``'s group was on at ``HEAD``: its keys are then sync's to retract."""
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
				if not _was_on(ctx, module):
					continue
				have = _plain(_get(doc, path))
				if have is None:
					continue
				if have == value:
					del table_at(table_path)[key]
					prune(table_path)
				else:
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
			project["dynamic"] = ["version"]
		elif "version" not in _plain(dynamic):
			dynamic.append("version")
	out = tomlkit.dumps(doc)
	blocks = [_table_text(path, values) for path, values in missing.items()]
	return Merged(_insert_tables(out, blocks), warnings)


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
		if not _on(ctx, module) and _was_on(ctx, module):
			for path, value in keys:
				view[f"{'.'.join(path)} (off)"] = _get(doc, path) == value
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
