"""``frappe-nix listing`` (``frappe-listing``): registry readiness, publishing and the README blocks
(spec §2.19, §2.21, §5.2).

  frappe-listing check    [--release --tag vX.Y.Z] [--links-only] [--no-getapp] [--format text|json|github]
                          [--write-baseline]
  frappe-listing registry [--tag vX.Y.Z | --ref SHA] [--branch B] [--onboard] [--refresh]
                          [--fork OWNER/REPO] [--upstream OWNER/REPO] [--dry-run | --yes] [--no-check]
  frappe-listing readme   --write | --check

``check`` runs rules L1 to L12 and writes ``.dev-dist/marketplace/report.json``.
``--write-baseline`` records the registry semgrep findings found now as
``marketplace/semgrep-baseline.json`` (the adoption PR's starting point; it only shrinks
after that). ``registry`` opens or updates the registry pull request, and only with
``--yes``: without it, it stops before the push and prints what it would send. ``readme``
renders or checks the README blocks, as ``frappe-init --sync`` and ``--check`` do.

Module ``listing`` (``readme`` for ``readme``): with it off, each subcommand prints a notice
and exits 0. Exit codes per subcommand are in ``frappe_nix_tools.listing.check`` and
``.registry``; ``readme``'s are sync's (0, 1 drift, 2 a missing marker).
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from frappe_nix_tools.common import repo
from frappe_nix_tools.common.report import CLEAN, DRIFT, INVALID, ConfigError, worst
from frappe_nix_tools.listing import baseline, check, output, registry, target


def _profile(args: argparse.Namespace) -> Path | None:
	return Path(args.profile_path).resolve() if getattr(args, "profile_path", None) else None


def _target(args: argparse.Namespace, module: str = "listing") -> target.Target | None:
	t = target.load(repo.toplevel(Path.cwd()), _profile(args))
	if not t.modules.get(module):
		print(
			f"frappe-listing: notice: the {module} module is off for this app; nothing to do", file=sys.stderr
		)
		return None
	return t


def _write_baseline(t: target.Target, outcome: check.Outcome) -> int:
	"""Record the blocking findings found now; every other rule's result is printed, not changed."""
	previous = baseline.load(t.root)
	path = t.root / baseline.PATH
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(baseline.render(outcome.blocking, outcome.marketplace_rev, previous))
	print(
		f"frappe-listing: wrote {baseline.PATH} ({len(outcome.blocking)} finding(s)); give each entry a reason"
	)
	others = [r for r in outcome.results if r.rule != "L7" and r.level == "error"]
	for r in others:
		print(f"{r.rule} {r.level}: {r.path}: {r.message}")
	return DRIFT if others else CLEAN


def run_check(args: argparse.Namespace) -> int:
	if args.release and not args.tag:
		raise ConfigError("--release needs --tag vX.Y.Z")
	t = _target(args)
	if t is None:
		return CLEAN
	options = check.Options(
		release=args.release, tag=args.tag, links_only=args.links_only, getapp=not args.no_getapp
	)
	outcome = check.run(t, options)
	if args.write_baseline:
		return _write_baseline(t, outcome)
	extra = {"marketplace_rev": outcome.marketplace_rev, "app": t.name}
	report = t.root / check.REPORT_DIR / "report.json"
	report.parent.mkdir(parents=True, exist_ok=True)
	report.write_text(json.dumps(output.document(outcome.results, outcome.code, extra), indent=2) + "\n")
	if outcome.semgrep_log:
		(report.parent / "semgrep.log").write_text(outcome.semgrep_log)
	sys.stdout.write(output.render(outcome.results, args.format, outcome.code, extra))
	return outcome.code


def run_registry(args: argparse.Namespace) -> int:
	t = _target(args)
	if t is None:
		return CLEAN
	fork, upstream = registry.settings(t, args.fork, args.upstream)
	release_branch = args.branch or t.ctx.branches.release or t.ctx.branches.integration
	live = args.yes and not args.dry_run
	if args.no_check and live:
		raise ConfigError("--no-check is for dry runs: a registry pull request needs check --release to pass")
	sha, version = "", t.ctx.version or ""
	if not args.refresh:
		sha, tag = registry.resolve_commit(t, args.tag, args.ref, release_branch)
		version = (tag or "").removeprefix(
			t.cfg["releases"]["tag-prefix"] if t.modules.get("releases") else "v"
		) or version
		if not args.no_check:
			try:
				registry.gate(t, sha, tag)
			except registry.GateFailed as e:
				print(f"frappe-listing registry: {e}", file=sys.stderr)
				return DRIFT
	branch = registry.fork_branch(fork, t.name)
	with tempfile.TemporaryDirectory(prefix="frappe-listing-registry-") as tmp:
		clone = Path(tmp) / "registry"
		registry.fresh_clone(clone, upstream, branch)
		if args.refresh and not registry.behind(clone, fork, branch):
			print(
				f"frappe-listing registry: {fork}:{branch} is up to date with {upstream} main; nothing to do"
			)
			return CLEAN
		plan = registry.prepare(
			t,
			clone,
			fork=fork,
			upstream=upstream,
			release_branch=release_branch,
			commit=sha or None,
			version=version,
			onboarding=args.onboard,
		)
		print(f"registry: {upstream} main ← {fork}:{plan.branch}")
		print(f"title: {plan.title}")
		if not plan.diff:
			print("frappe-listing registry: the registry already lists every pending release; nothing to do")
			return CLEAN
		sys.stdout.write(plan.diff)
		if not live:
			print(
				"frappe-listing registry: dry run; nothing was pushed (pass --yes to push and open the pull request)"
			)
			if args.keep:
				target_dir = Path(args.keep)
				target_dir.mkdir(parents=True, exist_ok=True)
				for rel in (f"apps/{t.name}.json", "apps.json"):
					src = clone / rel
					if src.is_file():
						(target_dir / rel).parent.mkdir(parents=True, exist_ok=True)
						(target_dir / rel).write_bytes(src.read_bytes())
			return CLEAN
		url = registry.publish(plan, clone)
		print(url)
	return CLEAN


