"""The plan: every managed file's current and wanted state, computed in memory.

``--check`` reports the plan; ``--write`` applies it. Both run the same code, so
``--check`` exits 0 right after a ``--sync`` that exited 0 (spec §3.4).

An ``Item`` is one file: what is on disk, what sync wants there (``None`` to delete it),
and the problem when the two differ or a rule is broken. A problem sync can fix is drift;
one it can't (a rule on an app-owned key) carries its own exit code and no new content.
"""

import contextlib
import difflib
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ironclad
from ironclad.common import data_path, flakelock, pyproject, repo
from ironclad.common.report import CLEAN, DRIFT, ENVIRONMENT, INVALID, ConfigError, EnvError, Finding
from ironclad.scaffold import (
	blocks,
	context,
	discover,
	floors,
	globs,
	hooks,
	jsonfmt,
	manifest,
	package_json,
	regions,
	tomlmerge,
)
from ironclad.scaffold import render as rendering
from ironclad.scaffold import schema as schema_check

# The version of an app that names none anywhere (§2.8: "<__version__ or 0.1.0>").
DEFAULT_VERSION = "0.1.0"
BLAME_LINE = re.compile(r"^[0-9a-f]{40}  # \S.*$")
_FLAKE_INPUT = re.compile(r"^\s{4}(?P<name>[A-Za-z_][A-Za-z0-9_'-]*)\s*=\s*\{")
_FLAKE_ATTR = re.compile(
	r'^\s{4}(?P<name>[A-Za-z_][A-Za-z0-9_\'-]*)\.(?P<attr>url|follows)\s*=\s*"(?P<value>[^"]*)";'
)
_FLAKE_INNER = re.compile(r'^\s{6}(?P<attr>url|follows)\s*=\s*"(?P<value>[^"]*)";')
_SKEW = re.compile(
	r"Avunu/frappe-nix/\.github/workflows/app-[A-Za-z0-9_-]+\.ya?ml@(?P<sha>[0-9a-f]{40})(?:[ \t]+#[ \t]*v?(?P<ver>\S+))?"
)


@dataclass
class Item:
	path: str
	strategy: str
	current: str | None
	wanted: str | None
	problem: str = ""
	code: int = CLEAN
	command: str | None = None
	phase: str = "b"

	@property
	def changes(self) -> bool:
		return self.code == DRIFT and (self.wanted != self.current or self.command is not None)

	def diff(self) -> str:
		if self.wanted is None and self.current is None:
			return ""
		a = (self.current or "").splitlines(keepends=True)
		b = (self.wanted or "").splitlines(keepends=True) if self.wanted is not None else []
		return "".join(difflib.unified_diff(a, b, "current", "rendered"))

	def finding(self) -> Finding:
		return Finding(
			self.path, self.strategy, self.problem, self.diff() if self.wanted != self.current else ""
		)


@dataclass
class Plan:
	root: Path
	app: context.App
	cfg: dict
	ctx: context.NS
	items: list[Item] = field(default_factory=list)
	created_config: dict | None = None
	# The one version the version block, the release-please manifest seed and package.json
	# are given when sync writes them (C5 holds from the first sync): see ``effective_version``.
	version: str = DEFAULT_VERSION

	def add(self, item: Item) -> None:
		self.items.append(item)

	@property
	def problems(self) -> list[Item]:
		return [i for i in self.items if i.code != CLEAN]

	@property
	def code(self) -> int:
		return max((i.code for i in self.items), default=CLEAN)


def read(root: Path, path: str) -> str | None:
	"""The text of ``path`` under ``root``; ``None`` when it is missing or a directory.

	A symlink is refused, never followed: ``--check`` runs on untrusted pull requests, and a
	committed link to a file outside the repository would print that file in the diff (and
	``--write`` would write through it)."""
	if (root / path).is_symlink():
		raise ConfigError(f"{path} is a symlink: a managed file must be a regular file (remove the link)")
	try:
		return (root / path).read_text()
	except FileNotFoundError:
		return None
	except IsADirectoryError:
		return None


# --- configuration -------------------------------------------------------------


