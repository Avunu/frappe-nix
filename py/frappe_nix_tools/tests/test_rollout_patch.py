"""``frappe-nix ci verify-rollout-patch``: what rollout.yml's publish job may push (spec §5.8).

Each case plans a rollout the way the plan job does (``flake.lock`` and ``nix/uv.lock`` moved
to the tag, ``sync --write``, everything staged, ``git diff --cached --binary``), optionally
forges the result, and checks the decision verify reaches on the base checkout."""

import base64
import copy
import json
import os
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import frappe_nix_tools
from frappe_nix_tools.ci import rolloutpatch
from frappe_nix_tools.common import appgates, github
from frappe_nix_tools.scaffold import manifest
from helpers import run_cli
from repo_helpers import FakeGitHub
from scaffold_helpers import REV, AppCase, flake_lock, git

R = "example/demo_app"
TAG = f"v{frappe_nix_tools.__version__}"
TS = "c" * 40
OTHER = "e" * 40
CI_CALLERS = (".github/workflows/release.yml", ".github/workflows/deps.yml")
NIXPKGS = {
	"locked": {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "rev": "9" * 40, "narHash": "sha256-n"},
	"original": {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "ref": "nixos-unstable"},
}
FN_LOCK: dict = {
	"nodes": {"nixpkgs": NIXPKGS, "root": {"inputs": {"nixpkgs": "nixpkgs"}}},
	"root": "root",
	"version": 7,
}


def app_lock(rev: str, profile_rev: str = "5" * 40) -> str:
	"""The app's flake.lock: frappe-nix at ``rev`` with its own nixpkgs, and an org profile node."""
	lock = flake_lock(["frappe"])
	lock["nodes"]["frappe-nix"]["locked"]["rev"] = rev
	lock["nodes"]["frappe-nix"]["inputs"] = {"nixpkgs": "nixpkgs_2"}
	lock["nodes"]["nixpkgs_2"] = copy.deepcopy(NIXPKGS)
	lock["nodes"]["standards-profile"] = {
		"flake": False,
		"locked": {
			"type": "github",
			"owner": "Avunu",
			"repo": "frappe-standards-profile",
			"rev": profile_rev,
			"narHash": "sha256-p",
		},
		"original": {"type": "github", "owner": "Avunu", "repo": "frappe-standards-profile"},
	}
	lock["nodes"]["root"]["inputs"]["standards-profile"] = "standards-profile"
	return json.dumps(lock, indent=2) + "\n"


def uv_lock(runtime: str, orjson: str = "3.10.0", requests: str = "2.32.0", extra: str = "") -> str:
	return f"""version = 1
revision = 3
requires-python = ">=3.14"

[[package]]
name = "frappe-runtime"
version = "0.1.0"
source = {{ git = "https://github.com/Avunu/frappe-nix?subdirectory=runtime#{runtime}" }}
dependencies = [{{ name = "orjson" }}]

[[package]]
name = "orjson"
version = "{orjson}"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://files.pythonhosted.org/packages/orjson-{orjson}.tar.gz", hash = "sha256:00" }}

[[package]]
name = "requests"
version = "{requests}"
source = {{ registry = "https://pypi.org/simple" }}
{extra}"""


