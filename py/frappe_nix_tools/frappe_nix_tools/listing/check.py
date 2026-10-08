"""``frappe-listing check``: every rule, in order, and the verdict (spec §5.2).

L1, L2, L8, L10 and L12 apply only when ``marketplace/listing.toml`` exists. L7 scans with the
registry's own semgrep check from the pinned ``frappe/marketplace``, L9 runs pilot's get-app
validator from the pinned ``frappe/pilot`` (when ``listing.getapp-check`` is on and
``--no-getapp`` is not given), L10 fetches the two URLs (``--links-only`` or ``--release``),
L11 reads the upstream registry (``--release``). ``--links-only`` runs L10 alone, as the
nightly ``links`` job does.

Exit codes: 0 pass (warnings allowed); 1 any error; 2 a config or parse error in
``listing.toml`` or ``pyproject.toml``; 3 an environment problem (network, a pin's narHash,
a pilot bench that could not be built). The JSON report goes to
``.dev-dist/marketplace/report.json``.
"""

from dataclasses import dataclass, field
from pathlib import Path

from frappe_nix_tools import icon
from frappe_nix_tools.common import flakelock, pins
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError, EnvError
from frappe_nix_tools.icon import svg
from frappe_nix_tools.listing import baseline, getapp, rules
from frappe_nix_tools.listing.rules import Result
from frappe_nix_tools.listing.target import Target
from frappe_nix_tools.scaffold import manifest

REPORT_DIR = ".dev-dist/marketplace"


@dataclass
class Options:
	release: bool = False
	tag: str | None = None
	links_only: bool = False
	getapp: bool = True


@dataclass
class Outcome:
	results: list[Result] = field(default_factory=list)
	semgrep_log: str = ""
	marketplace_rev: str = ""
	blocking: list[baseline.Hit] = field(default_factory=list)
	advisory: list[baseline.Hit] = field(default_factory=list)

	@property
	def code(self) -> int:
		return DRIFT if any(r.level == "error" for r in self.results) else CLEAN


def marketplace_tree(t: Target) -> tuple[Path, str]:
	"""The pinned ``frappe/marketplace`` tree and its rev (through the app's frappe-nix node)."""
	lock = t.root / "flake.lock"
	if not lock.is_file():
		raise EnvError("L7 needs flake.lock: the marketplace revision comes from frappe-nix's lock node")
	rev = flakelock.locked_pin(flakelock.load(lock), "marketplace").rev
	return pins.pin_path("marketplace", lock), rev


def l7(t: Target, outcome: Outcome, *, release: bool) -> None:
	tree, rev = marketplace_tree(t)
	outcome.marketplace_rev = rev
	floor = manifest.load().floors.get("uv", {}).get("semgrep", "1.179.0")
	blocking, advisory, log = baseline.scan(t.root, t.name, tree, floor)
	outcome.semgrep_log = log
	outcome.blocking, outcome.advisory = blocking, advisory
	entries = baseline.load(t.root)
	outcome.results += [Result("L7", "error", path, p) for path, p in baseline.compare(blocking, entries)]
	if release and entries:
		outcome.results.append(
			Result(
				"L7",
				"error",
				baseline.PATH,
				f"a release needs an empty baseline; it lists {len(entries)} finding key(s)",
			)
		)
	outcome.results += [
		Result("L7", "note", h.path, f"advisory {h.rule} at line {h.line}: {h.message}") for h in advisory
	]


def l8(t: Target) -> list[Result]:
	if not t.modules.get("icons"):
		return []
	try:
		found = icon.check(t, structural=True)
	except svg.IconError as e:
		raise ConfigError(str(e)) from e
	return [Result("L8", "error", path, problem) for path, problem in found]


def run(t: Target, options: Options) -> Outcome:
	"""Every rule that applies, in order."""
	outcome = Outcome()
	listing = t.listing
	listed = (t.root / rules.LISTING).is_file()
	if options.links_only:
		if listed:
			outcome.results += rules.l10(listing)
		return outcome
	if listed:
		outcome.results += rules.l1(listing)
		outcome.results += rules.l2(t)
	outcome.results += rules.l3(t)
	outcome.results += rules.l4(t, release=options.release, tag=options.tag)
	outcome.results += rules.l5(t)
	outcome.results += rules.l6(t)
	l7(t, outcome, release=options.release)
	if listed:
		outcome.results += l8(t)
	if options.getapp and t.cfg["listing"]["getapp-check"]:
		outcome.results += getapp.run(t, t.root / REPORT_DIR)
	if listed and options.release:
		outcome.results += rules.l10(listing)
	if options.release:
		outcome.results += rules.l11(t, rules.upstream_apps(t.cfg["listing"]["registry-upstream"]))
	if listed:
		outcome.results += rules.l12(t)
	return outcome
