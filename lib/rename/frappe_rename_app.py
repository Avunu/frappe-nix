"""frappe-rename-app: rename a Frappe app in place, code and sites (docs/app-standards/spec.md §5.10).

Two halves, run at different times:

    frappe-rename-app code --from OLD --to NEW [--fleet FILE | --profile REF | --no-replace-check]
                           [--dry-run]
        In the app's repository (PR 0 of a rename): `git mv OLD NEW`, the bundle files
        named OLD.*, every dotted path and asset URL in the tracked text, the names in
        pyproject.toml, package.json, hooks.py, CI and release-please, the `modified` of
        every standard JSON that changed, and an override_whitelisted_methods shim for the
        old paths. Module folders and modules.txt stay: the module names are kept, so no
        record moves.

        A pair that is replaced rather than renamed (a new app installed beside the old
        one, `replacedApps`) is refused. frappe-nix knows no such pair itself: they come
        from the `[[replace-apps]]` of the app's resolved profile when the app has
        `[tool.frappe-nix]`, and from the `"rename_mode": "replace"` entries of a fleet
        file (`--fleet`) or the profile `--profile` names. An app without
        `[tool.frappe-nix]` must name one of them, or pass `--no-replace-check`.

    frappe-rename-app --site S OLD=NEW [OLD=NEW ...] [--dry-run] [--scan] [--yes]
        On a site, once NEW's code is on the bench and before `bench migrate` (which
        crashes on an installed app it cannot import): one transaction over the
        installed_apps global, Module Def, the Patch Log, the scheduled jobs (in place),
        the app-name columns and the site-authored scripts and templates that name OLD.
        Run with the bench's interpreter from its sites/ directory. Standard library and
        frappe only.

Exit status: 0 done, or nothing to do; 1 a precondition failed (a dirty tree, a replace
pair, an app missing from the bench, both apps installed, a malformed OLD=NEW); 2 the code
half was given no source of replace pairs, or one it cannot read, or the site half hit a
database error (rolled back); 3 the code half needs frappe-nix-tools and cannot import it.
"""

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import NoReturn

NAME = re.compile(r"^[a-z][a-z0-9_]*$")

# Tracked files the code half never rewrites: history, and locks a tool writes.
SKIP_NAMES = {
	"CHANGELOG.md",
	".git-blame-ignore-revs",
	"yarn.lock",
	"uv.lock",
	"flake.lock",
	"package-lock.json",
	"pnpm-lock.yaml",
}

SHIM_BEGIN = "# frappe-nix:rename-shim-begin"
SHIM_END = "# frappe-nix:rename-shim-end"


def die(message: str, code: int = 1) -> NoReturn:
	print(f"frappe-rename-app: {message}", file=sys.stderr)
	sys.exit(code)


# ── the code half ──────────────────────────────────────────────────────────────


def git(repo: Path, *args: str, check: bool = True) -> str:
	proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
	if check and proc.returncode != 0:
		die(f"git {' '.join(args)}: {proc.stderr.strip()}")
	return proc.stdout


def fleet_pairs(path: Path) -> dict[str, str]:
	"""The ``"rename_mode": "replace"`` entries of a fleet file (spec §4.9): app → target_app."""
	try:
		doc = json.loads(path.read_text())
	except FileNotFoundError:
		die(f"--fleet {path}: no such file", 2)
	except (OSError, ValueError) as e:
		die(f"--fleet {path}: {e}", 2)
	entries = doc.get("apps") if isinstance(doc, dict) else None
	if not isinstance(entries, list):
		die(f"--fleet {path}: not a fleet file (no `apps` list)", 2)
	pairs = {}
	for entry in entries:
		if isinstance(entry, dict) and entry.get("rename_mode") == "replace" and entry.get("app"):
			pairs[str(entry["app"])] = str(entry.get("target_app") or "")
	return pairs


def _resolver():
	"""frappe_nix_tools' config resolver, which reads profiles; exit 3 when it is not importable."""
	try:
		from frappe_nix_tools.common import config, pyproject
		from frappe_nix_tools.common.report import FrappeNixError
	except ImportError:
		die(
			"reading a profile needs frappe-nix-tools, which this interpreter cannot import; "
			"run frappe-rename-app from the dev shell, or pass --fleet or --no-replace-check",
			3,
		)
	return config, pyproject, FrappeNixError


