"""``frappe-listing check``'s rules L1 to L6 and L10 to L12 (spec §5.2). L7 (registry semgrep)
is ``baseline``, L8 (the logo) ``frappe_nix_tools.icon``, L9 (pilot get-app) ``getapp``.

Each rule returns ``Result``s. An ``error`` fails the check (exit 1); a ``warning`` (L12, a
conditional hook assignment) is reported and passes. L1, L2, L8, L10 and L12 apply only
when ``marketplace/listing.toml`` exists: the others are the registry-readiness checks of
every app with ``listing`` on, listed or not.

Every expected organisation value (``app_publisher``, ``app_email``, ``app_license``, the URL
templates) is the resolved configuration's (S37). With the value unset, L2 only asks that
the hook be non-empty, and L12 has no pattern to compare.
"""

import ast
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from frappe_nix_tools.common import repo
from frappe_nix_tools.common.report import EnvError
from frappe_nix_tools.listing.readme import url_template
from frappe_nix_tools.listing.target import Target
from frappe_nix_tools.scaffold import blocks, context, hooks, ranges

LISTING = "marketplace/listing.toml"
TYPES = ("application", "extension", "integration", "utility")
CATEGORIES = ("Applications", "Compliance", "Developer Tools", "Extensions", "Integrations", "Utilities")
CHANNELS = ("stable", "nightly")
# Third-party marks a title may carry only as "… for X" or "… via X" (§2.21).
MARKS = ("Frappe", "ERPNext", "Frappe HR", "Frappe Cloud")
LISTING_KEYS = {
	"schema",
	"title",
	"tagline",
	"type",
	"category",
	"categories",
	"website",
	"documentation",
	"apps_screen",
	"apps_screen_entry",
	"registry",
}
# The hooks L2 compares, by the org value each one must equal when it is set.
ORG_HOOKS = (("app_publisher", "publisher"), ("app_email", "email"), ("app_license", "license"))


@dataclass(frozen=True)
class Result:
	rule: str
	level: str  # error | warning | note
	path: str
	message: str


def _error(rule: str, path: str, message: str) -> Result:
	return Result(rule, "error", path, message)


def _warning(rule: str, path: str, message: str) -> Result:
	return Result(rule, "warning", path, message)


# --- L1 -----------------------------------------------------------------------------------


def _title_marks(title: str) -> list[str]:
	"""Marks the title uses other than as ``… for X`` or ``… via X``."""
	bad = []
	for mark in MARKS:
		for m in re.finditer(rf"\b{re.escape(mark)}\b", title):
			before = title[: m.start()].rstrip().lower()
			if not (before.endswith(" for") or before.endswith(" via") or before.endswith(" for the")):
				bad.append(mark)
	return sorted(set(bad))


def l1(listing: dict) -> list[Result]:
	"""``listing.toml`` against its schema (§2.21)."""
	out: list[Result] = []

	def err(message: str) -> None:
		out.append(_error("L1", LISTING, message))

	unknown = set(listing) - LISTING_KEYS
	if unknown:
		err(f"unknown key(s) {sorted(unknown)}")
	if listing.get("schema") != 1:
		err("schema must be 1")
	title = listing.get("title")
	if not isinstance(title, str) or not title.strip():
		err("title is required")
	else:
		if len(title) > 40:
			err(f"title is {len(title)} characters; at most 40")
		for mark in _title_marks(title):
			err(f'title names {mark!r}: a third-party mark only as "… for {mark}" or "… via {mark}"')
	tagline = listing.get("tagline")
	if not isinstance(tagline, str) or not tagline.strip():
		err("tagline is required")
	else:
		if not 40 <= len(tagline) <= 80:
			err(f"tagline is {len(tagline)} characters; it must be 40 to 80")
		if tagline.rstrip().endswith("."):
			err("tagline must not end with a period")
	if listing.get("type") not in TYPES:
		err(f"type must be one of {', '.join(TYPES)}, not {listing.get('type')!r}")
	if listing.get("category") not in CATEGORIES:
		err(f"category must be one of {', '.join(CATEGORIES)}, not {listing.get('category')!r}")
	categories = listing.get("categories")
	if (
		not isinstance(categories, list)
		or not categories
		or not all(isinstance(c, str) and c.strip() for c in categories)
	):
		err("categories must be a non-empty list of names")
	for key in ("website", "documentation"):
		value = listing.get(key)
		if not isinstance(value, str) or not re.fullmatch(r"https://[^\s/]+\.[^\s/]+(/\S*)?", value):
			err(f"{key} must be an https URL, not {value!r}")
	screen = listing.get("apps_screen", False)
	if not isinstance(screen, bool):
		err("apps_screen must be true or false")
	entry = listing.get("apps_screen_entry")
	if screen and not isinstance(entry, dict):
		err("apps_screen = true needs an [apps_screen_entry] table (route, has_permission)")
	if not screen and entry is not None:
		err("[apps_screen_entry] is only for apps_screen = true")
	if isinstance(entry, dict):
		if set(entry) - {"route", "has_permission"}:
			err(f"[apps_screen_entry]: unknown key(s) {sorted(set(entry) - {'route', 'has_permission'})}")
		if not isinstance(entry.get("route"), str) or not entry["route"].startswith("/"):
			err('[apps_screen_entry] route must start with "/"')
		if not isinstance(entry.get("has_permission"), str) or not re.fullmatch(
			r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+", entry.get("has_permission", "")
		):
			err("[apps_screen_entry] has_permission must be a dotted path")
	registry = listing.get("registry")
	if registry is not None:
		if not isinstance(registry, dict) or set(registry) - {"stars", "channel"}:
			err("[registry] holds only stars and channel")
		else:
			stars = registry.get("stars", 0)
			if not isinstance(stars, int) or isinstance(stars, bool) or stars < 0:
				err("[registry] stars must be a whole number ≥ 0")
			if registry.get("channel", "stable") not in CHANNELS:
				err(f"[registry] channel must be one of {', '.join(CHANNELS)}")
	return out


