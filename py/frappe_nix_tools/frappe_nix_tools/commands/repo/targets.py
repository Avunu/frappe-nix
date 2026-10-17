"""What the ``repo`` commands work on: the fleet list, clones and each target's configuration (spec §4.9, §5.6).

The fleet list is not part of frappe-nix (S40): ``--all`` reads it from ``--fleet <file>``,
validated against ``schema/fleet.schema.json``. Each target is resolved from a shallow
clone of its default branch (or its integration branch, when the default isn't it yet):
an opted-in repository by its own ``[tool.frappe-nix]``, one that hasn't opted in by the
profile ``--profile``, its fleet entry or the fleet file names, with an empty app layer.

``clone`` and ``fetch_profile`` are module-level functions, so tests replace them.
"""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from frappe_nix_tools.common import appgates, config, pyproject, schema
from frappe_nix_tools.common.report import ConfigError, EnvError

FLEET_SCHEMA = "fleet.schema.json"
_cleanup: list[Path] = []


@dataclass
class Target:
	"""One repository and what is known about it."""

	repo: str
	entry: dict = field(default_factory=dict)
	root: Path | None = None
	opted_in: bool = False
	app: appgates.AppConfig | None = None
	resolved: config.Resolved | None = None
	gates: list[str] = field(default_factory=list)
	profile_dir: Path | None = None

	@property
	def cfg(self) -> dict:
		return self.resolved.cfg if self.resolved else {}

	@property
	def modules(self) -> dict:
		return self.resolved.modules if self.resolved else {}

	@property
	def integration(self) -> str:
		return self.cfg.get("integration-branch", "")

	@property
	def release_branch(self) -> str:
		if self.modules.get("releases") and self.cfg["releases"]["branching"] == "develop+version":
			return f"version-{self.cfg['frappe-major']}"
		return ""


def load_fleet(path: str | None) -> dict:
	"""The fleet file, validated; ``None`` is exit 2 (``--all`` needs ``--fleet``)."""
	if not path:
		raise ConfigError(
			"--all needs --fleet <file>: the fleet list lives in an org profile repository (S40)"
		)
	try:
		doc = json.loads(Path(path).read_text())
	except FileNotFoundError as e:
		raise ConfigError(f"--fleet {path}: no such file") from e
	except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
		raise ConfigError(f"--fleet {path}: {e}") from e
	errors = schema.Validator(FLEET_SCHEMA, f"--fleet {path}").errors(doc)
	if errors:
		raise ConfigError("; ".join(errors))
	return doc


def entries(repos: list[str], all_: bool, fleet_path: str | None) -> tuple[list[tuple[str, dict]], dict]:
	"""``[(repo, fleet entry)]`` and the fleet document (``{}`` without one)."""
	fleet = load_fleet(fleet_path) if all_ or fleet_path else {}
	by_repo = {a["repo"].lower(): a for a in fleet.get("apps", [])}
	if all_:
		if repos:
			raise ConfigError("give repositories or --all, not both")
		return [(a["repo"], a) for a in fleet.get("apps", [])], fleet
	if not repos:
		raise ConfigError("give one or more <owner/repo>, or --all --fleet <file>")
	for r in repos:
		if r.count("/") != 1 or not all(r.split("/")):
			raise ConfigError(f"{r!r} is not <owner/repo>")
	return [(r, by_repo.get(r.lower(), {})) for r in repos], fleet


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
	try:
		return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
	except FileNotFoundError as e:
		raise EnvError("git is not on PATH") from e


def clone(repo: str, branch: str | None = None, *, depth: int | None = 1) -> Path | None:
	"""A clone of ``repo`` (its default branch, or ``branch``) in a temporary directory, or
	``None`` when that branch doesn't exist. Authenticates through ``gh``'s credential helper."""
	dest = Path(tempfile.mkdtemp(prefix="frappe-nix-repo-"))
	_cleanup.append(dest)
	args = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", "clone", "-q"]
	if depth:
		args += ["--depth", str(depth)]
	if branch:
		args += ["--branch", branch]
	proc = _git(*args, f"https://github.com/{repo}.git", str(dest / "repo"))
	if proc.returncode != 0:
		if branch and "not found in upstream" in proc.stderr:
			return None
		raise EnvError(f"git clone {repo}{f' ({branch})' if branch else ''}: {proc.stderr.strip()}")
	return dest / "repo"


