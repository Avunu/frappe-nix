"""``frappe-nix repo rollout``: arguments, the dry-run plan, ``--emit-patch`` and the run by hand (spec §5.8)."""

import base64
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
BOT = "avunu-rollout[bot]"
#: frappe-nix's rollout.yml at TAG, as the contents API returns it: only its env.BOT is read.
ROLLOUT_YML = f"repos/Avunu/frappe-nix/contents/.github/workflows/rollout.yml?ref={TAG}"


def contents(text: str) -> dict:
	return {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}


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


def push_commit(case: "RolloutRepoCase", name: str, email: str, subject: str) -> str:
	"""A commit by ``name`` <``email``> pushed to standards/frappe-nix on ``case``'s origin; its SHA."""
	other = case.tmp / f"other-{len(list(case.tmp.glob('other-*')))}"
	subprocess.run(["git", "clone", "-q", str(case.origin), str(other)], check=True)
	git(other, "checkout", "-q", "-B", "standards/frappe-nix")
	(other / "fix.txt").write_text(f"by {name}\n")
	git(other, "add", "-A")
	env = {
		**os.environ,
		"GIT_AUTHOR_NAME": name,
		"GIT_AUTHOR_EMAIL": email,
		"GIT_COMMITTER_NAME": name,
		"GIT_COMMITTER_EMAIL": email,
	}
	subprocess.run(["git", "-C", str(other), "commit", "-q", "-m", subject], check=True, env=env)
	git(other, "push", "-q", "-f", "origin", "standards/frappe-nix")
	return git(case.origin, "rev-parse", "standards/frappe-nix").strip()


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
		self.gh.routes[("GET", ROLLOUT_YML)] = contents(f"name: rollout\nenv:\n  BOT: {BOT}\n")


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


class TestForeignCommits(RolloutRepoCase):
	"""Which commits on standards/frappe-nix are the rollout's own: its subject, made by exactly
	rollout.yml's bot or by you (``own_email``). Nothing else, bot or not."""

	SUBJECT = f"chore(deps): frappe-nix {TAG}"

	def foreign(self, bot: str = BOT) -> list[str]:
		return rollout.foreign_commits(
			self.clone, "standards/frappe-nix", "develop", "person@example.com", bot
		)

	def test_the_rollout_bot_is_its_own(self):
		push_commit(self, BOT, f"1+{BOT}@users.noreply.github.com", self.SUBJECT)
		self.assertEqual(self.foreign(), [])

	def test_your_own_commit_is_its_own(self):
		push_commit(self, "Rollout Person", "person@example.com", self.SUBJECT)
		self.assertEqual(self.foreign(), [])
		self.assertEqual(self.foreign(bot=""), [])

	def test_another_bot_with_the_subject_is_foreign(self):
		for bot in ("dependabot[bot]", "github-actions[bot]", "avunu-ci[bot]"):
			with self.subTest(bot=bot):
				push_commit(self, bot, f"1+{bot}@users.noreply.github.com", self.SUBJECT)
				self.assertEqual(self.foreign(), [bot])

	def test_the_rollout_bot_with_another_subject_is_foreign(self):
		push_commit(self, BOT, f"1+{BOT}@users.noreply.github.com", "fix: something else")
		self.assertEqual(self.foreign(), [BOT])

	def test_no_bot_known_counts_only_you(self):
		push_commit(self, BOT, f"1+{BOT}@users.noreply.github.com", self.SUBJECT)
		self.assertEqual(self.foreign(bot=""), [BOT])


LOCKED = {"owner": "Avunu", "repo": "frappe-nix"}


