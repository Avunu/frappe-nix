"""The rendered CI callers (spec §4.7, S36, S39; §7 N4 "Callers contain only enabled gates" and
"schedules").

Each test syncs a throwaway app offline and reads the rendered ``.github/`` as YAML. Turning a
gate's module off must remove exactly that gate's caller job (the all-on file with the job's
block removed), or the whole caller file of a workflow module; the context names stay
``ci / <gate>`` because every caller job is named ``ci``.
"""

import copy
import json
import unittest
from pathlib import Path

import yaml
from scaffold_helpers import AppCase, flake_lock

# Every gate on: listing (marketplace) and repo-policy need an org profile's values.
EVERYTHING = """schema = 1
name = "everything"
description = "Every gate and workflow on (a test fixture)"
extends = "recommended@1.0"

[org]
publisher = "Example Org"
email = "apps@example.org"
license = "MIT"
github-owner = "example-org"
website-url = "https://example.org/apps/{app}/"
docs-url = "https://{app_hyphen}.docs.example.org/"

[org.brand]
tile-color = "#336699"

[repo-policy]
enable = true

[pilot-assets]
enable = true

[listing]
enable = true
publish = true
registry-fork = "example-org/marketplace"
"""

TS = "export const answer: number = 42;\n"
WORKFLOWS = ".github/workflows"


def load(path: Path) -> dict:
	return yaml.safe_load(path.read_text())


def on(doc: dict) -> dict:
	# PyYAML reads the bare key `on` as the boolean True.
	return doc.get("on", doc.get(True))


class CallerCase(AppCase):
	profile = "./.standards-profile"

	def setUp(self) -> None:
		super().setUp()
		self.write(".standards-profile/profile.toml", EVERYTHING)
		self.base = self.read("pyproject.toml")
		self.write("flake.lock", json.dumps(flake_lock(["frappe"])))
		self.write("scripts/build.ts", TS)
		self.commit()

	def render(self, table: str = "") -> dict[str, dict]:
		"""Sync with ``table`` added to ``[tool.frappe-nix]``; the rendered .github files as YAML."""
		self.write("pyproject.toml", self.base + table)
		self.commit()
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		out = {}
		for path in sorted((self.root / ".github").rglob("*.yml")):
			out[path.relative_to(self.root).as_posix()] = load(path)
		return out


