"""``package.json`` and ``.stylelintrc.json``: the ``json-merge`` strategy (spec §2.8, §2.11).

Only the managed keys are set; every other key, and the order of all of them, is the app's.
A key sync adds goes at the end of its object. ``devDependencies`` floors are raised, never
lowered: the app's caret or exact range stands when its minimum is at least the floor.

Each managed key belongs to one module (a key group) and is managed only while that
module is on. When a module turns off (it was on in ``HEAD``'s configuration,
``ctx.previous``), sync removes each of its keys whose value still equals what the module
would render (a toolchain ``devDependencies`` entry counts as equal when its range is
``^<floor>`` or higher), and leaves any other value as the app's, with a warning (§3.3
step 6).
"""

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from frappe_nix_tools.common.report import DRIFT, INVALID
from frappe_nix_tools.scaffold import floors

VITE_REGISTER = "node scripts/vite-register.mjs"
FORBIDDEN_DEP = re.compile(r"^(eslint.*|prettier.*|@typescript-eslint/.+|eslint-config-.+)$")
DEP_MAPS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
# A specifier that names a tarball, a git ref or a path rather than a registry range. It has
# no minimum to hold to a floor, so it is the app's explicit choice: sync leaves it.
NON_REGISTRY = re.compile(r"^(https?://|git(\+[a-z]+)?:|github:|file:|link:)")
# Scripts sync retires (core.json's retire list, module vite-register): a script step that
# runs one is dropped when sync deletes it, so `yarn build` (which bench build runs) never
# calls a missing file.
RETIRED_SCRIPTS = ("update-assets.mjs",)
_RETIRED_STEP = re.compile(r"^node\s+(?:\./)?(?:" + "|".join(re.escape(s) for s in RETIRED_SCRIPTS) + r")$")
# The parts of scripts.check, in order (§2.8): each one only when that script exists.
CHECK_PARTS = ("format:check", "lint", "lint:css", "typecheck", "test:unit")
RESERVED_PREFIX = "frappe-nix:"


@dataclass
class Merged:
	"""The merged document, and what sync left as the app's although a module is off."""

	doc: dict
	warnings: list[str] = field(default_factory=list)


def drop_retired_steps(script: str) -> str | None:
	"""``script`` without its ``node update-assets.mjs`` steps (``&&``-joined); ``None`` when
	nothing else is left."""
	parts = [p.strip() for p in script.split("&&")]
	kept = [p for p in parts if not _RETIRED_STEP.match(p)]
	if len(kept) == len(parts):
		return script
	return " && ".join(kept) if kept else None


def created(ctx: Any, version: str | None = None) -> dict:
	"""The ``package.json`` sync writes when there is none (§2.8): ``license`` and ``author``
	only when the profile sets ``org.license`` and ``org.publisher``."""
	doc: dict[str, Any] = {
		"name": ctx.app_hyphen,
		"version": version or ctx.version or "0.1.0",
		"private": True,
		"description": ctx.tagline,
	}
	if ctx.org.get("license"):
		doc["license"] = ctx.org["license"]
	if ctx.org.get("publisher"):
		doc["author"] = ctx.org["publisher"]
	return doc


def has_ts(ctx: Any) -> bool:
	"""Any managed TypeScript project (desk and web only with check-js) or SPA."""
	return bool(ctx.discover.ts_projects) or bool(ctx.cfg.get("typescript", {}).get("spa"))


def dev_floors(ctx: Any) -> dict[str, dict[str, str]]:
	"""The ``devDependencies`` floors, by the module that owns them (§2.8)."""
	f = ctx.floors
	ts = dict(f.get("npm-ts", {})) if has_ts(ctx) else {}
	if ts and ctx.cfg.get("typescript", {}).get("preset", "frappe-types") == "frappe-types":
		ts.update(f.get("npm-frappe-types", {}))
	return {
		"js": dict(f.get("npm-js", {})),
		"typescript": ts,
		"stylelint": dict(f.get("npm-scss", {})) if ctx.discover.scss else {},
	}


def _number(value: Any) -> str:
	return str(int(value)) if float(value).is_integer() else str(value)