def flake_facts(text: str | None) -> tuple[int | None, list[str], str | None]:
	"""``frappeVersion``'s major, the sibling names and the ``siteName`` an existing ``flake.nix`` declares."""
	if not text:
		return None, [], None
	m = re.search(r'frappeVersion\s*=\s*"version-(\d+)"', text)
	block = re.search(r"siblings\s*=\s*\[(?P<body>.*?)\];", text, re.S)
	names = re.findall(r'name\s*=\s*"([^"]+)"', block["body"]) if block else []
	site = re.search(r'siteName\s*=\s*"([^"$\\]+)"', text)
	return (int(m[1]) if m else None), names, (site[1] if site else None)


def new_config(
	root: Path,
	app: str,
	app_hooks: dict,
	frappe_version: str | None,
	package: dict | None,
	tracked: list[str],
	site: str | None = None,
) -> dict:
	"""``[tool.ironclad]`` as sync creates it (§3.3 step 1). ``site`` is ``--site``
	(``frappe-init --app --site``), which wins over the site an existing flake names."""
	major = None
	if frappe_version:
		m = re.fullmatch(r"version-(\d+)", frappe_version)
		if not m:
			raise ConfigError(f"--frappe-version must be version-<major>, not {frappe_version!r}")
		major = int(m[1])
	flake_major, flake_siblings, flake_site = flake_facts(read(root, "flake.nix"))
	major = major or flake_major
	if major is None:
		raise ConfigError(
			"there is no [tool.ironclad] yet and nothing names the Frappe major: pass --frappe-version version-<N>"
		)
	siblings: list[str] = []
	for spelling in [*hooks.required_apps(app_hooks), *flake_siblings]:
		if hooks.bare(spelling) == "frappe":
			continue
		name = spelling if spelling.startswith("Avunu/") else hooks.bare(spelling)
		if name not in siblings and not any(hooks.bare(s) == hooks.bare(name) for s in siblings):
			siblings.append(name)
	cfg: dict[str, Any] = {"schema": 1, "frappe-major": major, "siblings": siblings}
	if site is not None and not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", site):
		raise ConfigError(f"--site {site!r} is not a site name (lower-case letters, digits, '.' and '-')")
	flake_site = site or flake_site
	# The dev site an existing frappe-nix flake already uses: a new name would orphan every
	# developer's site state (carbon_frappe's is carbon.localhost, not carbon-frappe.localhost).
	if (
		flake_site
		and re.fullmatch(r"[a-z0-9][a-z0-9.-]*", flake_site)
		and flake_site != f"{app.replace('_', '-')}.localhost"
	):
		cfg["site"] = flake_site
	# An app whose build script is a real step with nothing sync can see building:
	# say so, rather than create a configuration that fails its own first check. The
	# same discovery as the rendered package.json and compat's C8 (no SPA is declared yet).
	scripts = (package or {}).get("scripts") or {}
	facts = discover.facts(root, app, {}, tracked)
	if isinstance(scripts, dict) and "build" in scripts and not (facts["vite"] or facts["nested_frontends"]):
		cfg["build"] = True
	return cfg


def validate_config(raw: dict) -> dict:
	errs = schema_check.errors(raw, pyproject.schema())
	if errs:
		raise ConfigError("; ".join(errs))
	return pyproject.config({"tool": {"ironclad": raw}})


def load_app(root: Path) -> context.App:
	name = repo.app_name(root)
	doc = pyproject.load(root / "pyproject.toml")
	tracked = repo.ls_files(root)
	return context.App(root, name, doc, hooks.read(root / name / "hooks.py"), tracked)


def load_package(root: Path) -> dict | None:
	text = read(root, "package.json")
	if text is None:
		return None
	try:
		doc = json.loads(text)
	except json.JSONDecodeError as e:
		raise ConfigError(f"package.json: {e}") from e
	if not isinstance(doc, dict):
		raise ConfigError("package.json is not an object")
	return doc


def config_for(
	app: context.App, frappe_version: str | None, site: str | None = None
) -> tuple[dict, dict | None]:
	"""The validated configuration, and the raw table when sync has to create it."""
	raw = pyproject.tool_ironclad(app.pyproject)
	created = None
	if raw is None:
		raw = created = new_config(
			app.root, app.name, app.hooks, frappe_version, load_package(app.root), app.tracked, site
		)
	return validate_config(raw), created


