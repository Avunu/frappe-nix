"""The auto-merge decision table (spec §4.5), from ``dependabot/fetch-metadata`` v3's outputs.

========================  ==========================================  ===========================================
``package-ecosystem``     merged automatically (``auto``)              labelled ``needs-review`` (``review``)
========================  ==========================================  ===========================================
``github_actions``        the ``actions`` group                        the ``docs-actions`` group (or a name matching
                                                                       ``docs-site.action-patterns``) and the
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

``dependabot.auto-merge-policy`` chooses between that table (``decision-table``, the default)
and ``all``: every ``review`` becomes ``auto``, so the required checks alone decide what merges,
majors, the ``docs-actions``, ``pilot`` and ``test-utils`` groups and ``/docs-site`` included.
Two verdicts stay as they are under ``all``: a nix bump still waits for its relock
(``relock``) or is closed when it moves a rollout input (``close``), and a frappe-nix pin
bump is still ``review``, because frappe-nix moves only through ``frappe-nix repo rollout`` (S5).
"""

from fnmatch import fnmatchcase

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


def _moves_frappe_nix(ecosystem: str, deps: list[str], frappe_nix_repo: str) -> bool:
	"""A github-actions bump of the frappe-nix pin: ignored by dependabot.yml (S5), and never
	merged if it arrives anyway, under either policy."""
	return (
		ecosystem == "github_actions"
		and bool(frappe_nix_repo)
		and any(d.lower() == frappe_nix_repo.lower() for d in deps)
	)


def _table(
	ecosystem: str,
	update_type: str,
	group: str,
	dependency_names: str,
	directory: str,
	docs_patterns: list[str] | tuple[str, ...],
	frappe_nix_repo: str,
) -> str:
	"""The §4.5 decision table itself."""
	deps = names(dependency_names)
	directory = "/" + directory.strip("/") if directory else "/"
	if ecosystem == "github_actions":
		if _moves_frappe_nix(ecosystem, deps, frappe_nix_repo):
			return REVIEW
		if group in ("docs-actions", "pilot"):
			return REVIEW
		if any(_matches(d, list(docs_patterns)) or _matches(d, PILOT_PATTERNS) for d in deps):
			return REVIEW
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
	"""The verdict for one Dependabot PR: ``auto``, ``review``, ``relock`` or ``close``."""
	decision = _table(
		ecosystem, update_type, group, dependency_names, directory, docs_patterns, frappe_nix_repo
	)
	if (
		policy == ALL
		and decision == REVIEW
		and not _moves_frappe_nix(ecosystem, names(dependency_names), frappe_nix_repo)
	):
		return AUTO
	return decision


def nix_verdict(relock_state: str) -> str:
	"""A ``relock`` verdict once relock-plan has run: ``auto`` only when there was nothing to
	relock (``empty``); ``close`` when the PR moves a rollout input; otherwise the PR waits for
	relock-push, which enables auto-merge after its push (``patch``), so this job does nothing."""
	if relock_state == "empty":
		return AUTO
	if relock_state == "rollout-input-moved":
		return CLOSE
	return "wait"
