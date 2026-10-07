"""The JSON tool configs sync renders (spec §2.9, §2.10, §2.16), built as data.

Their templates are one line each (``{{ gen.oxlintrc() | json }}``); the shape lives here
because most of it is computed from the discovered file sets, and the JSON text comes
from ``jsonfmt``, which prints oxfmt's own form.
"""

from typing import Any

from ironclad.common.report import ConfigError

# The rules an app's [tool.ironclad.oxlint].overrides may not touch (§2.1).
LOCKED_RULES = (
	"typescript/no-explicit-any",
	"typescript/ban-ts-comment",
	"typescript/consistent-type-imports",
)

DESK_GLOBALS = ("frappe", "__", "cur_frm", "cur_list", "locals", "$", "jQuery", "moment")
WEB_GLOBALS = ("frappe", "__", "$", "jQuery")


def _unique(items: list[str]) -> list[str]:
	out: list[str] = []
	for item in items:
		if item not in out:
			out.append(item)
	return out


def oxlintrc(ctx: Any) -> dict:
	app, cfg, d = ctx.app, ctx.cfg, ctx.discover
	ox = cfg.get("oxlint", {})
	for i, override in enumerate(ox.get("overrides", [])):
		named = [r for r in LOCKED_RULES if r in override.get("rules", {})]
		if named:
			raise ConfigError(f"[tool.ironclad.oxlint].overrides[{i}] may not change {', '.join(named)}")
	desk_globals = {g: "readonly" for g in DESK_GLOBALS}
	for s in ctx.siblings:
		if s.desk_global:
			desk_globals[s.desk_global] = "readonly"
	desk_globals.update(ox.get("globals", {}))
	web_globals = {g: "readonly" for g in WEB_GLOBALS}
	web_globals.update(ox.get("globals", {}))
	overrides: list[dict] = [
		{
			"files": ["scripts/**", "marketplace/**", "ci/**", "test/**"],
			"env": {"node": True},
			"rules": {"no-console": "off", "no-await-in-loop": "off", "import/no-unassigned-import": "off"},
		},
		{
			"files": [f"{app}/public/js/*.bundle.ts", f"{app}/public/js/*.bundle.js"],
			"rules": {"import/no-unassigned-import": "off"},
		},
	]
	if d.desk_js:
		overrides.append({"files": sorted(d.desk_js), "globals": desk_globals})
	if d.web_js:
		overrides.append({"files": sorted(d.web_js), "globals": web_globals})
	overrides += [dict(o) for o in ox.get("overrides", [])]
	return {
		"$schema": "./node_modules/oxlint/configuration_schema.json",
		"plugins": ["typescript", "unicorn", "oxc", "import"],
		"categories": {"correctness": "error", "suspicious": "error", "perf": "warn", "pedantic": "off"},
		"env": {"builtin": True, "es2024": True, "browser": True},
		"rules": {
			"typescript/no-explicit-any": "error",
			"typescript/ban-ts-comment": "error",
			"typescript/consistent-type-imports": "error",
			"no-console": ["warn", {"allow": ["error", "warn"]}],
			"import/no-cycle": "off",
			"no-underscore-dangle": "off",
			"unicorn/consistent-function-scoping": "off",
			"unicorn/no-array-sort": "off",
		},
		"ignorePatterns": _unique(
			[
				f"{app}/public/dist/**",
				".frappe-nix/**",
				".dev-dist/**",
				"nix/**",
				"docs-site/**",
				"types/doctypes.d.ts",
				*cfg.get("generated", []),
				*ox.get("ignore", []),
			]
		),
		"overrides": overrides,
	}


def oxfmt_ignores(ctx: Any) -> list[str]:
	"""The ignorePatterns ``.oxfmtrc.jsonc`` adds after its fixed, commented ones."""
	return _unique([*ctx.cfg.get("generated", []), *ctx.cfg.get("oxfmt", {}).get("ignore", [])])


def _unchecked(ctx: Any) -> list[str]:
	return [u["path"] for u in ctx.cfg.get("unchecked-js", [])]


def tsconfig_solution(ctx: Any) -> dict:
	return {"files": [], "references": [{"path": f"./tsconfig.{p}.json"} for p in ctx.discover.ts_projects]}


def tsconfig_base(ctx: Any) -> dict:
	options: dict[str, Any] = {"noUnusedLocals": True, "noUnusedParameters": True, "noImplicitReturns": True}
	paths = ctx.cfg.get("typescript", {}).get("paths", {})
	if paths:
		options["paths"] = dict(paths)
	return {"extends": "frappe-types/tsconfig/base.json", "compilerOptions": options}