class VerifyCase(AppCase):
	def setUp(self) -> None:
		super().setUp()
		ships = mock.patch.object(manifest, "ships", lambda path: path in CI_CALLERS)
		ships.start()
		self.addCleanup(ships.stop)
		self.gh = FakeGitHub()
		previous = github.use(self.gh)
		self.addCleanup(github.use, previous)
		content = base64.b64encode(json.dumps(FN_LOCK).encode()).decode()
		self.gh.routes[("GET", f"repos/Avunu/frappe-nix/contents/flake.lock?ref={TS}")] = {"content": content}
		self.synced()
		self.write("flake.lock", app_lock(REV))
		self.write("nix/uv.lock", uv_lock(REV))
		self.commit()
		code, out = self.check()
		self.assertEqual(code, 0, out)
		self.base = git(self.root, "rev-parse", "HEAD").strip()
		self._out = tempfile.TemporaryDirectory()
		self.addCleanup(self._out.cleanup)
		self.out = Path(self._out.name)

	def plan(
		self,
		forge: Callable[[], None] | None = None,
		*,
		rev: str = TS,
		uv: str | None = None,
	) -> Path:
		"""The plan job's patch: the locks moved to ``rev``, sync, then ``forge``; the checkout
		is put back at the base afterwards."""
		self.write("flake.lock", app_lock(rev))
		self.write("nix/uv.lock", uv if uv is not None else uv_lock(rev))
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		if forge:
			forge()
		git(self.root, "add", "-A")
		patch = subprocess.run(
			["git", "-C", str(self.root), "diff", "--cached", "--binary", "--full-index", self.base],
			capture_output=True,
			check=True,
		).stdout
		git(self.root, "reset", "-q", "--hard", self.base)
		git(self.root, "clean", "-fdq")
		path = self.out / "patch"
		path.write_bytes(patch)
		return path

	def verify(self, patch: Path, *, base: str | None = None, tag_sha: str = TS) -> dict:
		out = self.out / "decision.json"
		code, stdout, err = run_cli(
			"ci",
			"verify-rollout-patch",
			"--repo",
			R,
			"--base-sha",
			base or self.base,
			"--tag",
			TAG,
			"--tag-sha",
			tag_sha,
			"--patch",
			str(patch),
			"--worktree",
			str(self.root),
			"--out",
			str(out),
		)
		self.assertEqual(code, 0, stdout + err)
		doc = json.loads(out.read_text())
		# Verify leaves the checkout as it found it: at the base, with no worktree of its own.
		self.assertEqual(git(self.root, "rev-parse", "HEAD").strip(), base or self.base)
		self.assertEqual(git(self.root, "status", "--porcelain"), "")
		self.assertEqual(len(git(self.root, "worktree", "list").splitlines()), 1)
		return doc

	def assertDecision(self, doc: dict, decision: str, reason: str = "") -> None:
		self.assertEqual(doc["decision"], decision, doc["reasons"])
		if reason:
			self.assertTrue(any(reason in r for r in doc["reasons"]), doc["reasons"])


class TestPublish(VerifyCase):
	def test_the_release_render_publishes(self):
		patch = self.plan()
		self.assertIn(b".github/workflows/release.yml", patch.read_bytes())
		doc = self.verify(patch)
		self.assertDecision(doc, "publish")
		self.assertEqual(doc["reasons"], [])
		self.assertEqual(doc["branch"], "standards/frappe-nix")
		self.assertEqual(doc["title"], f"chore(deps): frappe-nix {TAG}")
		self.assertIn(TS[:12], doc["body"])
		self.assertEqual(doc["merge_method"], "squash")
		self.assertFalse(doc["automerge"], "the branch rules require no gate here")
		self.assertEqual(doc["legacy_prs"], [])
		import hashlib

		self.assertEqual(doc["patch_sha256"], hashlib.sha256(patch.read_bytes()).hexdigest())
		# tree_id is what the publish job re-derives: the patch applied to the base in an index.
		env = {**os.environ, "GIT_INDEX_FILE": str(self.out / "index")}
		subprocess.run(["git", "-C", str(self.root), "read-tree", self.base], env=env, check=True)
		subprocess.run(["git", "-C", str(self.root), "apply", "--cached", str(patch)], env=env, check=True)
		tree = subprocess.run(
			["git", "-C", str(self.root), "write-tree"], env=env, check=True, capture_output=True, text=True
		).stdout.strip()
		self.assertEqual(doc["tree_id"], tree)

	def test_automerge_when_the_gates_protect_the_branch(self):
		app = appgates.load(self.root)
		self.gh.routes[("GET", f"repos/{R}/rules/branches/{app.integration}")] = [
			{
				"type": "required_status_checks",
				"parameters": {"required_status_checks": [{"context": f"ci / {g}"} for g in app.gates]},
			}
		]
		self.gh.routes[("GET", f"repos/{R}/pulls?state=open&per_page=100")] = [
			{"number": 7, "head": {"ref": "standards/frappe-nix-v2.5.0", "repo": {"full_name": R}}},
			{"number": 8, "head": {"ref": "standards/frappe-nix-v2.5.0", "repo": {"full_name": "fork/x"}}},
			{"number": 9, "head": {"ref": "dependabot/nix/x", "repo": {"full_name": R}}},
		]
		doc = self.verify(self.plan())
		self.assertDecision(doc, "publish")
		self.assertTrue(doc["automerge"])
		self.assertEqual(doc["legacy_prs"], [7])
		self.assertEqual(self.gh.writes(), [], "verify makes no write call")

	def test_a_move_inside_the_runtime_closure_is_explained(self):
		doc = self.verify(self.plan(uv=uv_lock(TS, orjson="3.11.0")))
		self.assertDecision(doc, "publish")

	def test_an_empty_patch_is_noop(self):
		path = self.out / "empty.patch"
		path.write_bytes(b"")
		self.assertDecision(self.verify(path), "noop")

	def test_rollout_contract(self):
		code, out, _ = run_cli("ci", "rollout-contract")
		self.assertEqual((code, out.strip()), (0, "1"))