# --- L2 -----------------------------------------------------------------------------------


def hook_values(path: Path) -> tuple[dict[str, Any], list[str]]:
	"""``hooks.py``'s top-level literal assignments, and the names assigned conditionally (inside
	an ``if``, ``try``, ``with`` or loop at module level), which the registry may read differently."""
	values = hooks.read(path)
	try:
		tree = ast.parse(path.read_text(), filename=str(path))
	except FileNotFoundError:
		return values, []
	conditional = set()
	for node in tree.body:
		if isinstance(node, ast.If | ast.Try | ast.With | ast.For | ast.While | ast.Match):
			for child in ast.walk(node):
				if isinstance(child, ast.Assign):
					conditional |= {t.id for t in child.targets if isinstance(t, ast.Name)}
				elif isinstance(child, ast.AnnAssign | ast.AugAssign) and isinstance(child.target, ast.Name):
					conditional.add(child.target.id)
	return values, sorted(conditional)


def l2(t: Target) -> list[Result]:
	"""hooks.py against the listing and the org values (§5.2)."""
	path = f"{t.name}/hooks.py"
	values, conditional = hook_values(t.root / path)
	listing = t.listing
	out = [
		_warning("L2", path, f"{name} is assigned conditionally; the registry reads the module as written")
		for name in conditional
		if name.startswith("app_") or name == "add_to_apps_screen"
	]

	def err(message: str) -> None:
		out.append(_error("L2", path, message))

	want = {"app_name": t.name, "app_title": listing.get("title"), "app_description": listing.get("tagline")}
	for hook, expected in want.items():
		if values.get(hook) != expected:
			err(f"{hook} is {values.get(hook)!r}, expected {expected!r}")
	for hook, key in ORG_HOOKS:
		expected = t.org.get(key, "")
		have = values.get(hook)
		if expected:
			if have != expected:
				err(f"{hook} is {have!r}, expected {expected!r} (org.{key})")
		elif not isinstance(have, str) or not have.strip():
			err(f"{hook} must be set (org.{key} is empty, so any non-empty value)")
	license_ = t.package.get("license")
	if not t.org.get("license") and isinstance(license_, str) and values.get("app_license") != license_:
		err(f"app_license is {values.get('app_license')!r}, but package.json's license is {license_!r}")
	if "app_logo_url" in values:
		err("app_logo_url is set: the logo comes from add_to_apps_screen and the desktop icon")
	if values.get("app_icon") == "octicon octicon-file-directory":
		err('app_icon is bench new-app\'s placeholder "octicon octicon-file-directory": remove it')
	if values.get("app_color") == "grey":
		err('app_color is bench new-app\'s placeholder "grey": remove it')
	screen = bool(listing.get("apps_screen"))
	entries = values.get("add_to_apps_screen")
	if screen and not entries:
		err("apps_screen = true, but hooks.py has no add_to_apps_screen")
	elif not screen and entries is not None:
		err("hooks.py has add_to_apps_screen, but apps_screen is false in marketplace/listing.toml")
	elif screen:
		entry = listing.get("apps_screen_entry") or {}
		if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
			err("add_to_apps_screen must be a list of tables")
		else:
			logo = f"/assets/{t.name}/images/{t.name}-logo.svg"
			for i, e in enumerate(entries):
				if e.get("logo") != logo:
					err(f"add_to_apps_screen[{i}].logo is {e.get('logo')!r}, expected {logo!r}")
				for key in ("route", "has_permission"):
					if e.get(key) != entry.get(key):
						err(
							f"add_to_apps_screen[{i}].{key} is {e.get(key)!r}, expected {entry.get(key)!r} ([apps_screen_entry])"
						)
	return out


