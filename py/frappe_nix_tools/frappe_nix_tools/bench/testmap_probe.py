"""The testmap probe (spec §5.1.1): which whitelisted functions and hook targets ran.

Run with the bench's interpreter, from anywhere::

    <bench>/env/bin/python testmap_probe.py --site S --app A --sites-path <bench>/sites \\
        --coverage-json OUT/coverage.json --repo-root <repo> --pyproject <repo>/pyproject.toml \\
        --out OUT/testmap.json

It connects to the site, so the hooks it reads are the evaluated ones
(``frappe.get_hooks(app_name=A)``: a hook inside ``if frappe_version >= 16:`` counts) and a
module that queries the database while it is imported still imports. A target is
**tested** when at least one line of its body ran during the coverage run that wrote
``coverage.json``. ``[[tool.frappe-nix.untested]]`` exempts a target; an exemption whose
target is tested, or no longer a target, is **stale**.

Standard library and frappe only: the bench interpreter has neither frappe-nix-tools nor its
dependencies. Exits 0 after writing the report (the verdict is in it), 3 when the site
cannot be reached or an input is unreadable.
"""

import argparse
import ast
import importlib
import inspect
import json
import os
import sys
import tomllib
import types
from pathlib import Path

# The callable-valued hooks of T6. jinja is handled by its two keys, and
# additional_timeline_content and website_context by their values.
T6_HOOKS = (
	"auth_hooks",
	"before_request",
	"after_request",
	"on_session_creation",
	"on_login",
	"on_logout",
	"boot_session",
	"after_install",
	"after_sync",
	"after_migrate",
	"before_uninstall",
	"before_tests",
)
T5_HOOKS = ("override_whitelisted_methods", "permission_query_conditions", "has_permission")
CLASS_HOOKS = ("extend_doctype_class", "override_doctype_class")
API_METHOD = "/api/method/"


def strings(value) -> list[str]:
	"""Every string in a hook value, however frappe nested it (dict values, lists)."""
	if isinstance(value, str):
		return [value]
	if isinstance(value, dict):
		return [s for v in value.values() for s in strings(v)]
	if isinstance(value, list | tuple):
		return [s for v in value for s in strings(v)]
	return []


def hook_targets(hooks: dict, app: str) -> list[tuple[str, str]]:
	"""``(dotted path, kind)`` for T2, T3, T5, T6 and the T4 classes, in hook order."""
	prefix = f"{app}."
	found: list[tuple[str, str]] = []

	def add(values, kind):
		for s in values:
			s = s.strip()
			if s.startswith(prefix):
				found.append((s, kind))

	add(strings(hooks.get("scheduler_events", {})), "T2")
	add(strings(hooks.get("doc_events", {})), "T3")
	for key in CLASS_HOOKS:
		add(strings(hooks.get(key, {})), "T4")
	for key in T5_HOOKS:
		add(strings(hooks.get(key, {})), "T5")
	for key in T6_HOOKS:
		add(strings(hooks.get(key, [])), "T6")
	jinja = hooks.get("jinja", {})
	if isinstance(jinja, dict):
		add(strings(jinja.get("methods", [])), "T6")
		add(strings(jinja.get("filters", [])), "T6")
	add(strings(hooks.get("additional_timeline_content", {})), "T6")
	for value in strings(hooks.get("website_context", {})):
		value = value.strip()
		if value.startswith(API_METHOD):
			value = value[len(API_METHOD) :].split("?", 1)[0]
		add([value], "T6")
	return found


def module_name(package_dir: Path, file: Path) -> str:
	"""``<app>.a.b`` for ``<package_dir>/a/b.py`` (``__init__.py`` names its package)."""
	rel = file.relative_to(package_dir.parent).with_suffix("")
	parts = list(rel.parts)
	if parts[-1] == "__init__":
		parts.pop()
	return ".".join(parts)


def _excluded(rel: Path) -> bool:
	parts = rel.parts
	return (
		"tests" in parts[:-1]
		or "patches" in parts[:-1]
		or "node_modules" in parts
		or parts[-1].startswith("test_")
	)


def _whitelist_names(tree: ast.Module) -> set[str]:
	"""The local names ``from frappe import whitelist [as x]`` binds."""
	names = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.ImportFrom) and node.module == "frappe":
			for alias in node.names:
				if alias.name == "whitelist":
					names.add(alias.asname or alias.name)
	return names


def _is_whitelist(decorator: ast.expr, local: set[str]) -> bool:
	target = decorator.func if isinstance(decorator, ast.Call) else decorator
	if isinstance(target, ast.Attribute):
		return (
			target.attr == "whitelist" and isinstance(target.value, ast.Name) and target.value.id == "frappe"
		)
	return isinstance(target, ast.Name) and target.id in local


