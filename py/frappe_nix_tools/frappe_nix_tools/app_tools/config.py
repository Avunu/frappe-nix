"""What lib/standards/tools/frappe-demo.nix and frappe-shots.nix bake into their
tools: store paths, as JSON (frappe_nix_tools.cli_config). frappe-test needs
none; what it runs is on its PATH.
"""

import os
from dataclasses import dataclass

from frappe_nix_tools.cli_config import ENV, ConfigError, load_dataclass, read_json

__all__ = ["ENV", "ConfigError", "DemoConfig", "ShotsConfig", "load_demo", "load_shots"]


@dataclass(frozen=True)
class DemoConfig:
	# lib/demo/frappe_demo.py: the demo script, run with the bench's interpreter.
	demo_py: str


@dataclass(frozen=True)
class ShotsConfig:
	# lib/shots, with its node_modules: runner.ts, clock.ts and the rest.
	shots_dir: str
	# libfaketime for frappe-demo's demo script, as `<dir>/$LIB/libfaketime.so.1`
	# with a literal $LIB, which the dynamic loader expands.
	faketime_lib: str


def _hint(tool: str) -> str:
	return f"run {tool} from an app's dev shell, not as python -m"


def load_demo(path: str | os.PathLike[str] | None = None) -> DemoConfig:
	return load_dataclass(DemoConfig, read_json(path, hint=_hint("frappe-demo")), "config")


def load_shots(path: str | os.PathLike[str] | None = None) -> ShotsConfig:
	return load_dataclass(ShotsConfig, read_json(path, hint=_hint("frappe-shots")), "config")
