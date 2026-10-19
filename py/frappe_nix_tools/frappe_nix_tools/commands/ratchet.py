"""``frappe-nix ratchet --base <sha>``: the shrink-only values only shrink (spec S24, §5.9).

The base is the PR's merge-base (``git merge-base "$BASE_SHA" HEAD``), never the base tip.
The ratchet applies only when the base's ``pyproject.toml`` has ``[tool.frappe-nix]``: the
adoption PR is the one that sets each starting value.

R1  [tests.coverage]       ``fail_under(head) ≥ fail_under(base)``
R2  [python-types]         ``ty: ignore`` count(head) ≤ count(base), and each one names a rule
                           and gives a reason of at least 10 characters
R3  [listing]              per semgrep baseline key ``count(head) ≤ count(base)``; no new key
R4  [tests.testmap]        ``[[tool.frappe-nix.untested]]`` targets(head) ⊆ targets(base)
R5  [semgrep or listing]   every ``nosemgrep`` names a rule, and the same or the previous line
                           carries a justification comment (a format rule, on head only)
R6  [typescript.check-js]  ``[[tool.frappe-nix.unchecked-js]]`` paths(head) ⊆ paths(base)
R7  [tests.coverage]       ``[[tool.frappe-nix.coverage-omit]]`` globs(head) ⊆ globs(base)

A rule whose module is off in head is skipped; one whose module is off in base starts from
head (nothing to compare). When ``[project].name`` changed (``frappe-rename-app``), the base's
targets, paths, globs and baseline paths are read under the new package name first, so a
renamed entry is not a new one. Exit 0, or 1 listing each violation.
"""

import argparse
import json
import re
import sys
import tomllib
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from frappe_nix_tools.common import config, pyproject, repo
from frappe_nix_tools.common.report import CLEAN, DRIFT, EnvError, FrappeNixError

_TY_ANY = re.compile(r"#\s*ty:\s*ignore")
_TY_OK = re.compile(r"#\s*ty:\s*ignore\[[a-z0-9-]+(,\s*[a-z0-9-]+)*\]\s+#\s+\S.{9,}")
_NOSEMGREP = re.compile(r"(?:#|//)\s*nosemgrep\b(?P<rest>.*)$")
_NOSEMGREP_RULE = re.compile(r"^:\s*(?P<rules>[\w.-]+(?:\s*,\s*[\w.-]+)*)(?P<tail>.*)$")
_COMMENT_LINE = re.compile(r"^\s*(#|//)\s*\S")
SOURCE = re.compile(r"\.(py|js|mjs|cjs|ts|tsx|vue)$")
BASELINE = "marketplace/semgrep-baseline.json"


def _show(root: Path, sha: str, path: str) -> str | None:
	try:
		return repo.git(root, "show", f"{sha}:./{path}")
	except EnvError:
		return None


def _toml(text: str | None) -> dict:
	if not text:
		return {}
	try:
		return tomllib.loads(text)
	except tomllib.TOMLDecodeError:
		return {}


def _fail_under(doc: dict) -> float | None:
	value = doc.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under")
	return float(value) if isinstance(value, int | float) else None


def _table(doc: dict) -> dict:
	return pyproject.tool_frappe_nix(doc) or {}


def _items(doc: dict, key: str, field: str) -> set[str]:
	return {e.get(field) for e in _table(doc).get(key, []) if isinstance(e, dict) and e.get(field)}


def _package(doc: dict) -> str | None:
	name = doc.get("project", {}).get("name")
	return name.replace("-", "_") if isinstance(name, str) and name else None


def _renamer(old: str | None, new: str | None) -> Callable[[str], str]:
	"""Map a base entry onto head's package name: ``old.x`` and ``old/x`` become ``new.x``/``new/x``."""
	if not old or not new or old == new:
		return lambda s: s

	def rename(s: str) -> str:
		if s == old:
			return new
		if s.startswith((old + ".", old + "/")):
			return new + s[len(old) :]
		return s

	return rename