def _js_oxc(ctx: Any) -> bool:
	return bool(ctx.modules.get("js")) and ctx.cfg.get("js", {}).get("tool", "oxc") == "oxc"


def _was_on(ctx: Any, module: str) -> bool:
	"""Whether ``module`` was on at ``HEAD``: only then are its keys sync's to retract."""
	previous = ctx.get("previous")
	if not previous:
		return False
	return _js_oxc(previous) if module == "js" else bool(previous.modules.get(module))


def scripts(ctx: Any) -> dict[str, dict[str, str | None]]:
	"""The managed scripts, by module; ``None`` means the key must be absent.

	``check`` is not here: it is computed from the merged scripts (``check_script``).
	"""
	d = ctx.discover
	tests = ctx.cfg.get("tests", {})
	checks = [s["check"] for s in ctx.cfg.get("typescript", {}).get("spa", [])]
	parts = ([f"tsc --build {d.solution}"] if d.ts_projects else []) + checks
	unit = None
	if tests.get("js-unit", True) and d.unit_tests:
		unit = (
			"node --test --experimental-test-coverage"
			f" --test-coverage-include='{ctx.app}/public/js/**'"
			f" --test-coverage-lines={_number(tests.get('js-coverage-min', 50))} 'test/unit/**/*.test.ts'"
		)
	return {
		"js": {"format": "oxfmt", "format:check": "oxfmt --check", "lint": "oxlint"},
		"stylelint": {"lint:css": " && ".join(f'stylelint "{g}"' for g in d.scss) or None},
		"typescript": {"typecheck": " && ".join(parts) if parts else None},
		"tests": {"test:unit": unit},
		"python-lint": {
			"lint:py": "uv run --frozen --project tools ruff check . && uv run --frozen --project tools ruff format --check ."
		},
		"python-types": {
			"typecheck:py": 'uv run --frozen --project tools ty check --python "${FRAPPE_BENCH_ROOT:-.frappe-nix/bench}/env"'
		},
	}


def check_script(present: dict) -> str | None:
	"""``scripts.check``: the parts that exist, whoever owns them (§2.8)."""
	parts = [f"yarn -s {name}" for name in CHECK_PARTS if isinstance(present.get(name), str)]
	return " && ".join(parts) if parts else None


def metadata_keys(ctx: Any) -> dict[str, Any]:
	"""The ``metadata`` group's exact keys; ``None`` where the profile leaves the key to the app."""
	meta = ctx.cfg.get("metadata", {})
	return {
		"private": True,
		"type": meta.get("package-type") or None,
		"license": ctx.org.get("license") or None,
		"author": ctx.org.get("publisher") or None,
		"engines.node": meta.get("node-engine") or None,
		"packageManager": meta.get("package-manager") or None,
		"frappe": {"major": str(ctx.frappe.major), "branch": ctx.frappe.branch},
	}


def _get(doc: dict, dotted: str) -> Any:
	head, _, rest = dotted.partition(".")
	if not rest:
		return doc.get(head)
	sub = doc.get(head)
	return sub.get(rest) if isinstance(sub, dict) else None


def _set(doc: dict, dotted: str, value: Any) -> None:
	head, _, rest = dotted.partition(".")
	if not rest:
		doc[head] = value
		return
	sub = doc.get(head)
	if not isinstance(sub, dict):
		sub = doc[head] = {}
	sub[rest] = value


def _drop(doc: dict, dotted: str) -> None:
	head, _, rest = dotted.partition(".")
	if not rest:
		doc.pop(head, None)
		return
	sub = doc.get(head)
	if isinstance(sub, dict):
		sub.pop(rest, None)
		if not sub:
			doc.pop(head, None)


def _put(table: dict, key: str, value: Any, order: list[str]) -> None:
	"""Set ``table[key]``; a new key goes before the first later key of ``order`` the table has,
	else at the end. Turning a module off and on again then restores the same order."""
	if key in table or key not in order:
		table[key] = value
		return
	later = order[order.index(key) + 1 :]
	items = list(table.items())
	at = next((i for i, (k, _) in enumerate(items) if k in later), len(items))
	items.insert(at, (key, value))
	table.clear()
	table.update(items)


