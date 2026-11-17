"""``frappe-nix repo rollout``: arguments, the dry-run plan, ``--emit-patch`` and the run by hand (spec §5.8)."""

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.commands.repo import rollout, targets
from frappe_nix_tools.common import github
from helpers import run_cli
from repo_helpers import RepoCase, write_app
from scaffold_helpers import git

R = "example/demo_app"
OLD = "a" * 40
TAG_SHA = "c" * 40
NEWER = "d" * 40
TAG = "v1.2.0"
IDENTITY = {
	"GIT_AUTHOR_NAME": "Rollout Person",
	"GIT_AUTHOR_EMAIL": "person@example.com",
	"GIT_COMMITTER_NAME": "Rollout Person",
	"GIT_COMMITTER_EMAIL": "person@example.com",
}


def lock(rev: str) -> str:
	return json.dumps(
		{
			"nodes": {
				"frappe-nix": {
					"locked": {"type": "github", "owner": "Avunu", "repo": "frappe-nix", "rev": rev},
					"original": {
						"type": "github",
						"owner": "Avunu",
						"repo": "frappe-nix",
						"ref": "release-2",
					},
				},
				"root": {"inputs": {"frappe-nix": "frappe-nix"}},
			},
			"root": "root",
			"version": 7,
		},
		indent=2,
	)


class TestRepoRollout(RepoCase):
	def setUp(self) -> None:
		super().setUp()
		self.clones[(R, None)] = write_app(self.tmp / "app")
		for name in ("clone_branch",):
			p = mock.patch.object(targets, name, return_value="develop")
			p.start()
			self.addCleanup(p.stop)
		p = mock.patch.object(rollout, "sh", side_effect=AssertionError("dry-run ran a command"))
		p.start()
		self.addCleanup(p.stop)

	def test_needs_a_target_release(self):
		code, _, err = run_cli("repo", "rollout", "--repo", R)
		self.assertEqual(code, 2)
		self.assertIn("--to", err)

	def test_to_must_be_a_release_tag(self):
		code, _, _ = run_cli("repo", "rollout", "--repo", R, "--to", "main")
		self.assertEqual(code, 2)

	def test_all_needs_fleet(self):
		code, _, _ = run_cli("repo", "rollout", "--all", "--to", "v1.2.0")
		self.assertEqual(code, 2)

	def test_dry_run_prints_the_plan(self):
		code, out, err = run_cli("repo", "rollout", "--repo", R, "--to", "v1.2.0", "--dry-run")
		self.assertEqual(code, 0, err)
		self.assertIn("nix flake update frappe-nix", out)
		self.assertIn("nix run --no-pure-eval .#frappe-init -- --sync", out)
		self.assertIn("chore(deps): frappe-nix v1.2.0", out)
		self.assertIn("on standards/frappe-nix\n", out)
		self.assertIn("--force-with-lease", out)
		self.assertIn("close the open standards/frappe-nix-v* pull requests", out)
		self.assertIn("auto-merge (squash) when develop requires every gate", out)
		self.assertEqual(self.gh.calls, [])
		code, out, _ = run_cli(
			"repo", "rollout", "--repo", R, "--to", "v1.2.0", "--dry-run", "--no-auto-merge"
		)
		self.assertNotIn("auto-merge", out)

	def test_profile_to_skips_a_builtin_profile(self):
		code, out, err = run_cli("repo", "rollout", "--repo", R, "--profile-to", "latest", "--dry-run")
		self.assertEqual(code, 0, err)
		self.assertIn("skipped", out)

	def test_emit_patch_arguments(self):
		base = ["repo", "rollout", "--repo", R, "--to", TAG]
		for extra in (
			["--emit-patch", str(self.tmp / "o")],  # no --base
			["--base", OLD],  # no --emit-patch
			["--emit-patch", str(self.tmp / "o"), "--base", "main"],  # not a SHA
			["--emit-patch", str(self.tmp / "o"), "--base", OLD, "--dry-run"],
			["--emit-patch", str(self.tmp / "o"), "--base", OLD, "--profile-to", "latest"],
			["--emit-patch", str(self.tmp / "o"), "--base", OLD, "--repo", "example/other"],
		):
			code, _, err = run_cli(*base, *extra)
			self.assertEqual(code, 2, (extra, err))