class TestGates(CallerCase):
	def test_every_gate_is_a_caller_job_named_ci(self) -> None:
		files = self.render()
		ci = files[f"{WORKFLOWS}/ci.yml"]
		self.assertEqual(list(ci["jobs"]), ["lint", "typecheck", "test", "marketplace"])
		for gate, job in ci["jobs"].items():
			self.assertEqual(job["name"], "ci", gate)
			self.assertRegex(
				job["uses"], rf"^Avunu/frappe-nix/\.github/workflows/app-{gate}\.yml@[0-9a-f]{{40}}$"
			)
			# test's own test-report job comments on the pull request.
			comments = "write" if gate == "test" else "read"
			self.assertEqual(job["permissions"], {"contents": "read", "pull-requests": comments})
			self.assertEqual(job["with"]["integration-branch"], "develop")
		for name in ("pr-policy", "release", "deps", "nightly", "assets"):
			self.assertIn(f"{WORKFLOWS}/{name}.yml", files)
		self.assertIn(".github/dependabot.yml", files)
		self.assertIn(".github/zizmor.yml", files)
		policy = files[f"{WORKFLOWS}/pr-policy.yml"]
		self.assertEqual(on(policy)["pull_request"]["types"], ["opened", "edited", "synchronize", "reopened"])
		self.assertEqual(list(policy["jobs"]), ["ci"])

	def test_turning_a_gate_off_removes_exactly_its_job(self) -> None:
		full = self.render()
		for module, gate in (("typescript", "typecheck"), ("listing", "marketplace")):
			with self.subTest(module=module):
				off = self.render(f"[tool.frappe-nix.{module}]\nenable = false\n")
				expected = copy.deepcopy(full[f"{WORKFLOWS}/ci.yml"])
				del expected["jobs"][gate]
				self.assertEqual(off[f"{WORKFLOWS}/ci.yml"], expected)

	def test_tests_off_keeps_test_while_python_types_or_nix_lint_need_the_shell(self) -> None:
		files = self.render("[tool.frappe-nix.tests]\nenable = false\n")
		self.assertIn("test", files[f"{WORKFLOWS}/ci.yml"]["jobs"])
		files = self.render(
			"[tool.frappe-nix.tests]\nenable = false\n[tool.frappe-nix.python-types]\nenable = false\n"
			"[tool.frappe-nix.nix-lint]\nenable = false\n"
		)
		self.assertNotIn("test", files[f"{WORKFLOWS}/ci.yml"]["jobs"])
		self.assertIs(files[f"{WORKFLOWS}/nightly.yml"]["jobs"]["nightly"]["with"]["run-integration"], False)

	def test_workflow_modules_remove_their_files(self) -> None:
		cases = (
			# releases needs commits: both go, and with them pr-policy.yml and release.yml.
			(
				"[tool.frappe-nix.commits]\nenable = false\n[tool.frappe-nix.releases]\nenable = false\n"
				"[tool.frappe-nix.pilot-assets]\nenable = false\n",
				["pr-policy.yml", "release.yml", "assets.yml"],
			),
			(
				"[tool.frappe-nix.releases]\nenable = false\n[tool.frappe-nix.pilot-assets]\nenable = false\n",
				["release.yml", "assets.yml"],
			),
			("[tool.frappe-nix.dependabot]\nenable = false\n", ["deps.yml", "../dependabot.yml"]),
			("[tool.frappe-nix.ci]\nnightly = false\n", ["nightly.yml"]),
		)
		full = set(self.render())
		for table, gone in cases:
			with self.subTest(table=table):
				files = set(self.render(table))
				removed = {
					str(Path(f"{WORKFLOWS}/{g}")).replace(".github/workflows/../", ".github/") for g in gone
				}
				self.assertEqual(full - files, removed)

	def test_ci_off_renders_no_caller(self) -> None:
		files = self.render(
			"[tool.frappe-nix.ci]\nenable = false\n[tool.frappe-nix.pilot-assets]\nenable = false\n"
		)
		self.assertEqual([f for f in files if f.startswith(WORKFLOWS)], [])

	def test_release_inputs_follow_the_profile(self) -> None:
		files = self.render()
		release = files[f"{WORKFLOWS}/release.yml"]["jobs"]["release"]
		self.assertEqual(release["with"]["release-branch"], "version-16")
		self.assertIs(release["with"]["pilot-assets"], True)
		self.assertEqual(release["with"]["registry-fork"], "example-org/marketplace")
		self.assertIn("REGISTRY_TOKEN", release["secrets"])
		files = self.render('[tool.frappe-nix.releases]\nbranching = "main+tags"\n')
		release = files[f"{WORKFLOWS}/release.yml"]["jobs"]["release"]
		self.assertEqual(release["with"]["release-branch"], "")
		self.assertEqual(on(files[f"{WORKFLOWS}/ci.yml"])["push"]["branches"], ["main"])
		self.assertIn("refs/tags/v", files[f"{WORKFLOWS}/assets.yml"]["jobs"]["assets"]["if"])


class TestSchedules(CallerCase):
	def test_empty_schedules_render_no_schedule(self) -> None:
		files = self.render(
			'[tool.frappe-nix.ci.schedules]\nci = ""\nrelease = ""\ndeps-sweep = ""\ndeps-relock = ""\nnightly = ""\n'
		)
		for name in ("ci", "release", "deps"):
			self.assertNotIn("schedule", on(files[f"{WORKFLOWS}/{name}.yml"]), name)
		self.assertEqual(list(on(files[f"{WORKFLOWS}/nightly.yml"])), ["workflow_dispatch"])
		self.assertEqual(files[f"{WORKFLOWS}/deps.yml"]["jobs"]["deps"]["with"]["relock-cron"], "")

	def test_default_schedules(self) -> None:
		files = self.render()
		self.assertEqual(on(files[f"{WORKFLOWS}/ci.yml"])["schedule"], [{"cron": "0 6 * * *"}])
		self.assertEqual(on(files[f"{WORKFLOWS}/release.yml"])["schedule"], [{"cron": "30 5 * * *"}])
		self.assertEqual(
			on(files[f"{WORKFLOWS}/deps.yml"])["schedule"], [{"cron": "0 9 * * *"}, {"cron": "0 8 * * 1"}]
		)
		self.assertEqual(files[f"{WORKFLOWS}/deps.yml"]["jobs"]["deps"]["with"]["relock-cron"], "0 8 * * 1")


