"""standards-manifest: the manifest is consistent, every template renders for each fixture
context of spec §7 N3 under each profile, and every profile value a template reads is in its
entry's ``uses``.

Run by tests/standards/sync.nix with frappe-nix-tools importable and ``nixfmt`` on PATH,
as ``python sync-contexts.py <example-org profile directory>``. For each context (the shapes
the fleet has) x profile (``minimal``, ``recommended``, the fictitious ``example-org`` with
every module on) it builds a throwaway app, runs ``frappe-nix sync --write --offline`` twice,
fakes the locks an offline sync leaves to uv, yarn and nix, and requires ``--check`` to exit
0, the second write to change nothing, and the rendered ``flake.nix`` to be byte-stable under
nixfmt. While it checks, the configuration every entry's code sees records each module and
org value it reads: one outside the entry's ``uses`` fails the check (§2.4).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from frappe_nix_tools import cli
from frappe_nix_tools.common import config, schema
from frappe_nix_tools.scaffold import engine, manifest
from frappe_nix_tools.scaffold.fixtures import CONTEXTS

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

[tool.frappe-nix]
schema = 1
profile = "{profile}"
frappe-major = 16
"""

PROFILES = ("minimal", "recommended", "example-org")
# pilot-assets needs ci and releases, which minimal has off: that combination is exit 2 by design.
SKIP = {("pilot-assets", "minimal")}
# Reads the engine itself makes for every whole file (whether to compare as data), not a template's.
ENGINE_READS = {"js", "js.tool"}


class Recording(dict):
	"""A configuration table that records the dotted path of each key read from it."""

	def __init__(self, data: dict, path: str, log: set[str]) -> None:
		super().__init__(data)
		self._path = path
		self._log = log

	def _seen(self, key: str, value: Any) -> Any:
		path = f"{self._path}.{key}" if self._path else key
		self._log.add(path)
		return Recording(value, path, self._log) if isinstance(value, dict) else value

	def get(self, key: Any, default: Any = None, /) -> Any:
		return self._seen(key, super().get(key, default)) if key in self else default

	def __getattr__(self, key: str) -> Any:
		try:
			return self[key]
		except KeyError as e:
			raise AttributeError(key) from e

	def __getitem__(self, key: str) -> Any:
		return self._seen(key, super().__getitem__(key))


def covered(path: str, uses: tuple[str, ...]) -> bool:
	keys = [u.removesuffix("?") for u in uses]
	return any(path == u or path.startswith(u + ".") or u.startswith(path + ".") for u in keys)


def static_problems() -> list[str]:
	"""Every entry's module exists (the loader refuses others) and every ``uses`` key is in a schema."""
	out = []
	for entry in manifest.load().entries:
		for key in entry.uses:
			key = key.removesuffix("?")
			if (
				schema.node_at(config.PROFILE_SCHEMA, key) is None
				and schema.node_at(config.APP_SCHEMA, key) is None
			):
				out.append(f"manifest: {entry.path} uses {key}, which no schema has")
	return out


READS: dict[str, set[str]] = {}
_entry_item = engine._entry_item


def recording_entry_item(plan, entry, path, *args):
	"""``engine._entry_item`` with the configuration recording what this entry reads."""
	log: set[str] = set()
	real = plan.ctx["cfg"]
	plan.ctx["cfg"] = Recording(real, "", log)
	try:
		return _entry_item(plan, entry, path, *args)
	finally:
		plan.ctx["cfg"] = real
		heads = (*config.MODULES, "org")
		for read in log:
			if read.split(".", 1)[0] in heads and read not in ENGINE_READS and not covered(read, entry.uses):
				READS.setdefault(f"{entry.path} reads {read}, which its uses does not name", set()).add(path)


def git(root: Path, *args: str) -> None:
	subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(root), *args], check=True)


def fn(root: Path, *argv: str) -> int:
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


def run(
	name: str, profile: str, extra: str, required: list[str], files: dict[str, str], work: Path, org: Path
) -> list[str]:
	label = f"{name} x {profile}"
	root = work / f"{name}-{profile}"
	root.mkdir()
	git(root, "init", "-q")
	source = profile
	if profile == "example-org":
		shutil.copytree(org, root / ".standards-profile")
		source = "./.standards-profile"
	(root / "pyproject.toml").write_text(BASE.replace("{profile}", source) + extra)
	(root / "ctx_app").mkdir()
	(root / "ctx_app/__init__.py").write_text('__version__ = "16.0.0"\n')
	(root / "ctx_app/hooks.py").write_text(f'app_title = "Ctx App"\nrequired_apps = {json.dumps(required)}\n')
	for rel, text in files.items():
		(root / rel).parent.mkdir(parents=True, exist_ok=True)
		(root / rel).write_text(text)
	git(root, "add", "-A")
	git(root, "commit", "-qm", "init")
	problems = []
	if fn(root, "sync", "--write") != 0:
		return [f"{label}: sync --write failed"]
	uv = 'version = 1\nrevision = 3\nrequires-python = ">=3.14"\n' + "".join(
		f'\n[[package]]\nname = "{p}"\nversion = "{v}"\n' for p, v in manifest.load().floors["uv"].items()
	)
	if (root / "tools/pyproject.toml").exists():
		(root / "tools/uv.lock").write_text(uv)
	if (root / "package.json").exists():
		# A yarn.lock that locks what package.json declares, as `yarn install` would leave it.
		pkg = json.loads((root / "package.json").read_text())
		(root / "yarn.lock").write_text(
			"# yarn lockfile v1\n"
			+ "".join(
				f'\n"{n}@{v}":\n  version "0.0.0"\n'
				for field in ("dependencies", "devDependencies", "optionalDependencies")
				for n, v in sorted((pkg.get(field) or {}).items())
			)
		)
	(root / "flake.lock").write_text(json.dumps(lock(["frappe", *required])))
	git(root, "add", "-A")
	git(root, "commit", "-qm", "synced")
	before = snapshot(root)
	if fn(root, "sync", "--write") != 0 or snapshot(root) != before:
		problems.append(f"{label}: a second sync --write changed the tree")
	engine._entry_item = recording_entry_item
	try:
		if fn(root, "sync", "--check") != 0:
			problems.append(f"{label}: --check after --write is not clean")
	finally:
		engine._entry_item = _entry_item
	fmt = subprocess.run(["nixfmt", "--check", str(root / "flake.nix")], capture_output=True, text=True)
	if fmt.returncode != 0:
		problems.append(f"{label}: flake.nix is not nixfmt-stable:\n{fmt.stderr}")
	rendered = sorted(
		p
		for p in before
		if not p.startswith(("ctx_app/", "frontend/", "portal/", "docs-site/", ".standards-profile/"))
	)
	print(f"ok   {label}: {', '.join(rendered)}")
	return problems


def main() -> int:
	org = Path(sys.argv[1]).resolve()
	os.environ["FRAPPE_NIX_OFFLINE"] = "1"
	m = manifest.load()
	print(f"ok   manifest: {len(m.entries)} entries, {len(m.retire)} retire rules, one fragment per path")
	problems = static_problems()
	with tempfile.TemporaryDirectory() as tmp:
		for name, (extra, required, files) in CONTEXTS.items():
			for profile in PROFILES:
				if (name, profile) in SKIP:
					continue
				problems += run(name, profile, extra, required, files, Path(tmp), org)
	problems += [f"uses: {what} ({', '.join(sorted(paths))})" for what, paths in sorted(READS.items())]
	for p in problems:
		print(f"FAIL {p}", file=sys.stderr)
	return 1 if problems else 0


if __name__ == "__main__":
	sys.exit(main())