def _replace_apps(cfg: dict) -> dict[str, str]:
	return {
		str(e["from"]): str(e.get("to") or "")
		for e in cfg.get("replace-apps", [])
		if isinstance(e, dict) and e.get("from")
	}


def opted_in(repo: Path) -> bool:
	"""Whether the app has ``[tool.frappe-nix]`` (spec S35), by the same line match the dev shell uses."""
	try:
		text = (repo / "pyproject.toml").read_text(encoding="utf-8")
	except (OSError, UnicodeDecodeError):
		return False
	return any(re.match(r"\s*\[{1,2}tool\.frappe-nix[].]", line) for line in text.splitlines())


def app_pairs(repo: Path) -> dict[str, str]:
	"""The ``[[replace-apps]]`` of an opted-in app's resolved profile (§8.1, §8.4)."""
	config, _, error = _resolver()
	try:
		return _replace_apps(config.resolve(repo / "pyproject.toml").cfg)
	except error as e:
		die(f"could not resolve {repo}/pyproject.toml's profile: {e}", 2)


def profile_pairs(repo: Path, ref: str) -> dict[str, str]:
	"""The ``[[replace-apps]]`` of the profile ``ref`` names, resolved with an empty app layer.

	``ref`` is a built-in name (which has none), a local checkout of an org profile's
	repository (a directory with profile.toml), ``./<dir>`` inside the app, or a flake URL
	the app's flake.lock pins as ``standards-profile``.
	"""
	config, _, error = _resolver()
	local = Path(ref).expanduser()
	profile_dir = None
	if not ref.startswith("./") and local.is_dir():
		profile_dir = local.resolve()
		ref = f"git+https://local.invalid/{local.name}"
	doc = {"tool": {"frappe-nix": {"schema": 1, "frappe-major": 16, "profile": ref}}}
	try:
		return _replace_apps(config.resolve_doc(doc, root=repo, profile_dir=profile_dir).cfg)
	except error as e:
		die(f"--profile {ref}: {e}", 2)


def replace_pairs(repo: Path, args) -> dict[str, str]:
	"""Every pair the code half must refuse, from the sources spec §5.10 names.

	Exits 2 when an app without ``[tool.frappe-nix]`` names none of them.
	"""
	pairs = {}
	if opted_in(repo):
		pairs |= app_pairs(repo)
	elif not (args.fleet or args.profile or args.no_replace_check):
		die(
			f"{repo} has no [tool.frappe-nix], so frappe-rename-app cannot tell which apps are "
			"replaced rather than renamed: pass --fleet <file>, --profile <ref>, or --no-replace-check",
			2,
		)
	if args.fleet:
		pairs |= fleet_pairs(Path(args.fleet))
	if args.profile:
		pairs |= profile_pairs(repo, args.profile)
	return pairs


def module_names(package: Path) -> set[str]:
	"""What ``<package>.<x>`` can name: its submodules and subpackages, and its top-level names."""
	names = {p.stem for p in package.glob("*.py")} | {p.name for p in package.iterdir() if p.is_dir()}
	init = package / "__init__.py"
	if init.is_file():
		try:
			tree = ast.parse(init.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):
			tree = ast.Module(body=[], type_ignores=[])
		for node in tree.body:
			if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
				names.add(node.name)
			elif isinstance(node, ast.Assign):
				names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
			elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
				names.add(node.target.id)
			elif isinstance(node, ast.Import | ast.ImportFrom):
				names |= {(a.asname or a.name).split(".")[0] for a in node.names}
	return names


def dotted(old: str) -> re.Pattern:
	"""``OLD.`` as a module path: not after a word character, a dot or a slash."""
	return re.compile(rf"(?<![\w./]){re.escape(old)}\.(?=[A-Za-z_])")


def module_path(old: str, modules: set[str]) -> re.Pattern:
	"""``OLD.<x>`` where ``<x>`` is one of ``modules`` (what NEW's package can name).

	Anything else after ``OLD.`` is not a path into the package: a JavaScript namespace
	(``frappe.provide("esign"); esign.accept(…)``) or a file name, which the rename keeps.
	"""
	if not modules:
		return re.compile(r"(?!)")
	names = "|".join(re.escape(m) for m in sorted(modules, key=len, reverse=True))
	return re.compile(rf"(?<![\w./]){re.escape(old)}\.(?=(?:{names})(?![\w-]))")


