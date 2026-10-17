"""A fake GitHub (``common.github.use``) and throwaway app clones for the ``repo`` command tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from frappe_nix_tools.commands.repo import targets
from frappe_nix_tools.common import github
from scaffold_helpers import HOOKS, INIT, PYPROJECT, git


class FakeGitHub(github.Transport):
	"""Answers from ``routes`` (``(method, endpoint)`` → a value, an int status to fail with, or
	a function of the request body returning either);
	an unknown GET is a 404, an unknown write succeeds with ``{}``. ``calls`` records every call."""

	def __init__(self, routes: dict | None = None, cli_results: dict | None = None):
		self.routes = dict(routes or {})
		self.cli_results = dict(cli_results or {})
		self.calls: list[tuple[str, str, Any]] = []
		self.cli_calls: list[tuple[str, ...]] = []

	def request(self, method, endpoint, body=None, paginate=False):
		self.calls.append((method, endpoint, body))
		key = (method, endpoint)
		if key in self.routes:
			value = self.routes[key]
			if callable(value):
				value = value(body)
			if isinstance(value, int):
				raise github.GitHubError(f"gh api {method} {endpoint}: HTTP {value}", value)
			return 200, value
		if method == "GET":
			raise github.GitHubError(f"gh api GET {endpoint}: Not Found (HTTP 404)", 404)
		return 201, {}

	def cli(self, *args):
		self.cli_calls.append(args)
		return self.cli_results.get(args[:2], github.Result(0, ""))

	def writes(self) -> list[tuple[str, str, Any]]:
		return [c for c in self.calls if c[0] != "GET"]


def write_app(root: Path, extra: str = "", *, opted_in: bool = True, branch: str = "develop") -> Path:
	root.mkdir(parents=True, exist_ok=True)
	git(root, "init", "-q", "-b", branch)
	text = PYPROJECT.replace("{profile}", "recommended")
	if opted_in:
		text = (
			text.replace("[tool.frappe-nix]\n", '[tool.frappe-nix]\nrepo = "example/demo_app"\n', 1) + extra
		)
	else:
		text = text[: text.index("[tool.frappe-nix]")]
	(root / "pyproject.toml").write_text(text)
	(root / "demo_app").mkdir(exist_ok=True)
	(root / "demo_app/__init__.py").write_text(INIT)
	(root / "demo_app/hooks.py").write_text(HOOKS.format(required=json.dumps([])))
	(root / "demo_app/modules.txt").write_text("Demo App\n")
	git(root, "add", "-A")
	git(root, "commit", "-q", "-m", "app")
	return root


REPO_POLICY_ON = "\n[tool.frappe-nix.repo-policy]\nenable = true\n"
ONLY_LINT_TEST = "\n[tool.frappe-nix.commits]\nenable = false\n\n[tool.frappe-nix.releases]\nenable = false\n"


class RepoCase(unittest.TestCase):
	"""``self.gh`` is the fake GitHub; ``self.clones`` maps ``(repo, branch)`` to a directory
	``targets.clone`` returns (``branch`` None: the default branch)."""

	def _clone(self, repo, branch=None, depth=1):
		return self.clones.get((repo, branch))

	def setUp(self) -> None:
		self._tmp = tempfile.TemporaryDirectory()
		self.tmp = Path(self._tmp.name)
		self.addCleanup(self._tmp.cleanup)
		env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "FRAPPE_NIX_OFFLINE": "1"})
		env.start()
		self.addCleanup(env.stop)
		self.gh = FakeGitHub()
		previous = github.use(self.gh)
		self.addCleanup(github.use, previous)
		self.clones: dict[tuple[str, str | None], Path] = {}
		patch = mock.patch.object(targets, "clone", side_effect=self._clone)
		patch.start()
		self.addCleanup(patch.stop)
