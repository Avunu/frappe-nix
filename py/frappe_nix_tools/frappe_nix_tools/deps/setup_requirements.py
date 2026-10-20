"""``bench setup requirements``, for a bench whose Python comes from uv.lock by
way of Nix and whose node_modules are installed by frappe-nix: not the
conventional bench's ``pip install -e`` and ``yarn install`` of every app, which
here would either fight the read-only environment or skip the checks that
matter. See lib/node-verify.py and lib/requirements-check.py. Was bash in
lib/scripts.nix (PLAN.md, Track D, D4); tests/setup-requirements.sh is its
acceptance suite.
"""

import os
import sys
from collections.abc import Sequence

from frappe_nix_tools.apps import proc

HELP = """\
Usage: bench setup requirements [--node | --python] [--check]

Verifies and repairs what the apps need installed, for a frappe-nix bench:

  node     the yarn cache and every app's node_modules (nested frontends
           included) for truncated native binaries, empty or partial packages
           and cache records that contradict their package; deletes what is
           damaged, then installs what is missing or out of date. Also lists
           lockfiles that differ from their commit.
  python   every requirement the apps declare is in uv.lock, and every app in
           sites/apps.txt imports. The environment is built by Nix from
           uv.lock, so this reports — `uv lock`, then re-enter the shell.

  --check  report only: change nothing, exit 1 if anything is wrong."""


class _Exit(Exception):
	def __init__(self, status: int) -> None:
		self.status = status


def parse(argv: Sequence[str]) -> tuple[bool, bool, bool]:
	"""(node, python, check), word by word as the bash read them: the first
	--help or unknown word decides, whatever follows it."""
	node = python = True
	check = False
	for arg in argv:
		if arg == "--node":
			python = False
		elif arg == "--python":
			node = False
		elif arg == "--check":
			check = True
		elif arg == "--dev":
			# Upstream's flag for the dev-only requirements; the workspace's are
			# already part of the environment.
			pass
		elif arg in ("-h", "--help"):
			print(HELP)
			raise _Exit(0)
		else:
			proc.warn(f"bench setup requirements: unknown option '{arg}' (see --help)")
			raise _Exit(2)
	return node, python, check


def _body(argv: Sequence[str]) -> int:
	try:
		do_node, do_python, check = parse(argv)
	except _Exit as e:
		return e.status
	os.chdir(proc.bench_root())
	from frappe_nix_tools.bench_config import load

	config = load()
	deps = config.deps
	rc = 0
	if deps.apps_with_node and do_node:
		proc.say("── node ──")
		verify = [
			deps.node_verify,
			"--full",
			"--lockfiles",
			*(["--check"] if check else []),
			".",
			*deps.apps_with_node,
		]
		if proc.attempt(verify) != 0:
			rc = 1
		if not check and proc.attempt([deps.node_modules, ".", *deps.apps_with_node]) != 0:
			rc = 1
		if rc == 0:
			proc.say(f"  ✓ node: {len(deps.apps_with_node)} app(s) verified")
	if not deps.app_mode and do_python:
		proc.say("── python ──")
		if proc.attempt([config.apps.python, deps.requirements_check, "."]) != 0:
			rc = 1
	return rc


def main(argv: Sequence[str] | None = None) -> int:
	return proc.main_guard(lambda: _body(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
	sys.exit(main())