class TestNeedsReview(VerifyCase):
	def test_a_new_package_needs_review(self):
		extra = '\n[[package]]\nname = "leftpad"\nversion = "1.0.0"\nsource = { registry = "https://pypi.org/simple" }\n'
		doc = self.verify(self.plan(uv=uv_lock(TS, extra=extra)))
		self.assertDecision(doc, "needs-review", "nix/uv.lock adds leftpad")
		self.assertFalse(doc["automerge"])
		self.assertIn("leftpad", doc["body"])

	def test_a_move_outside_the_runtime_closure_needs_review(self):
		doc = self.verify(self.plan(uv=uv_lock(TS, requests="2.33.0")))
		self.assertDecision(doc, "needs-review", "moves requests")

	def test_the_same_version_from_another_file_needs_review(self):
		"""orjson 3.10.0 is still orjson 3.10.0 by name, but its sdist is another project's."""
		uv = uv_lock(TS).replace(
			'packages/orjson-3.10.0.tar.gz", hash = "sha256:00"',
			'packages/evil-3.10.0.tar.gz", hash = "sha256:ff"',
		)
		doc = self.verify(self.plan(uv=uv))
		self.assertDecision(doc, "needs-review", "orjson 3.10.0 has another source or other files")
		self.assertFalse(doc["automerge"])

	def test_a_node_lock_stamp_that_is_not_the_base_or_the_seed(self):
		def forge():
			self.write("nix/node-locks/frappe/ui/source.json", json.dumps({"forced": True}) + "\n")

		self.assertDecision(self.verify(self.plan(forge)), "needs-review", "source.json is neither")