def _check_regions(path: str, text: str, entry: manifest.Entry) -> None:
	"""A local region may add, never redefine (§3.2)."""
	inside: list[str] = []
	outside: list[str] = []
	current_region = None
	for line in text.splitlines():
		m = re.match(r"^\s*# ironclad:local-(begin|end) ", line)
		if m:
			current_region = line if m[1] == "begin" else None
			continue
		(inside if current_region else outside).append(line)

	def keys(lines: list[str]) -> set[str]:
		found: set[str] = set()
		eco = None
		for line in lines:
			if m := re.match(r"^\s*-\s+id:\s*(\S+)", line):
				found.add(f"hook id {m[1]}")
			if m := re.match(r"^\s*-\s+package-ecosystem:\s*(\S+)", line):
				eco = m[1]
			if eco and (m := re.match(r"^\s+directory:\s*(\S+)", line)):
				found.add(f"updates entry {eco} {m[1]}")
				eco = None
			if entry.header == "ini" and (m := re.match(r"^\s*(\[[^\]]+\])\s*$", line)):
				found.add(f"section {m[1]}")
		return found

	clash = sorted(keys(inside) & keys(outside))
	if clash:
		raise ConfigError(f"{path}: the local region redefines what sync manages: {', '.join(clash)}")


# --- the plan --------------------------------------------------------------------


def _whole(plan: Plan, entry: manifest.Entry, path: str, current: str | None) -> str:
	text = rendering.render(entry.template or "", plan.ctx, current)
	if entry.local_regions:
		text = regions.splice(text, current, path, entry.local_regions)
		_check_regions(path, text, entry)
	return rendering.header(entry.header, bool(entry.local_regions), entry.header_note) + text


def _json(text: str | None, path: str) -> Any:
	if text is None:
		return None
	try:
		return json.loads(text)
	except json.JSONDecodeError as e:
		raise ConfigError(f"{path}: {e}") from e


def effective_version(root: Path, app: str, package: dict | None) -> str:
	"""The version sync writes wherever it writes one: ``__version__``, else the release-please
	manifest's, else package.json's, else 0.1.0. Each later source only fills a gap, so a
	first sync leaves the three agreeing (C5) whichever of them the app already had."""
	have = context.app_version(root, app)
	if have:
		return have
	try:
		manifest_doc = json.loads(read(root, ".release-please-manifest.json") or "null")
	except json.JSONDecodeError:
		manifest_doc = None
	if isinstance(manifest_doc, dict) and isinstance(manifest_doc.get("."), str) and manifest_doc["."]:
		return manifest_doc["."]
	version = (package or {}).get("version")
	if isinstance(version, str) and version:
		return version
	return DEFAULT_VERSION


def _seed_text(plan: Plan, entry: manifest.Entry) -> str:
	if entry.handler == "release-please-manifest":
		return jsonfmt.dumps({".": plan.version})
	return rendering.render(entry.template or "", plan.ctx, None)


def blame_problems(root: Path, text: str) -> list[str]:
	"""``.git-blame-ignore-revs`` rules (§2.20); reachability only when the history is all there."""
	out = []
	shas = []
	for number, line in enumerate(text.splitlines(), 1):
		if not line.strip() or line.startswith("#"):
			continue
		if not BLAME_LINE.match(line):
			out.append(f"line {number} is not `<40-hex sha>  # <subject>`: {line}")
		else:
			shas.append(line[:40])
	try:
		shallow = repo.git(root, "rev-parse", "--is-shallow-repository").strip() == "true"
	except EnvError:
		shallow = True
	if not shallow:
		for sha in shas:
			try:
				repo.git(root, "merge-base", "--is-ancestor", sha, "HEAD")
			except EnvError:
				out.append(f"{sha} is not reachable from HEAD")
	return out


def _validate_seed(plan: Plan, entry: manifest.Entry, path: str, current: str) -> list[Item]:
	out = []
	if entry.handler == "blame-ignore-revs":
		for problem in blame_problems(plan.root, current):
			out.append(Item(path, "seed", current, current, problem, DRIFT))
	elif entry.handler == "release-please-manifest":
		doc = _json(current, path)
		if not isinstance(doc, dict) or not isinstance(doc.get("."), str):
			out.append(Item(path, "seed", current, current, 'must be {".": "<version>"}', INVALID))
	return out


def _spa_config(ctx: context.NS, path: str, current: str | None) -> bool:
	"""``path`` is a tsconfig sync manages, but what is there is a Vite app's own config."""
	if current is None or not ctx.discover.vite:
		return False
	if not re.fullmatch(r"tsconfig[^/]*\.json", path):
		return False
	return rendering.BASE not in current.split("\n", 1)[0]