def tsconfig_browser(ctx: Any) -> dict:
	app, ts = ctx.app, ctx.cfg.get("typescript", {})
	include = list(ts.get("browser-include", [])) or [
		f"{app}/public/js/**/*.ts",
		f"{app}/public/js/**/*.d.ts",
		"types/**/*.d.ts",
	]
	return {
		"extends": "./tsconfig.base.json",
		"compilerOptions": {
			"composite": True,
			"types": ["frappe-types/global"],
			"allowImportingTsExtensions": True,
		},
		"include": include,
		"exclude": _unique(
			[
				"node_modules",
				f"{app}/public/dist",
				*ctx.discover.spa_globs,
				*ctx.cfg.get("generated", []),
				*ts.get("exclude", []),
			]
		),
	}


def tsconfig_scripts(ctx: Any) -> dict:
	return {
		"extends": "./tsconfig.base.json",
		"compilerOptions": {"composite": True, "types": ["node"], "allowImportingTsExtensions": True},
		"include": ["scripts/**/*.ts", "marketplace/**/*.ts", "ci/**/*.ts"],
		"exclude": _unique(["node_modules", *ctx.cfg.get("typescript", {}).get("exclude", [])]),
	}


def tsconfig_test(ctx: Any) -> dict:
	app = ctx.app
	return {
		"extends": "./tsconfig.base.json",
		"compilerOptions": {
			"composite": True,
			"types": ["node", "frappe-types/global"],
			"allowImportingTsExtensions": True,
		},
		"include": ["test/**/*.ts", f"{app}/public/js/**/*.ts", "scripts/lib/**/*.ts"],
		"exclude": _unique(
			["node_modules", f"{app}/public/dist", *ctx.cfg.get("typescript", {}).get("exclude", [])]
		),
	}


def tsconfig_desk(ctx: Any) -> dict:
	app, ts, d = ctx.app, ctx.cfg.get("typescript", {}), ctx.discover
	return {
		"extends": "frappe-types/tsconfig/desk-js.json",
		"compilerOptions": {"composite": True},
		"include": [f"{app}/**/*.js", "types/doctypes.d.ts", f"types/{app}.augment.d.ts"],
		"exclude": _unique(
			[
				"node_modules",
				"**/*.bundle.js",
				"**/public/dist/**",
				f"{app}/www/**",
				"**/web_form/**",
				f"{app}/templates/**",
				*[f"{n}/**" for n in d.nested_frontends],
				*d.spa_globs,
				*ts.get("web-include", []),
				*ctx.cfg.get("generated", []),
				*_unchecked(ctx),
				*ts.get("exclude", []),
			]
		),
	}


def tsconfig_web(ctx: Any) -> dict:
	app, ts, d = ctx.app, ctx.cfg.get("typescript", {}), ctx.discover
	return {
		"extends": "frappe-types/tsconfig/desk-js.json",
		"compilerOptions": {"composite": True, "types": ["frappe-types/web"]},
		"include": _unique(
			[
				f"{app}/www/**/*.js",
				f"{app}/**/web_form/**/*.js",
				f"{app}/templates/**/*.js",
				*ts.get("web-include", []),
				"types/doctypes.d.ts",
				f"types/{app}.augment.d.ts",
			]
		),
		"exclude": _unique(
			[
				"node_modules",
				"**/public/dist/**",
				*d.spa_globs,
				*ctx.cfg.get("generated", []),
				*_unchecked(ctx),
				*ts.get("exclude", []),
			]
		),
	}


TSCONFIGS = {
	"browser": tsconfig_browser,
	"scripts": tsconfig_scripts,
	"test": tsconfig_test,
	"desk": tsconfig_desk,
	"web": tsconfig_web,
}


def release_please_config(ctx: Any) -> dict:
	out: dict[str, Any] = {
		"$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
		"include-v-in-tag": True,
		"include-component-in-tag": False,
		"separate-pull-requests": True,
	}
	sha = ctx.cfg.get("release", {}).get("bootstrap-sha")
	if sha:
		out["bootstrap-sha"] = sha
	out["packages"] = {
		".": {
			"release-type": "node",
			"package-name": ctx.app_hyphen,
			"changelog-path": "CHANGELOG.md",
			"extra-files": [{"type": "generic", "path": f"{ctx.app}/__init__.py"}],
		}
	}
	return out


class Gen:
	"""The ``gen`` object templates call: ``{{ gen.tsconfig('desk') | jsonc }}``."""

	def __init__(self, ctx: Any) -> None:
		self.ctx = ctx

	def oxlintrc(self) -> dict:
		return oxlintrc(self.ctx)

	def oxfmt_ignores(self) -> list[str]:
		return oxfmt_ignores(self.ctx)

	def tsconfig(self, name: str) -> dict:
		if name == "solution":
			return tsconfig_solution(self.ctx)
		if name == "base":
			return tsconfig_base(self.ctx)
		return TSCONFIGS[name](self.ctx)

	def release_please_config(self) -> dict:
		return release_please_config(self.ctx)