class TestRolloutBot(RepoCase):
	"""rollout.yml's env.BOT, read from the frappe-nix repository the lock names at the release."""

	def test_reads_env_bot(self):
		self.gh.routes[("GET", ROLLOUT_YML)] = contents(f"on: push\nenv:\n  BOT: {BOT}\n")
		self.assertEqual(rollout.rollout_bot(LOCKED, TAG), BOT)

	def test_unreadable_or_odd_is_none(self):
		cases = {
			"missing": None,
			"server error": 502,
			"not yaml": contents("env: [\n"),
			"no env": contents("on: push\n"),
			"not a bot login": contents("env:\n  BOT: someone\n"),
			"not base64": {"content": "!!"},
		}
		for name, answer in cases.items():
			with self.subTest(name):
				self.gh.routes.pop(("GET", ROLLOUT_YML), None)
				if answer is not None:
					self.gh.routes[("GET", ROLLOUT_YML)] = answer
				self.assertEqual(rollout.rollout_bot(LOCKED, TAG), "")


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

	def human_commit(self) -> str:
		"""A commit by someone else pushed to standards/frappe-nix; its SHA."""
		return push_commit(self, "Human", "h@example.com", "fix")

	def test_updates_over_the_rollout_bots_commit(self):
		push_commit(self, BOT, f"1+{BOT}@users.noreply.github.com", f"{rollout.SUBJECT} v1.1.0")
		code, text = self.run_rollout()
		self.assertEqual(code, 0, text)
		self.assertNotIn("has commits by", text)
		subject = git(self.origin, "log", "-1", "--format=%s", "standards/frappe-nix").strip()
		self.assertEqual(subject, f"chore(deps): frappe-nix {TAG}")

	def test_pauses_on_another_bots_rollout_subject(self):
		"""Only avunu-rollout[bot] (or you) makes the rollout's commits; Dependabot's are foreign."""
		bot = "dependabot[bot]"
		before = push_commit(
			self, bot, "49699333+dependabot[bot]@users.noreply.github.com", f"{rollout.SUBJECT} v1.1.0"
		)
		self.gh.routes[("GET", f"repos/{R}/pulls?state=open&head=example:standards/frappe-nix")] = [
			{"number": 5, "html_url": "https://github.com/example/demo_app/pull/5"}
		]
		code, text = self.run_rollout()
		self.assertEqual(code, 0, text)
		self.assertIn(f"has commits by {bot}", text)
		self.assertEqual(git(self.origin, "rev-parse", "standards/frappe-nix").strip(), before)

	def latest_pr(self, state: str, head_sha: str) -> None:
		query = "state=all&head=example:standards/frappe-nix&sort=created&direction=desc&per_page=1"
		self.gh.routes[("GET", f"repos/{R}/pulls?{query}")] = [
			{"number": 5, "state": state, "head": {"sha": head_sha}}
		]

	def test_replaces_the_branch_a_closed_pr_left(self):
		"""Closing (or squash-merging) the PR lifts the pause: no open PR holds the commits."""
		before = self.human_commit()
		self.latest_pr("closed", before)
		code, text = self.run_rollout()
		self.assertEqual(code, 0, text)
		self.assertIn("replacing it", text)
		subject = git(self.origin, "log", "-1", "--format=%s", "standards/frappe-nix").strip()
		self.assertEqual(subject, f"chore(deps): frappe-nix {TAG}")

	def test_keeps_a_branch_that_moved_after_its_pr_closed(self):
		before = self.human_commit()
		self.latest_pr("closed", "f" * 40)
		code, text = self.run_rollout()
		self.assertEqual(code, 0, text)
		self.assertIn("has commits by Human", text)
		self.assertEqual(git(self.origin, "rev-parse", "standards/frappe-nix").strip(), before)

	def test_no_auto_merge_turns_off_an_earlier_one(self):
		self.gh.cli_results[("pr", "view")] = github.Result(
			0, json.dumps({"autoMergeRequest": {"mergeMethod": "SQUASH"}})
		)
		code, out, err = run_cli("repo", "rollout", "--repo", R, "--to", TAG, "--no-auto-merge")
		self.assertEqual(code, 0, out + err)
		self.assertTrue(
			any(c[:3] == ("pr", "merge", "--disable-auto") for c in self.gh.cli_calls), self.gh.cli_calls
		)

	def test_needs_review_turns_off_an_earlier_auto_merge(self):
		self.gh.cli_results[("pr", "view")] = github.Result(
			0, json.dumps({"autoMergeRequest": {"mergeMethod": "SQUASH"}})
		)
		code, text = self.run_rollout()
		self.assertEqual(code, 0, text)
		calls = self.gh.cli_calls
		off = [i for i, c in enumerate(calls) if c[:3] == ("pr", "merge", "--disable-auto")]
		label = [i for i, c in enumerate(calls) if c[:2] == ("pr", "edit") and "needs-review" in c]
		self.assertTrue(off and label and off[0] < label[0], calls)

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