def _spa_problem(path: str) -> str:
	return (
		f"{path} is the app's own TypeScript config (it has no ironclad:managed header) and"
		" the app has a Vite config: declare it in [[tool.ironclad.typescript.spa]]"
		f' (tsconfig = "{path}"), or delete it to let sync manage {path}'
	)


def _yarn_lock_missing(plan: Plan, lock: str) -> list[str]:
	"""``package_json.yarn_lock_missing`` for the ``package.json`` on disk."""
	try:
		package = load_package(plan.root)
	except ConfigError:
		return []  # reported by the package.json entry
	local: set[str] = set()
	if isinstance(package, dict) and package.get("workspaces"):
		for path in plan.app.tracked:
			if path != "package.json" and path.endswith("/package.json"):
				with contextlib.suppress(ConfigError, json.JSONDecodeError):
					doc = json.loads(read(plan.root, path) or "{}")
					if isinstance(doc, dict) and isinstance(doc.get("name"), str):
						local.add(doc["name"])
	return package_json.yarn_lock_missing(package, lock, local)


def _entry_item(plan: Plan, entry: manifest.Entry, path: str, seed_manifest: bool) -> list[Item]:
	"""The item(s) for one entry whose ``when`` holds."""
	root, ctx = plan.root, plan.ctx
	current = read(root, path)
	strategy = entry.strategy
	out: list[Item] = []

	def item(
		wanted: str | None, problem: str = "", code: int | None = None, command: str | None = None
	) -> Item:
		if code is None:
			code = DRIFT if (wanted != current or command) else CLEAN
		if code == DRIFT and not problem:
			problem = "missing" if current is None else "differs from the rendered file"
		return Item(path, strategy, current, wanted, problem, code, command, entry.phase)

	if strategy == "whole" and _spa_config(ctx, path, current):
		# §2.9: sync never writes over an SPA's config. A Vite app's own tsconfig that no
		# [[tool.ironclad.typescript.spa]] names yet is one sync would otherwise replace.
		out.append(item(current, _spa_problem(path), INVALID))
	elif strategy == "whole":
		out.append(item(_whole(plan, entry, path, current)))
	elif strategy == "blocks" and entry.handler == "gitignore":
		body = data_path("templates/gitignore.block").read_text()
		out.append(item(blocks.gitignore(current, body, path)))
	elif strategy == "blocks" and entry.handler == "init-py":
		wanted, offending = blocks.init_py(current, plan.version)
		out.append(item(wanted))
		if offending:
			out.append(
				Item(
					path,
					strategy,
					current,
					current,
					"only comments may sit outside the version block (marketplace rule); move this code to"
					f" {ctx.app}/api.py: {offending[0].strip()}",
					DRIFT,
				)
			)
	elif strategy == "toml-merge" and entry.handler == "pyproject":
		text = current or ""
		doc = tomllib.loads(text)
		if plan.created_config is not None:
			text = tomlmerge.add_tool_ironclad(text, plan.created_config)
		wanted = tomlmerge.merge(text, ctx)
		same = tomlmerge.managed_view(doc, ctx) == tomlmerge.managed_view(tomllib.loads(wanted), ctx)
		if plan.created_config is not None:
			same = False
		out.append(item(wanted if not same else current, "managed keys differ" if not same else ""))
		for code, problem in tomlmerge.problems(doc, ctx):
			out.append(Item(path, strategy, current, current, problem, code))
	elif strategy == "json-merge" and entry.handler == "package-json":
		doc = _json(current, path)
		if doc is not None and not isinstance(doc, dict):
			raise ConfigError(f"{path} is not a JSON object")
		merged = package_json.merge(doc, ctx, version=plan.version if seed_manifest else None)
		if merged == doc:
			out.append(item(current))
		else:
			out.append(item(jsonfmt.stringify(merged), "managed keys differ" if doc is not None else ""))
		for code, problem in package_json.problems(merged, ctx):
			out.append(Item(path, strategy, current, current, problem, code))
	elif strategy == "json-merge" and entry.handler == "stylelint":
		doc = _json(current, path)
		merged = package_json.stylelint_merge(doc, ctx)
		out.append(item(current if merged == doc else jsonfmt.dumps(merged)))
	elif strategy == "seed":
		if entry.command:
			missing = current is None
			problem = ""
			if not missing and entry.handler == "uv-lock":
				short = floors.lock_shortfalls(root / path, ctx.floors.get("uv", {}))
				if short:
					problem = f"below its floor or missing from the lock: {', '.join(short)}"
			if current is not None and entry.handler == "yarn-lock":
				absent = _yarn_lock_missing(plan, current)
				if absent:
					more = f" and {len(absent) - 5} more" if len(absent) > 5 else ""
					problem = f"does not lock package.json's {', '.join(absent[:5])}{more}"
			if missing or problem:
				out.append(item(current, problem or "missing", DRIFT, entry.command))
			else:
				out.append(item(current))
		elif current is None:
			wanted = _seed_text(plan, entry)
			out.append(item(wanted))
		else:
			out.append(item(current))
			out += _validate_seed(plan, entry, path, current)
	else:
		raise manifest.ManifestError(f"{entry.fragment}: {path}: no handler for {strategy}/{entry.handler}")
	return out