def whitelisted(package_dir: Path) -> list[tuple[str, Path, int]]:
	"""T1: ``(dotted path, file, def line)`` of every whitelisted function and method.

	Module-level functions and methods of module-level classes (nested classes too) are
	addressable; a function defined inside another function is not, and is skipped.
	"""
	found = []
	for file in sorted(package_dir.rglob("*.py")):
		rel = file.relative_to(package_dir)
		if _excluded(rel):
			continue
		try:
			tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
		except (SyntaxError, UnicodeDecodeError, OSError):
			continue
		local = _whitelist_names(tree)
		module = module_name(package_dir, file)

		def visit(body, qual):
			for node in body:
				if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
					if any(_is_whitelist(d, local) for d in node.decorator_list):
						found.append((f"{qual}.{node.name}", file, node.lineno))
				elif isinstance(node, ast.ClassDef):
					visit(node.body, f"{qual}.{node.name}")

		visit(tree.body, module)
	return found


def resolve(path: str):
	"""Import the longest importable module prefix of ``path`` and walk the rest.

	Returns ``(object, None)`` or ``(None, error text)``. A module that raises while it is
	imported is an error, not a missing module.
	"""
	parts = path.split(".")
	for cut in range(len(parts), 0, -1):
		name = ".".join(parts[:cut])
		try:
			obj = importlib.import_module(name)
		except ModuleNotFoundError as e:
			if e.name and (name == e.name or name.startswith(e.name + ".")):
				continue
			return None, f"importing {name}: {type(e).__name__}: {e}"
		except Exception as e:
			return None, f"importing {name}: {type(e).__name__}: {e}"
		for attr in parts[cut:]:
			try:
				obj = getattr(obj, attr)
			except AttributeError:
				return None, f"{name} has no attribute {'.'.join(parts[cut:])}"
		return obj, None
	return None, f"no module of {path} can be imported"


def _function(obj):
	"""The plain function behind ``obj`` (unwrapping methods and decorators), or None."""
	if isinstance(obj, staticmethod | classmethod):
		obj = obj.__func__
	if inspect.ismethod(obj):
		obj = obj.__func__
	try:
		obj = inspect.unwrap(obj)
	except ValueError:
		pass
	return obj if inspect.isfunction(obj) else None


def class_methods(cls: type, path: str) -> list[tuple[str, object]]:
	"""T4: the functions a class defines in its own ``__dict__``.

	Dunders are skipped except ``__init__``, and so is every other name starting with ``_``.
	"""
	out = []
	for name, value in vars(cls).items():
		if name.startswith("_") and name != "__init__":
			continue
		fn = _function(value)
		if fn is not None:
			out.append((f"{path}.{name}", fn))
	return out


def module_functions(module: types.ModuleType) -> list[tuple[str, object]]:
	"""The public functions a module defines (a jinja ``methods`` hook may name a module)."""
	return [
		(f"{module.__name__}.{name}", value)
		for name, value in vars(module).items()
		if not name.startswith("_") and inspect.isfunction(value) and value.__module__ == module.__name__
	]


_TREES: dict[Path, ast.Module] = {}


def _tree(file: Path) -> ast.Module:
	if file not in _TREES:
		_TREES[file] = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
	return _TREES[file]


def body_span(file: Path, first_line: int) -> tuple[int, int, int] | None:
	"""``(def line, first body line, last line)`` of the function starting at ``first_line``.

	``first_line`` is a decorator's line or the ``def`` line, as ``co_firstlineno`` gives
	it. The body starts after the signature and the docstring; a function that is only a
	docstring has an empty span (first > last), and so does a one-line function
	(``def ping(): return "pong"``): its body shares the ``def`` line, which runs when the
	module is imported, so no line of it shows whether the function itself ever ran.
	"""
	for node in ast.walk(_tree(file)):
		if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
			continue
		start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
		if first_line not in (start, node.lineno):
			continue
		body = node.body
		if (
			body
			and isinstance(body[0], ast.Expr)
			and isinstance(body[0].value, ast.Constant)
			and isinstance(body[0].value.value, str)
		):
			body = body[1:]
		end = node.end_lineno or node.lineno
		if not body or body[0].lineno <= node.lineno:
			return node.lineno, end + 1, end
		return node.lineno, body[0].lineno, end
	return None


def coverage_index(coverage_json: dict, repo_root: Path) -> dict[str, dict]:
	"""``coverage.json``'s files by real path (its keys are repo-relative or absolute)."""
	index = {}
	for key, entry in coverage_json.get("files", {}).items():
		path = Path(key)
		if not path.is_absolute():
			path = repo_root / path
		index[os.path.realpath(path)] = entry
	return index


def function_lines(fn) -> tuple[Path, int] | None:
	"""``(real source file, first line)`` of a function, or None when it has no source."""
	try:
		file = inspect.getsourcefile(fn)
	except TypeError:
		return None
	if not file:
		return None
	return Path(os.path.realpath(file)), fn.__code__.co_firstlineno