class TestScrubbedEnv(unittest.TestCase):
	def test_untrusted_commands_get_no_token_or_key(self):
		env = {
			"GH_TOKEN": "t1",
			"GITHUB_TOKEN": "t2",
			"FRAPPE_NIX_CACHE_TOKEN": "t3",
			"ACTIONS_ID_TOKEN_REQUEST_TOKEN": "t4",
			"AVUNU_CI_PRIVATE_KEY": "k1",
			"some_api_key": "k2",
			"SSH_AUTH_SOCK": "/tmp/agent",
			"KEEP_ME": "yes",
		}
		with mock.patch.dict(os.environ, env):
			out = rollout.sh(["env", "-0"], Path.cwd(), scrub=True)
			seen = dict(line.split("=", 1) for line in out.split("\0") if "=" in line)
			for name in env:
				if name != "KEEP_ME":
					self.assertNotIn(name, seen)
			self.assertEqual(seen["KEEP_ME"], "yes")
			gh_config = Path(seen["GH_CONFIG_DIR"])
			self.assertTrue(gh_config.is_dir())
			self.assertEqual(list(gh_config.iterdir()), [])
			# nix (the repository's own code) is scrubbed by default; git and gh are not.
			with mock.patch.object(rollout.subprocess, "run") as run:
				run.return_value = subprocess.CompletedProcess([], 0, "", "")
				rollout.sh(["nix", "flake", "update", "frappe-nix"], Path.cwd())
				self.assertNotIn("GH_TOKEN", run.call_args.kwargs["env"])
				rollout.sh(["git", "status"], Path.cwd())
				self.assertIsNone(run.call_args.kwargs["env"])


class TestYarnIgnoreScripts(unittest.TestCase):
	def test_the_plan_job_runs_no_lifecycle_script(self):
		from frappe_nix_tools.commands import sync

		with mock.patch.dict(os.environ, {"FRAPPE_NIX_YARN_IGNORE_SCRIPTS": "1"}):
			self.assertEqual(
				sync.yarn_install(), ["yarn", "install", "--non-interactive", "--ignore-scripts"]
			)
		with mock.patch.dict(os.environ, {"FRAPPE_NIX_YARN_IGNORE_SCRIPTS": ""}):
			self.assertEqual(sync.yarn_install(), ["yarn", "install", "--non-interactive"])


class RolloutRepoCase(RepoCase):
	"""An app on ``develop`` in a local bare ``origin``, a clone of it that ``targets.clone``
	returns, and ``nix`` faked: the flake update moves frappe-nix to ``self.locks_to``, relock
	writes ``nix/uv.lock``, sync deletes ``old.txt`` and writes ``new.txt``."""

	def setUp(self) -> None:
		super().setUp()
		env = mock.patch.dict(os.environ, IDENTITY)
		env.start()
		self.addCleanup(env.stop)
		src = write_app(self.tmp / "src")
		(src / "flake.lock").write_text(lock(OLD))
		(src / "old.txt").write_text("retired\n")
		git(src, "add", "-A")
		git(src, "commit", "-q", "-m", "base")
		self.base = git(src, "rev-parse", "HEAD").strip()
		self.origin = self.tmp / "origin.git"
		subprocess.run(["git", "clone", "-q", "--bare", str(src), str(self.origin)], check=True)
		self.clone = self.tmp / "clone"
		subprocess.run(["git", "clone", "-q", str(self.origin), str(self.clone)], check=True)
		self.clones[(R, None)] = self.clone
		self.locks_to = TAG_SHA
		self.nix_envs: list[dict] = []
		real = rollout.sh

		def fake_sh(cmd, cwd, *, scrub=None):
			if cmd[0] != "nix":
				return real(cmd, cwd, scrub=scrub)
			self.nix_envs.append({"scrub": scrub})
			if cmd[1:3] == ["flake", "update"]:
				(cwd / "flake.lock").write_text(lock(self.locks_to))
			elif cmd[-1] == ".#relock":
				(cwd / "nix").mkdir(exist_ok=True)
				(cwd / "nix/uv.lock").write_text("version = 1\n")
			else:
				(cwd / "old.txt").unlink()
				(cwd / "new.txt").write_text("rendered\n")
			return ""

		p = mock.patch.object(rollout, "sh", side_effect=fake_sh)
		p.start()
		self.addCleanup(p.stop)
		self.gh.routes[("GET", f"repos/Avunu/frappe-nix/commits/{TAG}")] = {"sha": TAG_SHA}