def _ty_lines(root: Path, ref: str | None) -> list[str]:
	"""Every line with a ``ty: ignore`` in the tracked Python files of ``ref`` (None: the work tree)."""
	args = ["grep", "-n", "-E", r"#\s*ty:\s*ignore"]
	if ref:
		args.append(ref)
	try:
		out = repo.git(root, *args, "--", "*.py")
	except EnvError:
		return []  # git grep exits 1 when nothing matches
	return [line for line in out.splitlines() if line]


def _ty_count(lines: list[str]) -> int:
	return sum(len(_TY_ANY.findall(line)) for line in lines)


def _baseline(text: str | None) -> Counter:
	if not text:
		return Counter()
	try:
		doc = json.loads(text)
	except json.JSONDecodeError:
		return Counter()
	out: Counter = Counter()
	for e in doc.get("findings", []) if isinstance(doc, dict) else []:
		if isinstance(e, dict):
			out[(e.get("rule"), e.get("path"), e.get("line_sha1"))] += int(e.get("count", 1) or 1)
	return out


def nosemgrep_problems(root: Path) -> list[str]:
	"""R5 on the work tree's tracked source files."""
	out = []
	for path in repo.ls_files(root):
		if not SOURCE.search(path) or "node_modules/" in path:
			continue
		try:
			lines = (root / path).read_text().splitlines()
		except (OSError, UnicodeDecodeError):
			continue
		for i, line in enumerate(lines):
			m = _NOSEMGREP.search(line)
			if not m:
				continue
			rule = _NOSEMGREP_RULE.match(m["rest"])
			where = f"{path}:{i + 1}"
			if not rule:
				out.append(f"R5 {where}: `nosemgrep` must name its rule (`# nosemgrep: <rule-id>`)")
				continue
			tail = rule["tail"].strip(" -—:#/")
			before = line[: m.start()]
			prev = lines[i - 1] if i else ""
			justified = (
				len(tail) >= 3
				or re.search(r"(#|//)\s*\S", before) is not None
				or (_COMMENT_LINE.match(prev) is not None and "nosemgrep" not in prev)
			)
			if not justified:
				out.append(
					f"R5 {where}: `nosemgrep: {rule['rules']}` needs a justification comment on the same or the previous line"
				)
	return out


def _modules_of(doc: dict, root: Path) -> dict[str, bool] | None:
	"""The base's module switches, resolved like head's; None when it cannot be resolved."""
	try:
		return config.resolve_doc(doc, root=root).modules
	except FrappeNixError:
		return None


def _cfg_of(doc: dict, root: Path) -> dict:
	try:
		return config.resolve_doc(doc, root=root).cfg
	except FrappeNixError:
		return {}


def _coverage_on(modules: dict, cfg: dict) -> bool:
	return bool(modules.get("tests")) and bool(cfg.get("tests", {}).get("coverage", {}).get("enable", True))


def _testmap_on(modules: dict, cfg: dict) -> bool:
	return bool(modules.get("tests")) and bool(cfg.get("tests", {}).get("testmap", True))


def _checkjs_on(modules: dict, cfg: dict) -> bool:
	return bool(modules.get("typescript")) and bool(cfg.get("typescript", {}).get("check-js", False))