def rewrite_paths(text: str, old: str, new: str, modules: set[str]) -> str:
	"""Every ``OLD.<module>`` in ``text`` as ``NEW.<module>``; the code half's patches.txt
	and the site half's Patch Log and site-authored text, so the three agree."""
	return module_path(old, modules).sub(f"{new}.", text)


class Rewriter:
	"""The text rewrite of step 2, for one OLD → NEW, after step 1's moves."""

	def __init__(self, old: str, new: str, package: Path, renamed: set[str]):
		self.old, self.new = old, new
		self.modules = module_names(package)
		self.renamed = renamed
		self.token = re.compile(rf"(?<![\w./]){re.escape(old)}(?=[./])")
		self.chain = re.compile(rf"{re.escape(old)}((?:\.[\w-]+)+)")
		self.left: list[tuple[str, int, str]] = []
		self.targeted = [
			# pyproject [project].name and flit's [tool.flit.module].name
			(re.compile(rf'^(\s*name\s*=\s*)"{re.escape(old)}"', re.M), rf'\1"{new}"', "pyproject.toml"),
			(re.compile(rf'("name"\s*:\s*)"{re.escape(old)}"'), rf'\1"{new}"', "package.json"),
			(re.compile(rf"^(app_name\s*=\s*)([\"']){re.escape(old)}\2", re.M), rf"\1\2{new}\2", "hooks.py"),
			(re.compile(rf'("package-name"\s*:\s*)"{re.escape(old)}"'), rf'\1"{new}"', None),
			# the CI's --app, in workflows and scripts
			(re.compile(rf"(--app[ =])['\"]?{re.escape(old)}\b(?![\w.])['\"]?"), rf"\g<1>{new}", None),
		]
		self.imports = re.compile(rf"^(\s*(?:from|import)\s+){re.escape(old)}\b(?![\w.])", re.M)

	def rewrite(self, rel: str, text: str) -> str:
		if Path(rel).name == "patches.txt":
			# Line for line with the site half's own rewrite, so every renamed line is
			# exactly the Patch Log entry the site half writes and no patch runs again.
			return rewrite_paths(text, self.old, self.new, self.modules)
		out = []
		pos = 0
		for m in self.token.finditer(text):
			start = m.start()
			out.append(text[pos:start])
			pos = start + len(self.old)
			if text[pos] == "/":
				out.append(self.new)
				continue
			chain = self.chain.match(text, start)
			segment = chain.group(1).split(".")[1] if chain else ""
			whole = chain.group(0) if chain else self.old
			if segment in self.modules or whole in self.renamed:
				out.append(self.new)
			else:
				# A bare file name (OLD.<rest>) that names no module and no renamed file:
				# rewriting it would point at a file that does not exist.
				out.append(self.old)
				self.left.append((rel, text.count("\n", 0, start) + 1, whole))
		out.append(text[pos:])
		text = "".join(out)
		text = text.replace(f"/assets/{self.old}/", f"/assets/{self.new}/")
		text = text.replace(f"/api/method/{self.old}.", f"/api/method/{self.new}.")
		# A renamed file named at the end of a path (`<app>/templates/OLD.html`).
		for name in self.renamed:
			text = re.sub(rf"(?<=/){re.escape(name)}(?![\w.-])", self.new + name[len(self.old) :], text)
		name = Path(rel).name
		for pattern, replacement, only in self.targeted:
			if only is None or name == only:
				text = pattern.sub(replacement, text)
		if rel.endswith(".py"):
			text = self.imports.sub(rf"\g<1>{self.new}", text)
		return text


def bump_modified(text: str) -> str:
	"""A standard JSON's top-level ``modified`` set to now (frappe's 1-space format)."""
	now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
	return re.sub(r'^( "modified": )"[^"]*"', rf'\1"{now}"', text, count=1, flags=re.M)


