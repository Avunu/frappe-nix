"""The steps the reusable workflows run as ``frappe-nix ci <step>`` (spec §4.4, §4.5, §4.6, S29).

The workflows set up the job and call these; the decisions (what auto-merges, whether a
patch may be pushed, whether the release branch may move) live here, where they are unit
tested, rather than in YAML. Every GitHub call goes through ``common.github``.

- ``verdict``: the auto-merge decision table of §4.5, from fetch-metadata's outputs;
- ``automerge``: applies a verdict under the auto-merge safety rule (§4.5);
- ``relock``: the relock-plan input check, the relock patch and the workflow-path refusal (S29);
- ``release``: ``dispatch-ci``, the sweep and the release-branch fast-forward with its guard (§4.4);
- ``drift``: the nightly reminders that frappe-nix or the org profile moved (§4.6).
"""

import os

# The labels the jobs apply, created (idempotently) before each use (§4.5, §4.9).
LABELS = {
	"dependencies": ("0366d6", "Dependency updates"),
	"needs-review": ("d93f0b", "Not merged automatically; a person reviews it"),
	"screenshots": ("c5def5", "Screenshot refresh from nightly"),
}


def output(**values: str) -> None:
	"""Append ``key=value`` lines to ``$GITHUB_OUTPUT`` when it is set (a step's outputs)."""
	path = os.environ.get("GITHUB_OUTPUT")
	if not path:
		return
	with open(path, "a") as f:
		for key, value in values.items():
			f.write(f"{key}={value}\n")


def notice(message: str) -> None:
	print(f"::notice::{message}")


def error(message: str) -> None:
	print(f"::error::{message}")
