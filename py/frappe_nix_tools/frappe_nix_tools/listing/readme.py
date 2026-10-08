"""The README generated blocks (spec §2.19): the ``blocks`` strategy with ``handler: readme``.

``README.md`` holds six managed blocks, in this order, with the app's own sections between
them::

    <!-- frappe-nix:begin header -->
    …rendered…
    <!-- frappe-nix:end header -->

``header``, ``compatibility``, ``installation``, ``support``, ``development`` and
``license``. Each body is rendered from ``frappe_nix_tools/data/templates/readme/<name>.md.j2``,
or from the org profile's ``templates/readme/<name>.md.j2`` (an override, §8.1). Only the
text between the markers is sync's; everything outside them is the app's. A missing,
repeated, unbalanced or out-of-order marker is exit 2 naming the block: sync never guesses
where the app wants a block.

The templates get the usual context (§2.3) plus ``readme``, the values this module works
out once so an override need not repeat them (the repository URL, the badges, the install
ref, the hero screenshot, the support links). Nothing here reads the clock: the licence
line's year is the first commit's (§3.4).

When ``readme`` turns off, ``strip`` removes the blocks with their markers (§3.3 step 6).
"""

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from frappe_nix_tools.common.report import ConfigError
from frappe_nix_tools.scaffold import context, manifest
from frappe_nix_tools.scaffold import render as rendering

BLOCKS = ("header", "compatibility", "installation", "support", "development", "license")
_MARKER = re.compile(r"^\s*<!--\s*frappe-nix:(?P<kind>begin|end)\s+(?P<name>[a-z-]+)\s*-->\s*$")
TEMPLATE_DIR = "readme"


def begin(name: str) -> str:
	return f"<!-- frappe-nix:begin {name} -->"


def end(name: str) -> str:
	return f"<!-- frappe-nix:end {name} -->"


def blocks(text: str, path: str = "README.md") -> list[tuple[str, int, int]]:
	"""``(name, begin line, end line)`` of each block, in order; exit 2 when one is missing,
	repeated, unbalanced, unknown or out of order."""
	found: list[tuple[str, int, int]] = []
	opened: tuple[str, int] | None = None
	for number, line in enumerate(text.split("\n")):
		m = _MARKER.match(line)
		if not m:
			continue
		name = m["name"]
		if name not in BLOCKS:
			raise ConfigError(
				f"{path}:{number + 1}: unknown frappe-nix block {name!r} (blocks: {', '.join(BLOCKS)})"
			)
		if m["kind"] == "begin":
			if opened is not None:
				raise ConfigError(f"{path}:{number + 1}: block {name} begins inside block {opened[0]}")
			opened = (name, number)
		else:
			if opened is None or opened[0] != name:
				raise ConfigError(f"{path}:{number + 1}: `{end(name)}` closes no open {name} block")
			found.append((name, opened[1], number))
			opened = None
	if opened is not None:
		raise ConfigError(f"{path}:{opened[1] + 1}: block {opened[0]} is never closed (`{end(opened[0])}`)")
	names = [b[0] for b in found]
	for name in BLOCKS:
		if names.count(name) > 1:
			raise ConfigError(f"{path}: block {name} appears {names.count(name)} times")
	missing = [name for name in BLOCKS if name not in names]
	if missing:
		raise ConfigError(
			f"{path} lacks the marker(s) of block {', '.join(missing)}: add `{begin(missing[0])}` and"
			f" `{end(missing[0])}` where the block goes (spec §2.19; blocks, in order: {', '.join(BLOCKS)})"
		)
	if names != list(BLOCKS):
		raise ConfigError(f"{path}: the blocks are in the order {', '.join(names)}, not {', '.join(BLOCKS)}")
	return found