def _requirement_names(text: str) -> list[str]:
	"""The package names a requirements file lists (PEP 503-normalised), options skipped."""
	names = []
	for line in text.splitlines():
		line = line.split("#", 1)[0].strip()
		if not line or line.startswith("-"):
			continue
		m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
		if m:
			names.append(re.sub(r"[-_.]+", "-", m[0]).lower())
	return names


def _missing_deps(plan: Plan, text: str) -> list[str]:
	"""The packages ``text`` lists that ``[project].dependencies`` does not."""
	deps = plan.app.pyproject.get("project", {}).get("dependencies", [])
	have = set(_requirement_names("\n".join(d for d in deps if isinstance(d, str))))
	return sorted({n for n in _requirement_names(text) if n not in have})


def _retired(plan: Plan, managed_paths: set[str], only: set[str] | None) -> list[Item]:
	out = []
	for path in plan.app.tracked:
		if only is not None and path not in only:
			continue
		link = (plan.root / path).is_symlink()
		if not link and not (plan.root / path).is_file():
			continue
		for rule in manifest.load().retire:
			if not globs.match(rule.glob, path) or globs.match_any(list(rule.unless), path):
				continue
			if rule.unmanaged and path in managed_paths:
				continue
			if link:
				# Never read through a link: deleting the link is all retiring it takes, and a
				# rule that looks inside the file cannot apply to one.
				if not (rule.contains or rule.only_section or rule.deps_in_project):
					out.append(Item(path, "retire", "", None, f"legacy file ({rule.rule})", DRIFT))
					break
				continue
			text = read(plan.root, path) or ""
			if rule.contains and not any(marker in text for marker in rule.contains):
				continue
			if rule.only_section:
				sections = set(re.findall(r"^\s*\[([^\]]+)\]\s*$", text, re.M))
				if sections != {rule.only_section}:
					continue
			missing = _missing_deps(plan, text) if rule.deps_in_project else []
			if missing:
				out.append(
					Item(
						path,
						"retire",
						text,
						text,
						f"legacy file ({rule.rule}), but [project].dependencies lacks"
						f" {', '.join(missing)}: add them there, then sync deletes it",
						INVALID,
					)
				)
			else:
				out.append(Item(path, "retire", text, None, f"legacy file ({rule.rule})", DRIFT))
			break
	return out


def _absent(plan: Plan, entry: manifest.Entry, path: str) -> Item | None:
	"""An entry whose ``when`` is false: delete the file only when sync wrote it as it is."""
	current = read(plan.root, path)
	if current is None:
		return None
	if entry.strategy == "whole" and _spa_config(plan.ctx, path, current):
		# A Vite app's own tsconfig where sync renders none (no browser, desk or scripts
		# project): the same §2.9 case as the one _entry_item reports, never sync's to delete.
		return Item(path, entry.strategy, current, current, _spa_problem(path), INVALID)
	would = None
	if entry.strategy == "whole":
		try:
			would = _whole(plan, entry, path, current)
		except Exception:
			would = None
		# What a render under the previous context produced is not knowable here, but a file
		# that still opens with the managed header and holds nothing in its local regions is
		# one nobody edited (the header says not to): it is sync's to delete.
		head = rendering.header(entry.header, bool(entry.local_regions), entry.header_note)
		if head and current.startswith(head):
			try:
				untouched = not regions.bodies(current, path, entry.local_regions).strip()
			except ConfigError:
				untouched = False
			if untouched:
				would = current
	elif entry.handler == "stylelint":
		would = jsonfmt.dumps(package_json.stylelint_created(plan.ctx))
		if _json(current, path) == package_json.stylelint_created(plan.ctx):
			would = current
	if would is not None and would == current:
		return Item(
			path,
			entry.strategy,
			current,
			None,
			"should not exist (sync wrote it; its condition no longer holds)",
			DRIFT,
		)
	return Item(
		path,
		entry.strategy,
		current,
		current,
		"file should not exist, and it is not what sync wrote (no managed header, or edited), so"
		f" sync leaves it: delete {path} if the app no longer needs it",
		DRIFT,
	)