# --- L3 to L6 -----------------------------------------------------------------------------


def l3(t: Target) -> list[Result]:
	"""``[tool.bench.frappe-dependencies]`` (§5.2): ranges.problems, shared with compat C4."""
	required = hooks.required_apps(t.app.hooks)
	found = ranges.problems(
		t.app.pyproject,
		required,
		int(t.cfg["frappe-major"]),
		siblings=[dict(s) for s in t.ctx.siblings],
		known=t.cfg.get("known-apps"),
	)
	return [_error("L3", "pyproject.toml", p) for p in found]


def _manifest_version(root: Path) -> str | None:
	try:
		doc = json.loads((root / ".release-please-manifest.json").read_text())
	except (FileNotFoundError, json.JSONDecodeError):
		return None
	return doc.get(".") if isinstance(doc, dict) and isinstance(doc.get("."), str) else None


def l4(t: Target, *, release: bool = False, tag: str | None = None) -> list[Result]:
	"""The version block, and one version everywhere; with ``--release``, the tag's (§5.2)."""
	path = f"{t.name}/__init__.py"
	out: list[Result] = []
	try:
		text = (t.root / path).read_text()
	except FileNotFoundError:
		return [_error("L4", path, "is missing")]
	wanted, _ = blocks.init_py(text)
	if wanted != text:
		out.append(
			_error(
				"L4",
				path,
				"__version__ is not in the x-release-please block form (frappe-init --sync writes it)",
			)
		)
	have = context.app_version(t.root, t.name)
	if not have:
		out.append(_error("L4", path, "has no __version__"))
	versions = {"__version__": have}
	if "version" in t.package:
		versions["package.json"] = t.package.get("version")
	manifest = _manifest_version(t.root)
	if manifest is not None or (t.root / ".release-please-manifest.json").exists():
		versions[".release-please-manifest.json"] = manifest
	if len(set(versions.values())) > 1:
		out.append(
			_error("L4", path, "versions disagree: " + ", ".join(f"{k} {v!r}" for k, v in versions.items()))
		)
	if release:
		if not tag:
			return [*out, _error("L4", path, "--release needs --tag vX.Y.Z")]
		prefix = t.cfg["releases"]["tag-prefix"] if t.modules.get("releases") else "v"
		if tag.removeprefix(prefix) != have:
			out.append(_error("L4", path, f"__version__ is {have!r}, but the tag is {tag!r}"))
		branch = t.ctx.branches.release or t.ctx.branches.integration
		try:
			commit = repo.git(t.root, "rev-parse", "--verify", f"{tag}^{{commit}}").strip()
			repo.git(t.root, "merge-base", "--is-ancestor", commit, f"origin/{branch}")
		except EnvError:
			out.append(_error("L4", path, f"tag {tag} is not a commit reachable from origin/{branch}"))
	return out


def _tracked_links(root: Path) -> list[str]:
	out = repo.git(root, "ls-files", "-s", "-z")
	return sorted(line.split("\t", 1)[1] for line in out.split("\0") if line.startswith("120000 "))


def l5(t: Target) -> list[Result]:
	"""``__init__.py`` has no side effects; no tracked link leaves the repository (§5.2)."""
	path = f"{t.name}/__init__.py"
	out: list[Result] = []
	try:
		_, offending = blocks.init_py((t.root / path).read_text())
	except FileNotFoundError:
		offending = []
	out += [
		_error(
			"L5", path, f"only comments may sit outside the version block (no side effects): {line.strip()}"
		)
		for line in offending
	]
	root = t.root.resolve()
	for link in _tracked_links(t.root):
		target = os.readlink(t.root / link) if (t.root / link).is_symlink() else ""
		resolved = (root / PurePosixPath(link).parent / target).resolve() if target else None
		if target.startswith("/") or resolved is None or not resolved.is_relative_to(root):
			out.append(_error("L5", link, f"is a symlink to {target!r}, outside the repository"))
	return out


