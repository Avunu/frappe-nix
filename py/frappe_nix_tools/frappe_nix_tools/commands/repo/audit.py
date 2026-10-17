"""``frappe-nix repo audit``: the scorecard, rows A1-A17 (spec §5.7).

For each repository: a shallow clone of the integration branch (resolved as ``repo apply``
resolves it, §5.6), the app's configuration, and the rows below, each ``green``, ``amber``,
``red``, ``n/a`` (its module is off) or ``unknown`` (the data couldn't be read, for example
because the token lacks admin). The commands a row runs on the clone (``sync --check``,
``compat``, ``listing``, ``icon``) run from frappe-nix-tools at the revision the app's
``flake.lock`` pins, through ``uv tool run`` (``--against latest``: the latest release's).

====  ==========================  ==================================================================
A1    repo-policy                 the default branch is the integration branch
A2    releases, develop+version   the release branch equals the newest release tag
A3    repo-policy                 settings and rulesets match repo-policy/ as rendered for the app
A4    (always)                    managed files present and ``sync --check`` clean
A5    ci                          the gates green on the integration branch; pr-policy and nightly green
A6    python-types                ty: 0 diagnostics in the test report
A7    typescript                  the typecheck job green; compat (C7) clean; amber with unchecked-js
A8    tests                       coverage ≥ fail_under, testmap clean; amber below the target
A9    (always)                    no legacy tooling (the retire rules, public/dist, pyupgrade)
A10   dependabot                  the last nix bump auto-merged with relock; frappe-nix and profile current
A11   releases                    a release tag exists, released by github-actions[bot]
A12   readme                      README blocks in sync, the dev-docs link present
A13   icons                       ``icon check --structural``
A14   listing                     ``listing check`` (and ``--links-only`` for a listed app); empty baseline
A15   screenshots                 the nightly shots job green; no screenshots PR older than 7 days
A16   listing.publish             the registry index has the app at the newest tag
A17   docs-site                   Pages with a CNAME and https enforced; DOCS_SITE_ENABLED
====  ==========================  ==================================================================

Exit codes: 0 every row green, n/a or unknown; 1 any amber or red; 2 a configuration error.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from frappe_nix_tools.commands.repo import apply as apply_cmd
from frappe_nix_tools.commands.repo import targets
from frappe_nix_tools.common import flakelock, github, repo
from frappe_nix_tools.common.github import GitHubError
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError, FrappeNixError

GREEN, AMBER, RED, NA, UNKNOWN = "green", "amber", "red", "n/a", "unknown"
ITEMS = {
	"A1": ("default branch is the integration branch", "repo-policy"),
	"A2": ("release branch equals the newest release tag", "releases"),
	"A3": ("settings and rulesets match repo-policy/", "repo-policy"),
	"A4": ("managed files present, sync --check clean", None),
	"A5": ("gates green on the integration branch, nightly green", "ci"),
	"A6": ("ty: 0 diagnostics", "python-types"),
	"A7": ("tsc clean, no hand-written frappe globals", "typescript"),
	"A8": ("coverage at or above the floor, testmap passing", "tests"),
	"A9": ("no legacy tooling", None),
	"A10": ("dependency bumps merge with relock; frappe-nix and profile current", "dependabot"),
	"A11": ("a release cut by release-please", "releases"),
	"A12": ("README blocks in sync", "readme"),
	"A13": ("icons pass", "icons"),
	"A14": ("listing check clean, empty semgrep baseline", "listing"),
	"A15": ("screenshots refreshed by nightly", "screenshots"),
	"A16": ("registry index at the newest tag", "listing"),
	"A17": ("docs site served over https", "docs-site"),
}
STALE_DAYS = 14


@dataclass
class Row:
	id: str
	item: str
	status: str
	detail: str = ""


@dataclass
class Audit:
	"""One repository's audit: the target, and what more than one row reads, read once."""

	t: targets.Target
	against: str = "pinned"
	cache: dict = field(default_factory=dict)

	@property
	def r(self) -> str:
		return self.t.repo

	@property
	def root(self) -> Path:
		"""The clone (every row that reads it runs only for an opted-in, cloned repository)."""
		if self.t.root is None:
			raise FrappeNixError(f"{self.t.repo} was not cloned")
		return self.t.root

	def once(self, key: str, fn) -> Any:
		if key not in self.cache:
			try:
				self.cache[key] = fn()
			except GitHubError as e:
				self.cache[key] = e
		value = self.cache[key]
		if isinstance(value, GitHubError):
			raise value
		return value


