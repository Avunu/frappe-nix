"""``frappe-init``'s entry point: the arguments, the mode, and dispatch.

Exit codes (docs/reference/scaffolder.md): 0 done or a finished --dry-run; 1 a
forced --migrate or --app on an empty or missing directory, or any error
without a more specific code; 2 --init into a directory that is not empty; 3
--sync or --check on a target that cannot be entered; 4 a flake.nix that is
not a frappe-nix wrapper; 5 the Frappe version (missing, unknown,
undetectable, unsupported) or no default site to be found; 6 the directory is
not the shape the mode needs, or the confirmation was declined; 7 a bench
skeleton with no app in it; 8 a bench inside another repository. --sync and
--check exit with frappe-nix sync's codes, and a usage error there is 2.
"""

import os
import sys

from frappe_nix_tools.frappe_init import ui
from frappe_nix_tools.frappe_init.args import USAGE, UsageError, parse_args
from frappe_nix_tools.frappe_init.config import ConfigError
from frappe_nix_tools.frappe_init.config import load as load_config
from frappe_nix_tools.frappe_init.detect import (
	dir_is_empty,
	has_foreign_flake,
	is_frappe_nix_bench,
	looks_like_bench,
	looks_like_empty_bench,
	looks_like_frappe_app,
)
from frappe_nix_tools.frappe_init.presets import PresetError
from frappe_nix_tools.frappe_init.presets import load as load_presets
from frappe_nix_tools.frappe_init.state import Run

#: Variables that would send every git command at the wrong repository: a hook,
#: or `git submodule foreach`, leaves them set.
GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


def decide_mode(mode: str, force: bool) -> str:
	"""The mode for the (non-empty) working directory: MODE when one was forced
	and the directory fits it (or FORCE), else what the directory looks like."""
	here = os.getcwd()
	if mode == "init":
		if not (dir_is_empty(".") or force):
			ui.die(
				f"'{here}' is not empty. Use --migrate to convert an existing bench, or --init --force to scaffold into it anyway.",
				2,
			)
		return "init"
	if mode == "migrate":
		if not (looks_like_bench() or force):
			ui.die(
				f"'{here}' does not look like a Frappe bench (no apps/ + sites/ with an app's hooks.py). Re-run with --migrate --force to try anyway.",
				6,
			)
		return "migrate"
	if mode == "app":
		if not (looks_like_frappe_app() or force):
			ui.die(
				f"'{here}' does not look like a Frappe app (no pyproject.toml whose [project].name names a package with hooks.py). Re-run with --app --force to try anyway.",
				6,
			)
		return "app"

	if is_frappe_nix_bench():
		ui.info("already a frappe-nix bench — reconciling (repair mode)")
		return "migrate"
	if has_foreign_flake() and looks_like_bench():
		ui.die(
			f"'{here}' is a bench, but its flake.nix is not a frappe-nix wrapper. Re-run with --migrate --force: the wrapper will be written to flake.nix.frappe-nix for you to merge.",
			4,
		)
	if looks_like_bench():
		return "migrate"
	if looks_like_frappe_app():
		# An app repository, not a bench: nothing to reconcile, so this writes a
		# flake that generates a bench around it.
		ui.info("this is a Frappe app, not a bench — setting up app mode")
		return "app"
	if looks_like_empty_bench():
		ui.die(
			f"'{here}' has apps/ and sites/ but no Frappe app (nothing matches apps/*/*/hooks.py). Nothing to migrate.",
			7,
		)
	ui.die(
		f"'{here}' is not empty and is not a Frappe bench. Use --init --force to scaffold into it, or --migrate --force to try anyway.",
		6,
	)


def _chdir(path: str, code: int) -> None:
	try:
		os.chdir(path)
	except OSError as e:
		print(f"frappe-init: cd: {path}: {e.strerror}", file=sys.stderr)
		ui.die(f"cannot enter {path}", code)


def dispatch(st: Run) -> None:
	from frappe_nix_tools.frappe_init.app_mode import cmd_app_init, cmd_app_sync
	from frappe_nix_tools.frappe_init.migrate import cmd_migrate
	from frappe_nix_tools.frappe_init.scaffold import cmd_init

	a = st.args

	# --sync / --check: the app in the current directory (or the target given),
	# handed to `frappe-nix sync`, which checks that it is one.
	if a.app_action:
		if a.target:
			_chdir(a.target, 3)
		cmd_app_sync(st)

	# No target: the current directory when it is a bench or an app, or when
	# --migrate or --app forces it (decide_mode then says what is wrong with
	# its shape); otherwise a new bench, which asks where.
	target = a.target
	if not target and (
		a.mode in ("migrate", "app")
		or looks_like_bench()
		or is_frappe_nix_bench()
		or looks_like_empty_bench()
		or looks_like_frappe_app()
	):
		target = "."

	if target and os.path.isdir(target) and not dir_is_empty(target):
		_chdir(target, 1)
		target = "."
		mode = decide_mode(a.mode, a.force)
	else:
		if a.mode == "migrate":
			ui.die(f"'{target or '.'}' is empty or does not exist — there is nothing to migrate.")
		if a.mode == "app":
			ui.die(
				f"'{target or '.'}' is empty or does not exist — app mode sets up an existing Frappe app's repository."
			)
		mode = "init"
	st.target = target

	# The app standards flags outside app mode: nothing would read them.
	if a.app_flag and mode != "app":
		print(USAGE, file=sys.stderr, end="")
		ui.die(
			f"unknown flag: {a.app_flag} (an app standards flag: app mode only, with --app, --sync or --check)"
		)

	if mode == "init":
		cmd_init(st)
	elif mode == "migrate":
		cmd_migrate(st)
	else:
		cmd_app_init(st)


def main(argv: list[str] | None = None) -> int:
	for var in GIT_ENV:
		os.environ.pop(var, None)
	try:
		try:
			args = parse_args(list(sys.argv[1:] if argv is None else argv))
		except UsageError as e:
			if e.show_usage:
				print(USAGE, file=sys.stderr, end="")
			raise ui.Die(str(e), e.code) from None
		if args.help:
			print(USAGE, end="")
			return 0
		try:
			config = load_config()
			presets = load_presets(config.presets)
		except (ConfigError, PresetError) as e:
			raise ui.Die(str(e)) from None
		# Read; the children frappe-init starts have configuration of their own.
		os.environ.pop("FRAPPE_NIX_CLI_CONFIG", None)
		st = Run(args=args, config=config, presets=presets, version=args.version, template=config.template)
		dispatch(st)
	except ui.Die as e:
		ui.error_line(str(e))
		return e.code
	except PresetError as e:
		ui.error_line(str(e))
		return 1
	except ui.Exit as e:
		return e.code
	except KeyboardInterrupt:
		return 130
	finally:
		sys.stdout.flush()
	return 0


def entry() -> None:
	sys.exit(main())
