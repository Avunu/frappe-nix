"""Scripts that run under a bench's interpreter, not frappe-nix-tools' (spec §5.1.1, §5.1.2).

Each module here is a standalone script: it imports only the standard library and
frappe, so ``<bench>/env/bin/python <this dir>/testmap_probe.py`` works without frappe-nix-tools
or its dependencies on the bench's path. ``frappe-nix testmap`` and ``frappe-nix
composition`` find them through :func:`script` and run them that way.
"""

from pathlib import Path


def script(name: str) -> Path:
	"""The path of the bench-side script ``<name>.py`` in this package."""
	return Path(__file__).resolve().parent / f"{name}.py"