class TestEmitPatch(RolloutRepoCase):
	def emit(self, base: str | None = None) -> tuple[int, str, Path]:
		out = self.tmp / "out"
		code, stdout, err = run_cli(
			"repo", "rollout", "--repo", R, "--to", TAG, "--base", base or self.base, "--emit-patch", str(out)
		)
		return code, stdout + err, out

	def test_writes_the_patch_and_meta_and_pushes_nothing(self):
		code, text, out = self.emit()
		self.assertEqual(code, 0, text)
		meta = json.loads((out / "meta.json").read_text())
		self.assertEqual(
			{k: meta[k] for k in ("repo", "integration", "base_sha", "tag", "tag_sha", "state")},
			{
				"repo": R,
				"integration": "develop",
				"base_sha": self.base,
				"tag": TAG,
				"tag_sha": TAG_SHA,
				"state": "patch",
			},
		)
		self.assertTrue(meta["tools_version"])
		patch = (out / "patch").read_bytes()
		self.assertIn(b"new file mode 100644", patch)
		self.assertIn(b"deleted file mode 100644", patch)
		self.assertTrue(all(e == {"scrub": True} for e in self.nix_envs), self.nix_envs)
		# Nothing was pushed, and nothing was written on GitHub.
		self.assertEqual(git(self.origin, "branch", "--list").split(), ["*", "develop"])
		self.assertEqual(self.gh.writes(), [])
		self.assertEqual(self.gh.cli_calls, [])
		# The patch applies to the base and gives the planned tree: created and deleted files.
		check = self.tmp / "check"
		subprocess.run(["git", "clone", "-q", str(self.origin), str(check)], check=True)
		git(check, "checkout", "-q", "--detach", self.base)
		git(check, "apply", "--index", str(out / "patch"))
		self.assertFalse((check / "old.txt").exists())
		self.assertEqual((check / "new.txt").read_text(), "rendered\n")
		self.assertEqual((check / "nix/uv.lock").read_text(), "version = 1\n")
		self.assertIn(TAG_SHA, (check / "flake.lock").read_text())

	def test_superseded_when_the_release_branch_moved_past_the_tag(self):
		self.locks_to = NEWER
		self.gh.routes[("GET", f"repos/Avunu/frappe-nix/compare/{TAG_SHA}...{NEWER}")] = {"status": "ahead"}
		code, text, out = self.emit()
		self.assertEqual(code, 0, text)
		self.assertEqual(json.loads((out / "meta.json").read_text())["state"], "superseded")
		self.assertFalse((out / "patch").exists())

	def test_behind_the_tag_fails(self):
		self.locks_to = NEWER
		self.gh.routes[("GET", f"repos/Avunu/frappe-nix/compare/{TAG_SHA}...{NEWER}")] = {"status": "behind"}
		code, text, _ = self.emit()
		self.assertEqual(code, 1)
		self.assertIn("has not reached", text)

	def test_base_must_be_on_the_integration_branch(self):
		other = self.tmp / "other"
		other.mkdir()
		git(other, "init", "-q")
		(other / "x").write_text("x\n")
		git(other, "add", "-A")
		git(other, "commit", "-q", "-m", "elsewhere")
		sha = git(other, "rev-parse", "HEAD").strip()
		code, text, _ = self.emit(sha)
		self.assertEqual(code, 1)
		self.assertIn("is not on develop", text)
		self.assertEqual(self.nix_envs, [])