class TestReject(VerifyCase):
	def test_a_workflow_edit_the_render_does_not_make(self):
		def forge():
			path = ".github/workflows/release.yml"
			self.write(path, self.read(path) + "# and a step of my own\n")

		self.assertDecision(self.verify(self.plan(forge)), "reject", ".github/workflows/release.yml")

	def test_a_new_workflow(self):
		self.assertDecision(
			self.verify(self.plan(lambda: self.write(".github/workflows/x.yml", "on: push\n"))),
			"reject",
			".github/workflows/x.yml: changed, but",
		)

	def test_a_pyproject_edit(self):
		def forge():
			self.write(
				"pyproject.toml",
				self.read("pyproject.toml").replace("dependencies = []", 'dependencies = ["evil"]'),
			)

		self.assertDecision(self.verify(self.plan(forge)), "reject", "pyproject.toml")

	def test_a_deleted_app_file(self):
		self.assertDecision(
			self.verify(self.plan(lambda: (self.root / "demo_app/modules.txt").unlink())),
			"reject",
			"demo_app/modules.txt",
		)

	def test_a_symlink(self):
		self.assertDecision(
			self.verify(self.plan(lambda: (self.root / "evil").symlink_to("/etc/passwd"))),
			"reject",
			"mode 120000",
		)

	def test_a_mode_change(self):
		self.assertDecision(
			self.verify(self.plan(lambda: (self.root / "demo_app/hooks.py").chmod(0o755))),
			"reject",
			"mode",
		)

	def test_a_foreign_uv_source(self):
		extra = '\n[[package]]\nname = "evil"\nversion = "1.0.0"\nsource = { registry = "https://evil.example/simple" }\n'
		self.assertDecision(self.verify(self.plan(uv=uv_lock(TS, extra=extra))), "reject", "evil.example")

	def test_a_foreign_git_source(self):
		extra = (
			'\n[[package]]\nname = "evil"\nversion = "1.0.0"\nsource = { git = "https://github.com/evil/evil#'
			+ "1" * 40
			+ '" }\n'
		)
		self.assertDecision(self.verify(self.plan(uv=uv_lock(TS, extra=extra))), "reject", "github.com/evil")

	def test_a_foreign_yarn_url(self):
		def forge():
			self.write(
				"yarn.lock",
				self.read("yarn.lock")
				+ '\n"x@^1":\n  version "1.0.0"\n  resolved "https://evil.example/x-1.0.0.tgz"\n',
			)

		self.assertDecision(self.verify(self.plan(forge)), "reject", "evil.example")

	def test_a_standards_profile_node_change(self):
		def forge():
			self.write("flake.lock", app_lock(TS, profile_rev="6" * 40))

		self.assertDecision(self.verify(self.plan(forge)), "reject", "standards-profile")

	def test_a_frappe_nix_input_not_at_the_tag(self):
		def forge():
			lock = json.loads(app_lock(TS))
			lock["nodes"]["nixpkgs_2"]["locked"]["rev"] = "8" * 40
			self.write("flake.lock", json.dumps(lock))

		self.assertDecision(self.verify(self.plan(forge)), "reject", "frappe-nix/nixpkgs")

	def test_a_local_region_edit(self):
		def forge():
			marker = "# frappe-nix:local-begin inputs\n"
			text = self.read("flake.nix")
			self.assertIn(marker, text)
			self.write("flake.nix", text.replace(marker, marker + '    evil.url = "github:evil/evil";\n'))

		self.assertDecision(self.verify(self.plan(forge)), "reject", "flake.nix")

	def test_the_wrong_tag_sha(self):
		self.gh.routes[("GET", f"repos/Avunu/frappe-nix/contents/flake.lock?ref={OTHER}")] = self.gh.routes[
			("GET", f"repos/Avunu/frappe-nix/contents/flake.lock?ref={TS}")
		]
		doc = self.verify(self.plan(rev=OTHER))
		self.assertDecision(doc, "reject", f"not github:Avunu/frappe-nix at {TS}")
		self.assertTrue(any("frappe-runtime" in r for r in doc["reasons"]), doc["reasons"])

	def test_a_base_mismatch(self):
		patch = self.plan()
		old = self.base
		self.write("README-extra.md", "moved on\n")
		self.commit()
		self.base = git(self.root, "rev-parse", "HEAD").strip()
		out = self.out / "decision.json"
		code, _, err = run_cli(
			"ci", "verify-rollout-patch", "--repo", R, "--base-sha", old, "--tag", TAG, "--tag-sha", TS,
			"--patch", str(patch), "--worktree", str(self.root), "--out", str(out),
		)  # fmt: skip
		self.assertEqual(code, 0, err)
		doc = json.loads(out.read_text())
		self.assertDecision(doc, "reject", "not the base")
		self.assertEqual(doc["legacy_prs"], [])
		self.assertFalse(doc["automerge"])

	def test_a_patch_that_does_not_apply(self):
		path = self.out / "bad.patch"
		path.write_bytes(b"diff --git a/nope b/nope\n--- a/nope\n+++ b/nope\n@@ -1 +1 @@\n-a\n+b\n")
		self.assertDecision(self.verify(path), "reject", "does not apply")

	def test_a_rename_header(self):
		path = self.out / "rename.patch"
		path.write_bytes(b"diff --git a/x b/y\nsimilarity index 100%\nrename from x\nrename to y\n")
		self.assertDecision(self.verify(path), "reject", "renames")

	def test_tools_from_another_release(self):
		with mock.patch.object(frappe_nix_tools, "__version__", "0.0.1"):
			self.assertDecision(self.verify(self.plan()), "reject", "not " + TAG)

	def test_bad_arguments_are_exit_2(self):
		code, _, _ = run_cli(
			"ci", "verify-rollout-patch", "--repo", R, "--base-sha", "main", "--tag", TAG, "--tag-sha", TS,
			"--patch", "p", "--worktree", ".", "--out", "o",
		)  # fmt: skip
		self.assertEqual(code, 2)