def whitelisted(package: Path) -> list[str]:
	"""Module-level whitelisted functions under ``package``, as dotted paths (spec §5.1.1 T1)."""
	found = []
	for file in sorted(package.rglob("*.py")):
		rel = file.relative_to(package)
		if "tests" in rel.parts[:-1] or "patches" in rel.parts[:-1] or rel.name.startswith("test_"):
			continue
		try:
			tree = ast.parse(file.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):
			continue
		local = {
			a.asname or a.name
			for n in ast.walk(tree)
			if isinstance(n, ast.ImportFrom) and n.module == "frappe"
			for a in n.names
			if a.name == "whitelist"
		}
		parts = list(file.relative_to(package.parent).with_suffix("").parts)
		if parts[-1] == "__init__":
			parts.pop()
		for node in tree.body:
			if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
				continue
			for d in node.decorator_list:
				t = d.func if isinstance(d, ast.Call) else d
				if (
					isinstance(t, ast.Attribute)
					and t.attr == "whitelist"
					and isinstance(t.value, ast.Name)
					and t.value.id == "frappe"
				) or (isinstance(t, ast.Name) and t.id in local):
					found.append(".".join([*parts, node.name]))
					break
	return found


def shim_block(old: str, new: str, paths: list[str]) -> str:
	lines = [
		f"{SHIM_BEGIN} {old}",
		f"# {old}'s whitelisted methods under their old dotted paths, for callers that still use",
		"# them (cached bundles, webhooks, bookmarks). Written by frappe-rename-app; keep it for",
		"# at least one minor release after the rename.",
		"override_whitelisted_methods = {",
		'\t**globals().get("override_whitelisted_methods", {}),',
	]
	lines += [f'\t"{old}{p[len(new) :]}": "{p}",' for p in paths]
	lines += ["}", SHIM_END]
	return "\n".join(lines) + "\n"


def add_shim(hooks: Path, old: str, new: str, paths: list[str]) -> None:
	text = hooks.read_text()
	block = re.compile(rf"\n*{re.escape(SHIM_BEGIN)} {re.escape(old)}\n.*?{re.escape(SHIM_END)}\n?", re.S)
	text = block.sub("\n", text).rstrip("\n") + "\n"
	if paths:
		text += "\n\n" + shim_block(old, new, paths)
	hooks.write_text(text)


def is_text(data: bytes) -> bool:
	if b"\0" in data:
		return False
	try:
		data.decode("utf-8")
	except UnicodeDecodeError:
		return False
	return True


def apply_code(repo: Path, old: str, new: str) -> dict:
	"""Steps 1 to 4 on ``repo``; returns what the report prints."""
	if not (repo / old / "hooks.py").is_file():
		die(f"{repo}/{old}/hooks.py does not exist: nothing named {old} to rename")
	if (repo / new).exists():
		die(f"{repo}/{new} already exists")

	# 1. the package, then every tracked OLD.* file under it
	git(repo, "mv", old, new)
	renamed = set()
	for rel in git(repo, "ls-files", "-z", "--", new).split("\0"):
		if not rel:
			continue
		path = Path(rel)
		if path.name.startswith(f"{old}."):
			target = path.with_name(new + path.name[len(old) :])
			git(repo, "mv", rel, str(target))
			renamed.add(path.name)

	# 2. the tracked text
	rewriter = Rewriter(old, new, repo / new, renamed)
	changed = []
	for rel in git(repo, "ls-files", "-z").split("\0"):
		if not rel or Path(rel).name in SKIP_NAMES:
			continue
		file = repo / rel
		if file.is_symlink() or not file.is_file():
			continue
		data = file.read_bytes()
		if not is_text(data):
			continue
		text = data.decode("utf-8")
		updated = rewriter.rewrite(rel, text)
		# 3. a standard JSON whose content changed is only re-imported on a newer `modified`
		if updated != text and rel.startswith(f"{new}/") and rel.endswith(".json"):
			updated = bump_modified(updated)
		if updated != text:
			file.write_text(updated, encoding="utf-8")
			changed.append(rel)

	# 4. the old whitelisted paths keep working
	paths = whitelisted(repo / new)
	add_shim(repo / new / "hooks.py", old, new, paths)

	git(repo, "add", "-A")
	return {"renamed": sorted(renamed), "changed": changed, "shim": paths, "left": rewriter.left}


