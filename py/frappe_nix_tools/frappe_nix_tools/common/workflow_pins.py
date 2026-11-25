"""The pin of each reusable workflow call in an app's .github/workflows (``jobs.<id>.uses``).

A call is pinned by a full commit SHA, or, to a reusable workflow that
``workflow-lint.release-tag-workflows`` lists, by its exact release tag (``vX.Y.Z``). That list
is an org's (a profile's ``[workflow-lint]`` or the app's ``[tool.frappe-nix.workflow-lint]``):
frappe-nix's built-in default is empty, so every call is SHA-pinned. A branch is never a pin, and
a local ``./`` call is not pinned. Workflow steps and composite actions are zizmor's business, not
this check's: they stay on the SHA, and ``.github/zizmor.yml`` renders the same list as
``ref-pin`` policies (zizmor cannot tell a tag from a branch, so this check refuses the branch).
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable

import yaml

SHA = re.compile(r"[0-9a-f]{40}")
RELEASE_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


def tag_allowed(target: str, release_tag_workflows: Iterable[str]) -> bool:
	"""Whether TARGET (``owner/repo/.github/workflows/x.yml``) may be pinned by a release tag: it
	matches an entry of ``workflow-lint.release-tag-workflows``, exactly or as a glob (``owner/*``)."""
	return any(fnmatch.fnmatchcase(target, pattern) for pattern in release_tag_workflows)


def findings(text: str, where: str, release_tag_workflows: Iterable[str] = ()) -> list[str]:
	"""One line per reusable workflow call in TEXT that is not pinned as the convention allows."""
	allowed = list(release_tag_workflows)
	try:
		doc = yaml.safe_load(text) or {}
	except yaml.YAMLError as e:
		return [f"{where}: {e}"]
	jobs = doc.get("jobs") if isinstance(doc, dict) else None
	out: list[str] = []
	for job_id, job in (jobs or {}).items():
		uses = job.get("uses") if isinstance(job, dict) else None
		if not isinstance(uses, str) or uses.startswith("./") or "/.github/workflows/" not in uses:
			continue
		target, _, pin = uses.rpartition("@")
		tag_ok = tag_allowed(target, allowed)
		if SHA.fullmatch(pin) or (tag_ok and RELEASE_TAG.fullmatch(pin)):
			continue
		by = "full commit SHA or exact release tag (vX.Y.Z)" if tag_ok else "full commit SHA"
		out.append(f"{where}: jobs.{job_id}.uses {uses} is not pinned by a {by}")
	return out