# --- the seams tests replace ---------------------------------------------------------------


def run_tool(root: Path, argv: list[str], spec: str | None) -> tuple[int, str]:
	"""``frappe-nix <argv>`` in ``root``, from frappe-nix-tools ``spec`` (a pip requirement),
	or the running one when ``spec`` is None."""
	env = dict(os.environ, FRAPPE_NIX_ALLOW_SKEW="1") if spec and "@" in spec else dict(os.environ)
	cmd = (
		["uv", "tool", "run", "--python", "3.14", "--from", spec, "frappe-nix", *argv]
		if spec
		else ["frappe-nix", *argv]
	)
	try:
		proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False, env=env)
	except FileNotFoundError as e:
		raise FrappeNixError(f"{cmd[0]} is not on PATH") from e
	return proc.returncode, proc.stdout


def fetch_url(url: str) -> str | None:
	"""The body of ``url`` over https, or ``None`` on any failure."""
	try:
		with urllib.request.urlopen(url, timeout=30) as resp:
			return resp.read().decode("utf-8", errors="replace")
	except Exception:
		return None


def download_report(r: str, run_id: int) -> dict | None:
	"""``frappe-test-report.json`` from the run's ``test-report`` artifact."""
	with tempfile.TemporaryDirectory() as d:
		res = github.cli("run", "download", str(run_id), "-R", r, "-n", "test-report", "-D", d)
		if res.code != 0:
			return None
		path = Path(d) / "frappe-test-report.json"
		if not path.is_file():
			return None
		try:
			return json.loads(path.read_text())
		except (OSError, json.JSONDecodeError):
			return None


# --- shared reads ----------------------------------------------------------------------------


def tool_spec(a: Audit) -> str | None:
	"""The frappe-nix-tools the app pins (or the latest release's)."""
	root = a.t.root
	if root is None or not (root / "flake.lock").is_file():
		return None
	lock = flakelock.load(root / "flake.lock")
	found = flakelock.node_at(lock, ["frappe-nix"])
	locked = (found[1].get("locked") or {}) if found else {}
	owner, name, rev = locked.get("owner"), locked.get("repo"), locked.get("rev")
	if not (owner and name and rev):
		return None
	if a.against == "latest":
		rel = github.get_or_none(f"repos/{owner}/{name}/releases/latest") or {}
		rev = rel.get("tag_name") or rev
	return f"frappe-nix-tools @ git+https://github.com/{owner}/{name}@{rev}#subdirectory=py/frappe_nix_tools"


def tool(a: Audit, *argv: str) -> tuple[int, str]:
	key = "tool:" + " ".join(argv)
	if key not in a.cache:
		a.cache[key] = run_tool(a.root, list(argv), a.once("spec", lambda: tool_spec(a)))
	return a.cache[key]


def sync_report(a: Audit) -> dict | None:
	def read() -> dict | None:
		_, out = tool(a, "sync", "--check", "--format", "json")
		try:
			return json.loads(out)
		except json.JSONDecodeError:
			return None

	return a.once("sync", read)


def tags(a: Audit) -> list[dict]:
	return a.once("tags", lambda: github.get(f"repos/{a.r}/tags", paginate=True) or [])


