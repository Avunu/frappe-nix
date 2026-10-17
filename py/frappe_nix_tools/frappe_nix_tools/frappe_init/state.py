"""One run's state: the arguments, what was resolved from them, and what the
steps found and did. The bash kept this in globals; here it is one object the
steps are handed.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from frappe_nix_tools.frappe_init import ui
from frappe_nix_tools.frappe_init.args import InitArgs
from frappe_nix_tools.frappe_init.config import InitConfig
from frappe_nix_tools.frappe_init.presets import Preset


@dataclass
class App:
	"""An app under apps/, as classified for migration (detect.classify_app)."""

	name: str
	# submodule | vendor | skip
	disp: str = ""
	url: str = ""
	branch: str = ""
	sha: str = ""
	remote: str = ""
	flags: list[str] = field(default_factory=list)

	def has(self, flag: str) -> bool:
		return flag in self.flags

	@property
	def flag_text(self) -> str:
		return ",".join(self.flags)


@dataclass
class Run:
	args: InitArgs
	config: InitConfig
	presets: dict[str, Preset]
	# Resolved from the arguments, the directory and the prompts.
	version: str = ""
	name: str = ""
	site: str = ""
	target: str = ""
	# The preset `version` names, once resolved.
	preset: Preset | None = None
	# App mode: the Frappe app this repository is.
	app_name: str = ""
	# [project].name for the workspace root: usually `name` (apps.resolve_project_name).
	project_name: str = ""
	# The template being rendered (templates/bench, or templates/app in app mode).
	template: str = ""
	staging: Path | None = None
	# apps/, in install order.
	apps: dict[str, App] = field(default_factory=dict)
	# The bench's own .git, when the bench root is its own repository.
	bench_git_dir: str = ""
	vendored: list[str] = field(default_factory=list)
	members: list[str] = field(default_factory=list)
	warnings: list[str] = field(default_factory=list)

	def warn(self, text: str) -> None:
		self.warnings.append(text)
		ui.warn_line(text)

	@property
	def p(self) -> Preset:
		if self.preset is None:
			raise RuntimeError("internal: no preset resolved")  # a bug here, not in the input
		return self.preset

	def resolve_preset(self) -> None:
		self.preset = self.presets[self.version]

	@property
	def preset_keys(self) -> str:
		"""The presets, as the messages list them."""
		return " ".join(self.presets)

	@property
	def versions_line(self) -> str:
		"""frappe version (python X / node Y), as the plans and reports print it."""
		return f"{self.version} (python {self.p.pyver} / node {self.p.node_version})"


def normalize_name(name: str) -> str:
	"""A bench name: a Nix string, a port seed and a container image prefix."""
	n = re.sub(r"[^a-z0-9-]", "-", name.lower())
	n = re.sub(r"-+", "-", n).strip("-")
	return n or "frappe-bench"


def normalize_dist(name: str) -> str:
	"""PEP 503 normalisation, so print_designer and print-designer compare equal."""
	return re.sub(r"[-_.]+", "-", name.lower())


def resolve_app_url(spec: str) -> str:
	"""A URL as-is; owner/repo on GitHub; a bare name under github.com/frappe."""
	if "://" in spec or spec.startswith("git@"):
		return spec
	if "/" in spec:
		return f"https://github.com/{spec}.git"
	return f"https://github.com/frappe/{spec}.git"


def url_basename(url: str) -> str:
	"""`basename <url> .git`: the repository's name."""
	base = url.rstrip("/").rsplit("/", 1)[-1]
	if base.endswith(".git") and base != ".git":
		base = base[: -len(".git")]
	return base
