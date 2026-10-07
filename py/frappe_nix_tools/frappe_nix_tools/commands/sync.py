"""``frappe-nix sync --write|--check``: the managed files of a Frappe app (spec §2, §3).

``frappe-init --sync`` and ``frappe-init --check`` run this (``lib/sh/app-sync.sh``). It
works in the current directory, which must be an app: a git work tree whose
``pyproject.toml`` ``[project].name`` names a package holding ``hooks.py``.

Only an app that opted in is synced (S35): one whose ``pyproject.toml`` has a
``[tool.frappe-nix]`` table. ``--standards <profile>`` creates the table; without either,
both modes exit 2 with the opt-in hint and write nothing.

``--write`` runs both phases (S32): phase A writes ``flake.nix`` and ``.envrc`` and locks the
flake; phase B renders every other managed file, retracts what a module that turned off
left, deletes what is retired, and brings the locks up (``tools/uv.lock``, ``yarn.lock``,
node-lock seeds, relock, README blocks). It stages what it wrote with ``git add`` and
commits nothing.

``--check`` computes the same plan in memory and runs nothing (no nix, uv or yarn), so it
works in a CI job without Nix. Exit codes: 0 clean, 1 drift, 2 invalid configuration that
sync can't fix, 3 environment (not an app, unreadable lock, version skew).
"""

import argparse
import contextlib
import importlib.util
import io
import os
import re
import secrets
import shutil
import sys
import traceback
from pathlib import Path

import frappe_nix_tools
from frappe_nix_tools.common import repo
from frappe_nix_tools.common.report import (
	CLEAN,
	DRIFT,
	ENVIRONMENT,
	INVALID,
	ConfigError,
	EnvError,
	Finding,
	FrappeNixError,
	render,
	worst,
)
from frappe_nix_tools.scaffold import bootstrap, defaults, engine, tomlmerge
from frappe_nix_tools.scaffold.engine import Item


def _only(args: argparse.Namespace) -> list[str] | None:
	paths = [p.strip() for chunk in (args.only or []) for p in chunk.split(",") if p.strip()]
	return paths or None


def _options(args: argparse.Namespace) -> dict:
	return {
		"init_listing": bool(getattr(args, "init_listing", False)),
		"force": bool(getattr(args, "force", False)),
	}


def _profile_dir(args: argparse.Namespace) -> Path | None:
	if not getattr(args, "profile_path", None):
		return None
	path = Path(args.profile_path).resolve()
	print(
		f"frappe-nix sync: warning: reading the org profile from {path} (--profile-path), not its locked"
		" input; nothing records this, so commit only what the locked profile renders",
		file=sys.stderr,
	)
	return path


def _passthrough(args: argparse.Namespace) -> list[str]:
	"""The flags a re-executed sync gets again (``frappe-init --sync`` or ``frappe-nix sync --write``)."""
	out = []
	if args.dry_run:
		out.append("--dry-run")
	if args.skip_lock:
		out.append("--skip-lock")
	if args.offline:
		out.append("--offline")
	if args.init_listing:
		out.append("--init-listing")
	if args.force:
		out.append("--force")
	if args.frappe_version:
		out += ["--frappe-version", args.frappe_version]
	if args.site:
		out += ["--site", args.site]
	if args.standards:
		out += ["--standards", args.standards]
	if args.profile_path:
		out += ["--profile-path", str(Path(args.profile_path).resolve())]
	for chunk in args.only or []:
		out += ["--only", chunk]
	return out


def _listing_readme(mode: str) -> tuple[int, str] | None:
	"""``frappe-nix listing readme --<mode>``, when N5's command is installed; else ``None``."""
	if importlib.util.find_spec("frappe_nix_tools.commands.listing") is None:
		return None
	from frappe_nix_tools import cli

	out = io.StringIO()
	with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
		code = cli.main(["listing", "readme", f"--{mode}"])
	return code, out.getvalue()


