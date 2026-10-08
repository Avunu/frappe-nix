"""``frappe-nix profile show|validate|list``: profiles, resolved and checked (spec §5.13, §8).

``show`` prints the app's resolved configuration (§8.4) and its module switches;
``--explain`` adds, per value, the layer that set it (``builtin:recommended@1.0``,
``org:<source>@<rev>``, ``app`` or ``default``), which answers "why is this gate on".

``validate <profile.toml>`` checks an org profile on its own, before it is published: the
profile schema, an ``extends`` that names a built-in, no app-only key, every module's needs
met under the profile's own switches, a ``requires-frappe-nix`` that admits this
frappe-nix-tools, ``[[extra-files]]`` paths that frappe-nix doesn't manage, and every file
in its ``templates/`` (or ``--templates``): each replaces an overridable template or is an
``[[extra-files]]`` template, and renders for each fixture context of §7 N3 (plain,
erpnext+hrms, scss, a nested frontend, an SPA at the root and in portal/, a docs site,
pilot-assets, Vite; ``scaffold/fixtures.py``) without an undefined variable, and every
``[[retire]]`` ``contains`` is a regular expression.

``list`` prints ``minimal`` and every ``recommended@<minor>`` snapshot with its description,
marking the one plain ``recommended`` means (S42).

Exit codes as sync's: 0, 2 (invalid) or 3 (environment).
"""

import argparse
import json
import os
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
from frappe_nix_tools.scaffold import engine, fixtures
from frappe_nix_tools.scaffold import render as rendering

# The throwaway apps a profile's templates must render for (§7 N3's fixture contexts).


def _plain(value: Any) -> Any:
	"""``value`` without ``None`` (TOML has no null)."""
	if isinstance(value, dict):
		return {k: _plain(v) for k, v in value.items() if v is not None}
	if isinstance(value, list):
		return [_plain(v) for v in value if v is not None]
	return value