# --- whole-repo checks -------------------------------------------------------------


def locked_rev(root: Path) -> str | None:
	lock = root / "flake.lock"
	if not lock.is_file():
		return None
	try:
		return flakelock.frappe_nix_rev(flakelock.load(lock))
	except EnvError:
		return None


def check_excludes(app: context.App, cfg: dict) -> None:
	"""``typescript.exclude`` is for non-source paths only (§2.1): sources go in unchecked-js."""
	for glob in cfg.get("typescript", {}).get("exclude", []):
		hit = [
			p
			for p in app.tracked
			if p.startswith(f"{app.name}/") and re.search(r"\.(js|ts|vue)$", p) and globs.match(glob, p)
		]
		if hit:
			raise ConfigError(
				f"[tool.ironclad.typescript].exclude {glob!r} matches source file {hit[0]}: list it in"
				" [[tool.ironclad.unchecked-js]] or declare an SPA instead"
			)


def build(
	root: Path,
	*,
	rev: str | None = None,
	frappe_version: str | None = None,
	site: str | None = None,
	only: list[str] | None = None,
	options: dict | None = None,
	phases: tuple[str, ...] = ("a", "b"),
) -> Plan:
	"""The plan for the app at ``root``."""
	app = load_app(root)
	cfg, created = config_for(app, frappe_version, site)
	check_excludes(app, cfg)
	if rev is None:
		rev = locked_rev(root)
	man = manifest.load()
	ctx = context.build(app, cfg, rev=rev, floors=man.floors, options=options)
	plan = Plan(root, app, cfg, ctx, created_config=created)
	try:
		package = load_package(root)
	except ConfigError:
		package = None  # reported by the package.json entry
	plan.version = effective_version(root, app.name, package)
	only_set = set(only) if only else None
	seed_manifest = read(root, ".release-please-manifest.json") is None
	managed_paths: set[str] = set()
	for entry in man.entries:
		path = rendering.render_string(entry.path, ctx)
		managed_paths.add(path)
		if entry.phase not in phases or (only_set is not None and path not in only_set):
			continue
		if rendering.evaluate(entry.when, ctx):
			for it in _entry_item(plan, entry, path, seed_manifest):
				plan.add(it)
		else:
			gone = _absent(plan, entry, path)
			if gone:
				plan.add(gone)
	if "b" in phases:
		for it in _retired(plan, managed_paths, only_set):
			plan.add(it)
	return plan


def input_spec(attr: str, value: str) -> str:
	"""A flake input reference in one comparable form: ``follows:<a>/<b>``, or the URL with a
	forge's owner and repository lower-cased (Nix matches them case-insensitively)."""
	if attr == "follows":
		return f"follows:{value}"
	forge = re.fullmatch(r"(github|gitlab|sourcehut):([^/?]+)/([^/?]+)(?:/([^?]+))?", value)
	if not forge:
		return value
	kind, owner, repo_name, ref = forge.groups()
	return f"{kind}:{owner.lower()}/{repo_name.lower()}" + (f"/{ref}" if ref else "")


def flake_input_specs(text: str) -> dict[str, str | None]:
	"""Each input a rendered ``flake.nix`` declares (top level of ``inputs = { … };``), with
	its ``url`` or ``follows:<path>`` normalised by ``input_spec``; ``None`` when it shows neither."""
	m = re.search(r"\n  inputs = \{\n(?P<body>.*?)\n  \};\n", text, re.S)
	specs: dict[str, str | None] = {}
	block = None
	for line in m["body"].splitlines() if m else []:
		if (one := _FLAKE_ATTR.match(line)) and not block:
			specs[one["name"]] = input_spec(one["attr"], one["value"])
		elif (opened := _FLAKE_INPUT.match(line)) and not block:
			block = opened["name"]
			specs.setdefault(block, None)
		elif block and re.match(r"^\s{4}\};", line):
			block = None
		elif block and (inner := _FLAKE_INNER.match(line)):
			specs[block] = input_spec(inner["attr"], inner["value"])
	return dict(sorted(specs.items()))


