"""The pin of each reusable workflow call in an app's .github/workflows (``jobs.<id>.uses``).

A call is pinned by a full commit SHA, or, to a reusable workflow of an Avunu repository only,
by its exact release tag (``vX.Y.Z``). A branch is never a pin, and a local ``./`` call is not
pinned. Workflow steps and composite actions are zizmor's business, not this check's: they stay
on the SHA, and the ``unpinned-uses`` rules in ``.github/zizmor.yml`` say which Avunu reusable
workflows may name a tag (zizmor cannot tell a tag from a branch, so this check refuses the branch).
"""

from __future__ import annotations

import re

import yaml

SHA = re.compile(r"[0-9a-f]{40}")
RELEASE_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


def findings(text: str, where: str) -> list[str]:
	"""One line per reusable workflow call in TEXT that is not pinned as the convention allows."""
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
		owner = target.split("/", 1)[0]
		if SHA.fullmatch(pin) or (owner == "Avunu" and RELEASE_TAG.fullmatch(pin)):
			continue
		by = "full commit SHA or exact release tag (vX.Y.Z)" if owner == "Avunu" else "full commit SHA"
		out.append(f"{where}: jobs.{job_id}.uses {uses} is not pinned by a {by}")
	return out