def _dict(doc: dict, key: str) -> dict:
	value = doc.get(key)
	if not isinstance(value, dict):
		value = doc[key] = {}
	return value


def merge(current: dict | None, ctx: Any, *, version: str | None) -> Merged:
	"""``current`` (or the created file) with every managed key set or retracted.

	``version`` is given only while sync seeds ``.release-please-manifest.json``: the one time
	sync sets ``version`` (§2.8), to the version it seeds the manifest and block with."""
	doc = copy.deepcopy(current) if current is not None else created(ctx, version)
	out = Merged(doc)
	modules = ctx.modules
	js_on = _js_oxc(ctx)

	def retract(key: str, wanted: Any, module: str) -> None:
		have = _get(doc, key)
		if have is None:
			return
		if have == wanted:
			_drop(doc, key)
		else:
			out.warnings.append(
				f"package.json {key} is the app's now that {module} is off (it differs from what sync wrote)"
			)

	if version:
		doc["version"] = version

	# metadata: the package's base keys.
	for key, value in metadata_keys(ctx).items():
		if modules.get("metadata"):
			if value is not None:
				_set(doc, key, value)
		elif value is not None and _was_on(ctx, "metadata"):
			retract(key, value, "metadata")

	# The scripts of each module (js only with tool = "oxc").
	sc = doc.get("scripts")
	if not isinstance(sc, dict):
		sc = None
	script_order = [k for table in scripts(ctx).values() for k in table] + ["check"]
	for module, table in scripts(ctx).items():
		on = js_on if module == "js" else bool(modules.get(module))
		for key, value in table.items():
			if on:
				if sc is None:
					sc = _dict(doc, "scripts")
				if value is None:
					sc.pop(key, None)
				else:
					_put(sc, key, value, script_order)
			elif sc is not None and value is not None and key in sc and _was_on(ctx, module):
				if sc[key] == value:
					del sc[key]
				else:
					out.warnings.append(
						f"package.json scripts.{key} is the app's now that {module} is off (it differs from what sync wrote)"
					)
	if sc is not None:
		# vite-register: the build ends with the registration step (C8, S30), and the steps
		# that ran a retired file are gone with it.
		if modules.get("vite-register"):
			for key in list(sc):
				if isinstance(sc[key], str):
					kept = drop_retired_steps(sc[key])
					if kept is None:
						del sc[key]
					else:
						sc[key] = kept
			build = sc.get("build")
			if ctx.discover.vite and isinstance(build, str) and not build.rstrip().endswith(VITE_REGISTER):
				sc["build"] = f"{build.rstrip()} && {VITE_REGISTER}"
		elif _was_on(ctx, "vite-register"):
			build = sc.get("build")
			if isinstance(build, str) and build.rstrip().endswith(f" && {VITE_REGISTER}"):
				sc["build"] = build.rstrip()[: -len(f" && {VITE_REGISTER}")]
		# check belongs to js (oxc); with js off it is the app's.
		check = check_script(sc)
		if js_on:
			if check is None:
				sc.pop("check", None)
			else:
				_put(sc, "check", check, script_order)
		elif (
			_was_on(ctx, "js") and "check" in sc and sc["check"] == check_script({**sc, **scripts(ctx)["js"]})
		):
			del sc["check"]
	if doc.get("scripts") == {} and (current is None or "scripts" not in current):
		doc.pop("scripts")

	# devDependencies floors (S9).
	dep_order = [pkg for table in dev_floors(ctx).values() for pkg in table]
	for module, table in dev_floors(ctx).items():
		on = js_on if module == "js" else bool(modules.get(module))
		dev = doc.get("devDependencies")
		for pkg, floor in table.items():
			have = dev.get(pkg) if isinstance(dev, dict) else None
			if on:
				if isinstance(have, str) and NON_REGISTRY.match(have):
					continue
				if not isinstance(have, str) or not floors.range_ok(have, floor):
					_put(_dict(doc, "devDependencies"), pkg, f"^{floor}", dep_order)
			elif isinstance(dev, dict) and have is not None and _was_on(ctx, module):
				if isinstance(have, str) and floors.range_ok(have, floor):
					del dev[pkg]
				else:
					out.warnings.append(
						f"package.json devDependencies.{pkg} is the app's now that {module} is off (below the floor sync sets)"
					)
	if doc.get("devDependencies") == {} and (
		current is None or "devDependencies" not in current or current["devDependencies"]
	):
		doc.pop("devDependencies")
	return out