def judge(file: Path, first_line: int, index: dict[str, dict]) -> tuple[int, bool, str | None]:
	"""``(def line, tested, error)`` for the function at ``file:first_line``."""
	span = body_span(file, first_line)
	if span is None:
		return first_line, False, "its definition was not found in the source"
	def_line, start, end = span
	if start > end and end == def_line:
		return (
			def_line,
			False,
			(
				"its body shares the def line, which runs on import, so coverage cannot show it ran; "
				"give the body a line of its own, or exempt it"
			),
		)
	entry = index.get(str(file))
	if entry is None:
		return def_line, False, None
	executed = set(entry.get("executed_lines", []))
	body = {n for n in executed | set(entry.get("missing_lines", [])) if start <= n <= end}
	if not body:
		return def_line, False, "no executable lines in its body"
	return def_line, bool(body & executed), None


def exemptions(pyproject: Path) -> dict[str, str]:
	"""``[[tool.frappe-nix.untested]]``: target → reason."""
	try:
		doc = tomllib.loads(pyproject.read_text())
	except FileNotFoundError:
		return {}
	entries = doc.get("tool", {}).get("frappe-nix", {}).get("untested", [])
	return {e["target"]: e.get("reason", "") for e in entries if isinstance(e, dict) and "target" in e}


def _display(file: Path, repo_root: Path) -> str:
	try:
		return str(file.relative_to(repo_root.resolve()))
	except ValueError:
		return str(file)


def probe(
	hooks: dict, app: str, package_dir: Path, coverage_json: dict, repo_root: Path, exempt: dict[str, str]
) -> dict:
	"""The testmap report for ``app``: every target, whether it ran, and the verdict lists."""
	index = coverage_index(coverage_json, repo_root)
	targets: dict[str, dict] = {}

	def record(path, kind, file=None, line=None, tested=False, error=None):
		if path in targets:
			if kind not in targets[path]["kind"].split("+"):
				targets[path]["kind"] += f"+{kind}"
			return
		entry = {
			"path": path,
			"kind": kind,
			"file": _display(file, repo_root) if file else None,
			"line": line,
			"tested": tested,
			"exempt": path in exempt,
		}
		if error:
			entry["error"] = error
		targets[path] = entry

	def judge_function(path, kind, fn):
		where = function_lines(fn)
		if where is None:
			record(path, kind, error="it has no Python source")
			return
		file, first = where
		line, tested, error = judge(file, first, index)
		record(path, kind, file, line, tested, error)

	for path, file, line in whitelisted(package_dir):
		obj, error = resolve(path)
		fn = _function(obj) if obj is not None else None
		if fn is None:
			record(path, "T1", Path(os.path.realpath(file)), line, False, error or "not a function")
		else:
			judge_function(path, "T1", fn)

	for path, kind in hook_targets(hooks, app):
		obj, error = resolve(path)
		if obj is None:
			record(path, kind, error=error)
		elif kind == "T4":
			if not inspect.isclass(obj):
				record(path, kind, error="not a class")
				continue
			for method_path, fn in class_methods(obj, path):
				judge_function(method_path, kind, fn)
		elif isinstance(obj, types.ModuleType):
			for fn_path, fn in module_functions(obj):
				judge_function(fn_path, kind, fn)
		else:
			fn = _function(obj)
			if fn is None:
				record(path, kind, error="not a function")
			else:
				judge_function(path, kind, fn)

	ordered = list(targets.values())
	untested = [t["path"] for t in ordered if not t["tested"] and not t["exempt"]]
	stale = sorted(target for target in exempt if target not in targets or targets[target]["tested"])
	return {
		"app": app,
		"targets": ordered,
		"untested": untested,
		"exempt": sum(1 for t in ordered if t["exempt"]),
		"stale_exemptions": stale,
	}


def main(argv=None) -> int:
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("--site", required=True)
	parser.add_argument("--app", required=True)
	parser.add_argument("--sites-path", default=".")
	parser.add_argument("--coverage-json", required=True, type=Path)
	parser.add_argument("--repo-root", required=True, type=Path)
	parser.add_argument("--pyproject", required=True, type=Path)
	parser.add_argument("--out", required=True, type=Path)
	args = parser.parse_args(argv)

	try:
		coverage_json = json.loads(args.coverage_json.read_text())
	except (OSError, ValueError) as e:
		print(f"testmap: cannot read {args.coverage_json}: {e}", file=sys.stderr)
		return 3

	import frappe

	try:
		frappe.init(site=args.site, sites_path=str(args.sites_path))
		frappe.connect()
	except Exception as e:
		print(f"testmap: cannot connect to {args.site}: {type(e).__name__}: {e}", file=sys.stderr)
		return 3
	try:
		hooks = frappe.get_hooks(app_name=args.app)
		package_dir = Path(os.path.realpath(frappe.get_app_path(args.app)))
		report = probe(
			dict(hooks),
			args.app,
			package_dir,
			coverage_json,
			args.repo_root,
			exemptions(args.pyproject),
		)
	finally:
		frappe.destroy()
	args.out.write_text(json.dumps(report, indent=2) + "\n")
	return 0


if __name__ == "__main__":
	sys.exit(main())