def _build(root: Path, args: argparse.Namespace, phases: tuple[str, ...] = ("a", "b")) -> engine.Plan:
	return engine.build(
		root,
		frappe_version=args.frappe_version,
		site=args.site,
		only=_only(args),
		options=_options(args),
		profile_dir=args.profile_dir,
		standards=getattr(args, "standards", None),
		phases=phases,
		frappe_nix_lock=getattr(args, "dry_run_lock", None),
	)


def _stderr(lines: list[str], fmt: str = "text") -> None:
	"""Diagnostics on stderr. Under ``--format github`` they are PR data the runner would parse
	for workflow commands as well, so they go inside a ``::stop-commands::`` block of their own,
	closed before the report reaches stdout (§3.3)."""
	if not lines:
		return
	if fmt == "github":
		token = secrets.token_hex(16)
		lines = [f"::stop-commands::{token}", *lines, f"::{token}::"]
	for line in lines:
		print(line, file=sys.stderr)
	sys.stderr.flush()


def _notices(plan: engine.Plan, fmt: str = "text") -> None:
	_stderr([f"frappe-nix sync: notice: {notice}" for notice in plan.notices], fmt)


def _report(findings: list[Finding], fmt: str, code: int, plan: engine.Plan | None) -> str:
	frappe_nix = {"rev": plan.ctx.frappe_nix.rev if plan else "", "version": frappe_nix_tools.__version__}
	text = render(findings, fmt, code, frappe_nix)
	if fmt == "json" and plan is not None:
		import json

		doc = json.loads(text)
		doc["profile"] = dict(plan.resolved.profile)
		doc["modules"] = dict(plan.resolved.modules)
		text = json.dumps(doc, indent=2) + "\n"
	return text


# --- --check -----------------------------------------------------------------------


def check(root: Path, args: argparse.Namespace) -> int:
	if args.profile_dir is not None and os.environ.get("CI"):
		raise EnvError("--profile-path is for profile authors: --check in CI reads the locked profile")
	plan = _build(root, args)
	_notices(plan, args.format)
	items = list(plan.problems)
	if plan.created_config is not None:
		items.insert(
			0,
			Item(
				"pyproject.toml",
				"toml-merge",
				None,
				None,
				"[tool.frappe-nix] is missing: --standards creates it",
				DRIFT,
			),
		)
	if not _only(args):
		items += engine.lock_problems(plan)
		items += engine.untracked_lock_problems(plan)
		if plan.ctx.modules.get("readme") and plan.ctx.discover.has_listing:
			readme = _listing_readme("check")
			if readme and readme[0] != CLEAN:
				items.append(
					Item(
						"README.md",
						"blocks",
						None,
						None,
						readme[1].strip() or "README blocks drift",
						readme[0],
					)
				)
	items += engine.skew_problems(plan, args.expect_rev)
	code = worst(*(i.code for i in items))
	findings = [i.finding() for i in items]
	sys.stdout.write(_report(findings, args.format, code, plan))
	return code


# An error message that starts with the file it is about: "package.json: …", ".editorconfig:12: …".
_ERROR_PATH = re.compile(r"^(?P<path>[^\s:;]+)(?::\d+)?: ")


def _error_path(root: Path, e: Exception) -> str:
	"""The file a raised error is about: the path its message starts with, when that names
	one in the app; else ``pyproject.toml`` for a configuration error and ``.`` otherwise."""
	m = _ERROR_PATH.match(str(e))
	if m and not Path(m["path"]).is_absolute() and ".." not in Path(m["path"]).parts:
		if os.path.lexists(root / m["path"]):
			return m["path"]
	return "pyproject.toml" if isinstance(e, ConfigError) else "."


