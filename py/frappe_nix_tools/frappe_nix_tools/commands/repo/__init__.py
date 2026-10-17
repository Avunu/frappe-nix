"""``frappe-nix repo apply|audit|rollout|doctor``: the repository-level tools (spec §5.6-§5.8, S40).

``apply`` sets an app's repository settings, labels and rulesets from ``repo-policy/``
(module ``repo-policy``); ``doctor`` reports which settings the enabled modules rely on;
``audit`` is the scorecard (rows A1-A17); ``rollout`` moves apps to a frappe-nix or org
profile release. Each takes ``<owner/repo>…`` or ``--all --fleet <file>``, the fleet list an
org profile repository keeps.
"""

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
	from frappe_nix_tools.commands.repo import apply, audit, doctor, rollout

	p = subparsers.add_parser(
		"repo",
		help="repository settings, rulesets, the scorecard and rollouts (apply, audit, rollout, doctor)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	sub = p.add_subparsers(dest="repo_command", metavar="<subcommand>", required=True)
	for module in (apply, audit, rollout, doctor):
		module.register(sub)