def clone_branch(root: Path) -> str:
	"""The branch a clone has checked out."""
	return _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()


def fetch_profile(ref: str) -> Path:
	"""An org profile's tree for a ``github:<owner>/<repo>[/<ref>]`` (or ``gitlab:``,
	``git+https:``) reference, cloned at that ref; a built-in name needs no tree."""
	if ref.startswith("github:"):
		parts = ref.removeprefix("github:").split("/")
		url, branch = f"https://github.com/{parts[0]}/{parts[1]}.git", "/".join(parts[2:]) or None
	elif ref.startswith("gitlab:"):
		parts = ref.removeprefix("gitlab:").split("/")
		url, branch = f"https://gitlab.com/{parts[0]}/{parts[1]}.git", "/".join(parts[2:]) or None
	elif ref.startswith("git+https://"):
		base, _, query = ref.removeprefix("git+").partition("?")
		url = base
		branch = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("ref")
	else:
		raise ConfigError(f"profile {ref!r} is not a github:, gitlab: or git+https:// reference")
	dest = Path(tempfile.mkdtemp(prefix="frappe-nix-profile-"))
	_cleanup.append(dest)
	args = ["clone", "-q", "--depth", "1", *(["--branch", branch] if branch else []), url, str(dest / "p")]
	proc = _git(*args)
	if proc.returncode != 0:
		raise EnvError(f"fetching profile {ref}: {proc.stderr.strip()}")
	return dest / "p"


def cleanup() -> None:
	while _cleanup:
		shutil.rmtree(_cleanup.pop(), ignore_errors=True)


def _opted_in(root: Path) -> bool:
	path = root / "pyproject.toml"
	if not path.is_file():
		return False
	try:
		doc = pyproject.parse(pyproject.read(path), path)
	except ConfigError:
		return False
	return pyproject.tool_frappe_nix(doc) is not None


def resolve_profile(repo: str, ref: str, integration: str | None) -> tuple[config.Resolved, Path | None]:
	"""A not-yet-opted-in repository's configuration: ``ref`` with an empty app layer."""
	table: dict[str, Any] = {"schema": 1, "profile": ref, "frappe-major": 16, "repo": repo}
	if integration:
		table["integration-branch"] = integration
	doc = {"tool": {"frappe-nix": table}}
	if config.is_builtin(ref):
		return config.resolve_doc(doc, on_github=True), None
	directory = fetch_profile(ref)
	return config.resolve_doc(doc, profile_dir=directory, on_github=True), directory


def load(repo: str, entry: dict, fleet: dict, profile: str | None = None) -> Target:
	"""Clone ``repo`` and resolve it (§5.6)."""
	t = Target(repo, entry)
	root = clone(repo)
	if root is None:
		raise EnvError(f"{repo}: could not clone the default branch")
	if _opted_in(root):
		app = appgates.load(root)
		if app.integration and root is not None:
			head = clone_branch(root)
			if head and head != app.integration:
				other = clone(repo, app.integration)
				if other is not None and _opted_in(other):
					root, app = other, appgates.load(other)
		t.root, t.opted_in, t.app, t.resolved, t.gates = root, True, app, app.resolved, app.gates
		source = app.resolved.profile.get("source", "builtin")
		if source != "builtin":
			try:
				t.profile_dir, _ = config.org_profile_dir(source, root)
			except (ConfigError, EnvError):
				t.profile_dir = None
		return t
	t.root = root
	ref = profile or entry.get("profile") or fleet.get("profile")
	if ref:
		t.resolved, t.profile_dir = resolve_profile(repo, ref, entry.get("integration_branch"))
	return t