def check_reported(root: Path, args: argparse.Namespace) -> int:
	"""``--check``, where an error that stops the plan (not opted in, not a repo, a schema
	error, an unknown sibling, a malformed local region, a crash) is still a report in
	``json`` and ``github``: ``frappe-nix repo audit`` and the CI annotations always get a
	document (§3.3)."""
	if args.format == "text":
		repo.toplevel(root)
		return check(root, args)
	try:
		repo.toplevel(root)
		return check(root, args)
	except FrappeNixError as e:
		code, problem, path = e.code, str(e), _error_path(root, e)
	except Exception as e:
		# The CLI's own internal-error path, with the report on top: never exit 1 (drift).
		if os.environ.get("FRAPPE_NIX_DEBUG"):
			traceback.print_exc()
		code, problem, path = ENVIRONMENT, f"internal error: {type(e).__name__}: {e}", "."
	_stderr([f"frappe-nix sync: {problem}"], args.format)
	rev = ""
	with contextlib.suppress(Exception):
		rev = engine.locked_rev(root) or ""
	frappe_nix = {"rev": rev, "version": frappe_nix_tools.__version__}
	sys.stdout.write(render([Finding(path, "", problem)], args.format, code, frappe_nix))
	return code


# --- --write -----------------------------------------------------------------------


def _apply(root: Path, items: list[Item], runner: bootstrap.Runner) -> list[str]:
	"""Write or delete every item whose content changes; returns the paths touched."""
	touched = []
	for item in items:
		if item.code != DRIFT or item.command or item.wanted == item.current:
			continue
		# No link on the way (a retired link itself is deleted, never followed).
		target = engine.inside(root, item.path, link_ok=True)
		if runner.dry_run:
			print(item.finding().path + f" ({item.strategy}): {item.problem}")
			print(item.diff(), end="")
		elif item.wanted is None:
			target.unlink(missing_ok=True)
			print(f"frappe-nix sync: - {item.path} ({item.problem})", file=sys.stderr)
		else:
			target.parent.mkdir(parents=True, exist_ok=True)
			if target.is_symlink():
				target.unlink()  # write a regular file, never through a link
			target.write_text(item.wanted)
			print(f"frappe-nix sync: + {item.path}", file=sys.stderr)
		touched.append(item.path)
	return touched


def _stage(root: Path, paths: list[str], runner: bootstrap.Runner) -> None:
	if runner.dry_run or not paths:
		return
	repo.git(root, "add", "-A", "--", *sorted(set(paths)))


def _invalid(items: list[Item]) -> int:
	bad = [i for i in items if i.code >= INVALID]
	for i in bad:
		print(f"frappe-nix sync: {i.path}: {i.problem}", file=sys.stderr)
	return worst(*(i.code for i in bad))


def _create_table(root: Path, plan: engine.Plan, runner: bootstrap.Runner) -> None:
	"""Step 1: write the ``[tool.frappe-nix]`` table ``--standards`` created."""
	if plan.created_config is None:
		return
	text = engine.read(root, "pyproject.toml") or ""
	new = tomlmerge.add_tool_frappe_nix(text, plan.created_config)
	if runner.dry_run:
		print("pyproject.toml (toml-merge): [tool.frappe-nix] is created")
		return
	(root / "pyproject.toml").write_text(new)
	print("frappe-nix sync: + pyproject.toml [tool.frappe-nix]", file=sys.stderr)
	_stage(root, ["pyproject.toml"], runner)