def release_tags(a: Audit) -> list[tuple[Version, dict]]:
	"""The release tags, newest first: ``<prefix><major>.*`` under ``frappe-major``, any
	``<prefix><version>`` under ``semver``."""
	rel = a.t.cfg.get("releases", {})
	prefix = rel.get("tag-prefix", "v")
	major = a.t.cfg.get("frappe-major")
	out = []
	for tag in tags(a):
		name = tag.get("name", "")
		if not name.startswith(prefix):
			continue
		try:
			v = Version(name[len(prefix) :])
		except InvalidVersion:
			continue
		if rel.get("version-scheme") == "frappe-major" and v.major != major:
			continue
		out.append((v, tag))
	return sorted(out, key=lambda x: x[0], reverse=True)


def latest_run(a: Audit, workflow: str, branch: str | None = None) -> dict | None:
	def read() -> dict | None:
		q = f"?status=completed&per_page=1{f'&branch={branch}' if branch else ''}"
		runs = (github.get(f"repos/{a.r}/actions/workflows/{workflow}/runs{q}") or {}).get(
			"workflow_runs", []
		)
		return runs[0] if runs else None

	return a.once(f"run:{workflow}:{branch}", read)


def run_jobs(a: Audit, run_id: int) -> list[dict]:
	return a.once(
		f"jobs:{run_id}",
		lambda: (github.get(f"repos/{a.r}/actions/runs/{run_id}/jobs?per_page=100") or {}).get("jobs", []),
	)


def test_report(a: Audit) -> dict | None:
	run = latest_run(a, "ci.yml", a.t.integration)
	if not run:
		return None
	return a.once("report", lambda: download_report(a.r, run["id"]))


def pyproject_doc(a: Audit) -> dict:
	try:
		return tomllib.loads((a.root / "pyproject.toml").read_text())
	except (OSError, tomllib.TOMLDecodeError):
		return {}


# --- the rows --------------------------------------------------------------------------------


def a1(a: Audit) -> tuple[str, str]:
	info = a.once("info", lambda: github.get(f"repos/{a.r}") or {})
	default = info.get("default_branch")
	return (
		(GREEN, default)
		if default == a.t.integration
		else (RED, f"default {default}, integration {a.t.integration}")
	)


def a2(a: Audit) -> tuple[str, str]:
	vb = a.t.release_branch
	if not vb:
		return NA, "no release branch under main+tags"
	newest = release_tags(a)
	if not newest:
		return AMBER, "no release tag yet"
	ref = github.get_or_none(f"repos/{a.r}/git/ref/heads/{vb}")
	if ref is None:
		return RED, f"{vb} does not exist"
	sha, tag = ref["object"]["sha"], newest[0][1]
	want = tag["commit"]["sha"]
	return (
		(GREEN, f"{vb} = {tag['name']}")
		if sha == want
		else (RED, f"{vb} is {sha[:12]}, {tag['name']} is {want[:12]}")
	)


def a3(a: Audit) -> tuple[str, str]:
	info = a.once("info", lambda: github.get(f"repos/{a.r}") or {})
	out = apply_cmd.Outcome(a.r)
	policy = apply_cmd.plan_policy(
		a.t, "full" if a.t.opted_in else "provisional", apply_cmd.documentation_url(a.t)
	)
	apply_cmd.apply_settings(out, a.r, policy, True)
	if info.get("private"):
		detail = "rulesets n/a (private)"
	else:
		try:
			apply_cmd.apply_rulesets(out, a.r, policy, True, False)
			detail = ""
		except FrappeNixError:
			return UNKNOWN, "rulesets not readable"
	if out.changes:
		return RED, "; ".join(c.call for c in out.changes)
	return GREEN, detail


def a4(a: Audit) -> tuple[str, str]:
	report = sync_report(a)
	if report is None:
		return UNKNOWN, "sync --check gave no report"
	status = report.get("status")
	if status == "clean":
		return GREEN, ""
	return RED, f"{status}: {', '.join(f['path'] for f in report.get('files', [])[:5])}"


