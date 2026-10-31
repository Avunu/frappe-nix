"""``frappe-nix ci <step>``: the decisions the reusable workflows delegate (spec §4.4, §4.5, §4.6, S29).

These are the steps of ``app-release.yml``, ``app-deps.yml`` and ``app-nightly.yml`` that
decide something; the workflows only set up the job and call them. See
``frappe_nix_tools.ci`` for each. ``--repo`` defaults to ``$GITHUB_REPOSITORY``; the GitHub
steps use the token ``gh`` holds (``GH_TOKEN``). Where a step's ``--gates`` or release
parameters are omitted, they are read from the app in the current directory, as sync
renders them.

=====================  ============================================================  ==========================
step                   prints                                                        ``$GITHUB_OUTPUT``
=====================  ============================================================  ==========================
``verdict``            ``auto`` | ``review`` | ``relock`` | ``close``                ``verdict``
``automerge``          what it did                                                   ``result``
``enable-automerge``   what it did                                                   ``result``
``relock-inputs``      ``ok`` | ``rollout-input-moved``                              ``state``, ``moved``
``relock-patch``       ``empty=true|false``                                          ``empty``
``refuse-workflows``   the workflow paths (exit 1) or nothing                        —
``dispatch-ci``        each dispatch                                                 ``dispatched``
``sweep``              each dispatch                                                 ``dispatched``
``fast-forward``       ``<branch> = <tag> = <sha>``                                  —
``drift``              one line per issue                                            —
=====================  ============================================================  ==========================

Exit codes: 0, or 1 when a refusal or the fast-forward guard fails; 2 for a bad argument;
3 when GitHub or git cannot be reached.
"""

import argparse
import json
import os
from pathlib import Path

from frappe_nix_tools.ci import automerge, drift, error, output, release, relock, verdict
from frappe_nix_tools.common import appgates
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError


def _repo(args: argparse.Namespace) -> str:
	repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
	if not repo or "/" not in repo:
		raise ConfigError("give --repo <owner>/<repo> (or set GITHUB_REPOSITORY)")
	return repo


def _csv(text: str) -> list[str]:
	return [x.strip() for x in text.split(",") if x.strip()]


def _gates(args: argparse.Namespace) -> list[str]:
	if args.gates is not None:
		return _csv(args.gates)
	return appgates.load(Path.cwd()).gates


def _decide(args: argparse.Namespace) -> str:
	try:
		patterns = json.loads(args.docs_patterns or "[]")
	except json.JSONDecodeError as e:
		raise ConfigError(f"--docs-patterns is not a JSON list: {e}") from e
	if not isinstance(patterns, list):
		raise ConfigError("--docs-patterns must be a JSON list of glob patterns")
	return verdict.decide(
		args.ecosystem,
		update_type=args.update_type,
		group=args.dependency_group,
		dependency_names=args.dependency_names,
		directory=args.directory,
		docs_patterns=patterns,
		frappe_nix_repo=args.frappe_nix_repo,
		docs_auto_merge=args.docs_auto_merge,
	)


def run_verdict(args: argparse.Namespace) -> int:
	v = _decide(args)
	print(v)
	output(verdict=v)
	return CLEAN


def run_automerge(args: argparse.Namespace) -> int:
	if args.verdict:
		decision = args.verdict
	elif args.ecosystem:
		decision = _decide(args)
	else:
		raise ConfigError("give --verdict, or the fetch-metadata outputs (--ecosystem …)")
	result = automerge.apply(
		decision,
		pr_url=args.pr_url,
		repo=_repo(args),
		integration=args.integration,
		gates=_gates(args),
		merge_method=args.merge_method,
		relock_state=args.relock,
		moved=_csv(args.moved),
	)
	output(result=result)
	return CLEAN


def run_enable(args: argparse.Namespace) -> int:
	result = automerge.enable(args.pr_url, _repo(args), args.integration, _gates(args), args.merge_method)
	output(result=result)
	return CLEAN