class TestRolloutByHand(RolloutRepoCase):
	def setUp(self) -> None:
		super().setUp()
		head = f"repos/{R}/pulls?state=open&head=example:standards/frappe-nix"
		self.gh.routes[("GET", head)] = []
		self.gh.routes[("GET", f"repos/{R}/pulls?state=open&per_page=100")] = [
			{"number": 3, "head": {"ref": "standards/frappe-nix-v1.1.0"}},
			{"number": 4, "head": {"ref": "dependabot/nix/x"}},
		]
		self.gh.cli_results[("pr", "create")] = github.Result(
			0, "https://github.com/example/demo_app/pull/9\n"
		)

	def run_rollout(self) -> tuple[int, str]:
		code, out, err = run_cli("repo", "rollout", "--repo", R, "--to", TAG)
		return code, out + err

	def test_pushes_the_one_branch_and_opens_the_pr(self):
		code, text = self.run_rollout()
		self.assertEqual(code, 0, text)
		subject = git(self.origin, "log", "-1", "--format=%s", "standards/frappe-nix").strip()
		self.assertEqual(subject, f"chore(deps): frappe-nix {TAG}")
		clis = [c[:3] for c in self.gh.cli_calls]
		self.assertIn(("pr", "create", "-R"), clis)
		self.assertIn(("pr", "close", "3"), clis)
		self.assertNotIn(("pr", "close", "4"), clis)
		# The branch is not protected by the gates here: needs-review, never a bare merge --auto.
		self.assertFalse(any(c[:2] == ("pr", "merge") for c in self.gh.cli_calls))
		self.assertTrue(any("needs-review" in c for c in self.gh.cli_calls))

	def test_runs_again_over_its_own_commit(self):
		code, text = self.run_rollout()
		self.assertEqual(code, 0, text)
		again = self.tmp / "clone2"
		subprocess.run(["git", "clone", "-q", str(self.origin), str(again)], check=True)
		self.clones[(R, None)] = again
		code, text = self.run_rollout()
		self.assertEqual(code, 0, text)
		self.assertNotIn("skipped", text)
		self.assertEqual(sum(c[:2] == ("pr", "create") for c in self.gh.cli_calls), 2)

	def test_leaves_a_branch_with_someone_elses_commit(self):
		human = self.tmp / "human"
		subprocess.run(["git", "clone", "-q", str(self.origin), str(human)], check=True)
		git(human, "checkout", "-q", "-b", "standards/frappe-nix")
		(human / "fix.txt").write_text("by hand\n")
		git(human, "add", "-A")
		subprocess.run(
			[
				"git",
				"-C",
				str(human),
				"-c",
				"user.name=Human",
				"-c",
				"user.email=h@example.com",
				"commit",
				"-q",
				"-m",
				"fix",
			],
			check=True,
			env={**os.environ, "GIT_AUTHOR_NAME": "Human", "GIT_AUTHOR_EMAIL": "h@example.com"},
		)
		git(human, "push", "-q", "origin", "standards/frappe-nix")
		before = git(self.origin, "rev-parse", "standards/frappe-nix").strip()
		self.gh.routes[("GET", f"repos/{R}/pulls?state=open&head=example:standards/frappe-nix")] = [
			{"number": 5, "html_url": "https://github.com/example/demo_app/pull/5"}
		]
		code, text = self.run_rollout()
		self.assertEqual(code, 0, text)
		self.assertIn("has commits by Human", text)
		self.assertEqual(git(self.origin, "rev-parse", "standards/frappe-nix").strip(), before)
		self.assertTrue(any(c[:3] == ("pr", "comment", "5") for c in self.gh.cli_calls))
		self.assertFalse(any(c[:2] == ("pr", "create") for c in self.gh.cli_calls))


if __name__ == "__main__":
	unittest.main()
