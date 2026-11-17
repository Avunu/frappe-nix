"""Applying an auto-merge verdict under the safety rule (spec §4.5).

Auto-merge is only safe where required checks protect the integration branch, and only
works where the repository allows it; ``recommended`` turns ``dependabot.auto-merge`` on
but leaves ``repo-policy`` off, so neither can be assumed. Before any ``gh pr merge --auto``
the branch's rules and classic protection must require every enabled gate's ``ci / <gate>``
context. If they don't, or the merge call fails (auto-merge disabled for the repository),
the PR is labelled ``needs-review`` with a notice naming ``frappe-nix repo doctor``, and the
step still succeeds. Every label is created (``--force``, idempotent) before it is applied.
A PR labelled ``needs-review`` never keeps an auto-merge an earlier run enabled: a push by a
user or App with write access leaves GitHub's auto-merge on, so it is turned off first.
"""

import json

from frappe_nix_tools.ci import LABELS, notice, verdict
from frappe_nix_tools.common import github
from frappe_nix_tools.common.report import EnvError

DOCTOR = "run `frappe-nix repo doctor` to see which repository settings it needs"


def ensure_label(name: str, repo: str = "") -> None:
	color, description = LABELS[name]
	args = ["label", "create", name, "--color", color, "--description", description, "--force"]
	if repo:
		args += ["--repo", repo]
	github.cli(*args)


def auto_merge_enabled(pr_url: str) -> bool:
	"""Whether the PR already has auto-merge on, so a job that runs again (the weekly relock, the
	nightly screenshots) leaves it as it is rather than asking twice."""
	res = github.cli("pr", "view", pr_url, "--json", "autoMergeRequest")
	if res.code != 0 or not res.stdout.strip():
		return False
	try:
		return bool(json.loads(res.stdout).get("autoMergeRequest"))
	except (json.JSONDecodeError, AttributeError):
		return False


def disable(pr_url: str) -> None:
	"""Turn off the auto-merge an earlier run enabled on ``pr_url``, if it has one. A PR that now
	needs a person must not merge on its own once its checks pass; failing to turn it off is
	an error, not a notice."""
	res = github.cli("pr", "view", pr_url, "--json", "autoMergeRequest")
	if res.code != 0:
		raise EnvError(f"could not tell whether {pr_url} has auto-merge on: {res.stderr.strip()}")
	try:
		armed = bool(res.stdout.strip()) and bool(json.loads(res.stdout).get("autoMergeRequest"))
	except (json.JSONDecodeError, AttributeError) as e:
		raise EnvError(f"gh pr view {pr_url}: {e}") from e
	if not armed:
		return
	res = github.cli("pr", "merge", "--disable-auto", pr_url)
	if res.code != 0:
		raise EnvError(f"could not turn off auto-merge on {pr_url}: {res.stderr.strip()}")
	print(f"auto-merge turned off on {pr_url}")


def label(pr_url: str, name: str, repo: str = "") -> None:
	if name == "needs-review":
		disable(pr_url)
	ensure_label(name, repo)
	res = github.cli("pr", "edit", pr_url, "--add-label", name)
	if res.code != 0:
		notice(f"could not label {pr_url} {name}: {res.stderr.strip()}")


def enable(pr_url: str, repo: str, integration: str, gates: list[str], merge_method: str) -> str:
	"""``gh pr merge --auto`` when the gates protect ``integration``; else label ``needs-review``.

	Returns ``merging`` or ``needs-review``."""
	if not github.gates_protected(repo, integration, gates):
		label(pr_url, "needs-review", repo)
		contexts = ", ".join(github.gate_contexts(gates)) or "(no gates)"
		notice(
			f"not enabling auto-merge: {integration} does not require every enabled gate ({contexts});"
			f" labelled needs-review. {DOCTOR}"
		)
		return "needs-review"
	if auto_merge_enabled(pr_url):
		print(f"auto-merge is already enabled on {pr_url}")
		return "merging"
	res = github.cli("pr", "merge", "--auto", f"--{merge_method}", pr_url)
	if res.code != 0:
		label(pr_url, "needs-review", repo)
		notice(f"`gh pr merge --auto` failed ({res.stderr.strip()}); labelled needs-review. {DOCTOR}")
		return "needs-review"
	print(f"auto-merge enabled ({merge_method}) on {pr_url}")
	return "merging"


def close(pr_url: str, moved: list[str]) -> None:
	what = " and ".join(moved) or "frappe-nix or standards-profile"
	body = (
		f"{what} moves through `frappe-nix repo rollout` (or a manual `nix flake update` and"
		" `frappe-init --sync` commit): its bump can rewrite workflow files, which this workflow"
		" cannot push. Closing."
	)
	github.cli("pr", "comment", pr_url, "--body", body)
	github.cli("pr", "close", pr_url)
	print(f"closed {pr_url}: {what} moves through frappe-nix repo rollout")


def apply(
	decision: str,
	*,
	pr_url: str,
	repo: str,
	integration: str,
	gates: list[str],
	merge_method: str,
	relock_state: str = "",
	moved: list[str] | None = None,
) -> str:
	"""Act on ``decision`` (a ``verdict`` value); returns what was done."""
	if decision == verdict.RELOCK:
		decision = verdict.nix_verdict(relock_state)
		if decision == "wait":
			print("nix: relock-push enables auto-merge after it pushes the relock")
			return "wait"
	if decision == verdict.CLOSE:
		close(pr_url, moved or [])
		return "closed"
	if decision == verdict.AUTO:
		return enable(pr_url, repo, integration, gates, merge_method)
	label(pr_url, "needs-review", repo)
	print(f"labelled {pr_url} needs-review")
	return "needs-review"