class TestLockChecks(unittest.TestCase):
	"""The lock rules on their own."""

	def test_flake_lock_root_inputs_must_match_the_base(self):
		lock = json.loads(app_lock(TS))
		lock["nodes"]["root"]["inputs"]["evil"] = "evil"
		lock["nodes"]["evil"] = {"locked": {"type": "github", "owner": "e", "repo": "e", "rev": "1" * 40}}
		problems = rolloutpatch.flake_lock_problems(app_lock(REV), json.dumps(lock), TS, FN_LOCK)
		self.assertTrue(any("root inputs" in p for p in problems), problems)

	def test_flake_lock_renumbered_nodes_are_the_same(self):
		lock = json.loads(app_lock(TS))
		lock["nodes"]["frappe_2"] = lock["nodes"].pop("frappe")
		lock["nodes"]["root"]["inputs"]["frappe"] = "frappe_2"
		self.assertEqual(rolloutpatch.flake_lock_problems(app_lock(REV), json.dumps(lock), TS, FN_LOCK), [])

	def test_flake_lock_unreachable_node(self):
		lock = json.loads(app_lock(TS))
		lock["nodes"]["stray"] = {"locked": {"type": "path", "path": "/"}}
		problems = rolloutpatch.flake_lock_problems(app_lock(REV), json.dumps(lock), TS, FN_LOCK)
		self.assertTrue(any("stray" in p for p in problems), problems)

	def test_a_follows_the_base_had_is_kept(self):
		base = json.loads(app_lock(REV))
		base["nodes"]["frappe-nix"]["inputs"]["nixpkgs"] = ["nixpkgs"]
		new = json.loads(app_lock(TS))
		new["nodes"]["frappe-nix"]["inputs"]["nixpkgs"] = ["nixpkgs"]
		for lock in (base, new):
			del lock["nodes"]["nixpkgs_2"]
			lock["nodes"]["root"]["inputs"]["nixpkgs"] = "nixpkgs"
			lock["nodes"]["nixpkgs"] = copy.deepcopy(NIXPKGS)
		self.assertEqual(rolloutpatch.flake_lock_problems(json.dumps(base), json.dumps(new), TS, FN_LOCK), [])
		new["nodes"]["frappe-nix"]["inputs"]["nixpkgs"] = ["frappe"]
		problems = rolloutpatch.flake_lock_problems(json.dumps(base), json.dumps(new), TS, FN_LOCK)
		self.assertTrue(any("follows" in p for p in problems), problems)

	def test_a_lock_of_shared_nodes_is_bounded(self):
		"""Forty nodes that each name the next twice are a tree of 2**40 paths: refused, quickly."""

		def diamond() -> dict:
			nodes = {
				f"n{i}": {"inputs": {"a": f"n{i + 1}", "b": f"n{i + 1}"}, "locked": {}} for i in range(40)
			}
			nodes["n40"] = {"locked": {}}
			return nodes

		new = json.loads(app_lock(TS))
		new["nodes"].update(diamond())
		new["nodes"]["frappe-nix"]["inputs"]["nixpkgs"] = "n0"
		fn = copy.deepcopy(FN_LOCK)
		fn["nodes"].update(diamond())
		fn["nodes"]["root"]["inputs"]["nixpkgs"] = "n0"
		del new["nodes"]["nixpkgs_2"]
		problems = rolloutpatch.flake_lock_problems(app_lock(REV), json.dumps(new), TS, fn)
		self.assertTrue(any("too large" in p for p in problems), problems)

	def test_paths(self):
		for path in ("/etc/passwd", "../x", "a/../b", "a/.git/config", ".GIT/hooks/x", "a\nb"):
			self.assertIsNotNone(rolloutpatch.path_problem(path), path)
		self.assertIsNone(rolloutpatch.path_problem(".github/workflows/ci.yml"))

	def uv_reviews(
		self, path: str, base: str, new: str, explained: frozenset[str] = frozenset()
	) -> list[str]:
		trusted = rolloutpatch.trusted_locks({path: base.encode()})
		rejects, reviews = rolloutpatch.uv_problems(path, base, new, TS, trusted, explained)
		self.assertEqual(rejects, [])
		return reviews

	def test_uv_entries_are_tied_to_their_files(self):
		wheel = (
			'\nwheels = [{{ url = "https://files.pythonhosted.org/packages/ab/{f}", hash = "sha256:{h}" }}]\n'
		)
		base = uv_lock(REV, extra=wheel.format(f="requests-2.32.0-py3-none-any.whl", h="11"))
		self.assertEqual(
			self.uv_reviews(
				"nix/uv.lock",
				base,
				uv_lock(TS, extra=wheel.format(f="requests-2.32.0-py3-none-any.whl", h="11")),
			),
			[],
		)
		# The same name and version, the same file name, another hash.
		swapped = uv_lock(TS, extra=wheel.format(f="requests-2.32.0-py3-none-any.whl", h="22"))
		self.assertTrue(
			any("requests 2.32.0 has another" in r for r in self.uv_reviews("nix/uv.lock", base, swapped))
		)
		# Another project's wheel under requests' name and version.
		swapped = uv_lock(TS, extra=wheel.format(f="evil-2.32.0-py3-none-any.whl", h="11"))
		self.assertTrue(
			any("requests 2.32.0 has another" in r for r in self.uv_reviews("nix/uv.lock", base, swapped))
		)
		# A new version (inside the runtime closure) must download that package's own files.
		moved = uv_lock(TS, orjson="3.11.0").replace(
			"packages/orjson-3.11.0.tar.gz", "packages/evil-3.11.0.tar.gz"
		)
		self.assertTrue(
			any("not a file of that package" in r for r in self.uv_reviews("nix/uv.lock", base, moved))
		)

	def test_pypi_file_names(self):
		ok = (
			(
				"PyYAML",
				"6.0.2",
				"https://files.pythonhosted.org/packages/a/b/pyyaml-6.0.2-cp314-cp314-manylinux_2_17_x86_64.whl",
			),
			(
				"zope.interface",
				"7.0",
				"https://files.pythonhosted.org/packages/a/b/zope_interface-7.0.tar.gz",
			),
			(
				"python-dateutil",
				"2.9.0.post0",
				"https://files.pythonhosted.org/packages/a/b/python-dateutil-2.9.0.post0.tar.gz",
			),
		)
		for name, version, url in ok:
			self.assertIsNone(rolloutpatch.pypi_file_problem(name, version, url), url)
		self.assertIsNotNone(
			rolloutpatch.pypi_file_problem(
				"requests", "2.32.0", "https://files.pythonhosted.org/x/requests-2.31.0.tar.gz"
			)
		)
		self.assertIsNotNone(
			rolloutpatch.pypi_file_problem(
				"requests", "2.32.0", "https://files.pythonhosted.org/x/evil-2.32.0-py3-none-any.whl"
			)
		)

	def test_tools_lock_moves_only_what_a_floor_explains(self):
		base = uv_lock(REV)
		new = uv_lock(REV, requests="2.33.0")
		self.assertTrue(any("moves requests" in r for r in self.uv_reviews("tools/uv.lock", base, new)))
		self.assertEqual(self.uv_reviews("tools/uv.lock", base, new, frozenset({"requests"})), [])

	def test_yarn_entries_are_tied_to_their_tarballs(self):
		base = (
			'# yarn lockfile v1\n\nlodash@^4.17.21:\n  version "4.17.21"\n'
			'  resolved "https://registry.yarnpkg.com/lodash/-/lodash-4.17.21.tgz#abc"\n  integrity sha512-aa\n\n'
			'"string-width-cjs@npm:string-width@^4.2.0":\n  version "4.2.3"\n'
			'  resolved "https://registry.yarnpkg.com/string-width/-/string-width-4.2.3.tgz#def"\n'
			"  integrity sha512-bb\n"
		)
		trusted = rolloutpatch.trusted_locks({"yarn.lock": base.encode()})

		def reviews(new: str) -> list[str]:
			rejects, out = rolloutpatch.yarn_problems("yarn.lock", base, new, trusted, False)
			self.assertEqual(rejects, [])
			return out

		self.assertEqual(reviews(base), [])
		# An existing key that now resolves another package on the registry.
		evil = base.replace("lodash/-/lodash-4.17.21.tgz#abc", "evil/-/evil-1.0.0.tgz#abc")
		found = reviews(evil)
		self.assertTrue(
			any("changes the version, URL or integrity of lodash@^4.17.21" in r for r in found), found
		)
		self.assertTrue(any("not lodash 4.17.21's tarball" in r for r in found), found)
		# Only the integrity changed.
		self.assertTrue(reviews(base.replace("sha512-aa", "sha512-zz")))
		# A new range of a known package, resolved to its own tarball, is explained.
		more = base + (
			'\nlodash@^4.17.0:\n  version "4.17.21"\n'
			'  resolved "https://registry.yarnpkg.com/lodash/-/lodash-4.17.21.tgz#abc"\n  integrity sha512-aa\n'
		)
		self.assertEqual(reviews(more), [])
		scoped = {"version": "7.24.0", "resolved": "https://registry.npmjs.org/@babel/core/-/core-7.24.0.tgz"}
		self.assertIsNone(rolloutpatch.npm_url_problem("@babel/core@^7.0.0", scoped))

	def test_yarn_names(self):
		names, urls = rolloutpatch.yarn_entries(
			'# yarn lockfile v1\n\n"@scope/a@^1", "@scope/a@^1.1":\n  version "1.1.0"\n'
			'  resolved "https://registry.yarnpkg.com/@scope/a/-/a-1.1.0.tgz#abc"\n\nb@~2:\n  version "2.0.0"\n'
		)
		self.assertEqual(names, {"@scope/a", "b"})
		self.assertEqual(urls, {"https://registry.yarnpkg.com/@scope/a/-/a-1.1.0.tgz#abc"})


if __name__ == "__main__":
	unittest.main()