def a5(a: Audit) -> tuple[str, str]:
	run = latest_run(a, "ci.yml", a.t.integration)
	if not run:
		return RED, f"no completed ci.yml run on {a.t.integration}"
	problems = []
	if run.get("conclusion") != "success":
		problems.append(f"ci.yml {run.get('conclusion')}")
	jobs = {j.get("name"): j.get("conclusion") for j in run_jobs(a, run["id"])}
	for gate in a.t.gates:
		if gate == "pr-policy":
			continue
		if jobs.get(f"ci / {gate}") != "success":
			problems.append(f"ci / {gate} {jobs.get(f'ci / {gate}', 'absent')}")
	if a.t.modules.get("commits"):
		pr = latest_run(a, "pr-policy.yml")
		if pr and pr.get("conclusion") != "success":
			problems.append(f"pr-policy {pr.get('conclusion')}")
	if a.t.cfg.get("ci", {}).get("nightly", True):
		nightly = latest_run(a, "nightly.yml")
		if nightly is None or nightly.get("conclusion") != "success":
			problems.append(f"nightly {nightly.get('conclusion') if nightly else 'never ran'}")
	return (RED, "; ".join(problems)) if problems else (GREEN, "")


def a6(a: Audit) -> tuple[str, str]:
	report = test_report(a)
	ty = (report or {}).get("ty")
	if not isinstance(ty, dict):
		return UNKNOWN, "no ty result in the test report"
	return (
		(GREEN, f"{ty.get('ignores', 0)} ignores")
		if ty.get("diagnostics") == 0
		else (RED, f"{ty.get('diagnostics')} diagnostics")
	)


def a7(a: Audit) -> tuple[str, str]:
	problems = []
	if "typecheck" in a.t.gates:
		run = latest_run(a, "ci.yml", a.t.integration)
		jobs = {j.get("name"): j.get("conclusion") for j in run_jobs(a, run["id"])} if run else {}
		if jobs.get("ci / typecheck") != "success":
			problems.append(f"ci / typecheck {jobs.get('ci / typecheck', 'absent')}")
	code, _ = tool(a, "compat")
	if code != 0:
		problems.append("frappe-nix compat failed")
	if problems:
		return RED, "; ".join(problems)
	ts = a.t.cfg.get("typescript", {})
	if ts.get("check-js") and a.t.cfg.get("unchecked-js"):
		return AMBER, f"{len(a.t.cfg['unchecked-js'])} unchecked-js file(s)"
	return GREEN, ""


def a8(a: Audit) -> tuple[str, str]:
	report = test_report(a)
	if not report:
		return UNKNOWN, "no test report"
	cov = report.get("coverage") or {}
	percent, floor = cov.get("percent"), cov.get("fail_under")
	untested = (report.get("testmap") or {}).get("untested", [])
	if percent is None or floor is None:
		return UNKNOWN, "no coverage in the test report"
	if percent < floor or untested:
		return RED, f"coverage {percent} (floor {floor}); untested {len(untested)}"
	target = a.t.cfg.get("tests", {}).get("coverage", {}).get("target", 80)
	if floor < target or a.t.cfg.get("coverage-omit"):
		return AMBER, f"coverage {percent}, floor {floor} below the target {target}"
	return GREEN, f"coverage {percent}"


def a9(a: Audit) -> tuple[str, str]:
	problems = []
	report = sync_report(a) or {}
	legacy = [f["path"] for f in report.get("files", []) if "legacy" in f.get("problem", "")]
	if legacy:
		problems.append(f"legacy files: {', '.join(legacy)}")
	dist = repo.ls_files(a.root, ":(glob)**/public/dist/**")
	if dist:
		problems.append(f"{len(dist)} tracked file(s) under public/dist")
	pre = a.root / ".pre-commit-config.yaml"
	text = pre.read_text() if pre.is_file() else ""
	for tool_name in ("pyupgrade", "codesorter"):
		if tool_name in text.lower():
			problems.append(f"{tool_name} in .pre-commit-config.yaml")
	if problems:
		return RED, "; ".join(problems)
	exclude = pyproject_doc(a).get("tool", {}).get("ruff", {}).get("extend-exclude")
	if exclude:
		return AMBER, f"tool.ruff.extend-exclude: {exclude}"
	return GREEN, ""