def phase_a(root: Path, args: argparse.Namespace, runner: bootstrap.Runner) -> bool:
	"""Steps 1 to 4. Returns whether ``flake.lock`` changed.

	``--only`` limits it too: the flake is written, locked and handed over only when ``--only``
	is not given or names ``flake.nix`` or ``.envrc``. ``[tool.frappe-nix]`` is created either
	way, since every other file renders from it."""
	only = _only(args)
	flake_step = only is None or bool({"flake.nix", ".envrc"} & set(only))
	plan = _build(root, args, phases=("a",))
	code = _invalid(plan.items)
	if code:
		raise SystemExit(code)
	if flake_step:
		bootstrap.require_release_branch(runner, plan.ctx.frappe_nix.url)
	_create_table(root, plan, runner)
	touched = _apply(root, plan.items, runner)
	# A flake sees only tracked files: stage them before `nix flake lock` reads the flake.
	_stage(root, touched, runner)
	changed = False
	if flake_step and not args.offline and not runner.dry_run:
		inputs = engine.flake_input_specs((root / "flake.nix").read_text())
		changed = bootstrap.phase_a_lock(runner, inputs, plan.ctx.frappe_nix.url)
		if (root / "flake.lock").is_file():
			_stage(root, ["flake.lock"], runner)
		# An org profile read only now that it is locked: render the flake again from it
		# (its modules can change flake.nix, the docs-site exclusion), then lock once more.
		again = _build(root, args, phases=("a",))
		code = _invalid(again.items)
		if code:
			raise SystemExit(code)
		redone = _apply(root, again.items, runner)
		if redone:
			_stage(root, redone, runner)
			changed = (
				bootstrap.phase_a_lock(
					runner,
					engine.flake_input_specs((root / "flake.nix").read_text()),
					again.ctx.frappe_nix.url,
				)
				or changed
			)
			if (root / "flake.lock").is_file():
				_stage(root, ["flake.lock"], runner)
		bootstrap.maybe_reexec(runner, _passthrough(args), changed or bootstrap.inherited_lock_change())
	elif flake_step and runner.dry_run:
		print(
			"would run: nix flake lock (when flake.lock is missing or stale, or frappe-nix is not locked from flake.nix's URL)"
		)
		# §3.3: phase B of a dry run renders against the rev that lock would take.
		url = plan.ctx.frappe_nix.url
		if not runner.offline and not bootstrap.override_url() and bootstrap.release_ref_needed(root, url):
			rev = bootstrap.release_branch_rev(root, f"release-{plan.ctx.frappe_nix.major}")
			if rev:
				args.dry_run_lock = {
					"rev": rev,
					"owner": plan.ctx.frappe_nix.owner,
					"repo": plan.ctx.frappe_nix.name,
				}
	return changed


def _repo_settings(ns: object | None) -> list[str]:
	"""The modules of ``ns`` (a plan's context or ``previous``) on that need a repository
	setting (§3.3 step 5): ``releases``, and ``dependabot`` with ``auto-merge``."""
	if ns is None:
		return []
	modules, cfg = ns["modules"], ns["cfg"]
	out = ["releases"] if modules.get("releases") else []
	if modules.get("dependabot") and (cfg.get("dependabot") or {}).get("auto-merge", True):
		out.append("dependabot (auto-merge)")
	return out


def needs_repo_settings(plan: engine.Plan) -> list[str]:
	"""What this sync turns on that needs a repository setting: on now, and off at ``HEAD``
	(or no table there yet)."""
	before = set(_repo_settings(plan.ctx.get("previous")))
	return [m for m in _repo_settings(plan.ctx) if m not in before]


