"""``frappe-nix profile show|validate|list``: profiles, resolved and checked (spec §5.13, §8).

``show`` prints the app's resolved configuration (§8.4) and its module switches;
``--explain`` adds, per value, the layer that set it (``builtin:recommended@1.0``,
``org:<source>@<rev>``, ``app`` or ``default``), which answers "why is this gate on".

``validate <profile.toml>`` checks an org profile on its own, before it is published: the
profile schema, an ``extends`` that names a built-in, no app-only key, every module's needs
met under the profile's own switches, a ``requires-frappe-nix`` that admits this
frappe-nix-tools, ``[[extra-files]]`` paths that frappe-nix doesn't manage, and every file
in its ``templates/`` (or ``--templates``): each replaces an overridable template or is an
``[[extra-files]]`` template, and renders for a plain app and one with erpnext and hrms
without an undefined variable.

``list`` prints ``minimal`` and every ``recommended@<minor>`` snapshot, marking the one plain
``recommended`` means (S42).

Exit codes as sync's: 0, 2 (invalid) or 3 (environment).
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import jinja2
import tomlkit

from frappe_nix_tools.common import config, repo
from frappe_nix_tools.common.report import CLEAN, ConfigError, EnvError
from frappe_nix_tools.scaffold import engine
from frappe_nix_tools.scaffold import render as rendering

# The throwaway apps a profile's templates must render for (§7 N3's fixture contexts).
CONTEXTS = {"plain": [], "erpnext+hrms": ["erpnext", "hrms"]}


def _plain(value: Any) -> Any:
	"""``value`` without ``None`` (TOML has no null)."""
	if isinstance(value, dict):
		return {k: _plain(v) for k, v in value.items() if v is not None}
	if isinstance(value, list):
		return [_plain(v) for v in value if v is not None]
	return value


def _flat(value: Any, prefix: str = "") -> dict[str, Any]:
	if isinstance(value, dict) and value:
		out: dict[str, Any] = {}
		for key, item in value.items():
			out.update(_flat(item, f"{prefix}.{key}" if prefix else key))
		return out
	return {prefix: value}


def show(args: argparse.Namespace) -> int:
	path = Path(args.pyproject) if args.pyproject else repo.find_up("pyproject.toml")
	resolved = config.resolve(
		path,
		lock_path=Path(args.lock) if args.lock else None,
		profile_dir=Path(args.profile_path) if args.profile_path else None,
	)
	for notice in resolved.notices:
		print(f"notice: {notice}", file=sys.stderr)
	doc = {"profile": resolved.profile, "modules": resolved.modules, "config": resolved.cfg}
	if args.explain:
		for key, value in _flat(resolved.cfg).items():
			print(f"{key} = {json.dumps(value, sort_keys=True)}  # {resolved.source(key)}")
		for module, on in resolved.modules.items():
			print(f"modules.{module} = {'true' if on else 'false'}")
		return CLEAN
	if args.format == "json":
		print(json.dumps(doc, indent=2, sort_keys=False))
	else:
		sys.stdout.write(tomlkit.dumps(_plain(doc)))
	return CLEAN


def _throwaway(root: Path, profile: Path, siblings: list[str]) -> Path:
	"""An app in a git repository under ``root`` that uses the profile in ``profile`` in-repo."""
	app = root / f"app-{len(list(root.iterdir()))}"
	(app / "profile_check").mkdir(parents=True)
	shutil.copytree(profile, app / ".standards-profile")
	(app / "pyproject.toml").write_text(
		'[project]\nname = "profile_check"\ndynamic = ["version"]\n\n'
		f'[tool.frappe-nix]\nschema = 1\nprofile = "./.standards-profile"\nfrappe-major = 16\nsiblings = {json.dumps(siblings)}\n'
	)
	(app / "profile_check" / "__init__.py").write_text('__version__ = "16.0.0"\n')
	(app / "profile_check" / "hooks.py").write_text(
		f'app_title = "Profile Check"\nrequired_apps = {json.dumps(siblings)}\n'
	)
	for argv in (
		["init", "-q"],
		["add", "-A"],
		["-c", "user.name=p", "-c", "user.email=p@example.org", "commit", "-qm", "app"],
	):
		subprocess.run(["git", "-C", str(app), *argv], check=True, capture_output=True)
	return app


def validate(args: argparse.Namespace) -> int:
	path = Path(args.file)
	try:
		doc = tomllib.loads(path.read_text())
	except FileNotFoundError as e:
		raise EnvError(f"{path} does not exist") from e
	except tomllib.TOMLDecodeError as e:
		raise ConfigError(f"{path}: {e}") from e
	label = f"profile {path}"
	config.validate_profile(doc, label, org=True)
	config.builtin_profile(doc["extends"])
	config.check_requires(doc.get("requires-frappe-nix"), label)
	templates = Path(args.templates) if args.templates else path.parent / "templates"
	with tempfile.TemporaryDirectory() as tmp:
		profile = Path(tmp) / "profile"
		profile.mkdir()
		(profile / "profile.toml").write_text(path.read_text())
		if templates.is_dir():
			shutil.copytree(templates, profile / "templates")
		for name, siblings in CONTEXTS.items():
			app = _throwaway(Path(tmp), profile, siblings)
			# Resolution checks the needs under the profile's own switches (the app sets none),
			# and the plan checks [[extra-files]] and every override's place.
			plan = engine.build(app, options={"init_listing": False})
			for file in (
				sorted(p for p in (profile / "templates").rglob("*") if p.is_file())
				if (profile / "templates").is_dir()
				else []
			):
				rel = file.relative_to(profile / "templates").as_posix()
				try:
					rendering.render(rel, plan.ctx, None, directory=profile / "templates")
				except jinja2.TemplateError as e:
					raise ConfigError(f"templates/{rel} does not render for the {name} app: {e}") from e
	print(f"{path}: valid (extends {doc['extends']})")
	return CLEAN


def list_profiles(_args: argparse.Namespace) -> int:
	newest = config.builtin_name("recommended")
	for name in config.builtin_names():
		mark = "  (recommended)" if name == newest else ""
		print(f"{name}{mark}")
	return CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"profile",
		help="show the resolved profile, validate an org profile, list the built-ins",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	sub = p.add_subparsers(dest="profile_command", metavar="<show|validate|list>", required=True)
	s = sub.add_parser("show", help="the app's resolved configuration and modules")
	s.add_argument("--explain", action="store_true", help="name the layer that set each value")
	s.add_argument("--format", choices=("toml", "json"), default="toml")
	s.add_argument("--pyproject", help="the pyproject.toml to read (default: the nearest one)")
	s.add_argument("--lock", help="the flake.lock an org profile is pinned in")
	s.add_argument("--profile-path", help="read the org profile from this directory")
	s.set_defaults(func=show)
	v = sub.add_parser("validate", help="check an org profile before it is published")
	v.add_argument("file", metavar="profile.toml")
	v.add_argument("--templates", help="its template directory (default: templates/ beside the file)")
	v.set_defaults(func=validate)
	ls = sub.add_parser("list", help="the built-in profiles")
	ls.set_defaults(func=list_profiles)