def _days_since(stamp: str | None) -> float:
	if not stamp:
		return 0.0
	when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
	return (dt.datetime.now(dt.UTC) - when).total_seconds() / 86400


def a10(a: Audit) -> tuple[str, str]:
	notes, worst = [], GREEN
	prs = github.get(f"repos/{a.r}/pulls?state=closed&per_page=100&sort=updated&direction=desc") or []
	nix = [
		p
		for p in prs
		if p.get("merged_at") and p.get("head", {}).get("ref", "").startswith("dependabot/nix/")
	]
	if nix:
		pr = nix[0]
		commits = github.get(f"repos/{a.r}/pulls/{pr['number']}/commits") or []
		relocked = any(
			c.get("commit", {}).get("message", "").startswith("chore(deps): relock and sync") for c in commits
		)
		merger = (pr.get("merged_by") or {}).get("login", "")
		if not (relocked or len(commits) == 1) or merger not in (
			"github-actions[bot]",
			"dependabot[bot]",
			"",
		):
			worst = AMBER
			notes.append(
				f"#{pr['number']} merged by {merger or '?'}{'' if relocked else ' without a relock'}"
			)
	lock_path = a.root / "flake.lock"
	if lock_path.is_file():
		lock = flakelock.load(lock_path)
		found = flakelock.node_at(lock, ["frappe-nix"])
		locked = (found[1].get("locked") or {}) if found else {}
		if locked.get("owner"):
			latest = github.get_or_none(f"repos/{locked['owner']}/{locked['repo']}/releases/latest") or {}
			tag = latest.get("tag_name")
			if tag:
				tag_sha = (
					github.get_or_none(f"repos/{locked['owner']}/{locked['repo']}/commits/{tag}") or {}
				).get("sha")
				if (
					tag_sha
					and tag_sha != locked.get("rev")
					and _days_since(latest.get("published_at")) > STALE_DAYS
				):
					worst = AMBER
					notes.append(
						f"frappe-nix {tag} available: frappe-nix repo rollout --to {tag} --repo {a.r}"
					)
		prof = flakelock.input_node(lock, flakelock.PROFILE_INPUT)
		src = a.t.resolved.profile.get("source", "builtin") if a.t.resolved else "builtin"
		if prof and src.startswith("github:"):
			plocked, parts = prof[1].get("locked") or {}, src.removeprefix("github:").split("/")
			ref = "/".join(parts[2:]) or "HEAD"
			head = github.get_or_none(f"repos/{parts[0]}/{parts[1]}/commits/{ref}") or {}
			if head.get("sha") and head["sha"] != plocked.get("rev"):
				date = (head.get("commit") or {}).get("committer", {}).get("date")
				if _days_since(date) > STALE_DAYS:
					worst = AMBER
					notes.append(
						f"standards profile behind {ref}: frappe-nix repo rollout --profile-to latest --repo {a.r}"
					)
	return worst, "; ".join(notes)


def a11(a: Audit) -> tuple[str, str]:
	newest = release_tags(a)
	if not newest:
		return RED, "no release tag"
	name = newest[0][1]["name"]
	rel = github.get_or_none(f"repos/{a.r}/releases/tags/{name}")
	if rel is None:
		return RED, f"{name} has no GitHub release"
	author = (rel.get("author") or {}).get("login", "")
	return (GREEN, name) if author == "github-actions[bot]" else (AMBER, f"{name} released by {author}")


def a12(a: Audit) -> tuple[str, str]:
	code, _ = tool(a, "listing", "readme", "--check")
	readme = a.root / "README.md"
	link = a.t.cfg.get("org", {}).get("dev-docs-url", "")
	has_link = readme.is_file() and link and link in readme.read_text()
	if code != 0:
		return RED, "listing readme --check failed"
	return (GREEN, "") if has_link else (RED, "README lacks the org.dev-docs-url link")