# The callers of frappe-nix's reusable workflows; assets.yml calls pilot's, which has no runner input.
FRAPPE_NIX_CALLERS = {
	"ci": None,
	"pr-policy": ["ci"],
	"release": ["release"],
	"deps": ["deps"],
	"nightly": ["nightly"],
}
SELF_HOSTED = '[tool.frappe-nix.ci]\nruns-on = ["self-hosted", "nixos"]\n'


class TestRunsOn(CallerCase):
	def jobs(self, files: dict[str, dict]) -> list[tuple[str, dict]]:
		out = []
		for name, ids in FRAPPE_NIX_CALLERS.items():
			doc = files[f"{WORKFLOWS}/{name}.yml"]
			out += [(f"{name}:{job}", doc["jobs"][job]) for job in (ids or list(doc["jobs"]))]
		return out

	def test_unset_passes_no_runner(self) -> None:
		for where, job in self.jobs(self.render()):
			self.assertNotIn("runs-on", job["with"], where)

	def test_labels_reach_every_frappe_nix_caller_as_json(self) -> None:
		unset = self.render()
		files = self.render(SELF_HOSTED)
		for where, job in self.jobs(files):
			self.assertEqual(job["with"]["runs-on"], '["self-hosted","nixos"]', where)
			self.assertEqual(json.loads(job["with"]["runs-on"]), ["self-hosted", "nixos"], where)
		for name in FRAPPE_NIX_CALLERS:
			path = self.root / WORKFLOWS / f"{name}.yml"
			self.assertIn('      runs-on: \'["self-hosted","nixos"]\'\n', path.read_text(), name)
		# Nothing else moves: without the input, each file is the unset render.
		for path, doc in files.items():
			for job in doc.get("jobs", {}).values():
				job.get("with", {}).pop("runs-on", None)
			self.assertEqual(doc, unset[path], path)

	def test_a_single_label(self) -> None:
		files = self.render('[tool.frappe-nix.ci]\nruns-on = "big-box"\n')
		for where, job in self.jobs(files):
			self.assertEqual(json.loads(job["with"]["runs-on"]), "big-box", where)

	def test_a_profile_sets_it(self) -> None:
		self.write(
			".standards-profile/profile.toml", EVERYTHING + '\n[ci]\nruns-on = ["self-hosted", "nixos"]\n'
		)
		self.commit()
		for where, job in self.jobs(self.render()):
			self.assertEqual(job["with"]["runs-on"], '["self-hosted","nixos"]', where)

	def test_invalid_values_are_exit_2(self) -> None:
		for value in ("[]", '["self-hosted", "self-hosted"]', '["bad label"]', '""', "3"):
			with self.subTest(value=value):
				self.write("pyproject.toml", self.base + f"[tool.frappe-nix.ci]\nruns-on = {value}\n")
				self.commit()
				code, _, err = self.fn("sync", "--write")
				self.assertEqual(code, 2, err)
				self.assertIn("runs-on", err)


