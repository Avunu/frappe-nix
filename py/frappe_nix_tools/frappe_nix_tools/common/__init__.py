"""Helpers every command shares. See the module docstrings for each."""

from importlib.resources import files
from pathlib import Path, PurePosixPath

from frappe_nix_tools.common.report import ConfigError


def data_path(rel: str) -> Path:
	"""The absolute path of ``frappe_nix_tools/data/<rel>``, a file or a directory.

	The data ships inside the package (spec S10), so the path is the same shape for the
	Nix build and for a ``uv tool install`` from the git subdirectory. A relative path
	that leaves the data directory, or names nothing, is a ``ConfigError``.
	"""
	pure = PurePosixPath(rel)
	if not rel or pure.is_absolute() or ".." in pure.parts:
		raise ConfigError(f"data path must be relative to frappe_nix_tools/data and stay inside it: {rel!r}")
	resource = files("frappe_nix_tools").joinpath("data", *pure.parts)
	if not isinstance(resource, Path):
		# A zipped install: there is no file to hand to another program.
		raise ConfigError(f"frappe_nix_tools is not installed as files on disk, so {rel!r} has no path")
	if not resource.exists():
		raise ConfigError(f"no such data file: frappe_nix_tools/data/{rel}")
	return resource.resolve()