def a13(a: Audit) -> tuple[str, str]:
	code, _ = tool(a, "icon", "check", "--structural")
	return (GREEN, "") if code == 0 else (RED, "icon check --structural failed")


def a14(a: Audit) -> tuple[str, str]:
	problems = []
	if a.t.entry.get("list", True):
		code, _ = tool(a, "listing", "check", "--links-only")
		if code != 0:
			problems.append("links")
	code, _ = tool(a, "listing", "check")
	if code != 0:
		problems.append("listing check")
	base = a.root / "marketplace" / "semgrep-baseline.json"
	try:
		findings = json.loads(base.read_text()).get("findings", [])
	except (OSError, json.JSONDecodeError):
		findings = []
	if findings:
		problems.append(f"{len(findings)} baseline finding(s)")
	return (RED, "; ".join(problems)) if problems else (GREEN, "")


def a15(a: Audit) -> tuple[str, str]:
	run = latest_run(a, "nightly.yml")
	if not run:
		return RED, "nightly never ran"
	shots = [j for j in run_jobs(a, run["id"]) if "shots" in (j.get("name") or "")]
	if not shots or shots[0].get("conclusion") != "success":
		return RED, f"shots {shots[0].get('conclusion') if shots else 'absent'}"
	prs = github.get(f"repos/{a.r}/pulls?state=open&per_page=100") or []
	old = [
		p["number"]
		for p in prs
		if any(lb.get("name") == "screenshots" for lb in p.get("labels", []))
		and _days_since(p.get("created_at")) > 7
	]
	return (AMBER, f"screenshots PR #{old[0]} older than 7 days") if old else (GREEN, "")


def a16(a: Audit) -> tuple[str, str]:
	if not a.t.cfg.get("listing", {}).get("publish") or not a.t.entry.get("list", True):
		return NA, "not published"
	upstream = a.t.cfg["listing"].get("registry-upstream", "frappe/marketplace")
	app = a.t.app.app if a.t.app else a.t.entry.get("app", "")
	index = fetch_url(f"https://raw.githubusercontent.com/{upstream}/main/apps.json")
	if index is None:
		return UNKNOWN, "apps.json not readable"
	if app not in index:
		return RED, f"{app} is not in {upstream} apps.json"
	newest = release_tags(a)
	doc = fetch_url(f"https://raw.githubusercontent.com/{upstream}/main/apps/{app}.json")
	try:
		version = (json.loads(doc or "{}").get("releases") or [{}])[0].get("version")
	except json.JSONDecodeError:
		version = None
	want = str(newest[0][0]) if newest else None
	return (
		(GREEN, version or "") if version and version == want else (RED, f"registry {version}, latest {want}")
	)


def a17(a: Audit) -> tuple[str, str]:
	pages = github.get_or_none(f"repos/{a.r}/pages")
	if pages is None:
		return RED, "no Pages site"
	var = github.get_or_none(f"repos/{a.r}/actions/variables/DOCS_SITE_ENABLED") or {}
	if not pages.get("cname") or not pages.get("https_enforced"):
		return RED, "Pages needs a CNAME and https enforced"
	return (GREEN, pages["cname"]) if var.get("value") == "true" else (RED, "DOCS_SITE_ENABLED is not true")


EVALUATORS = {
	"A1": a1,
	"A2": a2,
	"A3": a3,
	"A4": a4,
	"A5": a5,
	"A6": a6,
	"A7": a7,
	"A8": a8,
	"A9": a9,
	"A10": a10,
	"A11": a11,
	"A12": a12,
	"A13": a13,
	"A14": a14,
	"A15": a15,
	"A16": a16,
	"A17": a17,
}


def module_on(a: Audit, module: str | None) -> bool:
	return module is None or bool(a.t.modules.get(module))


