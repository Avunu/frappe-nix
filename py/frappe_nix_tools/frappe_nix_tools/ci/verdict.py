"""The auto-merge decision table (spec §4.5), from ``dependabot/fetch-metadata`` v3's outputs.

========================  ==========================================  ===========================================
``package-ecosystem``     merged automatically (``auto``)              labelled ``needs-review`` (``review``)
========================  ==========================================  ===========================================
``github_actions``        never                                        every one, whatever its group (``actions``,
                                                                       ``docs-actions``, ``pilot``) or update type
``npm_and_yarn``          minor and patch, in ``/`` and nested         ``directory == /docs-site``; any
                          frontends                                    ``version-update:semver-major``
``uv``                    minor and patch                              semver-major
``pre_commit``            the ``hooks`` group                          the ``test-utils`` group (anything touching
                                                                       ``agritheory/test_utils``)
``nix``                   ``relock``: decided by the relock state      a PR moving ``frappe-nix`` or
                          (``automerge``)                              ``standards-profile`` is ``close``\\ d
``submodules``            always                                       —
========================  ==========================================  ===========================================

Anything else (an ecosystem the table does not name) is ``review``: nothing merges without a
rule that says it may.

A ``github_actions`` bump is never merged automatically: the action or reusable workflow it
moves runs in the repository's workflows, and some of their jobs hold the GitHub App's key
(``ci.github-app``, §4.1), which an action step can read. Required checks cannot tell a
compromised release of an action from a good one, so a person reviews each such bump. That
covers the frappe-nix pin too, which the ``github-actions`` entry ignores and which moves only
through ``frappe-nix repo rollout`` (S5).

``dependabot.auto-merge-policy`` chooses between that table (``decision-table``, the default)
and ``all``: every ``review`` becomes ``auto``, so the required checks alone decide what merges,
majors, the ``test-utils`` group and ``/docs-site`` included. Three verdicts stay as they are
under ``all``: a nix bump still waits for its relock (``relock``) or is closed when it moves a
rollout input (``close``), and a ``github_actions`` bump is still ``review``.
"""

AUTO = "auto"
REVIEW = "review"
RELOCK = "relock"
CLOSE = "close"
VERDICTS = (AUTO, REVIEW, RELOCK, CLOSE)

DECISION_TABLE = "decision-table"
ALL = "all"
POLICIES = (DECISION_TABLE, ALL)

MAJOR = "version-update:semver-major"
MINOR_PATCH = ("version-update:semver-minor", "version-update:semver-patch")
# Ecosystems that stay review under the `all` policy: what they move runs next to the App's key.
ALWAYS_REVIEW = ("github_actions",)
TEST_UTILS = "agritheory/test_utils"
# Inputs that move only through `frappe-nix repo rollout` (S5, S38).
ROLLOUT_INPUTS = ("frappe-nix", "standards-profile")


def names(raw: str) -> list[str]:
	"""fetch-metadata's ``dependency-names``: comma-separated."""
	return [n.strip() for n in raw.split(",") if n.strip()]


def _minor_or_patch(update_type: str, group: str) -> bool:
	"""A minor or patch update; a grouped PR without an update type is one too, because the
	npm and uv groups take only minor and patch updates (§2.17)."""
	if update_type == MAJOR:
		return False
	return update_type in MINOR_PATCH or (not update_type and bool(group))


def _table(ecosystem: str, update_type: str, group: str, dependency_names: str, directory: str) -> str:
	"""The §4.5 decision table itself."""
	deps = names(dependency_names)
	directory = "/" + directory.strip("/") if directory else "/"
	if ecosystem == "github_actions":
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


def decide(
	ecosystem: str,
	update_type: str = "",
	group: str = "",
	dependency_names: str = "",
	directory: str = "/",
	docs_patterns: list[str] | tuple[str, ...] = (),
	frappe_nix_repo: str = "",
	policy: str = DECISION_TABLE,
) -> str:
	"""The verdict for one Dependabot PR: ``auto``, ``review``, ``relock`` or ``close``.

	``docs_patterns`` and ``frappe_nix_repo`` (``docs-site.action-patterns`` and the app's
	frappe-nix repository) told ``github_actions`` bumps apart; every one is ``review`` now, so
	they decide nothing and stay for the callers that pass them."""
	del docs_patterns, frappe_nix_repo
	decision = _table(ecosystem, update_type, group, dependency_names, directory)
	if policy == ALL and decision == REVIEW and ecosystem not in ALWAYS_REVIEW:
		return AUTO
	return decision


def nix_verdict(relock_state: str, locks: str = "") -> str:
	"""A ``relock`` verdict once relock-plan has run: ``close`` when the PR moves a rollout
	input; when there was nothing to relock (``empty``), ``auto`` only if ``frappe-nix ci
	verify-relock`` found the PR's lock files ``ok`` against its merge-base (a relock pushed
	earlier and held for review stays held), else ``review``; otherwise the PR waits for
	relock-push, which checks the lock files and enables auto-merge after its push (``patch``),
	so this job does nothing."""
	if relock_state == "empty":
		return AUTO if locks == "ok" else REVIEW
	if relock_state == "rollout-input-moved":
		return CLOSE
	return "wait"
