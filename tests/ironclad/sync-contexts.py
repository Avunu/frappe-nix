"""ironclad-manifest: every template renders for each fixture context of spec §7 N3.

Run by tests/ironclad/sync.nix with the packaged ``ironclad`` importable and ``nixfmt`` on
PATH. For each context it builds a throwaway app (the shapes the fleet has), runs
``ironclad sync --write --offline`` twice, fakes the locks offline sync leaves to uv, yarn
and nix, and requires ``--check`` to exit 0, the second write to change nothing, and the
rendered ``flake.nix`` to be byte-stable under nixfmt.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ironclad import cli
from ironclad.scaffold import manifest

BASE = """[project]
name = "ctx_app"
description = "A context app"
requires-python = ">=3.14"
dynamic = ["version"]
dependencies = []

[build-system]
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[tool.coverage.report]
fail_under = 10

[tool.ironclad]
schema = 1
frappe-major = 16
"""

SPA_ROOT = (
	'typescript = { spa = [{ root = ".", include = ["ctx_app/public/js/app/**"], tsconfig = "tsconfig.json",'
	' check = "vue-tsc --noEmit -p tsconfig.json" }] }\n'
)
SPA_PORTAL = (
	'typescript = { spa = [{ root = "portal", include = ["portal/src/**"], tsconfig = "portal/tsconfig.json",'
	' check = "vue-tsc --noEmit -p portal/tsconfig.json" }] }\n'
)

# name: (extra [tool.ironclad] lines, required_apps, files)
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
	"pilot-assets": ("pilot-assets = true\n", [], {}),
	"vite": (
		"",
		[],
		{
			"vite.config.ts": "export default {};\n",
			"ctx_app/public/js/x.bundle.ts": "export {};\n",
			"ctx_app/ctx_app/doctype/a/a.js": "frappe.ui.form.on('A', {});\n",
			"ctx_app/www/p.js": "frappe.ready(() => {});\n",
			"package.json": '{"name": "ctx-app", "scripts": {"build": "vite build"}}\n',
		},
	),
}


def git(root: Path, *args: str) -> None:
	subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(root), *args], check=True)


def ironclad(root: Path, *argv: str) -> int:
	old = Path.cwd()
	os.chdir(root)
	try:
		return cli.main(list(argv))
	finally:
		os.chdir(old)


def snapshot(root: Path) -> dict[str, bytes]:
	return {
		str(p.relative_to(root)): p.read_bytes()
		for p in root.rglob("*")
		if p.is_file() and ".git" not in p.relative_to(root).parts
	}


def lock(apps: list[str]) -> dict:
	nodes: dict = {}
	for name, owner, ref in [
		("frappe-nix", "Avunu", "release-1"),
		*[(a, "frappe", "version-16") for a in apps],
	]:
		nodes[name] = {
			"locked": {
				"type": "github",
				"owner": owner,
				"repo": name,
				"rev": "a" * 40,
				"narHash": "sha256-x",
			},
			"original": {"type": "github", "owner": owner, "repo": name, "ref": ref},
		}
	nodes["root"] = {"inputs": {**{n: n for n in nodes}, "nixpkgs": ["frappe-nix", "nixpkgs"]}}
	return {"nodes": nodes, "root": "root", "version": 7}


def run(name: str, extra: str, required: list[str], files: dict[str, str], work: Path) -> list[str]:
	root = work / name
	root.mkdir()
	git(root, "init", "-q")
	(root / "pyproject.toml").write_text(BASE + extra)
	(root / "ctx_app").mkdir()
	(root / "ctx_app/__init__.py").write_text('__version__ = "16.0.0"\n')
	(root / "ctx_app/hooks.py").write_text(f'app_title = "Ctx App"\nrequired_apps = {json.dumps(required)}\n')
	for rel, text in files.items():
		(root / rel).parent.mkdir(parents=True, exist_ok=True)
		(root / rel).write_text(text)
	git(root, "add", "-A")
	git(root, "commit", "-qm", "init")
	problems = []
	if ironclad(root, "sync", "--write") != 0:
		return [f"{name}: sync --write failed"]
	uv = 'version = 1\nrevision = 3\nrequires-python = ">=3.14"\n' + "".join(
		f'\n[[package]]\nname = "{p}"\nversion = "{v}"\n' for p, v in manifest.load().floors["uv"].items()
	)
	(root / "tools/uv.lock").write_text(uv)
	(root / "yarn.lock").write_text("# yarn lockfile v1\n")
	(root / "flake.lock").write_text(json.dumps(lock(["frappe", *required])))
	git(root, "add", "-A")
	before = snapshot(root)
	if ironclad(root, "sync", "--write") != 0 or snapshot(root) != before:
		problems.append(f"{name}: a second sync --write changed the tree")
	if ironclad(root, "sync", "--check") != 0:
		problems.append(f"{name}: --check after --write is not clean")
	fmt = subprocess.run(["nixfmt", "--check", str(root / "flake.nix")], capture_output=True, text=True)
	if fmt.returncode != 0:
		problems.append(f"{name}: flake.nix is not nixfmt-stable:\n{fmt.stderr}")
	rendered = sorted(
		p for p in before if not p.startswith(("ctx_app/", "frontend/", "portal/", "docs-site/"))
	)
	print(f"ok   {name}: {', '.join(rendered)}")
	return problems


def main() -> int:
	os.environ["IRONCLAD_OFFLINE"] = "1"
	problems: list[str] = []
	with tempfile.TemporaryDirectory() as tmp:
		for name, (extra, required, files) in CONTEXTS.items():
			problems += run(name, extra, required, files, Path(tmp))
	for p in problems:
		print(f"FAIL {p}", file=sys.stderr)
	return 1 if problems else 0


if __name__ == "__main__":
	sys.exit(main())