def run_relock_inputs(args: argparse.Namespace) -> int:
	root = Path.cwd()
	base = relock.merge_base(root, args.base)
	head_text = (root / "flake.lock").read_text() if (root / "flake.lock").is_file() else ""
	if not head_text:
		raise ConfigError("no flake.lock in the current directory")
	moved = relock.moved_inputs(relock.lock_at(root, base), head_text)
	state = "rollout-input-moved" if moved else "ok"
	print(state)
	if moved:
		print(f"moved: {', '.join(moved)} (these move through frappe-nix repo rollout)")
	output(state=state, moved=",".join(moved))
	return CLEAN


def run_relock_patch(args: argparse.Namespace) -> int:
	empty, refused = relock.make_patch(Path.cwd(), Path(args.out))
	if refused:
		error(f"relock would change a workflow ({', '.join(refused)}); run frappe-nix repo rollout")
		return DRIFT
	print(f"empty={'true' if empty else 'false'}")
	output(empty="true" if empty else "false")
	return CLEAN


def run_refuse(args: argparse.Namespace) -> int:
	if not args.cached and not args.base:
		raise ConfigError("give --base <ref>, or --cached")
	paths = relock.workflow_paths(relock.changed(Path.cwd(), args.base, args.cached))
	if paths:
		for p in paths:
			error(f"refusing to push a change to {p}: GITHUB_TOKEN cannot push workflow files (S29)")
		return DRIFT
	return CLEAN


def _contexts(pairs: list[str]) -> tuple[tuple[str, str], ...]:
	out = []
	for pair in pairs:
		context, sep, workflow = pair.partition("=")
		if not sep or not context.strip() or not workflow.strip():
			raise ConfigError(f"--context {pair!r}: give <check name>=<workflow file>")
		out.append((context.strip(), workflow.strip()))
	return tuple(out)


def run_dispatch(args: argparse.Namespace) -> int:
	contexts = _contexts(args.context) if args.context else release.CI_CONTEXTS
	done = release.dispatch_ci(_repo(args), args.integration, args.package, contexts)
	output(dispatched=",".join(done))
	return CLEAN


def run_sweep(args: argparse.Namespace) -> int:
	done = release.sweep(_repo(args))
	output(dispatched=",".join(done))
	return CLEAN


def run_fast_forward(args: argparse.Namespace) -> int:
	scheme, prefix, major = args.version_scheme, args.tag_prefix, args.frappe_major
	if scheme is None or prefix is None or (scheme == "frappe-major" and major is None):
		app = appgates.load(Path.cwd())
		releases = app.cfg.get("releases", {})
		scheme = scheme or releases.get("version-scheme", "semver")
		prefix = releases.get("tag-prefix", "v") if prefix is None else prefix
		major = int(app.cfg["frappe-major"]) if major is None else major
	if not args.release_branch:
		raise ConfigError("--release-branch is empty: there is no release branch under main+tags")
	return release.FastForward(
		repo=_repo(args),
		branch=args.release_branch,
		sha=args.sha,
		tag=args.tag,
		version_scheme=scheme,
		tag_prefix=prefix,
		frappe_major=major or 0,
		pilot_assets=args.pilot_assets,
		dry_run=args.dry_run,
	).run()


def run_drift(args: argparse.Namespace) -> int:
	summary = Path(args.summary_file).read_text() if args.summary_file else ""
	done = drift.run(Path.cwd(), _repo(args), args.latest, summary, check_profile=not args.no_profile)
	for what, state in done.items():
		print(f"{what}: {state}")
	return CLEAN


def _metadata_args(p: argparse.ArgumentParser) -> None:
	p.add_argument("--ecosystem", default="", help="fetch-metadata package-ecosystem")
	p.add_argument("--update-type", default="", help="fetch-metadata update-type")
	p.add_argument("--dependency-group", default="", help="fetch-metadata dependency-group")
	p.add_argument("--dependency-names", default="", help="fetch-metadata dependency-names")
	p.add_argument("--directory", default="/", help="fetch-metadata directory")
	p.add_argument("--docs-patterns", default="[]", help="docs-site.action-patterns, as a JSON list")
	p.add_argument(
		"--docs-auto-merge",
		action="store_true",
		help="docs-site.auto-merge-actions: merge the docs-actions group like the actions group",
	)
	p.add_argument("--frappe-nix-repo", default="", help="the <owner>/<repo> the app locks frappe-nix from")