def phase_b(root: Path, args: argparse.Namespace, runner: bootstrap.Runner, lock_changed: bool) -> int:
	"""Steps 5 to 13."""
	lock_changed = lock_changed or bootstrap.inherited_lock_change()
	bootstrap.ensure_tools(runner, _passthrough(args), lock_changed)
	only = _only(args)
	plan = _build(root, args, phases=("b",))
	_notices(plan)
	defaults.warn_changed(root, plan)
	code = _invalid(plan.items)
	if code:
		return code
	for module in needs_repo_settings(plan):
		print(
			f"frappe-nix sync: notice: {module} is on now and needs a repository setting:"
			" run `frappe-nix repo doctor` (spec §5.6)",
			file=sys.stderr,
		)
	touched = _apply(root, plan.items, runner)
	# Staged now as well as at step 13, so a lock or formatter step that fails below leaves
	# what sync wrote staged rather than half-applied in the work tree.
	_stage(root, touched, runner)

	# Steps 8 and 9: the locks the plan cannot write itself.
	by_path = {i.path: i for i in plan.items}
	uv_item = by_path.get("tools/uv.lock")
	if uv_item and (uv_item.command or "tools/pyproject.toml" in touched):
		short = engine.floors.lock_shortfalls(root / "tools/uv.lock", engine.uv_floors(plan.ctx))
		argv = ["uv", "lock", "--project", "tools"]
		if (root / "tools/uv.lock").is_file():
			for pkg in short:
				argv += ["--upgrade-package", pkg]
		runner.run(argv)
		touched.append("tools/uv.lock")
		if (root / "tools/uv.lock").is_file():
			_stage(root, ["tools/uv.lock"], runner)  # now, so a failure below leaves it staged
	yarn_item = by_path.get("yarn.lock")
	if yarn_item and (yarn_item.command or "package.json" in touched):
		runner.run(["yarn", "install", "--non-interactive"])
		touched.append("yarn.lock")
		if (root / "yarn.lock").is_file():
			_stage(root, ["yarn.lock"], runner)

	# Formatting (§3.2): whole files are already in oxfmt's form; this settles the merged ones.
	oxfmt = root / "node_modules" / ".bin" / "oxfmt"
	written = [p for p in touched if (root / p).is_file() and not p.endswith(".lock")]
	if written and oxfmt.exists() and engine._js_oxc(plan.ctx):
		# The installed binary itself, not through yarn: --offline runs no yarn (and may have none).
		runner.run([str(oxfmt), "--no-error-on-unmatched-pattern", *written], network=False)
	init_py = f"{plan.app.name}/__init__.py"
	if (
		init_py in touched
		and plan.ctx.modules.get("python-lint")
		and (root / "tools/uv.lock").is_file()
		and shutil.which("uv")
	):
		runner.run(["uv", "run", "--frozen", "--project", "tools", "ruff", "format", init_py])

	# Step 10: node-lock seeds for the apps the bench carries.
	if not only:
		seeded = bootstrap.seed_node_locks(
			root, plan.ctx.frappe.major, [s.name for s in plan.ctx.siblings], dry_run=runner.dry_run
		)
		for path in seeded:
			print(f"frappe-nix sync: + {path} (seed)", file=sys.stderr)
		touched += seeded
		_stage(root, seeded, runner)  # before relock, which may fail

	# Step 11: the bench lock follows the flake inputs.
	if not only and not args.skip_lock and (lock_changed or not (root / "nix/uv.lock").is_file()):
		runner.run(["nix", "run", "--no-pure-eval", *bootstrap.nix_args(), ".#relock"])
		# Relock stages what it writes (nix/uv.lock, nix/node-locks/); never the rest of nix/.
		touched.append("nix/uv.lock")

	# Step 12: the README blocks.
	if not only and plan.ctx.modules.get("readme") and plan.ctx.discover.has_listing and not runner.dry_run:
		readme = _listing_readme("write")
		if readme is not None:
			if readme[0] not in (CLEAN, DRIFT):
				print(readme[1], file=sys.stderr)
				return readme[0]
			touched.append("README.md")

	# Step 13 (the deletions are staged already, right after they were made). A lock or seed
	# an earlier, failed run wrote is staged too: its step does not run again to stage it.
	untracked = [] if runner.dry_run or only else engine.untracked_locks(root)
	_stage(root, [p for p in touched if (root / p).exists()] + untracked, runner)

	if runner.dry_run:
		return CLEAN
	# What sync could not fix: rules on app-owned keys, and (offline) the locks it did not run.
	after = _build(root, args)
	left = [i for i in after.problems if not (args.offline and i.command)]
	for i in left:
		print(f"frappe-nix sync: {i.path}: {i.problem}", file=sys.stderr)
	if args.offline:
		for i in after.problems:
			if i.command:
				print(f"frappe-nix sync: {i.path}: {i.problem} (offline: run `{i.command}`)", file=sys.stderr)
	return worst(*(i.code for i in left))


