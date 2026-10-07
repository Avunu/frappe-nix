"""``ironclad sync --write|--check``: the managed files of a Frappe app (spec §2, §3).

``frappe-init --sync`` and ``frappe-init --check`` run this (``lib/sh/app-sync.sh``). It
works in the current directory, which must be an app: a git work tree whose
``pyproject.toml`` ``[project].name`` names a package holding ``hooks.py``.

``--write`` runs both phases (S32): phase A writes ``flake.nix`` and ``.envrc`` and locks the
flake; phase B renders every other managed file, deletes what is retired, and brings the
locks up (``tools/uv.lock``, ``yarn.lock``, node-lock seeds, relock, README blocks). It
stages what it wrote with ``git add`` and commits nothing.

``--check`` computes the same plan in memory and runs nothing (no nix, uv or yarn), so it
works in a CI job without Nix. Exit codes: 0 clean, 1 drift, 2 invalid configuration that
sync can't fix, 3 environment (not an app, unreadable lock, version skew).
"""

import argparse
import contextlib
import importlib.util
import io
import os
import shutil
import sys
from pathlib import Path

import ironclad
from ironclad.common import repo
from ironclad.common.report import CLEAN, DRIFT, ENVIRONMENT, INVALID, IroncladError, render, worst
from ironclad.scaffold import bootstrap, context, engine, tomlmerge
from ironclad.scaffold.engine import Item


def _only(args: argparse.Namespace) -> list[str] | None:
	paths = [p.strip() for chunk in (args.only or []) for p in chunk.split(",") if p.strip()]
	return paths or None


def _options(args: argparse.Namespace) -> dict:
	return {"init_listing": bool(getattr(args, "init_listing", False))}


def _passthrough(args: argparse.Namespace) -> list[str]:
	"""The flags a re-executed sync gets again (``frappe-init --sync`` or ``ironclad sync --write``)."""
	out = []
	if args.dry_run:
		out.append("--dry-run")
	if args.skip_lock:
		out.append("--skip-lock")
	if args.offline:
		out.append("--offline")
	if args.init_listing:
		out.append("--init-listing")
	if args.frappe_version:
		out += ["--frappe-version", args.frappe_version]
	if args.site:
		out += ["--site", args.site]
	for chunk in args.only or []:
		out += ["--only", chunk]
	return out


def _listing_readme(mode: str) -> tuple[int, str] | None:
	"""``ironclad listing readme --<mode>``, when N5's command is installed; else ``None``."""
	if importlib.util.find_spec("ironclad.commands.listing") is None:
		return None
	from ironclad import cli

	out = io.StringIO()
	with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
		code = cli.main(["listing", "readme", f"--{mode}"])
	return code, out.getvalue()


# --- --check -----------------------------------------------------------------------


def check(root: Path, args: argparse.Namespace) -> int:
	plan = engine.build(
		root, frappe_version=args.frappe_version, site=args.site, only=_only(args), options=_options(args)
	)
	items = list(plan.problems)
	if plan.created_config is not None:
		items.insert(
			0,
			Item(
				"pyproject.toml",
				"toml-merge",
				None,
				None,
				"[tool.ironclad] is missing: sync creates it",
				DRIFT,
			),
		)
	if not _only(args):
		items += engine.lock_problems(plan)
		if plan.ctx.discover.has_listing:
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
	sys.stdout.write(
		render(findings, args.format, code, {"rev": plan.ctx.frappe_nix.rev, "version": ironclad.__version__})
	)
	return code


# --- --write -----------------------------------------------------------------------


def _apply(root: Path, items: list[Item], runner: bootstrap.Runner) -> list[str]:
	"""Write or delete every item whose content changes; returns the paths touched."""
	touched = []
	for item in items:
		if item.code != DRIFT or item.command or item.wanted == item.current:
			continue
		target = root / item.path
		if runner.dry_run:
			print(item.finding().path + f" ({item.strategy}): {item.problem}")
			print(item.diff(), end="")
		elif item.wanted is None:
			target.unlink(missing_ok=True)
			print(f"ironclad sync: - {item.path} ({item.problem})", file=sys.stderr)
		else:
			target.parent.mkdir(parents=True, exist_ok=True)
			if target.is_symlink():
				target.unlink()  # write a regular file, never through a link
			target.write_text(item.wanted)
			print(f"ironclad sync: + {item.path}", file=sys.stderr)
		touched.append(item.path)
	return touched


def _stage(root: Path, paths: list[str], runner: bootstrap.Runner) -> None:
	if runner.dry_run or not paths:
		return
	repo.git(root, "add", "-A", "--", *sorted(set(paths)))