def left_alone(repo: Path, old: str) -> list[str]:
	"""Step 5: every remaining mention of ``old`` in the tracked text, for review.

	The shim block is skipped: naming the old paths is its job.
	"""
	word = re.compile(rf"(?<![\w./-]){re.escape(old)}(?![\w-])")
	out = []
	for rel in git(repo, "ls-files", "-z").split("\0"):
		if not rel or Path(rel).name in SKIP_NAMES:
			continue
		file = repo / rel
		if file.is_symlink() or not file.is_file():
			continue
		data = file.read_bytes()
		if not is_text(data):
			continue
		in_shim = False
		for n, line in enumerate(data.decode("utf-8").splitlines(), 1):
			if line.startswith(SHIM_BEGIN):
				in_shim = True
			if not in_shim and word.search(line):
				out.append(f"{rel}:{n}: {line.strip()[:140]}")
			if line.startswith(SHIM_END):
				in_shim = False
	return out


def print_report(result: dict, repo: Path, old: str, new: str) -> None:
	print(f"frappe-rename-app: {old} → {new}")
	for name in result["renamed"]:
		print(f"  renamed  {name} → {new}{name[len(old) :]}")
	print(f"  rewrote  {len(result['changed'])} file(s)")
	if result["shim"]:
		print(
			f"  shim     {len(result['shim'])} whitelisted path(s) in {new}/hooks.py (override_whitelisted_methods)"
		)
	if result["left"]:
		print("  left alone, no file of that name was renamed (check each by hand):")
		for rel, line, token in result["left"]:
			print(f"    {rel}:{line}: {token}")
	remaining = left_alone(repo, old)
	if remaining:
		print(
			f"  still naming {old}, deliberately kept: custom fieldnames, DocType names, Communication types,\n"
			"  CSS classes, localStorage keys and module names (module names stay, so no record moves):"
		)
		for r in remaining:
			print(f"    {r}")


def cmd_code(args) -> int:
	old, new = args.from_, args.to
	for n in (old, new):
		if not NAME.match(n):
			die(f"{n!r} is not an app name (lower case, digits and _)")
	if old == new:
		die("--from and --to are the same")
	repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
	pairs = replace_pairs(repo, args)
	if old in pairs:
		target = pairs[old] or "its replacement"
		die(
			f"{old} is replaced, not renamed: {target} is a new app installed beside it and {old} "
			"is uninstalled (spec §5.10, services.frappe.sites.<site>.replacedApps)"
		)
	if git(repo, "status", "--porcelain"):
		die(f"{repo} has uncommitted changes; commit or stash them first")

	if args.dry_run:
		with tempfile.TemporaryDirectory(prefix="frappe-rename-app-") as tmp:
			clone = Path(tmp) / "repo"
			subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(repo), str(clone)], check=True)
			result = apply_code(clone, old, new)
			print(git(clone, "diff", "--cached", "--find-renames", "--stat"), end="")
			print(git(clone, "diff", "--cached", "--find-renames"), end="")
			print_report(result, clone, old, new)
		print("frappe-rename-app: dry run; nothing was changed")
		return 0

	result = apply_code(repo, old, new)
	print_report(result, repo, old, new)
	print("frappe-rename-app: staged; review with `git diff --cached`, then commit")
	return 0


# ── the site half ──────────────────────────────────────────────────────────────

# Columns that hold an app name and nothing else.
APP_COLUMNS = [
	("Module Def", "app_name"),
	("Desktop Icon", "app"),
	("Dock", "app"),
	("Sidebar", "app"),
	("Sidebar Item Group", "app"),
	("Workspace Sidebar", "app"),
	("Notification Log", "app"),
	("User", "default_app"),
	("User Invitation", "app_name"),
	("Website Theme Ignore App", "app"),
]
SINGLE_APP_FIELDS = [("System Settings", "default_app")]