def run_readme(args: argparse.Namespace) -> int:
	t = _target(args, "readme")
	if t is None:
		return CLEAN
	from frappe_nix_tools.scaffold import engine

	plan = engine.build(t.root, only=["README.md"], profile_dir=_profile(args), phases=("b",))
	items = [i for i in plan.items if i.path == "README.md"]
	if not items:
		print(
			"frappe-listing: notice: README.md has no blocks to render (the app has no marketplace/listing.toml)",
			file=sys.stderr,
		)
		return CLEAN
	code = worst(*(i.code for i in items))
	if code >= INVALID:
		for i in items:
			if i.code >= INVALID:
				print(f"frappe-listing readme: {i.problem}", file=sys.stderr)
		return code
	item = items[0]
	if args.check:
		if item.code == DRIFT:
			sys.stdout.write(item.diff())
			print(
				"frappe-listing readme: README.md's blocks differ from what renders: run frappe-listing readme --write"
			)
		return item.code
	if item.code == DRIFT and item.wanted is not None:
		(t.root / "README.md").write_text(item.wanted)
		print("frappe-listing readme: wrote README.md's blocks")
	return CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"listing",
		help="registry readiness checks, the registry pull request and the README blocks (frappe-listing)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	sub = p.add_subparsers(dest="listing_command", metavar="<subcommand>", required=True)

	def common(q: argparse.ArgumentParser) -> None:
		q.add_argument("--profile-path", help="read the org profile from this directory (profile authors)")

	q = sub.add_parser("check", help="rules L1 to L12")
	q.add_argument(
		"--release", action="store_true", help="the release gate: L4's tag, L7's empty baseline, L10, L11"
	)
	q.add_argument("--tag", help="the release tag, vX.Y.Z (with --release)")
	q.add_argument("--links-only", action="store_true", help="only L10: website and documentation answer 200")
	q.add_argument("--no-getapp", action="store_true", help="skip L9 (pilot get-app); for local use only")
	q.add_argument("--format", choices=("text", "json", "github"), default="text")
	q.add_argument(
		"--write-baseline", action="store_true", help=f"record today's semgrep findings in {baseline.PATH}"
	)
	common(q)
	q.set_defaults(func=run_check)

	q = sub.add_parser("registry", help="open or update the registry pull request (only with --yes)")
	which = q.add_mutually_exclusive_group()
	which.add_argument("--tag", help="the release tag (default: the tag of __version__)")
	which.add_argument("--ref", help="the release commit")
	q.add_argument(
		"--branch", help="the branch the release is on (default: the release or integration branch)"
	)
	q.add_argument("--onboard", action="store_true", help="add the app's apps.json entry and apps/<app>.json")
	q.add_argument(
		"--refresh", action="store_true", help="rebuild the open pull request's branch on upstream main"
	)
	q.add_argument("--fork", help="the registry fork, <owner>/<repo> (default: listing.registry-fork)")
	q.add_argument("--upstream", help="the registry, <owner>/<repo> (default: listing.registry-upstream)")
	mode = q.add_mutually_exclusive_group()
	mode.add_argument(
		"--dry-run", action="store_true", help="stop before the push and print the diff (the default)"
	)
	mode.add_argument(
		"--yes", action="store_true", help="push to the fork and open or update the pull request"
	)
	q.add_argument("--no-check", action="store_true", help="skip check --release (dry runs and tests only)")
	q.add_argument(
		"--keep", metavar="DIR", help="dry run: copy the rendered apps.json and apps/<app>.json to DIR"
	)
	common(q)
	q.set_defaults(func=run_registry)

	q = sub.add_parser("readme", help="render (--write) or check (--check) the README blocks")
	mode = q.add_mutually_exclusive_group(required=True)
	mode.add_argument("--write", action="store_true")
	mode.add_argument("--check", action="store_true")
	common(q)
	q.set_defaults(func=run_readme)