def _invalid(items: list[Item]) -> int:
	bad = [i for i in items if i.code >= INVALID]
	for i in bad:
		print(f"ironclad sync: {i.path}: {i.problem}", file=sys.stderr)
	return worst(*(i.code for i in bad))


def phase_a(root: Path, args: argparse.Namespace, runner: bootstrap.Runner) -> bool:
	"""Steps 1 to 4. Returns whether ``flake.lock`` changed.

	``--only`` limits it too: the flake is written, locked and handed over only when ``--only``
	is not given or names ``flake.nix`` or ``.envrc``. ``[tool.ironclad]`` is created either
	way, since every other file renders from it."""
	only = _only(args)
	flake_step = only is None or bool({"flake.nix", ".envrc"} & set(only))
	if flake_step:
		bootstrap.require_release_branch(runner, context.frappe_nix_major(ironclad.__version__))
	app = engine.load_app(root)
	_cfg, created = engine.config_for(app, args.frappe_version, args.site)
	if created is not None:
		text = (root / "pyproject.toml").read_text()
		new = tomlmerge.add_tool_ironclad(text, created)
		if runner.dry_run:
			print("pyproject.toml (toml-merge): [tool.ironclad] is created")
		else:
			(root / "pyproject.toml").write_text(new)
			print("ironclad sync: + pyproject.toml [tool.ironclad]", file=sys.stderr)
			_stage(root, ["pyproject.toml"], runner)
	plan = engine.build(
		root,
		phases=("a",),
		frappe_version=args.frappe_version,
		site=args.site,
		only=only,
		options=_options(args),
	)
	code = _invalid(plan.items)
	if code:
		raise SystemExit(code)
	touched = _apply(root, plan.items, runner)
	# A flake sees only tracked files: stage them before `nix flake lock` reads the flake.
	_stage(root, touched, runner)
	inputs = (
		engine.flake_input_specs((root / "flake.nix").read_text()) if (root / "flake.nix").is_file() else {}
	)
	changed = False
	if flake_step and not args.offline and not runner.dry_run:
		changed = bootstrap.phase_a_lock(runner, inputs, plan.ctx.frappe_nix.major)
		if (root / "flake.lock").is_file():
			_stage(root, ["flake.lock"], runner)
		bootstrap.maybe_reexec(runner, _passthrough(args), changed or bootstrap.inherited_lock_change())
	elif flake_step and runner.dry_run:
		print("would run: nix flake lock (when flake.lock is missing, stale or not on release-<N>)")
	return changed