# Columns where OLD appears as a dotted path or an /assets/OLD/ URL.
TOKEN_COLUMNS = [
	("Scheduled Job Type", "method"),
	("Scheduler Event", "method"),
	("Number Card", "method"),
	("DocType Action", "action"),
	("Navbar Item", "action"),
	("Report", "javascript"),
	("Report", "report_script"),
	("Server Script", "script"),
	("Client Script", "script"),
	("Print Format", "html"),
	("Print Format", "css"),
	("Web Form", "client_script"),
	("Web Form", "custom_css"),
	("Web Form", "introduction_text"),
	("Web Page", "main_section"),
	("Web Page", "main_section_html"),
	("Web Page", "main_section_md"),
	("Web Page", "javascript"),
	("Web Page", "css"),
	("Web Template", "template"),
	("Custom HTML Block", "html"),
	("Custom HTML Block", "script"),
	("Custom HTML Block", "style"),
	("Letter Head", "content"),
	("Letter Head", "footer"),
	("Letter Head", "header_script"),
	("Letter Head", "footer_script"),
	("Website Route Redirect", "source"),
	("Website Route Redirect", "target"),
	("Website Theme", "custom_scss"),
	("Website Theme", "theme_scss"),
	("Website Theme", "js"),
	("Email Template", "response"),
	("Email Template", "response_html"),
	("Notification", "message"),
]
SINGLE_TEXT_DOCTYPES = ["Website Settings", "Navbar Settings", "Portal Settings"]

# History, not configuration: --scan does not report these.
LOG_TABLES = {
	"Error Log",
	"Version",
	"Communication",
	"Email Queue",
	"Email Queue Recipient",
	"Deleted Document",
	"Activity Log",
	"Access Log",
	"Scheduled Job Log",
	"Patch Log",
	"Route History",
	"Integration Request",
	"Prepared Report",
	"Submission Queue",
	"Comment",
	"Notification Log",
	"RQ Job",
	"Recorder",
	"API Request Log",
	"Webhook Request Log",
	"Console Log",
	"Data Import Log",
	"Transaction Log",
	"Unhandled Email",
}

CACHE_KEYS = ("app_modules", "installed_app_modules", "installed_apps", "all_apps")


class Precondition(Exception):
	pass


def rewrite_text(text, old: str, new: str, modules: set[str]):
	if not isinstance(text, str) or not text:
		return text
	return rewrite_paths(text.replace(f"/assets/{old}/", f"/assets/{new}/"), old, new, modules)


def package_dir(app: str) -> Path:
	"""Where ``app``'s package is on this bench, found without importing it."""
	import importlib.util

	spec = importlib.util.find_spec(app)
	if spec is None or not spec.submodule_search_locations:
		raise Precondition(f"cannot find {app}'s package on this bench")
	return Path(next(iter(spec.submodule_search_locations)))


def mentions(text, old: str) -> bool:
	return isinstance(text, str) and (f"/assets/{old}/" in text or bool(dotted(old).search(text)))


