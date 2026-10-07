"""The fixture contexts of spec §7 N3: the shapes of app the fleet has, which every template
must render for. ``frappe-nix profile validate`` renders an org profile against each (§5.13),
and frappe-nix's standards-manifest check (tests/standards/sync-contexts.py) syncs each under
every profile. The app is named ``ctx_app``.
"""

APP = "ctx_app"

SPA_ROOT = (
	'typescript.spa = [{ root = ".", include = ["ctx_app/public/js/app/**"], tsconfig = "tsconfig.json",'
	' check = "vue-tsc --noEmit -p tsconfig.json" }]\n'
)
SPA_PORTAL = (
	'typescript.spa = [{ root = "portal", include = ["portal/src/**"], tsconfig = "portal/tsconfig.json",'
	' check = "vue-tsc --noEmit -p portal/tsconfig.json" }]\n'
)

# name: (extra [tool.frappe-nix] lines, required_apps, files)
CONTEXTS = {
	"plain": ("", [], {}),
	"erpnext+hrms": ('siblings = ["erpnext", "hrms"]\n', ["erpnext", "hrms"], {}),
	"scss": ("", [], {"ctx_app/public/scss/a.scss": "a { color: red; }\n"}),
	"nested-frontend": (
		"",
		[],
		{
			"frontend/package.json": '{"name": "fe"}\n',
			"frontend/vite.config.ts": "export default {};\n",
			"package.json": '{"name": "ctx-app", "scripts": {"build": "cd frontend && yarn build"}}\n',
		},
	),
	"spa-root": (
		SPA_ROOT,
		[],
		{
			"tsconfig.json": "{}\n",
			"vite.config.ts": "export default {};\n",
			"ctx_app/public/js/app/main.ts": "export {};\n",
			"package.json": '{"name": "ctx-app", "scripts": {"build": "vite build"}}\n',
		},
	),
	"spa-portal": (
		SPA_PORTAL,
		[],
		{
			"portal/tsconfig.json": "{}\n",
			"portal/src/main.ts": "export {};\n",
			"package.json": '{"name": "ctx-app", "scripts": {"build": "vite build --config portal/vite.config.ts"}}\n',
		},
	),
	"docs-site": ("", [], {"docs-site/package.json": '{"name": "docs"}\n'}),
	"pilot-assets": ("pilot-assets.enable = true\n", [], {}),
	"vite": (
		"",
		[],
		{
			"vite.config.ts": "export default {};\n",
			"ctx_app/public/js/x.bundle.ts": "export {};\n",
			"ctx_app/ctx_app/doctype/a/a.js": "frappe.ui.form.on('A', {});\n",
			"ctx_app/www/p.js": "frappe.ready(() => {});\n",
			# test_ts and unit_tests: tsconfig.test.json and the test:unit script.
			"test/unit/a.test.ts": "export {};\n",
			"package.json": '{"name": "ctx-app", "scripts": {"build": "vite build"}}\n',
		},
	),
}