def l6(t: Target) -> list[Result]:
	"""Every ``override_doctype_class`` has an allow-list entry, and every entry a hook (§5.2)."""
	path = f"{t.name}/hooks.py"
	overrides = t.app.hooks.get("override_doctype_class") or {}
	if not isinstance(overrides, dict):
		return [_error("L6", path, "override_doctype_class must be a table of doctype → class")]
	allowed = {e.get("doctype") for e in t.cfg.get("override-doctype-class", [])}
	out = [
		_error(
			"L6",
			path,
			f"override_doctype_class[{doctype!r}] has no [[tool.frappe-nix.override-doctype-class]] entry"
			" (extend_doctype_class composes with other apps; overriding needs a reason)",
		)
		for doctype in sorted(overrides)
		if doctype not in allowed
	]
	out += [
		_error(
			"L6",
			"pyproject.toml",
			f"[[tool.frappe-nix.override-doctype-class]] {doctype!r} has no override_doctype_class hook: remove the entry",
		)
		for doctype in sorted(d for d in allowed if isinstance(d, str))
		if doctype not in overrides
	]
	return out


# --- L10 to L12 ---------------------------------------------------------------------------


def fetch_status(url: str, *, timeout: float = 20, retries: int = 2) -> tuple[int, str]:
	"""``(status, final URL)`` of a GET, following redirects; ``(0, error)`` when it never answered."""
	last = ""
	for attempt in range(retries + 1):
		try:
			request = urllib.request.Request(url, headers={"User-Agent": "frappe-nix-listing"})
			with urllib.request.urlopen(request, timeout=timeout) as response:
				return response.status, response.url
		except urllib.error.HTTPError as e:
			return e.code, e.url or url
		except (urllib.error.URLError, TimeoutError, OSError) as e:
			last = str(getattr(e, "reason", e))
			if attempt < retries:
				time.sleep(2)
	return 0, last


def l10(listing: dict) -> list[Result]:
	"""``website`` and ``documentation`` answer 200 over https, after redirects (§5.2)."""
	out = []
	for key in ("website", "documentation"):
		url = listing.get(key)
		if not isinstance(url, str) or not url.startswith("https://"):
			continue  # L1 reports it
		status, final = fetch_status(url)
		if status != 200 or not final.startswith("https://"):
			why = f"answered {status}" if status else f"did not answer ({final})"
			if status == 200:
				why = f"redirected to {final}, which is not https"
			out.append(_error("L10", LISTING, f"{key} {url} {why}"))
	return out


def l11(t: Target, apps_json: list[dict]) -> list[Result]:
	"""Every ``<owner>/<repo>`` required app is listed in the upstream registry (§5.2)."""
	listed = {
		str(a.get("repo", "")).rstrip("/").removesuffix(".git").lower()
		for a in apps_json
		if isinstance(a, dict)
	}
	out = []
	for spelling in hooks.required_apps(t.app.hooks):
		if "/" not in spelling:
			continue
		url = f"https://github.com/{spelling}".lower()
		if url not in listed:
			out.append(
				_error(
					"L11",
					f"{t.name}/hooks.py",
					f"required app {spelling} is not in the registry's apps.json: publish it first",
				)
			)
	return out


def upstream_apps(upstream: str) -> list[dict]:
	"""The upstream registry's ``apps.json`` on ``main``."""
	url = f"https://raw.githubusercontent.com/{upstream}/main/apps.json"
	try:
		with urllib.request.urlopen(url, timeout=30) as response:
			doc = json.loads(response.read())
	except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
		raise EnvError(f"L11: cannot read {url}: {e}") from e
	if not isinstance(doc, list):
		raise EnvError(f"L11: {url} is not a list of apps")
	return doc


def l12(t: Target) -> list[Result]:
	"""``website`` and ``documentation`` follow the profile's URL templates (warning, §2.21)."""
	out = []
	for key, org_key in (("website", "website-url"), ("documentation", "docs-url")):
		template = t.org.get(org_key, "")
		if not template:
			continue
		expected = url_template(template, t.ctx)
		have = t.listing.get(key)
		if have != expected:
			out.append(_warning("L12", LISTING, f"{key} is {have!r}; org.{org_key} expects {expected!r}"))
	return out