def strip(text: str) -> str:
	"""``text`` without the managed blocks and their markers (``readme`` turned off).

	Only the seams change: a run of blank lines where a block stood keeps one blank line (a
	block usually stood in a paragraph of its own). Every other line is the app's, blank
	lines in its own code blocks included.
	"""
	out: list[str] = []
	seams: set[int] = set()
	inside = False
	for line in text.split("\n"):
		m = _MARKER.match(line)
		if m and m["name"] in BLOCKS:
			inside = m["kind"] == "begin"
			seams.add(len(out))
			continue
		if not inside:
			out.append(line)
	kept: list[str] = []
	i = 0
	while i < len(out):
		if out[i].strip():
			kept.append(out[i])
			i += 1
			continue
		end = i
		while end < len(out) and not out[end].strip():
			end += 1
		run = out[i:end]
		touches = any(i <= seam <= end for seam in seams)
		kept += run[:1] if touches else run
		i = end
	return "\n".join(kept)


def has_markers(text: str) -> bool:
	return any((m := _MARKER.match(line)) and m["name"] in BLOCKS for line in text.split("\n"))


# --- the values the templates read -------------------------------------------------------


def shield(text: str) -> str:
	"""A shields.io static-badge path segment: ``-`` and ``_`` doubled, the rest URL-quoted."""
	return urllib.parse.quote(text.replace("-", "--").replace("_", "__"), safe="")


def url_template(template: str, ctx: context.NS) -> str:
	"""An org URL template with ``{app}``, ``{app_hyphen}`` and ``{repo}`` filled in."""
	return (
		template.replace("{app_hyphen}", ctx.app_hyphen).replace("{app}", ctx.app).replace("{repo}", ctx.repo)
	)


def hero(root: Path, ctx: context.NS) -> dict | None:
	"""The shot ``docs/screenshots/manifest.json`` marks ``readme: "hero"`` (frappe-shots writes
	it, §5.5): ``{name, alt, light, dark}``, each theme ``True`` when its image is listed."""
	if not ctx.modules.get("screenshots"):
		return None
	try:
		doc = json.loads((root / "docs/screenshots/manifest.json").read_text())
	except FileNotFoundError:
		return None
	except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
		raise ConfigError(f"docs/screenshots/manifest.json: {e}") from e
	if not isinstance(doc, list):
		raise ConfigError("docs/screenshots/manifest.json is not a list (frappe-shots writes it)")
	shots = [s for s in doc if isinstance(s, dict) and s.get("readme") == "hero"]
	if not shots:
		return None
	name = shots[0].get("name")
	themes = {s.get("theme") for s in shots if s.get("name") == name}
	return {
		"name": name,
		"alt": str(shots[0].get("alt") or ""),
		"light": "light" in themes,
		"dark": "dark" in themes,
	}


# --- rendering ---------------------------------------------------------------------------


def table(header: list[str], rows: list[list[str]]) -> str:
	"""A markdown table in the form prettier and oxfmt print one: every column padded to its
	widest cell, the separator row of dashes as wide (at least three)."""
	widths = [max(3, len(header[i]), *(len(r[i]) for r in rows)) for i in range(len(header))]

	def line(cells: list[str]) -> str:
		return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)) + " |"

	return "\n".join([line(header), line(["-" * w for w in widths]), *(line(r) for r in rows)])