class SiteRename:
	def __init__(self, frappe, dry_run: bool):
		self.frappe = frappe
		self.db = frappe.db
		self.dry_run = dry_run
		self.log: list[str] = []
		# NEW → what its package can name after `NEW.` (module_names), from check().
		self.modules: dict[str, set[str]] = {}

	def has_column(self, doctype: str, field: str) -> bool:
		return self.db.table_exists(doctype) and self.db.has_column(doctype, field)

	def installed(self) -> list[str]:
		return json.loads(self.db.get_global("installed_apps") or "[]")

	def check(self, old: str, new: str, installed: list[str], bench_apps: list[str]) -> bool:
		"""Whether the pair has anything to do; a failed precondition raises."""
		if old not in installed:
			print(f"{old} not installed on {self.frappe.local.site}: nothing to do")
			return False
		if new in installed:
			raise Precondition(
				f"both {old} and {new} are installed on {self.frappe.local.site}; resolve that by hand"
			)
		if new not in bench_apps:
			raise Precondition(f"{new} is not in sites/apps.txt: put its code on the bench first")
		self.modules[new] = module_names(package_dir(new))
		# Raw SQL, not frappe.get_all: the query builder loads every installed app's hooks
		# (filters_config), and OLD's package is no longer there to import.
		owned = [
			r[0] for r in self.db.sql("select name from `tabModule Def` where app_name=%s and custom=0", old)
		]
		declared = self.frappe.get_module_list(new)
		missing = sorted(set(owned) - set(declared))
		if missing:
			raise Precondition(f"{new}/modules.txt does not declare {old}'s module(s) {', '.join(missing)}")
		if old in bench_apps:
			print(
				f"warning: {old} is still in sites/apps.txt, so two apps claim its modules until it is removed"
			)
		return True

	def update(self, sql: str, values) -> None:
		if not self.dry_run:
			self.db.sql(sql, values)

	def rename(self, old: str, new: str) -> None:
		# What NEW's package can name after `NEW.`: the code half rewrote exactly those
		# `OLD.<x>`, and kept every other (JS namespaces, file names), so the site does too.
		modules = self.modules[new]
		installed = self.installed()
		after = [new if a == old else a for a in installed]
		self.log.append(f"installed_apps: {installed} -> {after}")
		if not self.dry_run:
			self.db.set_global("installed_apps", json.dumps(after))

		for doctype, field in APP_COLUMNS:
			if not self.has_column(doctype, field):
				continue
			count = self.db.sql(f"select count(*) from `tab{doctype}` where `{field}`=%s", old)[0][0]
			if count:
				self.log.append(f"{doctype}.{field}: {count} row(s) {old} -> {new}")
				self.update(f"update `tab{doctype}` set `{field}`=%s where `{field}`=%s", (new, old))
		for doctype, field in SINGLE_APP_FIELDS:
			value = self.db.sql(
				"select value from `tabSingles` where doctype=%s and field=%s", (doctype, field)
			)
			if value and value[0][0] == old:
				self.log.append(f"{doctype}.{field}: {old} -> {new}")
				self.update(
					"update `tabSingles` set value=%s where doctype=%s and field=%s", (new, doctype, field)
				)

		# The Patch Log matches patches.txt lines exactly; NEW's are OLD's, renamed.
		for name, patch in self.db.sql(
			"select name, patch from `tabPatch Log` where patch like %s", (f"%{old}.%",)
		):
			renamed = rewrite_paths(patch, old, new, modules)
			if renamed != patch:
				self.log.append(f"Patch Log [{name}]: {patch} -> {renamed}")
				self.update("update `tabPatch Log` set patch=%s where name=%s", (renamed, name))

		for doctype, field in TOKEN_COLUMNS:
			if not self.has_column(doctype, field):
				continue
			rows = self.db.sql(
				f"select name, `{field}` from `tab{doctype}` where `{field}` like %s or `{field}` like %s",
				(f"%{old}.%", f"%/assets/{old}/%"),
			)
			for name, value in rows:
				renamed = rewrite_text(value, old, new, modules)
				if renamed != value:
					self.log.append(f"{doctype}.{field} [{name}]: {value[:100]!r} -> {renamed[:100]!r}")
					self.update(f"update `tab{doctype}` set `{field}`=%s where name=%s", (renamed, name))
		for doctype in SINGLE_TEXT_DOCTYPES:
			for field, value in self.db.sql(
				"select field, value from `tabSingles` where doctype=%s", doctype
			):
				renamed = rewrite_text(value, old, new, modules)
				if renamed != value:
					self.log.append(f"{doctype}.{field}: {value[:100]!r} -> {renamed[:100]!r}")
					self.update(
						"update `tabSingles` set value=%s where doctype=%s and field=%s",
						(renamed, doctype, field),
					)

	def scan(self, old: str) -> None:
		"""Every text column, in every table but the logs, that still names OLD (read-only)."""
		columns = self.db.sql(
			"""select table_name, column_name from information_schema.columns
			where table_schema=%s and data_type in ('char', 'varchar', 'tinytext', 'text', 'mediumtext', 'longtext', 'json')""",
			self.frappe.conf.db_name,
		)
		for table, column in columns:
			doctype = table[3:] if table.startswith("tab") else table
			if doctype in LOG_TABLES:
				continue
			try:
				values = self.db.sql(
					f"select `{column}` from `{table}` where `{column}` like %s or `{column}` like %s",
					(f"%{old}.%", f"%/assets/{old}/%"),
				)
			except Exception:
				continue
			hits = sum(1 for (value,) in values if mentions(value, old))
			if hits:
				self.log.append(f"scan: {table}.{column}: {hits} row(s) still name {old}")