def builds(ctx: Any) -> bool:
	"""Whether a ``build`` script is a real step: a Vite config, a nested frontend, an SPA, or ``build = true``."""
	d = ctx.discover
	return bool(
		d.vite or d.nested_frontends or ctx.cfg.get("typescript", {}).get("spa") or ctx.cfg.get("build")
	)


def problems(doc: dict, ctx: Any) -> list[tuple[int, str]]:
	"""Rules on the app-owned keys (§2.8 "Forbidden", and C8 when there is no build to append to)."""
	out: list[tuple[int, str]] = []
	raw = doc.get("scripts")
	sc: dict = raw if isinstance(raw, dict) else {}
	modules = ctx.modules
	if modules.get("metadata"):
		if "build" in sc and not builds(ctx):
			out.append(
				(
					INVALID,
					"scripts.build exists, but there is no Vite config, nested frontend or SPA entry and"
					" [tool.frappe-nix] build is not true: frappe's esbuild runs it on every bench",
				)
			)
		if ctx.cfg.get("build") and "build" not in sc:
			out.append((INVALID, "[tool.frappe-nix] build = true, but package.json has no build script"))
	if modules.get("vite-register") and ctx.discover.vite and "build" not in sc:
		out.append(
			(DRIFT, f"scripts.build is missing: a Vite app's build must end with `{VITE_REGISTER}` (S30)")
		)
	for key, value in sc.items():
		if key.startswith(RESERVED_PREFIX):
			out.append((INVALID, f"scripts.{key}: the {RESERVED_PREFIX} prefix is reserved"))
		if not modules.get("vite-register"):
			continue
		named = [s for s in RETIRED_SCRIPTS if isinstance(value, str) and s in value]
		if named:
			out.append(
				(
					INVALID,
					f"scripts.{key} runs {named[0]}, which sync deletes as a legacy file: remove that step",
				)
			)
	if _js_oxc(ctx):
		for name in DEP_MAPS:
			deps = doc.get(name)
			if isinstance(deps, dict):
				for pkg in deps:
					if FORBIDDEN_DEP.match(pkg):
						out.append(
							(INVALID, f"{name}.{pkg}: eslint and prettier are replaced by oxlint and oxfmt")
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


# Specifiers yarn resolves inside the checkout, which yarn.lock (v1) has no entry for.
_UNLOCKED = re.compile(r"^(link:|portal:|workspace:)")


def yarn_lock_keys(text: str) -> set[str]:
	"""Every ``name@range`` a ``yarn.lock`` resolves: the comma-separated keys of each
	top-level entry (berry's ``name@npm:range`` read as ``name@range``)."""
	berry = re.search(r"^__metadata:", text, re.M) is not None
	keys: set[str] = set()
	for line in text.splitlines():
		if not line or line[0] in " \t#" or not line.endswith(":"):
			continue
		for part in line[:-1].split(","):
			key = part.strip().strip('"')
			keys.add(key.replace("@npm:", "@", 1) if berry else key)
	return keys


def yarn_lock_missing(package: dict | None, lock: str, local: set[str]) -> list[str]:
	"""The ``name@range`` of each dependency ``package.json`` declares that ``lock`` has no
	entry for: yarn.lock is stale, and ``yarn install --frozen-lockfile`` would refuse it.
	``local`` names the workspace packages, which yarn links rather than locks."""
	if not isinstance(package, dict):
		return []
	keys = yarn_lock_keys(lock)
	out: set[str] = set()
	for name in ("dependencies", "devDependencies", "optionalDependencies"):
		deps = package.get(name)
		if not isinstance(deps, dict):
			continue
		for pkg, spec in deps.items():
			if not isinstance(spec, str) or _UNLOCKED.match(spec) or pkg in local:
				continue
			if f"{pkg}@{spec}" not in keys:
				out.add(f"{pkg}@{spec}")
	return sorted(out)
