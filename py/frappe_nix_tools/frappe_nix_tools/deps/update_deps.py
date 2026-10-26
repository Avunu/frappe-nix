"""``update-deps``: re-lock and reinstall every dependency of the bench — the
relock (``uv lock``, the managed build-dependency block, node-locks/), then
``uv sync`` and each node app's ``yarn install``. Was bash in lib/scripts.nix;
tests/update-deps.sh is its acceptance suite.

In a bench the apps are other people's repositories, and a yarn.lock rewritten
here is a modified file their next ``git checkout`` refuses over (``bench
update`` then dies on the app). ``--pure-lockfile`` still installs from
package.json — a lock that drifted from it is no obstacle, unlike under
``--frozen-lockfile`` — but never writes the lock. In app mode apps/<app> is
the developer's own repository, the lock is theirs to commit, and Python is
``nix run .#relock``'s: the workspace root it would lock lives in the store.
"""

import os
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from frappe_nix_tools.apps import proc
from frappe_nix_tools.apps.proc import say, warn

if TYPE_CHECKING:
	from frappe_nix_tools.bench_config import CliConfig

HELP = """\
Usage: update-deps

Re-locks and reinstalls the bench's dependencies: uv lock (and the managed
build-dependency block), uv sync, yarn install in every app with a
package.json, then node-locks/ for the apps without a yarn.lock of their own.
In app mode: yarn install only; the locks are `nix run .#relock`'s."""


def update_deps(config: "CliConfig") -> int:
	from frappe_nix_tools.deps.update import UV_LOCK, build_deps_sync, node_locks_soft

	deps = config.deps
	failed: list[str] = []
	if deps.app_mode:
		say(
			"Python dependencies are resolved from the flake's pins, not from",
			"this bench — the workspace root it would lock lives in the Nix store.",
			"Use:  nix run .#relock",
		)
	else:
		say("Updating Python dependencies...")
		status = proc.attempt(UV_LOCK)
		if status != 0:
			return status
		build_deps_sync(config)
		if proc.attempt(["uv", "sync"]) != 0:
			failed.append("uv sync")
	say("")
	say("Updating Node dependencies...")
	for app in deps.apps_with_node:
		say(f"  yarn install: {app}")
		flags = [] if deps.app_mode else ["--pure-lockfile"]
		if proc.attempt(["yarn", "install", *flags], cwd=f"apps/{app}") != 0:
			failed.append(f"yarn install in apps/{app}")
	say("")
	if deps.app_mode:
		say("Done! Commit any changed yarn.lock, then: nix run .#relock")
	else:
		say("Refreshing node-locks/ for the apps without a yarn.lock of their own…")
		node_locks_soft(config)
		say(
			"Done! Commit uv.lock and node-locks/.",
			"The apps' own yarn.lock files were not rewritten; to update one on purpose,",
			"run `yarn install` in that app yourself. (An app's postinstall can still",
			"rewrite a nested frontend's lock; `bench-update --pull` discards such a change",
			"when the pull would overwrite it.)",
		)
	if failed:
		warn(f"update-deps: failed: {'; '.join(failed)} — see above")
		return 1
	return 0


def _body(argv: Sequence[str]) -> int:
	for arg in argv:
		if arg in ("-h", "--help"):
			say(HELP)
			return 0
		warn(f"update-deps: unexpected argument '{arg}' (see --help)")
		return 2
	from frappe_nix_tools.bench_config import load

	config = load()
	os.chdir(proc.bench_root())
	return update_deps(config)


def main(argv: Sequence[str] | None = None) -> int:
	return proc.main_guard(lambda: _body(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
	sys.exit(main())