def drop_caches(frappe) -> None:
	"""Forget the cached app and module maps (redis and frappe's per-process cache)."""
	for cache in (frappe.cache, getattr(frappe, "client_cache", None)):
		if cache is not None:
			for key in CACHE_KEYS:
				cache.delete_value(key)


def cmd_site(args) -> int:
	pairs = []
	for pair in args.pairs:
		old, sep, new = pair.partition("=")
		if not sep or not NAME.match(old) or not NAME.match(new) or old == new:
			die(f"{pair!r} is not OLD=NEW")
		pairs.append((old, new))
	read_only = args.dry_run or args.scan

	import frappe
	from frappe.installer import update_site_config

	frappe.init(site=args.site, sites_path=args.sites_path)
	frappe.connect()
	try:
		# The cached app lists still name OLD; frappe would try to import it.
		drop_caches(frappe)
		job = SiteRename(frappe, dry_run=read_only)
		installed = job.installed()
		bench_apps = frappe.get_all_apps(with_internal_apps=False)
		try:
			todo = [(old, new) for old, new in pairs if job.check(old, new, installed, bench_apps)]
		except Precondition as e:
			die(str(e))
		if not todo and not args.scan:
			return 0
		if todo and not read_only and not args.yes:
			if not sys.stdin.isatty():
				die("pass --yes to rename without a prompt")
			answer = input(f"Rename {', '.join(f'{o} -> {n}' for o, n in todo)} on {args.site}? [y/N] ")
			if answer.strip().lower() not in ("y", "yes"):
				die("not renamed")
		try:
			for old, new in todo:
				job.rename(old, new)
			if args.scan:
				for old, _ in pairs:
					job.scan(old)
		except Exception as e:
			frappe.db.rollback()
			print("\n".join(job.log))
			die(f"database error, rolled back: {type(e).__name__}: {e}", 2)
		print("\n".join(job.log))
		if read_only:
			frappe.db.rollback()
			print("frappe-rename-app: read-only run; nothing was changed")
			return 0
		frappe.db.commit()

		# After the commit: the site_config.json mirror (frappe 16 writes one; nothing reads
		# it, but it should not lie) and the caches that hold the app and module maps.
		if "installed_apps" in frappe.get_site_config():
			update_site_config("installed_apps", job.installed())
		drop_caches(frappe)
		frappe.clear_cache()
		print(
			f"frappe-rename-app: renamed {', '.join(f'{o} -> {n}' for o, n in todo)} on {args.site}; now migrate"
		)
		return 0
	finally:
		frappe.destroy()


def main(argv=None) -> int:
	argv = sys.argv[1:] if argv is None else argv
	if argv[:1] == ["code"]:
		p = argparse.ArgumentParser(
			prog="frappe-rename-app code",
			description=__doc__,
			formatter_class=argparse.RawDescriptionHelpFormatter,
		)
		p.add_argument("--from", dest="from_", required=True, metavar="OLD")
		p.add_argument("--to", required=True, metavar="NEW")
		p.add_argument("--dry-run", action="store_true", help="print the diff; change nothing")
		source = p.add_mutually_exclusive_group()
		source.add_argument(
			"--fleet", metavar="FILE", help='a fleet file whose "rename_mode": "replace" entries are refused'
		)
		source.add_argument(
			"--profile",
			metavar="REF",
			help="a profile (a local checkout, ./<dir>, or a locked flake URL) whose [[replace-apps]] are refused",
		)
		source.add_argument(
			"--no-replace-check",
			action="store_true",
			help="an app without [tool.frappe-nix]: rename without checking for a replace pair",
		)
		return cmd_code(p.parse_args(argv[1:]))
	p = argparse.ArgumentParser(
		prog="frappe-rename-app", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	p.add_argument("--site", required=True)
	p.add_argument("pairs", nargs="+", metavar="OLD=NEW")
	p.add_argument("--dry-run", action="store_true", help="print what would change; change nothing")
	p.add_argument(
		"--scan", action="store_true", help="read-only: also report every text column still naming OLD"
	)
	p.add_argument("--yes", action="store_true", help="do not ask before renaming")
	p.add_argument(
		"--sites-path", default=".", help="the bench's sites/ directory (default: the current one)"
	)
	return cmd_site(p.parse_args(argv))


if __name__ == "__main__":
	sys.exit(main())