class TestDependabot(CallerCase):
	def test_every_entry_has_a_cooldown_and_targets_the_integration_branch(self) -> None:
		files = self.render()
		updates = files[".github/dependabot.yml"]["updates"]
		self.assertTrue(updates)
		for entry in updates:
			self.assertIn("cooldown", entry, entry["package-ecosystem"])
			self.assertEqual(entry["target-branch"], "develop")
		nix = next(e for e in updates if e["package-ecosystem"] == "nix")
		self.assertEqual(
			[i["dependency-name"] for i in nix["ignore"]],
			["frappe-nix"],
			"a built-in-free org profile is in-repo",
		)
		actions = next(e for e in updates if e["package-ecosystem"] == "github-actions")
		self.assertEqual(actions["ignore"], [{"dependency-name": "Avunu/frappe-nix"}])
		self.assertEqual(actions["groups"]["pilot"]["patterns"], ["frappe/pilot*"])

	def test_zizmor_cooldown_follows_the_profile(self) -> None:
		files = self.render("[tool.frappe-nix.dependabot]\ncooldown-days = 5\n")
		self.assertEqual(files[".github/zizmor.yml"]["rules"]["dependabot-cooldown"]["config"]["days"], 5)
		self.assertEqual(files[".github/dependabot.yml"]["updates"][0]["cooldown"]["default-days"], 5)

	def test_ignore_goes_into_the_matching_entries(self) -> None:
		"""An ecosystem key reaches every managed entry of it, an ecosystem:directory key one
		entry; both follow the entry's own ignores."""
		self.write("frontend/package.json", json.dumps({"name": "frontend"}))
		files = self.render(
			"[tool.frappe-nix.dependabot.ignore]\n"
			'npm = [{ dependency-name = "tailwindcss", versions = [">=4"] }]\n'
			'"npm:/frontend" = [{ dependency-name = "@vitejs/*", update-types = ["version-update:semver-major"] }]\n'
			'nix = [{ dependency-name = "nixpkgs" }]\n'
			'"github-actions:/" = [{ dependency-name = "actions/checkout", versions = [">=5", "<7"] }]\n'
			'"uv:/elsewhere" = [{ dependency-name = "ruff" }]\n'
		)
		updates = files[".github/dependabot.yml"]["updates"]
		entry = {(e["package-ecosystem"], e["directory"]): e for e in updates}
		tailwind = {"dependency-name": "tailwindcss", "versions": [">=4"]}
		self.assertEqual(entry["npm", "/"]["ignore"], [tailwind])
		self.assertEqual(
			entry["npm", "/frontend"]["ignore"],
			[tailwind, {"dependency-name": "@vitejs/*", "update-types": ["version-update:semver-major"]}],
		)
		self.assertEqual(
			entry["nix", "/"]["ignore"], [{"dependency-name": "frappe-nix"}, {"dependency-name": "nixpkgs"}]
		)
		self.assertEqual(
			entry["github-actions", "/"]["ignore"],
			[
				{"dependency-name": "Avunu/frappe-nix"},
				{"dependency-name": "actions/checkout", "versions": [">=5", "<7"]},
			],
		)
		# A directory with no managed entry renders nothing.
		self.assertNotIn("ignore", entry["uv", "/tools"])

	def test_ignore_is_validated(self) -> None:
		for value, problem in (
			('{ cargo = [{ dependency-name = "x" }] }', "does not match"),
			('{ "npm:docs-site" = [{ dependency-name = "x" }] }', "does not match"),
			('{ npm = [{ versions = [">=4"] }] }', "dependency-name is required"),
			('{ npm = [{ dependency-name = "x", version = ">=4" }] }', "unknown key"),
			('{ npm = [{ dependency-name = "x", update-types = ["major"] }] }', "must be one of"),
			('{ npm = { dependency-name = "x" } }', "expected array"),
		):
			with self.subTest(value=value):
				self.write("pyproject.toml", self.base + f"[tool.frappe-nix.dependabot]\nignore = {value}\n")
				self.commit()
				code, _, err = self.fn("sync", "--write")
				self.assertEqual(code, 2, err)
				self.assertIn("dependabot.ignore", err)
				self.assertIn(problem, err)


class TestOptIn(AppCase):
	profile = "minimal"

	def test_minimal_renders_no_ci(self) -> None:
		code, _, err = self.fn("sync", "--write")
		self.assertEqual(code, 0, err)
		self.assertFalse((self.root / ".github").exists())


if __name__ == "__main__":
	unittest.main()
