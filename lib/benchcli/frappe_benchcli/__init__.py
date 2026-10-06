"""Hand ``bench update`` and ``bench build`` to frappe-nix's own, whichever ``bench`` runs.

Loaded at interpreter start from ``zzz-frappe-benchcli.pth`` inside the
development virtualenv, like ``frappe_nodebuild``.

In the dev shell ``bench`` is the umbrella wrapper (``lib/scripts.nix``), first
on PATH, and it sends ``update`` to ``bench-update`` and ``build`` to
``bench-build``. Put the virtualenv's ``bin/`` ahead of it — ``source
env/bin/activate`` does, and so can an editor that activates ``./env`` in the
terminals it opens (VS Code's Python extension can, for a directory with a
``pyvenv.cfg``) — and the stock commands run instead:

``update`` dies in ``is_version_upgrade()``: ``git show upstream/<branch>:<app>/
__init__.py`` needs a remote called ``upstream`` in every app, and a bench's apps
are submodules whose remote is ``origin``. Given one it would go on to write
``maintenance_mode`` into the reconciled ``common_site_config.json``, back up the
sites, install into a read-only environment and ``git pull --rebase`` (or
hard-reset) every app.

``build`` skips ``bench-build``'s node_modules refresh, so an install that
predates an app's package.json surfaces as a missing-package error from a vite
config several apps deep.

This wraps ``bench.cli.cli``, which the console script calls, so the command is
handed over before bench logs it or looks at the working directory. Only
``bench update …`` and ``bench build …`` are handed over, as the wrapper does
(``$1`` is the command; ``bench --verbose build`` is stock in both), and with the
arguments the wrapper would have passed. ``ROUTES`` mirrors the wrapper's
``case`` arms in ``lib/scripts.nix``; ``tests/test_benchcli.py`` checks the two
agree.

``_FRAPPE_BENCH_RAW`` is the switch the specialised scripts export so their own
nested ``bench …`` calls reach the real bench; set, nothing is handed over, which
is also how to run a stock command once. A script that is not on PATH — this
virtualenv used outside the dev shell — is refused with the reason, since running
the stock command there is the failure this exists to prevent.

Development only, by construction (``lib/python.nix``): a deployed host has no
``bench-update`` and its units call ``bench`` for workers and the scheduler.
"""

import functools
import os
import shutil
import sys

__all__ = ["RAW", "ROUTES", "install", "route"]

#: Exported by every frappe-nix script that calls the real bench. See
#: ``bench.exec`` in lib/scripts.nix.
RAW = "_FRAPPE_BENCH_RAW"

#: `bench <key> …` -> the script the umbrella wrapper runs instead. Keep in step
#: with the wrapper's `case`; the test compares them.
ROUTES = {"update": "bench-update", "build": "bench-build"}

_INSTALLED = False

#: The wrappers installed over bench.cli.cli, so patching twice wraps once.
_WRAPPERS = []


def route(argv):
	"""Replace this process with the script ``argv`` names, if it names one.

	Returns when nothing is to be handed over. Otherwise it does not return: it
	execs the script, or exits 2 when the script is not there.
	"""
	if os.environ.get(RAW):
		return
	script = ROUTES.get(argv[1]) if len(argv) > 1 else None
	if script is None:
		return

	command = f"bench {argv[1]}"
	target = shutil.which(script)
	if target is None:
		sys.stderr.write(
			f"frappe_benchcli: `{command}` is {script} in a frappe-nix bench, and "
			f"{script} is not on PATH. Enter the dev shell (direnv allow, or nix "
			"develop) and run it again.\n"
		)
		raise SystemExit(2)

	sys.stderr.write(
		f"frappe_benchcli: `{command}` is {script} here. This shell reached the "
		"virtualenv's bench ahead of the devenv's, which activating ./env does. "
		f"Running {script}.\n"
	)
	sys.stderr.flush()
	os.execv(target, [script, *argv[2:]])


def _patch_cli(module):
	original = getattr(module, "cli", None)
	if not callable(original):
		raise ImportError(
			"frappe_benchcli: bench.cli.cli is gone, so `bench update` and `bench "
			f"build` cannot be handed to frappe-nix's. Set {RAW}=1 to run the stock "
			"command, and update lib/benchcli for this frappe-bench."
		)
	if any(original is wrapper for wrapper in _WRAPPERS):
		return

	@functools.wraps(original)
	def cli(*args, **kwargs):
		route(sys.argv)
		return original(*args, **kwargs)

	_WRAPPERS.append(cli)
	module.cli = cli


def install():
	"""Hook bench.cli once it is imported. Idempotent."""
	global _INSTALLED
	if _INSTALLED:
		return
	_INSTALLED = True

	from ._hook import on_import

	on_import("bench.cli", _patch_cli)