def write(root: Path, args: argparse.Namespace) -> int:
	runner = bootstrap.Runner(root, dry_run=args.dry_run, offline=args.offline)
	bootstrap.override_url()
	lock_changed = False
	if args.phase == "preflight":
		# frappe-init --app, before it writes its template files: what phase A would refuse
		# (an invalid table, a --standards or --frappe-version it contradicts, an unknown
		# profile, a missing release branch) is refused now, with nothing written.
		plan = _build(root, args, phases=("a",))
		code = _invalid(plan.items)
		if code:
			return code
		only = _only(args)
		if only is None or {"flake.nix", ".envrc"} & set(only):
			bootstrap.require_release_branch(runner, plan.ctx.frappe_nix.url)
		return CLEAN
	if args.phase in ("all", "a"):
		lock_changed = phase_a(root, args, runner)
	if args.phase == "a":
		return CLEAN
	return phase_b(root, args, runner, lock_changed)


def _run(args: argparse.Namespace) -> int:
	root = Path.cwd()
	args.profile_dir = _profile_dir(args)
	if args.check:
		return check_reported(root, args)
	repo.toplevel(root)
	if os.environ.get("FRAPPE_NIX_OFFLINE") == "1":
		args.offline = True
	try:
		return write(root, args)
	except SystemExit as e:
		if isinstance(e.code, int):
			return e.code
		return CLEAN if e.code is None else ENVIRONMENT


def run(args: argparse.Namespace) -> int:
	try:
		code = _run(args)
	except FrappeNixError as e:
		bootstrap.report_result(e.code)
		raise
	bootstrap.report_result(code)
	return code


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"sync",
		help="render, write or check the frappe-nix managed files of the app in the current directory",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	mode = p.add_mutually_exclusive_group(required=True)
	mode.add_argument("--write", action="store_true", help="write the managed files (frappe-init --sync)")
	mode.add_argument(
		"--check", action="store_true", help="report drift and write nothing (frappe-init --check)"
	)
	p.add_argument(
		"--standards",
		metavar="PROFILE",
		help="--write: opt in: create [tool.frappe-nix] with this profile (minimal, recommended,"
		" recommended@<minor>, github:<owner>/<repo>[/<ref>], ./<dir>)",
	)
	p.add_argument(
		"--profile-path",
		metavar="DIR",
		help="read the org profile from this directory instead of its locked input (profile authors only)",
	)
	p.add_argument(
		"--force",
		action="store_true",
		help="--write: replace an unmanaged flake.nix or .envrc on first opt-in (review the diff first)",
	)
	p.add_argument(
		"--dry-run", action="store_true", help="--write: print the diff and the commands, write nothing"
	)
	p.add_argument(
		"--skip-lock", action="store_true", help="--write: do not relock the bench (nix run .#relock)"
	)
	p.add_argument(
		"--offline",
		action="store_true",
		help="--write: run no nix, uv or yarn command (also FRAPPE_NIX_OFFLINE=1); the locks they make are reported",
	)
	p.add_argument("--only", action="append", metavar="PATH[,PATH…]", help="limit to these managed paths")
	p.add_argument("--init-listing", action="store_true", help="--write: also seed marketplace/listing.toml")
	p.add_argument(
		"--frappe-version", metavar="version-<N>", help="the Frappe major when [tool.frappe-nix] is created"
	)
	p.add_argument(
		"--site",
		metavar="NAME",
		help="the dev site when [tool.frappe-nix] is created and differs from <app-hyphen>.localhost",
	)
	p.add_argument(
		"--format", choices=("text", "json", "github"), default="text", help="--check: report format"
	)
	p.add_argument(
		"--expect-rev",
		default="",
		help="--check: the frappe-nix revision this frappe-nix-tools was installed from",
	)
	p.add_argument("--phase", choices=("all", "a", "b", "preflight"), default="all", help=argparse.SUPPRESS)
	p.set_defaults(func=run)