def audit(a: Audit) -> list[Row]:
	rows = []
	for rid, (item, module) in ITEMS.items():
		if a.t.resolved is None or not a.t.opted_in:
			if rid == "A4":
				rows.append(Row(rid, item, RED, "no [tool.frappe-nix] on the integration branch"))
			else:
				rows.append(Row(rid, item, UNKNOWN if a.t.resolved is None else NA, "not opted in"))
			continue
		if not module_on(a, module):
			rows.append(Row(rid, item, NA, f"{module} is off"))
			continue
		try:
			status, detail = EVALUATORS[rid](a)
		except GitHubError as e:
			status, detail = UNKNOWN, str(e).splitlines()[0]
		except FrappeNixError as e:
			status, detail = UNKNOWN, str(e).splitlines()[0]
		rows.append(Row(rid, item, status, detail))
	return rows


def markdown(repo_name: str, rows: list[Row]) -> str:
	out = [f"## {repo_name}", "", "| Row | Item | Status | Detail |", "|---|---|---|---|"]
	out += [f"| {r.id} | {r.item} | {r.status} | {r.detail.replace('|', '/')} |" for r in rows]
	return "\n".join(out) + "\n"


def file_issue(issue_repo: str, repo_name: str, body: str) -> None:
	"""Open or update ``scorecard: <repo>``. ``FRAPPE_NIX_ISSUE_TOKEN``, when set, is the token
	for these calls: the audit token is read-only, and the scorecard issues live in the
	repository that runs the audit (fleet-audit.yml passes its own GITHUB_TOKEN)."""
	title = f"scorecard: {repo_name}"
	token = os.environ.get("FRAPPE_NIX_ISSUE_TOKEN")
	saved = os.environ.get("GH_TOKEN")
	if token:
		os.environ["GH_TOKEN"] = token
	try:
		issues = github.get(f"repos/{issue_repo}/issues?state=open&per_page=100", paginate=True) or []
		found = [i for i in issues if i.get("title") == title]
		if found:
			github.request("PATCH", f"repos/{issue_repo}/issues/{found[0]['number']}", {"body": body})
		else:
			github.request("POST", f"repos/{issue_repo}/issues", {"title": title, "body": body})
	finally:
		if token:
			if saved is None:
				os.environ.pop("GH_TOKEN", None)
			else:
				os.environ["GH_TOKEN"] = saved


def run(args: argparse.Namespace) -> int:
	if args.issue and not args.issue_repo:
		raise ConfigError("--issue needs --issue-repo <owner/repo>")
	pairs, fleet = targets.entries(args.repo or [], args.all, args.fleet)
	results = []
	code = CLEAN
	try:
		for r, entry in pairs:
			t = targets.load(r, entry, fleet)
			rows = audit(Audit(t, args.against))
			results.append((r, rows))
			if any(row.status in (AMBER, RED) for row in rows):
				code = DRIFT
			if args.issue:
				file_issue(args.issue_repo, r, markdown(r, rows))
	finally:
		targets.cleanup()
	if args.format == "json":
		text = json.dumps(
			{"repos": [{"repo": r, "rows": [row.__dict__ for row in rows]} for r, rows in results]}, indent=2
		)
	else:
		text = "\n".join(markdown(r, rows) for r, rows in results)
	if args.out:
		Path(args.out).write_text(text + "\n")
	else:
		print(text)
	return code


def register(sub: argparse._SubParsersAction) -> None:
	p = sub.add_parser(
		"audit",
		help="the scorecard (rows A1-A17) for one or more repositories",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("--repo", action="append", metavar="owner/repo")
	p.add_argument("--all", action="store_true")
	p.add_argument("--fleet")
	p.add_argument("--format", choices=("md", "json"), default="md")
	p.add_argument("--out")
	p.add_argument("--issue", action="store_true", help="open or update `scorecard: <repo>` in --issue-repo")
	p.add_argument("--issue-repo", metavar="owner/repo")
	p.add_argument("--against", choices=("pinned", "latest"), default="pinned")
	p.set_defaults(func=run)