def violations(root: Path, base: str) -> tuple[list[str], list[str]]:
	"""``(violations, notices)`` for the work tree at ``root`` against commit ``base``."""
	base_doc = _toml(_show(root, base, "pyproject.toml"))
	if not pyproject.opted_in(base_doc):
		return [], [f"the base {base[:12]} has no [tool.frappe-nix]: the ratchet starts from this PR"]
	head_doc = pyproject.load(root / "pyproject.toml")
	head = config.resolve(root / "pyproject.toml")
	hm, hc = head.modules, head.cfg
	bm = _modules_of(base_doc, root)
	bc = _cfg_of(base_doc, root)
	notices: list[str] = []
	if bm is None:
		bm, bc = hm, hc
		notices.append("the base's configuration could not be resolved; comparing against head's modules")
	old_pkg, new_pkg = _package(base_doc), _package(head_doc)
	ren = _renamer(old_pkg, new_pkg)
	if old_pkg and new_pkg and old_pkg != new_pkg:
		notices.append(
			f"the package was renamed {old_pkg} → {new_pkg}: base entries are compared under the new name"
		)

	def base_items(key: str, field: str) -> set[str]:
		return {ren(v) for v in _items(base_doc, key, field)}

	out: list[str] = []

	def both(on) -> bool:
		return on(hm, hc) and on(bm, bc)

	if both(_coverage_on):
		h, b = _fail_under(head_doc), _fail_under(base_doc)
		if b is not None and (h is None or h < b):
			out.append(f"R1 pyproject.toml: [tool.coverage.report] fail_under went down: {b} → {h}")
		new = _items(head_doc, "coverage-omit", "glob") - base_items("coverage-omit", "glob")
		out += [f"R7 pyproject.toml: new [[tool.frappe-nix.coverage-omit]] glob {g!r}" for g in sorted(new)]
	if hm.get("python-types"):
		lines = _ty_lines(root, None)
		for line in lines:
			path, _, rest = line.partition(":")
			lineno, _, text = rest.partition(":")
			if not _TY_OK.search(text):
				out.append(
					f"R2 {path}:{lineno}: `ty: ignore` must name its rule and give a reason: "
					"`# ty: ignore[<rule>]  # <reason, 10+ characters>`"
				)
		if bm.get("python-types"):
			h, b = _ty_count(lines), _ty_count(_ty_lines(root, base))
			if h > b:
				out.append(f"R2: {h} `ty: ignore` comments, the base has {b} (it only goes down)")
	if both(lambda m, _c: m.get("listing")):
		hb = _baseline((root / BASELINE).read_text() if (root / BASELINE).is_file() else None)
		bb: Counter = Counter()
		for (rule, path, sha), count in _baseline(_show(root, base, BASELINE)).items():
			bb[(rule, ren(path), sha)] += count
		for key, count in sorted(hb.items(), key=lambda kv: tuple(str(k) for k in kv[0])):
			if key not in bb:
				out.append(f"R3 {BASELINE}: new entry {key[0]} in {key[1]} (the baseline only shrinks)")
			elif count > bb[key]:
				out.append(f"R3 {BASELINE}: {key[0]} in {key[1]}: count {bb[key]} → {count}")
	if both(_testmap_on):
		new = _items(head_doc, "untested", "target") - base_items("untested", "target")
		out += [f"R4 pyproject.toml: new [[tool.frappe-nix.untested]] target {t!r}" for t in sorted(new)]
	if both(_checkjs_on):
		new = _items(head_doc, "unchecked-js", "path") - base_items("unchecked-js", "path")
		out += [f"R6 pyproject.toml: new [[tool.frappe-nix.unchecked-js]] path {p!r}" for p in sorted(new)]
	if hm.get("semgrep") or hm.get("listing"):
		out += nosemgrep_problems(root)
	return out, notices


def run(args: argparse.Namespace) -> int:
	root = repo.find_up("pyproject.toml").parent
	try:
		repo.git(root, "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}")
	except EnvError as e:
		raise EnvError(
			f"the base {args.base!r} is not a commit here (actions/checkout: fetch-depth: 0)"
		) from e
	found, notices = violations(root, args.base)
	for n in notices:
		print(f"notice: {n}", file=sys.stderr)
	for v in found:
		print(v)
	if not found:
		print("frappe-nix ratchet: ok")
	return DRIFT if found else CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"ratchet",
		help="the shrink-only values only shrink (R1-R7, against the merge-base)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("--base", required=True, help="the merge-base commit to compare against")
	p.set_defaults(func=run)
