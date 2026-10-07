"""``ironclad compat``: versions, ranges and majors agree (spec §5.9, rules C1 to C9).

The prek hook every app runs (``.pre-commit-config.yaml``), and part of ``pr-policy`` on a
release PR. It reads the app in the current directory:

C1  package.json's version major is ≤ frappe-major; equal once a ``v<major>.*`` tag exists
    or on a ``release-please--*`` branch
C2  package.json ``frappe`` = ``{major, branch}``
C3  flake.nix ``frappeVersion``, and the flake.lock ``frappe`` and siblings' refs, are
    ``version-<major>``
C4  the ``[tool.bench.frappe-dependencies]`` range rules (L3)
C5  the version block holds, and ``__version__`` = package.json = release-please manifest
C6  ``hooks.required_apps`` ⊆ siblings; frappe is not in ``required_apps``
C7  no hand-written frappe globals (augmentations only in ``types/<app>.augment.d.ts``,
    each member ``// app-owned: <reason>``); ``.git-blame-ignore-revs`` is valid
C8  with a Vite config, ``scripts.build`` ends with ``node scripts/ironclad-vite-register.mjs``
C9  every unchecked-js path and coverage-omit glob matches a tracked file; reasons ≥ 10 characters

Exit 0 or 1 (each violation printed as ``C<n> <path>: <problem>``), 2 when a file can't be
parsed. ``--add-blame-ignore <sha>`` appends a commit to ``.git-blame-ignore-revs`` instead.
"""

import argparse
import json
import os
import re
import tomllib
from pathlib import Path

from ironclad.common import data_path, flakelock, repo
from ironclad.common.report import CLEAN, DRIFT, ConfigError, EnvError
from ironclad.scaffold import blocks, context, discover, engine, globs, hooks, package_json, ranges

_DECLARE = re.compile(r"\bdeclare\s+(?:var|let|const)\s+frappe\b")
_DECLARE_NS = re.compile(r"\bdeclare\s+namespace\s+frappe\b")
_NAMESPACE = re.compile(r"(?<!declare )\bnamespace\s+frappe(?:\.[A-Za-z_.]+)?\s*\{")
_WINDOW = re.compile(r"\binterface\s+Window\s*\{")
_MEMBER = re.compile(
	r"^\s*(?:export\s+)?(?:(?:function|const|let|var|class|interface|type|namespace|enum)\s+[A-Za-z_$]|[A-Za-z_$][\w$]*\s*[?:(<])"
)
_APP_OWNED = re.compile(r"^\s*//\s*app-owned:\s*\S")


def _body(text: str, start: int) -> str:
	"""The text inside the braces that open at or after ``start``."""
	open_at = text.index("{", start)
	depth = 0
	for i in range(open_at, len(text)):
		if text[i] == "{":
			depth += 1
		elif text[i] == "}":
			depth -= 1
			if depth == 0:
				return text[open_at + 1 : i]
	return text[open_at + 1 :]


def globals_problems(root: Path, app: str, tracked: list[str], generated: list[str]) -> list[str]:
	"""C7's frappe-globals half: redeclarations, and augmentations outside their one file."""
	augment = f"types/{app}.augment.d.ts"
	skip = ["**/node_modules/**", "types/doctypes.d.ts", "marketplace/shots.d.ts", *generated]
	out = []
	for path in tracked:
		if not re.search(r"\.(c|m)?tsx?$|\.vue$", path) or globs.match_any(skip, path):
			continue
		try:
			text = (root / path).read_text()
		except (OSError, UnicodeDecodeError):
			continue
		if _DECLARE.search(text):
			out.append(f"{path}: `declare var/let/const frappe` redeclares frappe-types' global")
		if _DECLARE_NS.search(text):
			out.append(f"{path}: `declare namespace frappe` redeclares frappe-types' namespace")
		for m in _WINDOW.finditer(text):
			if re.search(r"^\s*(?:readonly\s+)?frappe\s*[?:]", _body(text, m.start()), re.M):
				out.append(f"{path}: `interface Window` declares a frappe member")
		spaces = list(_NAMESPACE.finditer(text))
		if not spaces:
			continue
		if path != augment:
			out.append(f"{path}: frappe namespace augmentations belong in {augment}")
			continue
		if "declare global" not in text:
			out.append(f"{path}: the frappe augmentations must sit inside `declare global {{ … }}`")
		for m in spaces:
			lines = _body(text, m.start()).split("\n")
			depth = 0
			previous = ""
			for line in lines:
				stripped = line.strip()
				if depth == 0 and stripped and _MEMBER.match(line) and not _APP_OWNED.match(previous):
					out.append(
						f"{path}: `{stripped[:60]}` needs a `// app-owned: <reason>` comment on the line before"
					)
				depth += line.count("{") - line.count("}")
				if stripped:
					previous = line
	return out


def _package(root: Path) -> dict:
	doc = engine.load_package(root)
	return doc or {}


def _tags(root: Path, major: int) -> bool:
	try:
		return bool(repo.git(root, "tag", "--list", f"v{major}.*").strip())
	except EnvError:
		return False


def _branch(root: Path) -> str:
	head = os.environ.get("GITHUB_HEAD_REF") or ""
	if head:
		return head
	try:
		return repo.git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
	except EnvError:
		return ""