def _flat(value: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
	if isinstance(value, dict) and value:
		out: dict[tuple[str, ...], Any] = {}
		for key, item in value.items():
			out.update(_flat(item, (*prefix, str(key))))
		return out
	return {prefix: value}


def _toml_key(path: tuple[str, ...]) -> str:
	return ".".join(tomlkit.key(part).as_string() for part in path)


def _toml_inline(value: Any) -> str:
	"""``value`` on one line: tables inline, as TOML spells them."""
	if isinstance(value, dict):
		inner = ", ".join(f"{tomlkit.key(k).as_string()} = {_toml_inline(v)}" for k, v in value.items())
		return "{ " + inner + " }" if inner else "{}"
	if isinstance(value, list):
		return "[" + ", ".join(_toml_inline(v) for v in value) + "]"
	return tomlkit.item(value).as_string()


def _explained(resolved: config.Resolved, fmt: str) -> str:
	"""``show --explain``: what ``show`` prints, with the layer that set each value, in
	``fmt``. TOML gives each ``[config]`` value as a dotted key with the layer as its comment
	(no ``None``: TOML has no null); JSON adds a ``sources`` table, keyed by the dotted name."""
	flat = _flat(resolved.cfg)
	if fmt == "json":
		doc = {
			"profile": resolved.profile,
			"modules": resolved.modules,
			"config": resolved.cfg,
			"sources": {".".join(k): resolved.source(".".join(k)) for k in flat},
		}
		return json.dumps(doc, indent=2) + "\n"
	lines = ["[profile]"]
	for key, value in _flat(dict(resolved.profile)).items():
		if value is not None:
			lines.append(f"{_toml_key(key)} = {_toml_inline(_plain(value))}")
	lines += ["", "[modules]"]
	for module, on in resolved.modules.items():
		lines.append(f"{tomlkit.key(module).as_string()} = {'true' if on else 'false'}")
	lines += ["", "[config]"]
	for key, value in flat.items():
		if value is not None:
			line = f"{_toml_key(key)} = {_toml_inline(_plain(value))}"
			lines.append(f"{line}  # {resolved.source('.'.join(key))}")
	return "\n".join(lines) + "\n"


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
		sys.stdout.write(_explained(resolved, args.format))
		return CLEAN
	if args.format == "json":
		print(json.dumps(doc, indent=2, sort_keys=False))
	else:
		sys.stdout.write(tomlkit.dumps(_plain(doc)))
	return CLEAN


def _throwaway(root: Path, profile: Path, extra: str, required: list[str], files: dict[str, str]) -> Path:
	"""An app in a git repository under ``root`` that uses the profile in ``profile`` in-repo,
	shaped as one fixture context (``extra`` table lines, ``required`` apps, ``files``)."""
	app = root / f"app-{len(list(root.iterdir()))}"
	name = fixtures.APP
	(app / name).mkdir(parents=True)
	shutil.copytree(profile, app / ".standards-profile")
	(app / "pyproject.toml").write_text(
		f'[project]\nname = "{name}"\ndynamic = ["version"]\n\n'
		'[tool.frappe-nix]\nschema = 1\nprofile = "./.standards-profile"\nfrappe-major = 16\n' + extra
	)
	(app / name / "__init__.py").write_text('__version__ = "16.0.0"\n')
	(app / name / "hooks.py").write_text(f'app_title = "Ctx App"\nrequired_apps = {json.dumps(required)}\n')
	for rel, text in files.items():
		(app / rel).parent.mkdir(parents=True, exist_ok=True)
		(app / rel).write_text(text)
	# A repository of its own: none of the user's git configuration (commit signing, a global
	# core.hooksPath, init templates) applies to it, and no hook runs.
	env = {
		**os.environ,
		"GIT_CONFIG_GLOBAL": os.devnull,
		"GIT_CONFIG_NOSYSTEM": "1",
		"GIT_AUTHOR_NAME": "profile-validate",
		"GIT_AUTHOR_EMAIL": "profile-validate@example.org",
		"GIT_COMMITTER_NAME": "profile-validate",
		"GIT_COMMITTER_EMAIL": "profile-validate@example.org",
	}
	for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
		env.pop(key, None)
	for argv in (
		["init", "-q", "--template="],
		["add", "-A"],
		[
			"-c",
			"commit.gpgsign=false",
			"-c",
			f"core.hooksPath={os.devnull}",
			"commit",
			"-q",
			"--no-verify",
			"-m",
			"app",
		],
	):
		subprocess.run(["git", "-C", str(app), *argv], check=True, capture_output=True, env=env)
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
		if templates.is_symlink():
			raise ConfigError(
				f"{templates} is a symlink: a profile's templates are read only as regular files"
			)
		if templates.is_dir():
			# A link in it is refused here, as sync refuses it, rather than copied as its target.
			rendering.template_files(templates)
			shutil.copytree(templates, profile / "templates", symlinks=True)
		plain: engine.Plan | None = None
		for name, (extra, required, files) in fixtures.CONTEXTS.items():
			if name == "pilot-assets" and plain is not None:
				# pilot-assets needs ci and releases: with one off, turning it on is exit 2 by design.
				if not all(plain.resolved.modules.get(m) for m in config.NEEDS["pilot-assets"]):
					continue
			app = _throwaway(Path(tmp), profile, extra, required, files)
			# Resolution checks the needs under the profile's own switches (the app sets none),
			# and the plan checks [[extra-files]], every override's place and every [[retire]].
			try:
				plan = engine.build(app, options={"init_listing": False})
			except ConfigError as e:
				raise ConfigError(f"the {name} app: {e}") from e
			plain = plain or plan
			for file in (
				rendering.template_files(profile / "templates") if (profile / "templates").is_dir() else []
			):
				rel = file.relative_to(profile / "templates").as_posix()
				try:
					rendering.render(rel, plan.ctx, None, directory=profile / "templates")
				except jinja2.TemplateError as e:
					raise ConfigError(f"templates/{rel} does not render for the {name} app: {e}") from e
	print(f"{path}: valid (extends {doc['extends']})")
	return CLEAN


def list_profiles(_args: argparse.Namespace) -> int:
	"""Each built-in with its description (§5.13), marking the one plain ``recommended`` means."""
	newest = config.builtin_name("recommended")
	names = config.builtin_names()
	labels = {name: name + ("  (recommended)" if name == newest else "") for name in names}
	width = max(len(label) for label in labels.values())
	for name in names:
		description = config.builtin_profile(name)[1].get("description", "")
		print(f"{labels[name]:<{width}}  {description}".rstrip())
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
