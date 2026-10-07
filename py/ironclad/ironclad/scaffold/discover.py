"""The facts sync reads from the tracked tree instead of asking for them (spec §2.2).

Every fact is computed from ``git ls-files``, so the answer is the same on every machine,
and ``--check`` recomputes them: a first ``.ts`` file under ``scripts/`` is drift until sync
renders ``tsconfig.scripts.json``.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

from ironclad.common.report import ConfigError
from ironclad.scaffold import globs

# Top-level directories that are never a nested frontend (§2.2).
NOT_FRONTENDS = {"docs-site", "node_modules", ".frappe-nix", "nix"}

_VITE = re.compile(r"vite(\.[^/]+)?\.config\.[^/]+")


def _listing(root: Path, tracked: set[str]) -> dict[str, Any]:
	if "marketplace/listing.toml" not in tracked:
		return {}
	try:
		return tomllib.loads((root / "marketplace/listing.toml").read_text())
	except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
		raise ConfigError(f"marketplace/listing.toml: {e}") from e


def facts(root: Path, app: str, cfg: dict, tracked: list[str]) -> dict[str, Any]:
	"""The §2.2 table for the app ``app`` at ``root`` with configuration ``cfg``."""
	ts = cfg.get("typescript", {})
	spas = ts.get("spa", [])
	generated = list(cfg.get("generated", []))
	web_include = list(ts.get("web-include", []))
	tracked_set = set(tracked)

	spa_globs: list[str] = []
	for spa in spas:
		for g in spa["include"]:
			if g not in spa_globs:
				spa_globs.append(g)
		root_glob = spa["root"].strip("/")
		if root_glob not in (".", "") and f"{root_glob}/**" not in spa_globs:
			spa_globs.append(f"{root_glob}/**")

	nested = sorted(
		{
			p.split("/", 1)[0]
			for p in tracked
			if p.count("/") == 1 and p.endswith("/package.json") and p.split("/", 1)[0] not in NOT_FRONTENDS
		}
	)
	nested_globs = [f"{d}/**" for d in nested]

	desk_out = [
		"**/*.bundle.js",
		"**/public/dist/**",
		"**/node_modules/**",
		f"{app}/www/**",
		"**/web_form/**",
		f"{app}/templates/**",
		*nested_globs,
		*spa_globs,
		*web_include,
		*generated,
	]
	desk_js = [p for p in tracked if globs.match(f"{app}/**/*.js", p) and not globs.match_any(desk_out, p)]

	web_in = [f"{app}/www/**/*.js", f"{app}/**/web_form/**/*.js", f"{app}/templates/**/*.js", *web_include]
	web_js = [
		p
		for p in tracked
		if p.endswith(".js")
		and globs.match_any(web_in, p)
		and not globs.match_any([*spa_globs, *generated], p)
	]

	# What tsc would actually check once the unchecked-js paths and typescript.exclude
	# are excluded: a project is rendered only when this is non-empty, so no app hits
	# TS18003 ("no inputs") by excluding everything (§2.9).
	unchecked = {u["path"] for u in cfg.get("unchecked-js", [])}
	ts_exclude = list(ts.get("exclude", []))
	desk_project = [p for p in desk_js if p not in unchecked and not globs.match_any(ts_exclude, p)]
	web_project = [p for p in web_js if p not in unchecked and not globs.match_any(ts_exclude, p)]

	public_ts = [
		p
		for p in tracked
		if globs.match(f"{app}/public/**/*.ts", p)
		and not globs.match(f"{app}/public/dist/**", p)
		and not globs.match_any([*spa_globs, *generated], p)
	]
	browser_ts = ts.get("browser", True) is not False and bool(ts.get("browser-include") or public_ts)

	# At the root, in a nested frontend, or at the root of an SPA declared without a
	# package.json of its own.
	vite_dirs = set(nested) | {s["root"].strip("/") for s in spas} - {".", ""}
	vite_configs = [
		p
		for p in tracked
		if _VITE.fullmatch(p.rsplit("/", 1)[-1])
		and (p.count("/") == 0 or (p.count("/") == 1 and p.split("/", 1)[0] in vite_dirs))
	]

	scripts_ts = bool(globs.select(["scripts/**/*.ts", "marketplace/**/*.ts", "ci/**/*.ts"], tracked))
	test_ts = bool(globs.select(["test/**/*.ts"], tracked))
	unit_tests = bool(globs.select(["test/unit/**/*.test.ts"], tracked))

	stylelint_globs = list(cfg.get("stylelint", {}).get("globs", []))
	default_scss = f"{app}/public/**/*.scss"
	scss = stylelint_globs or ([default_scss] if globs.select([default_scss], tracked) else [])

	solution = (
		"tsconfig.ironclad.json" if any(s["tsconfig"] == "tsconfig.json" for s in spas) else "tsconfig.json"
	)
	projects = [
		name
		for name, present in (
			("browser", browser_ts),
			("scripts", scripts_ts),
			("test", test_ts),
			("desk", bool(desk_project)),
			("web", bool(web_project)),
		)
		if present
	]

	listing = _listing(root, tracked_set)
	return {
		"spa_globs": spa_globs,
		"desk_js": desk_js,
		"web_js": web_js,
		"desk_project": desk_project,
		"web_project": web_project,
		"browser_ts": browser_ts,
		"vite": bool(vite_configs),
		"vite_configs": vite_configs,
		"solution": solution,
		"ts_projects": projects,
		"scripts_ts": scripts_ts,
		"test_ts": test_ts,
		"unit_tests": unit_tests,
		"scss": scss,
		"nested_frontends": nested,
		"docs_site": "docs-site/package.json" in tracked_set,
		"gitmodules": ".gitmodules" in tracked_set,
		# test_utils' validate_patches needs <app>/patches/ to exist (it finds the app by it).
		"patches_dir": any(p.startswith(f"{app}/patches/") for p in tracked),
		"has_listing": bool(listing) or "marketplace/listing.toml" in tracked_set,
		"has_shots": "marketplace/screenshots.ts" in tracked_set,
		"app_type": listing.get("type", "extension") if listing else "extension",
		"listing": listing,
	}