def flake_inputs(text: str) -> list[str]:
	"""The input names a rendered ``flake.nix`` declares."""
	return list(flake_input_specs(text))


def locked_spec(lock: dict, ref: object) -> str | None:
	"""What a root input of ``flake.lock`` was locked from, in ``input_spec``'s form; ``None``
	when the lock records it in a form this can't compare (not a forge, not a ``follows``)."""
	if isinstance(ref, list):
		return "follows:" + "/".join(str(step) for step in ref)
	node = lock["nodes"].get(ref) if isinstance(ref, str) else None
	original = node.get("original") if isinstance(node, dict) else None
	if not isinstance(original, dict) or original.get("type") not in ("github", "gitlab", "sourcehut"):
		return None
	tail = original.get("ref") or original.get("rev")
	return input_spec(
		"url",
		f"{original['type']}:{original.get('owner')}/{original.get('repo')}" + (f"/{tail}" if tail else ""),
	)


def stale_inputs(lock: dict, wanted: dict[str, str | None], skip: tuple[str, ...] = ()) -> list[str]:
	"""The inputs ``flake.nix`` declares that ``flake.lock`` has no node for, or locked from
	another URL or ``follows`` (a frappe-major bump moves ``frappe`` to ``version-<N+1>``)."""
	root_inputs = lock["nodes"].get(lock["root"], {}).get("inputs", {})
	out = []
	for name, spec in wanted.items():
		if name in skip:
			continue
		if name not in root_inputs:
			out.append(f"{name} (no node)")
			continue
		have = locked_spec(lock, root_inputs[name])
		if spec is not None and have is not None and have != spec:
			out.append(
				f"{name} (locked from {have.removeprefix('follows:')}, flake.nix has {spec.removeprefix('follows:')})"
			)
	return out


def lock_problems(plan: Plan) -> list[Item]:
	"""``flake.lock`` must hold a node for every input ``flake.nix`` declares, locked from the
	URL (or ``follows``) ``flake.nix`` gives it (§3.3)."""
	text = read(plan.root, "flake.nix")
	if text is None:
		return []
	lock_path = plan.root / "flake.lock"
	if not lock_path.is_file():
		return [Item("flake.lock", "lock", None, None, "missing: run `frappe-init --sync`", DRIFT)]
	try:
		lock = flakelock.load(lock_path)
	except EnvError as e:
		return [Item("flake.lock", "lock", None, None, str(e), ENVIRONMENT)]
	# frappe-nix's self-tests lock the checkout under test in its place (IRONCLAD_FRAPPE_NIX_URL).
	skip = ("frappe-nix",) if os.environ.get("IRONCLAD_ALLOW_SKEW") == "1" else ()
	stale = stale_inputs(lock, flake_input_specs(text), skip)
	if stale:
		return [
			Item(
				"flake.lock",
				"lock",
				None,
				None,
				f"does not follow flake.nix: {'; '.join(stale)}: run `frappe-init --sync`",
				DRIFT,
			)
		]
	return []


def skew_problems(plan: Plan, expect_rev: str | None) -> list[Item]:
	"""§3.7: the caller workflows, the lock and the running ironclad name one frappe-nix."""
	if os.environ.get("IRONCLAD_ALLOW_SKEW") == "1":
		return []
	rev = plan.ctx.frappe_nix.rev
	out = []
	if expect_rev and rev and expect_rev != rev:
		out.append(
			Item(
				"flake.lock",
				"skew",
				None,
				None,
				f"version skew: this ironclad was installed from {expect_rev}, flake.lock pins frappe-nix {rev}",
				ENVIRONMENT,
			)
		)
	for path in plan.app.tracked:
		if not globs.match(".github/workflows/*.y*ml", path):
			continue
		for m in _SKEW.finditer(read(plan.root, path) or ""):
			if m["sha"] != rev or (m["ver"] or "") != ironclad.__version__:
				out.append(
					Item(
						path,
						"skew",
						None,
						None,
						f"version skew: calls frappe-nix @{m['sha'][:12]} # v{m['ver']}, but flake.lock pins"
						f" {rev[:12] or '(nothing)'} and this ironclad is v{ironclad.__version__}",
						ENVIRONMENT,
					)
				)
				break
	return out
