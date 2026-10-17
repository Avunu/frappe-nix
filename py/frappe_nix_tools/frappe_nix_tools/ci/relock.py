"""relock-plan's input check, the relock patch, and the workflow-path refusal (spec §4.5, S29).

- ``moved_inputs``: a Dependabot ``nix`` PR that moves the ``frappe-nix`` or the
  ``standards-profile`` lock node is not relocked; it moves through ``frappe-nix repo
  rollout``, and the auto-merge job closes it.
- ``make_patch``: ``git add -A`` then ``git diff --cached --binary``. Sync stages its writes and
  new files (fresh ``nix/node-locks/**``) are untracked, so a plain ``git diff`` would miss
  both. A patch that names any path under ``.github/workflows/`` is refused: ``GITHUB_TOKEN``
  cannot push it, and no workflow ever pushes one (S29).
- ``workflow_paths``: the paths of a commit range or of the index under ``.github/workflows/``.
"""

import json
import re
from pathlib import Path

from frappe_nix_tools.common import repo
from frappe_nix_tools.common.report import ConfigError, EnvError

ROLLOUT_INPUTS = ("frappe-nix", "standards-profile")
# `.github/workflows/` at the repository root or, for an app in a subdirectory (frappe-nix's
# own fixture), at the app's root: either way a workflow file.
WORKFLOW = re.compile(r"(^|/)\.github/workflows/")


def _lock_doc(text: str, where: str) -> dict:
	try:
		doc = json.loads(text)
	except json.JSONDecodeError as e:
		raise ConfigError(f"{where}: not JSON: {e}") from e
	if not isinstance(doc, dict) or "nodes" not in doc:
		raise ConfigError(f"{where}: not a flake.lock")
	return doc


def input_node(doc: dict, name: str) -> object:
	"""The lock data of the root input ``name`` (its ``locked`` table), ``None`` when absent."""
	nodes = doc.get("nodes", {})
	root = nodes.get(doc.get("root", "root"), {})
	ref = root.get("inputs", {}).get(name)
	if ref is None:
		return None
	if isinstance(ref, list):
		return ["follows", *ref]
	return nodes.get(ref, {}).get("locked")


def moved_inputs(base_lock: str, head_lock: str) -> list[str]:
	"""The rollout inputs whose lock node differs between two ``flake.lock`` texts."""
	base = _lock_doc(base_lock, "flake.lock at the merge-base") if base_lock.strip() else {"nodes": {}}
	head = _lock_doc(head_lock, "flake.lock")
	return [n for n in ROLLOUT_INPUTS if input_node(base, n) != input_node(head, n)]


def merge_base(root: Path, base: str) -> str:
	try:
		return repo.git(root, "merge-base", base, "HEAD").strip()
	except EnvError:
		return base


def lock_at(root: Path, rev: str) -> str:
	"""``flake.lock`` (beside the app's root) at ``rev``; empty when it did not exist."""
	try:
		return repo.git(root, "show", f"{rev}:./flake.lock")
	except EnvError:
		return ""


def workflow_paths(paths: list[str]) -> list[str]:
	return [p for p in paths if WORKFLOW.search(p)]


def make_patch(root: Path, out: Path) -> tuple[bool, list[str]]:
	"""Stage everything and write the binary patch to ``out``.

	Returns ``(empty, refused)``: ``refused`` lists the workflow paths the patch names (and
	then nothing should be pushed)."""
	repo.git(root, "add", "-A")
	patch = repo.git(root, "diff", "--cached", "--binary")
	out.write_text(patch)
	names = repo.git(root, "diff", "--cached", "--name-only").split("\n")
	return not patch.strip(), workflow_paths([n for n in names if n])


def changed(root: Path, base: str | None, cached: bool) -> list[str]:
	"""Paths changed in ``base..HEAD``, or staged in the index with ``cached``."""
	if cached:
		out = repo.git(root, "diff", "--cached", "--name-only", *([base] if base else []))
	else:
		out = repo.git(root, "diff", "--name-only", f"{base}..HEAD")
	return [p for p in out.split("\n") if p]
