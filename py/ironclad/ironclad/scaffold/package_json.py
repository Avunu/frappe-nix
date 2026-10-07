"""``package.json`` and ``.stylelintrc.json``: the ``json-merge`` strategy (spec §2.8, §2.11).

Only the managed keys are set; every other key, and the order of all of them, is the app's.
A key sync adds goes at the end of its object. ``devDependencies`` floors are raised, never
lowered: the app's caret or exact range stands when its minimum is at least the floor.
"""

import copy
import re
from typing import Any

from ironclad.common.report import DRIFT, INVALID
from ironclad.scaffold import floors

VITE_REGISTER = "node scripts/ironclad-vite-register.mjs"
FORBIDDEN_DEP = re.compile(r"^(eslint.*|prettier.*|@typescript-eslint/.+|eslint-config-.+)$")
DEP_MAPS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
# A specifier that names a tarball, a git ref or a path rather than a registry range. It has
# no minimum to hold to a floor, so it is the app's explicit choice: sync leaves it (the
# fixture app installs an unpublished frappe-types this way).
NON_REGISTRY = re.compile(r"^(https?://|git(\+[a-z]+)?:|github:|file:|link:)")


def created(ctx: Any) -> dict:
	"""The ``package.json`` sync writes when there is none."""
	return {
		"name": ctx.app_hyphen,
		"version": ctx.version or "0.1.0",
		"private": True,
		"description": ctx.tagline,
		"license": "MIT",
		"author": "Avunu LLC",
	}


def has_ts(ctx: Any) -> bool:
	return bool(ctx.discover.ts_projects) or bool(ctx.discover.desk_js) or bool(ctx.discover.web_js)


def dev_floors(ctx: Any) -> dict[str, str]:
	"""The ``devDependencies`` floors this app gets (§2.8)."""
	f = ctx.floors
	out = dict(f.get("npm", {}))
	if has_ts(ctx):
		out.update(f.get("npm-ts", {}))
	if ctx.discover.scss:
		out.update(f.get("npm-scss", {}))
	return out


def _number(value: Any) -> str:
	return str(int(value)) if float(value).is_integer() else str(value)


def _scripts(ctx: Any, current: dict) -> dict[str, str | None]:
	"""Managed scripts; ``None`` means the key must be absent."""
	d = ctx.discover
	lint = "oxlint" + "".join(f' && stylelint "{g}"' for g in d.scss)
	checks = [s["check"] for s in ctx.cfg.get("typescript", {}).get("spa", [])]
	parts = ([f"tsc --build {d.solution}"] if d.ts_projects else []) + checks
	typecheck = " && ".join(parts) if parts else None
	unit = None
	if d.unit_tests:
		unit = (
			"node --test --experimental-test-coverage"
			f" --test-coverage-include='{ctx.app}/public/js/**'"
			f" --test-coverage-lines={_number(ctx.cfg['js-coverage-min'])} 'test/unit/**/*.test.ts'"
		)
	check = "yarn -s format:check && yarn -s lint"
	if typecheck:
		check += " && yarn -s typecheck"
	if unit:
		check += " && yarn -s test:unit"
	out: dict[str, str | None] = {
		"format": "oxfmt",
		"format:check": "oxfmt --check",
		"lint": lint,
		"typecheck": typecheck,
		"test:unit": unit,
		"lint:py": "uv run --frozen --project tools ruff check . && uv run --frozen --project tools ruff format --check .",
		"typecheck:py": 'uv run --frozen --project tools ty check --python "${FRAPPE_BENCH_ROOT:-.frappe-nix/bench}/env"',
		"check": check,
	}
	build = current.get("build")
	if d.vite and isinstance(build, str) and not build.rstrip().endswith(VITE_REGISTER):
		out["build"] = f"{build.rstrip()} && {VITE_REGISTER}"
	return out


def merge(current: dict | None, ctx: Any, *, seed_version: bool) -> dict:
	"""``current`` (or the created file) with every managed key set."""
	doc = copy.deepcopy(current) if current is not None else created(ctx)
	exact: dict[str, Any] = {
		"private": True,
		"type": "module",
		"license": "MIT",
		"engines": None,
		"packageManager": "yarn@1.22.22",
		"frappe": {"major": str(ctx.frappe.major), "branch": ctx.frappe.branch},
	}
	if seed_version and ctx.version:
		doc["version"] = ctx.version
	for key, value in exact.items():
		if key == "engines":
			engines = doc.get("engines")
			if not isinstance(engines, dict):
				engines = doc["engines"] = {}
			engines["node"] = ">=24"
			continue
		doc[key] = value
	scripts = doc.get("scripts")
	if not isinstance(scripts, dict):
		scripts = doc["scripts"] = {}
	for key, value in _scripts(ctx, scripts).items():
		if value is None:
			scripts.pop(key, None)
		else:
			scripts[key] = value
	dev = doc.get("devDependencies")
	if not isinstance(dev, dict):
		dev = doc["devDependencies"] = {}
	for pkg, floor in dev_floors(ctx).items():
		have = dev.get(pkg)
		if isinstance(have, str) and NON_REGISTRY.match(have):
			continue
		if not isinstance(have, str) or not floors.range_ok(have, floor):
			dev[pkg] = f"^{floor}"
	return doc


def problems(doc: dict, ctx: Any) -> list[tuple[int, str]]:
	"""Rules on the app-owned keys (§2.8 "Forbidden", and C8 when there is no build to append to)."""
	out: list[tuple[int, str]] = []
	raw = doc.get("scripts")
	scripts: dict = raw if isinstance(raw, dict) else {}
	d = ctx.discover
	builds = bool(
		d.vite or d.nested_frontends or ctx.cfg.get("typescript", {}).get("spa") or ctx.cfg.get("build")
	)
	if "build" in scripts and not builds:
		out.append(
			(
				INVALID,
				"scripts.build exists, but there is no Vite config, nested frontend or SPA entry and"
				" [tool.ironclad] build is not true: frappe's esbuild runs it on every bench",
			)
		)
	if ctx.cfg.get("build") and "build" not in scripts:
		out.append((INVALID, "[tool.ironclad] build = true, but package.json has no build script"))
	if d.vite and "build" not in scripts:
		out.append(
			(DRIFT, f"scripts.build is missing: a Vite app's build must end with `{VITE_REGISTER}` (S30)")
		)
	for key in scripts:
		if key.startswith("ironclad:"):
			out.append((INVALID, f"scripts.{key}: the ironclad: prefix is reserved"))
	for field in DEP_MAPS:
		deps = doc.get(field)
		if isinstance(deps, dict):
			for pkg in deps:
				if FORBIDDEN_DEP.match(pkg):
					out.append(
						(INVALID, f"{field}.{pkg}: eslint and prettier are replaced by oxlint and oxfmt")
					)
	return out


def stylelint_created(ctx: Any) -> dict:
	return {"extends": ["stylelint-config-standard-scss"], "ignoreFiles": [f"{ctx.app}/public/dist/**"]}


def stylelint_merge(current: dict | None, ctx: Any) -> dict:
	doc = copy.deepcopy(current) if current is not None else stylelint_created(ctx)
	doc["extends"] = ["stylelint-config-standard-scss"]
	ignore = doc.get("ignoreFiles")
	if isinstance(ignore, str):
		ignore = [ignore]
	if not isinstance(ignore, list):
		ignore = []
	want = f"{ctx.app}/public/dist/**"
	if want not in ignore:
		ignore.append(want)
	doc["ignoreFiles"] = ignore
	return doc