def _merge_args(p: argparse.ArgumentParser) -> None:
	p.add_argument("--pr-url", required=True)
	p.add_argument("--repo", default="")
	p.add_argument("--integration", required=True, help="branches.integration")
	p.add_argument("--gates", default=None, help="the enabled gates, comma-separated (default: the app's)")
	p.add_argument("--merge-method", default="squash", choices=("squash", "merge", "rebase"))


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"ci",
		help="the steps the reusable workflows run (auto-merge, relock, release, drift)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	sub = p.add_subparsers(dest="step", metavar="<step>", required=True)

	q = sub.add_parser("verdict", help="the auto-merge verdict for a Dependabot PR (§4.5)")
	_metadata_args(q)
	q.set_defaults(func=run_verdict)

	q = sub.add_parser("automerge", help="apply the auto-merge verdict under the safety rule (§4.5)")
	_merge_args(q)
	_metadata_args(q)
	q.add_argument("--verdict", choices=verdict.VERDICTS, default="")
	q.add_argument(
		"--relock", default="", help="relock-plan's state for nix: empty, patch or rollout-input-moved"
	)
	q.add_argument("--moved", default="", help="the rollout inputs a nix PR moves, comma-separated")
	q.set_defaults(func=run_automerge)

	q = sub.add_parser("enable-automerge", help="gh pr merge --auto under the safety rule (§4.5)")
	_merge_args(q)
	q.set_defaults(func=run_enable)

	q = sub.add_parser("relock-inputs", help="whether a nix PR moves frappe-nix or standards-profile")
	q.add_argument("--base", required=True, help="the PR's base SHA (the merge-base with HEAD is used)")
	q.set_defaults(func=run_relock_inputs)

	q = sub.add_parser("relock-patch", help="stage everything and write the patch, refusing workflows")
	q.add_argument("--out", required=True)
	q.set_defaults(func=run_relock_patch)

	q = sub.add_parser("refuse-workflows", help="exit 1 when a change touches .github/workflows/ (S29)")
	q.add_argument("--base", default="", help="compare <base>..HEAD")
	q.add_argument("--cached", action="store_true", help="compare the index with HEAD (or --base)")
	q.set_defaults(func=run_refuse)

	q = sub.add_parser("dispatch-ci", help="dispatch CI on the open release PR (§4.4)")
	q.add_argument("--repo", default="")
	q.add_argument("--integration", required=True)
	q.add_argument("--package", default="", help="release-please's component name")
	q.add_argument(
		"--context",
		action="append",
		default=[],
		metavar="CHECK=WORKFLOW",
		help="a required check and the workflow that reports it (repeatable; default:"
		" 'ci / lint=ci.yml' and 'ci / pr-policy=pr-policy.yml')",
	)
	q.set_defaults(func=run_dispatch)

	q = sub.add_parser("sweep", help="dispatch CI on bot PRs whose head has no ci runs (§4.5)")
	q.add_argument("--repo", default="")
	q.set_defaults(func=run_sweep)

	q = sub.add_parser("fast-forward", help="move the release branch to the release and guard it (§4.4)")
	q.add_argument("--repo", default="")
	q.add_argument("--release-branch", required=True)
	q.add_argument("--sha", required=True)
	q.add_argument("--tag", required=True)
	q.add_argument("--version-scheme", choices=("semver", "frappe-major"), default=None)
	q.add_argument("--tag-prefix", default=None)
	q.add_argument("--frappe-major", type=int, default=None)
	q.add_argument("--pilot-assets", action="store_true")
	q.add_argument("--dry-run", action="store_true", help="read, but make no write call")
	q.set_defaults(func=run_fast_forward)

	q = sub.add_parser("drift", help="keep the frappe-nix and profile update issues (§4.6)")
	q.add_argument("--repo", default="")
	q.add_argument("--latest", required=True, help="the latest frappe-nix release, vX.Y.Z")
	q.add_argument("--summary-file", default="", help="`frappe-nix sync --check` output to quote")
	q.add_argument("--no-profile", action="store_true", help="skip the standards-profile comparison")
	q.set_defaults(func=run_drift)
