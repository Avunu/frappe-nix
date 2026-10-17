"""``frappe-nix ci``: the auto-merge decisions, the safety rule, the release dispatches and the
fast-forward guard (spec §4.4, §4.5, §7 N4), against a fake GitHub (``common.github.use``)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.ci import automerge, drift, release, verdict
from frappe_nix_tools.common import github
from frappe_nix_tools.common.github import GitHubError, Result
from helpers import run_cli

REPO = "example/app"
PR = "https://github.com/example/app/pull/7"
GATES = ["pr-policy", "lint", "typecheck", "test"]

# Recorded dependabot/fetch-metadata v3 outputs, one per row of the §4.5 table.
METADATA = [
	(
		{
			"package-ecosystem": "github_actions",
			"update-type": "version-update:semver-minor",
			"dependency-group": "actions",
			"dependency-names": "actions/checkout, actions/setup-node",
			"directory": "/",
		},
		"auto",
	),
	(
		{
			"package-ecosystem": "github_actions",
			"update-type": "version-update:semver-patch",
			"dependency-group": "docs-actions",
			"dependency-names": "example/docs-tool/.github/workflows/docs.yml",
			"directory": "/",
		},
		"review",
	),
	(
		{
			"package-ecosystem": "github_actions",
			"update-type": "",
			"dependency-group": "pilot",
			"dependency-names": "frappe/pilot/.github/workflows/app-assets.yml",
			"directory": "/",
		},
		"review",
	),
	(
		{
			"package-ecosystem": "github_actions",
			"update-type": "version-update:semver-major",
			"dependency-group": "",
			"dependency-names": "frappe/pilot/.github/workflows/app-assets.yml",
			"directory": "/",
		},
		"review",
	),
	(
		{
			"package-ecosystem": "npm_and_yarn",
			"update-type": "version-update:semver-minor",
			"dependency-group": "toolchain",
			"dependency-names": "oxlint, oxfmt",
			"directory": "/",
		},
		"auto",
	),
	(
		{
			"package-ecosystem": "npm_and_yarn",
			"update-type": "version-update:semver-patch",
			"dependency-group": "frontend",
			"dependency-names": "vue",
			"directory": "/frontend",
		},
		"auto",
	),
	(
		{
			"package-ecosystem": "npm_and_yarn",
			"update-type": "version-update:semver-minor",
			"dependency-group": "docs-site-packages",
			"dependency-names": "vitepress",
			"directory": "/docs-site",
		},
		"review",
	),
	(
		{
			"package-ecosystem": "npm_and_yarn",
			"update-type": "version-update:semver-major",
			"dependency-group": "",
			"dependency-names": "typescript",
			"directory": "/",
		},
		"review",
	),
	(
		{
			"package-ecosystem": "uv",
			"update-type": "version-update:semver-patch",
			"dependency-group": "tools",
			"dependency-names": "ruff",
			"directory": "/tools",
		},
		"auto",
	),
	(
		{
			"package-ecosystem": "uv",
			"update-type": "version-update:semver-major",
			"dependency-group": "",
			"dependency-names": "semgrep",
			"directory": "/tools",
		},
		"review",
	),
	(
		{
			"package-ecosystem": "pre_commit",
			"update-type": "",
			"dependency-group": "hooks",
			"dependency-names": "https://github.com/pre-commit/pre-commit-hooks",
			"directory": "/",
		},
		"auto",
	),
	(
		{
			"package-ecosystem": "pre_commit",
			"update-type": "",
			"dependency-group": "test-utils",
			"dependency-names": "https://github.com/agritheory/test_utils",
			"directory": "/",
		},
		"review",
	),
	(
		{
			"package-ecosystem": "nix",
			"update-type": "",
			"dependency-group": "flake-inputs",
			"dependency-names": "frappe",
			"directory": "/",
		},
		"relock",
	),
	(
		{
			"package-ecosystem": "nix",
			"update-type": "",
			"dependency-group": "",
			"dependency-names": "standards-profile",
			"directory": "/",
		},
		"close",
	),
	(
		{
			"package-ecosystem": "submodules",
			"update-type": "",
			"dependency-group": "",
			"dependency-names": "vendor/lib",
			"directory": "/",
		},
		"auto",
	),
]


class Fake(github.Transport):
	"""Answers REST calls from ``routes`` ((method, endpoint) → data, or a status to fail
	with) and ``gh`` subcommands from ``cli_answers`` (first argv words → Result)."""

	def __init__(self, routes=None, cli_answers=None):
		self.routes = dict(routes or {})
		self.cli_answers = dict(cli_answers or {})
		self.requests: list[tuple[str, str, object]] = []
		self.clis: list[tuple[str, ...]] = []

	def request(self, method, endpoint, body=None, paginate=False):
		self.requests.append((method, endpoint, body))
		key = (method, endpoint)
		if key not in self.routes:
			raise GitHubError(f"no route for {method} {endpoint}", 404)
		answer = self.routes[key]
		if callable(answer):
			answer = answer()
		if isinstance(answer, int):
			raise GitHubError(f"{method} {endpoint}: HTTP {answer}", answer)
		return 200, answer

	def cli(self, *args):
		self.clis.append(args)
		for prefix, result in self.cli_answers.items():
			if args[: len(prefix)] == prefix:
				return result
		return Result(0, "", "")

	def writes(self):
		return [r for r in self.requests if r[0] != "GET"]


def protected(contexts):
	return {
		("GET", f"repos/{REPO}/rules/branches/develop"): [
			{
				"type": "required_status_checks",
				"parameters": {"required_status_checks": [{"context": c} for c in contexts]},
			}
		],
		("GET", f"repos/{REPO}/branches/develop"): {"protected": False},
	}


class FakeCase(unittest.TestCase):
	def use(self, fake):
		previous = github.use(fake)
		self.addCleanup(github.use, previous)
		return fake


class TestVerdict(FakeCase):
	def test_recorded_metadata(self):
		for meta, want in METADATA:
			with self.subTest(meta=meta):
				got = verdict.decide(
					meta["package-ecosystem"],
					update_type=meta["update-type"],
					group=meta["dependency-group"],
					dependency_names=meta["dependency-names"],
					directory=meta["directory"],
				)
				self.assertEqual(got, want)

	def test_docs_patterns_and_frappe_nix(self):
		self.assertEqual(
			verdict.decide(
				"github_actions",
				dependency_names="example/docs-tool",
				docs_patterns=["example/docs-tool*"],
			),
			"review",
		)
		self.assertEqual(
			verdict.decide(
				"github_actions",
				group="actions",
				dependency_names="example/frappe-nix",
				frappe_nix_repo="Example/frappe-nix",
			),
			"review",
		)
		self.assertEqual(verdict.decide("cargo", dependency_names="x"), "review")

	def test_cli(self):
		out = Path(tempfile.mkdtemp()) / "out"
		with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}):
			code, stdout, _ = run_cli(
				"ci", "verdict", "--ecosystem", "uv", "--update-type", "version-update:semver-minor",
				"--dependency-group", "tools",
			)  # fmt: skip
		self.assertEqual((code, stdout.strip()), (0, "auto"))
		self.assertEqual(out.read_text(), "verdict=auto\n")


class TestAutomerge(FakeCase):
	def apply(self, decision, **kw):
		return automerge.apply(
			decision,
			pr_url=PR,
			repo=REPO,
			integration="develop",
			gates=GATES,
			merge_method="squash",
			**kw,
		)

	def test_auto_when_protected(self):
		fake = self.use(Fake(protected([f"ci / {g}" for g in GATES])))
		self.assertEqual(self.apply("auto"), "merging")
		self.assertIn(("pr", "merge", "--auto", "--squash", PR), fake.clis)

	def test_unprotected_labels_with_notice(self):
		fake = self.use(Fake(protected(["ci / lint"])))  # lacks the other gates
		with mock.patch("builtins.print") as p:
			self.assertEqual(self.apply("auto"), "needs-review")
		self.assertFalse(any(c[:2] == ("pr", "merge") for c in fake.clis))
		create = [i for i, c in enumerate(fake.clis) if c[:3] == ("label", "create", "needs-review")]
		edit = [i for i, c in enumerate(fake.clis) if c[:2] == ("pr", "edit")]
		self.assertTrue(create and edit and create[0] < edit[0], fake.clis)
		self.assertIn("--force", fake.clis[create[0]])
		printed = " ".join(str(a) for call in p.call_args_list for a in call.args)
		self.assertIn("::notice::", printed)
		self.assertIn("frappe-nix repo doctor", printed)

	def test_no_rules_at_all(self):
		fake = self.use(
			Fake(
				{
					("GET", f"repos/{REPO}/rules/branches/develop"): [],
					("GET", f"repos/{REPO}/branches/develop"): {},
				}
			)
		)
		with mock.patch("builtins.print"):
			self.assertEqual(self.apply("auto"), "needs-review")
		self.assertFalse(any(c[:2] == ("pr", "merge") for c in fake.clis))

	def test_merge_auto_refused(self):
		fake = self.use(
			Fake(
				protected([f"ci / {g}" for g in GATES]),
				{("pr", "merge"): Result(1, "", "GraphQL: Auto merge is not allowed for this repository")},
			)
		)
		with mock.patch("builtins.print") as p:
			self.assertEqual(self.apply("auto"), "needs-review")
		self.assertTrue(any(c[:2] == ("pr", "edit") and "needs-review" in c for c in fake.clis))
		self.assertIn("::notice::", " ".join(str(a) for call in p.call_args_list for a in call.args))

	def test_nix_never_auto_with_a_relock_patch(self):
		fake = self.use(Fake(protected([f"ci / {g}" for g in GATES])))
		with mock.patch("builtins.print"):
			self.assertEqual(self.apply("relock", relock_state="patch"), "wait")
			self.assertEqual(self.apply("relock", relock_state=""), "wait")
		self.assertEqual(fake.clis, [])
		self.assertEqual(self.apply("relock", relock_state="empty"), "merging")

	def test_nix_rollout_input_closes(self):
		fake = self.use(Fake())
		with mock.patch("builtins.print"):
			self.assertEqual(
				self.apply("relock", relock_state="rollout-input-moved", moved=["frappe-nix"]), "closed"
			)
		self.assertEqual([c[:2] for c in fake.clis], [("pr", "comment"), ("pr", "close")])
		self.assertIn("frappe-nix moves through `frappe-nix repo rollout`", fake.clis[0][-1])

	def test_review_labels(self):
		fake = self.use(Fake())
		with mock.patch("builtins.print"):
			self.assertEqual(self.apply("review"), "needs-review")
		self.assertEqual(fake.clis[0][:3], ("label", "create", "needs-review"))

	def test_cli_exit_0(self):
		self.use(Fake(protected([])))
		code, _, err = run_cli(
			"ci", "automerge", "--pr-url", PR, "--repo", REPO, "--integration", "develop",
			"--gates", "lint,test", "--ecosystem", "uv", "--update-type", "version-update:semver-patch",
		)  # fmt: skip
		self.assertEqual(code, 0, err)


class TestDispatch(FakeCase):
	def routes(self, sha, have):
		routes = {}
		for ctx in ("ci / lint", "ci / pr-policy"):
			routes[("GET", f"repos/{REPO}/commits/{sha}/check-runs?check_name={ctx.replace(' ', '%20')}")] = {
				"total_count": 1 if ctx in have else 0
			}
		for wf in ("ci.yml", "pr-policy.yml"):
			routes[("GET", f"repos/{REPO}/contents/.github/workflows/{wf}?ref={sha}")] = {"name": wf}
		return routes

	def test_release_pr_each_context_independently(self):
		sha = "a" * 40
		prs = [
			{"number": 3, "headRefName": "release-please--branches--develop", "headRefOid": sha},
			{"number": 4, "headRefName": "evil", "headRefOid": "b" * 40},
		]
		fake = self.use(Fake(self.routes(sha, {"ci / lint"}), {("pr", "list"): Result(0, json.dumps(prs))}))
		with mock.patch("builtins.print"):
			done = release.dispatch_ci(REPO, "develop")
		self.assertEqual(done, ["pr-policy.yml"])
		runs = [c for c in fake.clis if c[:2] == ("workflow", "run")]
		self.assertEqual(
			runs, [("workflow", "run", "pr-policy.yml", "--repo", REPO, "--ref", prs[0]["headRefName"])]
		)

	def test_sweep_bots_only(self):
		sha = "c" * 40
		prs = [
			{
				"number": 1,
				"author": {"login": "app/dependabot"},
				"headRefName": "dependabot/uv/x",
				"headRefOid": sha,
			},
			{"number": 2, "author": {"login": "someone"}, "headRefName": "feat", "headRefOid": "d" * 40},
		]
		fake = self.use(Fake(self.routes(sha, set()), {("pr", "list"): Result(0, json.dumps(prs))}))
		with mock.patch("builtins.print"):
			done = release.sweep(REPO)
		self.assertEqual(done, ["ci.yml", "pr-policy.yml"])
		self.assertTrue(all("dependabot/uv/x" in c for c in fake.clis if c[:2] == ("workflow", "run")))


class TestFastForward(FakeCase):
	SHA = "e" * 40

	def routes(self, branch_at, tag="v1.3.0", tag_at=None, exists=True):
		routes = {
			("GET", f"repos/{REPO}/git/ref/tags/{tag}"): {"object": {"type": "tag", "sha": "f" * 40}},
			("GET", f"repos/{REPO}/git/tags/{'f' * 40}"): {
				"object": {"type": "commit", "sha": tag_at or self.SHA}
			},
			("PATCH", f"repos/{REPO}/git/refs/heads/version-16"): {},
			("POST", f"repos/{REPO}/git/refs"): {},
		}
		if exists:
			routes[("GET", f"repos/{REPO}/git/ref/heads/version-16")] = {"object": {"sha": branch_at}}
		return routes

	def ff(self, tag="v1.3.0", scheme="semver", **kw):
		return release.FastForward(
			repo=REPO, branch="version-16", sha=self.SHA, tag=tag, version_scheme=scheme, tag_prefix="v",
			frappe_major=16, **kw,
		)  # fmt: skip

	def test_semver_dry_run_passes(self):
		fake = self.use(Fake(self.routes(self.SHA)))
		with mock.patch("builtins.print"):
			ff = self.ff(dry_run=True)
			self.assertEqual(ff.run(), 0)
		self.assertEqual(fake.writes(), [])
		self.assertTrue(ff.calls[0].startswith("PATCH repos/example/app/git/refs/heads/version-16"))

	def test_semver_read_back_differs(self):
		self.use(Fake(self.routes("0" * 40)))
		with mock.patch("builtins.print") as p:
			self.assertEqual(self.ff(dry_run=True).run(), 1)
		self.assertIn("::error::", " ".join(str(a) for call in p.call_args_list for a in call.args))

	def test_frappe_major_refuses_v1(self):
		fake = self.use(Fake(self.routes(self.SHA)))
		with mock.patch("builtins.print"):
			self.assertEqual(self.ff(scheme="frappe-major", dry_run=True).run(), 1)
		self.assertEqual(fake.requests, [])

	def test_frappe_major_v16(self):
		self.use(Fake(self.routes(self.SHA, tag="v16.1.0")))
		with mock.patch("builtins.print"):
			self.assertEqual(self.ff(tag="v16.1.0", scheme="frappe-major", dry_run=True).run(), 0)

	def test_first_release_creates_and_dispatches_assets(self):
		state = {"created": False}
		routes = self.routes(self.SHA, exists=False)

		def ref():
			if not state["created"]:
				return 404
			return {"object": {"sha": self.SHA}}

		def create():
			state["created"] = True
			return {}

		routes[("GET", f"repos/{REPO}/git/ref/heads/version-16")] = ref
		routes[("POST", f"repos/{REPO}/git/refs")] = create
		fake = self.use(Fake(routes))
		with mock.patch("builtins.print"):
			self.assertEqual(self.ff(pilot_assets=True).run(), 0)
		self.assertEqual([w[:2] for w in fake.writes()], [("POST", f"repos/{REPO}/git/refs")])
		self.assertIn(("workflow", "run", "assets.yml", "--repo", REPO, "--ref", "version-16"), fake.clis)

	def test_cli(self):
		self.use(Fake(self.routes(self.SHA)))
		code, out, err = run_cli(
			"ci", "fast-forward", "--repo", REPO, "--release-branch", "version-16", "--sha", self.SHA,
			"--tag", "v1.3.0", "--version-scheme", "semver", "--tag-prefix", "v", "--frappe-major", "16",
			"--dry-run",
		)  # fmt: skip
		self.assertEqual(code, 0, out + err)


class TestDrift(FakeCase):
	def test_open_and_close(self):
		root = Path(tempfile.mkdtemp())
		issues = []
		fake = self.use(Fake({("GET", f"repos/{REPO}/issues?state=open&per_page=100"): lambda: issues,
			("POST", f"repos/{REPO}/issues"): {}, ("PATCH", f"repos/{REPO}/issues/9"): {}}))  # fmt: skip
		from packaging.version import Version

		with mock.patch("frappe_nix_tools.scaffold.defaults.head_version", return_value=Version("1.0.0")):
			self.assertEqual(drift.run(root, REPO, "v1.2.0", check_profile=False), {"frappe-nix": "opened"})
			post = fake.writes()[-1]
			self.assertEqual(post[2]["title"], "frappe-nix v1.2.0 available")
			self.assertIn(f"frappe-nix repo rollout --to v1.2.0 --repo {REPO}", post[2]["body"])
			issues.append({"number": 9, "title": "frappe-nix v1.2.0 available"})
			self.assertEqual(drift.run(root, REPO, "v1.0.0", check_profile=False), {"frappe-nix": "closed"})
			self.assertEqual(fake.writes()[-1], ("PATCH", f"repos/{REPO}/issues/9", {"state": "closed"}))

	def test_profile_issue(self):
		root = Path(tempfile.mkdtemp())
		lock = {
			"root": "root",
			"nodes": {
				"root": {"inputs": {"standards-profile": "standards-profile"}},
				"standards-profile": {
					"locked": {"type": "github", "owner": "example", "repo": "profile", "rev": "1" * 40},
					"original": {"type": "github", "owner": "example", "repo": "profile", "ref": "v1"},
				},
			},
		}
		(root / "flake.lock").write_text(json.dumps(lock))
		fake = self.use(Fake({("GET", f"repos/{REPO}/issues?state=open&per_page=100"): [],
			("POST", f"repos/{REPO}/issues"): {}}))  # fmt: skip
		with (
			mock.patch("frappe_nix_tools.scaffold.defaults.head_version", return_value=None),
			mock.patch("frappe_nix_tools.ci.drift.ls_remote", return_value="2" * 40) as ls,
		):
			out = drift.run(root, REPO, "v1.0.0")
		ls.assert_called_with("https://github.com/example/profile", "v1")
		self.assertEqual(out["profile"], "opened")
		self.assertEqual(fake.writes()[-1][2]["title"], "standards profile update available")


if __name__ == "__main__":
	unittest.main()
