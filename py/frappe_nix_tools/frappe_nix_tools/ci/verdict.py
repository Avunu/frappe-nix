"""The auto-merge decision table (spec §4.5), from ``dependabot/fetch-metadata`` v3's outputs.

========================  ==========================================  ===========================================
``package-ecosystem``     merged automatically (``auto``)              labelled ``needs-review`` (``review``)
========================  ==========================================  ===========================================
``github_actions``        the ``actions`` group; the ``docs-actions``  the ``docs-actions`` group (or a name matching
                          group too with                               ``docs-site.action-patterns``) without
                          ``docs-site.auto-merge-actions``             ``docs-site.auto-merge-actions``, and the
                                                                       ``pilot`` group (``frappe/pilot*``)
``npm_and_yarn``          minor and patch, in ``/`` and nested         ``directory == /docs-site``; any
                          frontends                                    ``version-update:semver-major``
``uv``                    minor and patch                              semver-major
``pre_commit``            the ``hooks`` group                          the ``test-utils`` group (anything touching
                                                                       ``agritheory/test_utils``)
``nix``                   ``relock``: decided by the relock state      a PR moving ``frappe-nix`` or
                          (``automerge``)                              ``standards-profile`` is ``close``\\ d
``submodules``            always                                       —
========================  ==========================================  ===========================================

Anything else (an ecosystem the table does not name, a frappe-nix bump that arrived despite
the ignore) is ``review``: nothing merges without a rule that says it may.
"""

from fnmatch import fnmatchcase

AUTO = "auto"
REVIEW = "review"
RELOCK = "relock"
CLOSE = "close"
VERDICTS = (AUTO, REVIEW, RELOCK, CLOSE)

MAJOR = "version-update:semver-major"
MINOR_PATCH = ("version-update:semver-minor", "version-update:semver-patch")
PILOT_PATTERNS = ("frappe/pilot*",)
TEST_UTILS = "agritheory/test_utils"
# Inputs that move only through `frappe-nix repo rollout` (S5, S38).
ROLLOUT_INPUTS = ("frappe-nix", "standards-profile")


def names(raw: str) -> list[str]:
	"""fetch-metadata's ``dependency-names``: comma-separated."""
	return [n.strip() for n in raw.split(",") if n.strip()]


def _matches(name: str, patterns: list[str] | tuple[str, ...]) -> bool:
	return any(fnmatchcase(name.lower(), p.lower()) for p in patterns)


def _minor_or_patch(update_type: str, group: str) -> bool:
	"""A minor or patch update; a grouped PR without an update type is one too, because the
	npm and uv groups take only minor and patch updates (§2.17)."""
	if update_type == MAJOR:
		return False
	return update_type in MINOR_PATCH or (not update_type and bool(group))


def decide(
	ecosystem: str,
	update_type: str = "",
	group: str = "",
	dependency_names: str = "",
	directory: str = "/",
	docs_patterns: list[str] | tuple[str, ...] = (),
	frappe_nix_repo: str = "",
	docs_auto_merge: bool = False,
) -> str:
	"""The verdict for one Dependabot PR: ``auto``, ``review``, ``relock`` or ``close``.

	``docs_auto_merge`` is ``docs-site.auto-merge-actions``: the docs tool's workflow is pinned
	to its releases and only checks, so its bump merges like any other action's."""
	deps = names(dependency_names)
	directory = "/" + directory.strip("/") if directory else "/"
	if ecosystem == "github_actions":
		if frappe_nix_repo and any(d.lower() == frappe_nix_repo.lower() for d in deps):
			return REVIEW  # ignored by dependabot.yml (S5); never merged if it arrives anyway
		if group == "pilot" or any(_matches(d, PILOT_PATTERNS) for d in deps):
			return REVIEW
		if group == "docs-actions" or any(_matches(d, list(docs_patterns)) for d in deps):
			return AUTO if docs_auto_merge else REVIEW
		if group == "actions" or (not group and deps):
			return AUTO
		return REVIEW
	if ecosystem == "npm_and_yarn":
		if directory == "/docs-site":
			return REVIEW
		return AUTO if _minor_or_patch(update_type, group) else REVIEW
	if ecosystem == "uv":
		return AUTO if _minor_or_patch(update_type, group) else REVIEW
	if ecosystem == "pre_commit":
		if group == "test-utils" or any(TEST_UTILS in d.lower() for d in deps):
			return REVIEW
		if group == "hooks" or (not group and deps):
			return AUTO
		return REVIEW
	if ecosystem == "nix":
		if any(d in ROLLOUT_INPUTS for d in deps):
			return CLOSE
		return RELOCK
	if ecosystem == "submodules":
		return AUTO
	return REVIEW


def nix_verdict(relock_state: str) -> str:
	"""A ``relock`` verdict once relock-plan has run: ``auto`` only when there was nothing to
	relock (``empty``); ``close`` when the PR moves a rollout input; otherwise the PR waits for
	relock-push, which enables auto-merge after its push (``patch``), so this job does nothing."""
	if relock_state == "empty":
		return AUTO
	if relock_state == "rollout-input-moved":
		return CLOSE
	return "wait"