def values(root: Path, ctx: context.NS) -> dict[str, Any]:
	"""What the block templates read besides the context (§2.19), as ``readme``."""
	cfg, org, modules = ctx.cfg, ctx.org, ctx.modules
	if not ctx.repo:
		raise ConfigError(
			"README.md: the blocks link the repository, which is unknown: set [tool.frappe-nix] repo"
			' ("<owner>/<repo>"), an origin remote, or org.github-owner'
		)
	github = ctx.get("repo_host", "") in ("", "github.com")
	releases = modules.get("releases", False)
	main_tags = releases and cfg["releases"]["branching"] == "main+tags"
	install_ref = cfg["readme"]["install-ref"]
	if not install_ref:
		if main_tags:
			if not ctx.version:
				raise ConfigError(
					f"README.md: under main+tags the installation names the released tag, and {ctx.app}/__init__.py"
					" has no __version__"
				)
			install_ref = f"{cfg['releases']['tag-prefix']}{ctx.version}"
		else:
			install_ref = ctx.branches.release or ctx.branches.integration
	year = ctx.first_commit_year
	if year is None:
		raise ConfigError(
			"README.md: the licence line needs the first commit's year, and there is no commit yet"
		)
	links = [{"label": str(link["label"]), "url": str(link["url"])} for link in org["readme"]["links"]]
	if not org["support-url"] and not org["email"] and not github:
		# The support block's default is GitHub's issues page; off GitHub it needs one of the two.
		raise ConfigError(
			"README.md needs org.support-url (or org.email): the repository is not on GitHub, so there is"
			" no issues page to default to"
		)
	return {
		"repo_url": url_template(org["repo-url"], ctx),
		"github": github,
		"issues_url": f"https://github.com/{ctx.repo}/issues" if github else "",
		"support_url": url_template(org["support-url"], ctx) if org["support-url"] else "",
		"email": org["email"],
		"links": links,
		"badges": [dict(b) for b in org["readme"]["badges"]],
		# The CI badge names the managed caller (N4's ci.yml); without it there is no workflow to show.
		"ci_badge": bool(modules.get("ci")) and github and manifest.ships(".github/workflows/ci.yml"),
		"release_badge": bool(releases) and github,
		"license": org["license"],
		"license_badge": f"https://img.shields.io/badge/license-{shield(org['license'])}-blue",
		"frappe_badge": f"https://img.shields.io/badge/Frappe-v{ctx.frappe.major}-blue",
		"logo": f"{ctx.app}/public/images/{ctx.app}-logo.svg" if modules.get("icons") else "",
		"hero": hero(root, ctx),
		"install_ref": install_ref,
		"release_markers": main_tags and not cfg["readme"]["install-ref"],
		"marketplace": bool(cfg["listing"]["publish"]),
		"branch": ctx.branches.release or ctx.branches.integration,
		"required": [s for s in ctx.siblings if s.required],
		"branch_table": table(
			["Frappe", "Branch"],
			[[f"v{ctx.frappe.major}", f"`{ctx.branches.release or ctx.branches.integration}`"]],
		),
		"requires_table": table(
			["Requires", "Version"],
			[
				[f"[{s.name}](https://github.com/{s.repo})", f"`{s.range}`"]
				for s in ctx.siblings
				if s.required
			],
		),
		"prek": bool(ctx.precommit_live),
		"tests": bool(modules.get("tests")),
		"year": year,
		"copyright_holder": org["copyright-holder"],
		"dev_docs_url": org["dev-docs-url"],
	}


def template_dir(templates: Path | None, name: str) -> Path | None:
	"""The org profile's ``templates/`` when it overrides block ``name``, else ``None`` (built-in)."""
	rel = f"{TEMPLATE_DIR}/{name}.md.j2"
	if templates is not None and (templates / rel).is_file():
		return templates
	return None


def body(name: str, ctx: dict, templates: Path | None) -> str:
	text = rendering.render(
		f"{TEMPLATE_DIR}/{name}.md.j2", ctx, None, directory=template_dir(templates, name)
	)
	return text.strip("\n")


def render(
	root: Path, ctx: context.NS, current: str | None, templates: Path | None, path: str = "README.md"
) -> str:
	"""``current`` with every block's body rendered (exit 2 when a marker is wrong or missing)."""
	if current is None:
		raise ConfigError(
			f"{path} does not exist, and the readme module renders blocks into it: create it with the"
			f" markers of {', '.join(BLOCKS)} (spec §2.19)"
		)
	found = blocks(current, path)
	data = context.NS({**ctx, "readme": context.ns(values(root, ctx))})
	lines = current.split("\n")
	out: list[str] = []
	last = 0
	for name, start, stop in found:
		out += lines[last : start + 1]
		rendered = body(name, data, templates)
		if rendered:
			# A blank line on each side: the markdown formatter (oxfmt, prettier) separates an
			# HTML comment from the blocks around it, so this is the form it leaves alone.
			out += ["", *rendered.split("\n"), ""]
		out.append(lines[stop])
		last = stop + 1
	out += lines[last:]
	return "\n".join(out)
