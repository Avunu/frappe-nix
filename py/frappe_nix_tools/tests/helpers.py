"""Shared test fixtures: a small tree whose NAR hash Nix computed, and a captured CLI run."""

import contextlib
import io
import os
from pathlib import Path

from frappe_nix_tools import cli

# `nix hash path --type sha256 --sri` of the tree make_tree() builds.
TREE_NAR_HASH = "sha256-WnQ6pRq2vaNuq7WoQLwf1j4T6Ggr+DoCIXhN1ChYXmI="


def make_tree(root: Path) -> Path:
	(root / "sub").mkdir(parents=True)
	(root / "a").write_text("hello\n")
	(root / "sub" / "x").write_text("#!/bin/sh\n")
	(root / "sub" / "x").chmod(0o755)
	(root / "a").chmod(0o644)
	(root / "sub" / "link").symlink_to("../a")
	(root / "empty").write_text("")
	(root / "emptydir").mkdir()
	return root


def run_cli(*argv: str, cwd: Path | None = None) -> tuple[int, str, str]:
	"""Run ``frappe-nix <argv>`` in-process; returns (exit code, stdout, stderr)."""
	out, err = io.StringIO(), io.StringIO()
	old = Path.cwd()
	try:
		if cwd:
			os.chdir(cwd)
		with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
			try:
				code = cli.main(list(argv))
			except SystemExit as e:
				code = e.code if isinstance(e.code, int) else 1
	finally:
		os.chdir(old)
	return code, out.getvalue(), err.getvalue()