def phase_b(root: Path, args: argparse.Namespace, runner: bootstrap.Runner, lock_changed: bool) -> int:
	"""Steps 5 to 13."""
	lock_changed = lock_changed or bootstrap.inherited_lock_change()
	bootstrap.ensure_tools(runner, _passthrough(args), lock_changed)
	only = _only(args)
	plan = engine.build(
		root,
		phases=("b",),
		frappe_version=args.frappe_version,
		site=args.site,
		only=only,
		options=_options(args),
	)
	code = _invalid(plan.items)
	if code:
		return code
	touched = _apply(root, plan.items, runner)
	# Staged now as well as at step 13, so a lock or formatter step that fails below leaves
	# what sync wrote staged rather than half-applied in the work tree.
	_stage(root, touched, runner)

	# Steps 8 and 9: the locks the plan cannot write itself.
	by_path = {i.path: i for i in plan.items}
	uv_item = by_path.get("tools/uv.lock")
	if uv_item and (uv_item.command or "tools/pyproject.toml" in touched):
		short = engine.floors.lock_shortfalls(root / "tools/uv.lock", plan.ctx.floors.get("uv", {}))
		argv = ["uv", "lock", "--project", "tools"]
		if (root / "tools/uv.lock").is_file():
			for pkg in short:
				argv += ["--upgrade-package", pkg]
		runner.run(argv)
		touched.append("tools/uv.lock")
	yarn_item = by_path.get("yarn.lock")
	if yarn_item and (yarn_item.command or "package.json" in touched):
		runner.run(["yarn", "install", "--non-interactive"])
		touched.append("yarn.lock")

	# Formatting (§3.2): whole files are already in oxfmt's form; this settles the merged ones.
	oxfmt = root / "node_modules" / ".bin" / "oxfmt"
	written = [p for p in touched if (root / p).is_file() and not p.endswith(".lock")]
	if written and oxfmt.exists():
		# The installed binary itself, not through yarn: --offline runs no yarn (and may have none).
		runner.run([str(oxfmt), "--no-error-on-unmatched-pattern", *written], network=False)
	init_py = f"{plan.app.name}/__init__.py"
	if init_py in touched and (root / "tools/uv.lock").is_file() and shutil.which("uv"):
		runner.run(["uv", "run", "--frozen", "--project", "tools", "ruff", "format", init_py])

	# Step 10: node-lock seeds for the apps the bench carries.
	if not only:
		seeded = bootstrap.seed_node_locks(
			root, plan.ctx.frappe.major, [s.name for s in plan.ctx.siblings], dry_run=runner.dry_run
		)
		for path in seeded:
			print(f"ironclad sync: + {path} (seed)", file=sys.stderr)
		touched += seeded

	# Step 11: the bench lock follows the flake inputs.
	if not only and not args.skip_lock and (lock_changed or not (root / "nix/uv.lock").is_file()):
		runner.run(["nix", "run", "--no-pure-eval", *bootstrap.nix_args(), ".#relock"])
		# Relock stages what it writes (nix/uv.lock, nix/node-locks/); never the rest of nix/.
		touched.append("nix/uv.lock")

	# Step 12: the README blocks.
	if not only and plan.ctx.discover.has_listing and not runner.dry_run:
		readme = _listing_readme("write")
		if readme is not None:
			if readme[0] not in (CLEAN, DRIFT):
				print(readme[1], file=sys.stderr)
				return readme[0]
			touched.append("README.md")

	# Step 13 (the deletions are staged already, right after they were made).
	_stage(root, [p for p in touched if (root / p).exists()], runner)

	if runner.dry_run:
		return CLEAN
	# What sync could not fix: rules on app-owned keys, and (offline) the locks it did not run.
	after = engine.build(
		root, frappe_version=args.frappe_version, site=args.site, only=only, options=_options(args)
	)
	left = [i for i in after.problems if not (args.offline and i.command)]
	for i in left:
		print(f"ironclad sync: {i.path}: {i.problem}", file=sys.stderr)
	if args.offline:
		for i in after.problems:
			if i.command:
				print(f"ironclad sync: {i.path}: {i.problem} (offline: run `{i.command}`)", file=sys.stderr)
	return worst(*(i.code for i in left))


def write(root: Path, args: argparse.Namespace) -> int:
	runner = bootstrap.Runner(root, dry_run=args.dry_run, offline=args.offline)
	bootstrap.override_url()
	lock_changed = False
	if args.phase == "preflight":
		# frappe-init --app, before it writes its template files.
		bootstrap.require_release_branch(runner, context.frappe_nix_major(ironclad.__version__))
		return CLEAN
	if args.phase in ("all", "a"):
		lock_changed = phase_a(root, args, runner)
	if args.phase == "a":
		return CLEAN
	return phase_b(root, args, runner, lock_changed)


def _run(args: argparse.Namespace) -> int:
	root = Path.cwd()
	repo.toplevel(root)
	if os.environ.get("IRONCLAD_OFFLINE") == "1":
		args.offline = True
	if args.check:
		return check(root, args)
	try:
		return write(root, args)
	except SystemExit as e:
		if isinstance(e.code, int):
			return e.code
		return CLEAN if e.code is None else ENVIRONMENT


def run(args: argparse.Namespace) -> int:
	try:
		code = _run(args)
	except IroncladError as e:
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
		"--dry-run", action="store_true", help="--write: print the diff and the commands, write nothing"
	)
	p.add_argument(
		"--skip-lock", action="store_true", help="--write: do not relock the bench (nix run .#relock)"
	)
	p.add_argument(
		"--offline",
		action="store_true",
		help="--write: run no nix, uv or yarn command (also IRONCLAD_OFFLINE=1); the locks they make are reported",
	)
	p.add_argument("--only", action="append", metavar="PATH[,PATH…]", help="limit to these managed paths")
	p.add_argument("--init-listing", action="store_true", help="--write: also seed marketplace/listing.toml")
	p.add_argument(
		"--frappe-version", metavar="version-<N>", help="the Frappe major when [tool.ironclad] is created"
	)
	p.add_argument(
		"--site",
		metavar="NAME",
		help="the dev site when [tool.ironclad] is created and differs from <app-hyphen>.localhost",
	)
	p.add_argument(
		"--format", choices=("text", "json", "github"), default="text", help="--check: report format"
	)
	p.add_argument(
		"--expect-rev", default="", help="--check: the frappe-nix revision this ironclad was installed from"
	)
	p.add_argument("--phase", choices=("all", "a", "b", "preflight"), default="all", help=argparse.SUPPRESS)
	p.set_defaults(func=run)