def violations(root: Path) -> list[str]:
	app = engine.load_app(root)
	cfg, _created = engine.config_for(app, None)
	major = int(cfg["frappe-major"])
	branch = f"version-{major}"
	pkg = _package(root)
	required = hooks.required_apps(app.hooks)
	out: list[str] = []

	# C1, C2
	version = pkg.get("version")
	have = context.app_version(root, app.name)
	if isinstance(version, str):
		head = version.split(".", 1)[0]
		if not head.isdigit():
			out.append(f"C1 package.json: version {version!r} is not semver")
		else:
			strict = _tags(root, major) or _branch(root).startswith("release-please--")
			if int(head) > major or (strict and int(head) != major):
				rule = "must be" if strict else "may not exceed"
				out.append(f"C1 package.json: version {version} — its major {rule} the Frappe major {major}")
	else:
		out.append("C1 package.json: no version")
	if pkg.get("frappe") != {"major": str(major), "branch": branch}:
		out.append(f'C2 package.json: "frappe" must be {{"major": "{major}", "branch": "{branch}"}}')

	# C3
	flake = engine.read(root, "flake.nix") or ""
	m = re.search(r'frappeVersion\s*=\s*"([^"]+)"', flake)
	if m and m[1] != branch:
		out.append(f"C3 flake.nix: frappeVersion is {m[1]!r}, not {branch!r}")
	if (root / "flake.lock").is_file():
		lock = flakelock.load(root / "flake.lock")
		for name in ["frappe", *cfg.get("siblings", [])]:
			found = flakelock.node_at(lock, [hooks.bare(name)])
			ref = (found[1].get("original") or {}).get("ref") if found else None
			if found and ref != branch:
				out.append(f"C3 flake.lock: {hooks.bare(name)} follows {ref!r}, not {branch!r}")

	# C4
	out += [f"C4 pyproject.toml: {p}" for p in ranges.problems(app.pyproject, required, major)]

	# C5
	init = engine.read(root, f"{app.name}/__init__.py")
	if init is not None:
		wanted, offending = blocks.init_py(init)
		if wanted != init:
			out.append(f"C5 {app.name}/__init__.py: __version__ is not in the x-release-please block form")
		for line in offending:
			out.append(
				f"C5 {app.name}/__init__.py: only comments may sit outside the version block: {line.strip()}"
			)
	manifest_text = engine.read(root, ".release-please-manifest.json")
	manifest = None
	if manifest_text:
		doc = json.loads(manifest_text)
		if not isinstance(doc, dict) or not isinstance(doc.get("."), str):
			raise ConfigError('.release-please-manifest.json: must be {".": "<version>"}')
		manifest = doc["."]
	if not (have == version == manifest):
		out.append(
			f"C5 versions disagree: __version__ {have!r}, package.json {version!r}, .release-please-manifest.json {manifest!r}"
		)

	# C6
	siblings = {hooks.bare(s) for s in cfg.get("siblings", [])}
	for r in required:
		if hooks.bare(r) == "frappe":
			out.append("C6 hooks.py: required_apps must not name frappe")
		elif hooks.bare(r) not in siblings:
			out.append(f"C6 hooks.py: required app {r!r} is not in [tool.ironclad].siblings")

	# C7
	out += [f"C7 {p}" for p in globals_problems(root, app.name, app.tracked, list(cfg.get("generated", [])))]
	blame = engine.read(root, ".git-blame-ignore-revs")
	if blame is not None:
		out += [f"C7 .git-blame-ignore-revs: {p}" for p in engine.blame_problems(root, blame)]

	# C8
	# discover.vite, the rule sync's build-append and §2.8's forbidden-build rule use too.
	vite = discover.facts(root, app.name, cfg, app.tracked)["vite"]
	build = (pkg.get("scripts") or {}).get("build", "")
	if vite and not str(build).rstrip().endswith(package_json.VITE_REGISTER):
		out.append(f"C8 package.json: scripts.build must end with `{package_json.VITE_REGISTER}` (S30)")

	# C9
	tracked = set(app.tracked)
	for u in cfg.get("unchecked-js", []):
		if u["path"] not in tracked:
			out.append(f"C9 [[tool.ironclad.unchecked-js]] {u['path']}: not a tracked file (stale entry)")
		if len(u.get("reason", "")) < 10:
			out.append(
				f"C9 [[tool.ironclad.unchecked-js]] {u['path']}: the reason needs at least 10 characters"
			)
	for o in cfg.get("coverage-omit", []):
		if not globs.select([o["glob"]], app.tracked):
			out.append(
				f"C9 [[tool.ironclad.coverage-omit]] {o['glob']}: matches no tracked file (stale entry)"
			)
		if len(o.get("reason", "")) < 10:
			out.append(
				f"C9 [[tool.ironclad.coverage-omit]] {o['glob']}: the reason needs at least 10 characters"
			)
	return out


def add_blame_ignore(root: Path, sha: str) -> int:
	full = repo.git(root, "rev-parse", "--verify", f"{sha}^{{commit}}").strip()
	subject = repo.git(root, "log", "-1", "--format=%s", full).strip()
	path = root / ".git-blame-ignore-revs"
	text = path.read_text() if path.is_file() else ""
	if any(line.startswith(full) for line in text.splitlines()):
		print(f"{full} is already listed")
		return CLEAN
	if not text:
		text = data_path("templates/git-blame-ignore-revs.j2").read_text()
	if not text.endswith("\n"):
		text += "\n"
	path.write_text(f"{text}{full}  # {subject}\n")
	print(f"+ {full}  # {subject}")
	return CLEAN


def run(args: argparse.Namespace) -> int:
	root = Path.cwd()
	repo.toplevel(root)
	if args.add_blame_ignore:
		return add_blame_ignore(root, args.add_blame_ignore)
	try:
		found = violations(root)
	except (json.JSONDecodeError, tomllib.TOMLDecodeError) as e:
		raise ConfigError(str(e)) from e
	for line in found:
		print(line)
	return DRIFT if found else CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"compat",
		help="check that versions, ranges and majors agree (C1-C9)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument(
		"--add-blame-ignore", metavar="SHA", help="append SHA and its subject to .git-blame-ignore-revs"
	)
	p.set_defaults(func=run)
