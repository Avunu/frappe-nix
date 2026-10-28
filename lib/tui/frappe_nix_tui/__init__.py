"""The `frappe ui` store: python-devops-nix's devtui plugin (devtui.plugin), in a
bench (app mode has no apps of its own to add: siblings are flake inputs).

The catalog is frappe_nix_tools/data/frappe-apps.json (frappe-nix apps-catalog sync): the
marketplace registry's apps and the frappe organisation's unlisted ones. For the bench's
Frappe (apps/frappe's __version__) each app gets the branch `bench get-app --branch` takes:

1. its ``version-<major>`` branch, the convention frappe-nix pins siblings by;
2. else the branch of a registry release whose ``frappe_core`` range holds the version
   (stable releases first), which also says which apps it needs;
3. else, on a develop bench, a ``develop`` default branch.

Anything else is listed but not offered, with why. "Installed" is sites/apps.txt.

Nothing here changes the bench: an add is `frappe get-app` per app (what it needs first), a
removal `frappe uninstall-app` then `frappe remove-app`, run by the TUI from the bench root.
After an add the stack restarts from a re-evaluated shell, and its apps-reconcile task
installs the new apps on the site.

Standard library only: it runs on the TUI's interpreter, not the bench's.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devtui.catalog import Catalog, Item
from devtui.plugin import Closure, Field, Form, Step
from devtui.spec import Action, Spec

TIMEOUT = 6.0
_VERSION = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)
_SPEC = re.compile(r"^\s*(>=|<=|==|!=|~=|>|<)\s*(\S+)\s*$")


def version_key(text: str) -> tuple[int, ...]:
	"""A version as a sortable tuple: its numbers, then -1 for a pre-release
	(16.0.0-dev sorts below 16.0.0)."""
	main, _, pre = text.strip().lstrip("v").partition("-")
	numbers = [int(n) for n in re.findall(r"\d+", main)[:3]]
	numbers += [0] * (3 - len(numbers))
	return (*numbers, -1 if pre else 0)


def satisfies(version: str, spec: str) -> bool:
	"""Whether `version` meets every clause of a PEP 440-ish `spec`
	(">=15.116.1,<17.0.0"). A clause this does not read is ignored."""
	v = version_key(version)
	for clause in spec.split(","):
		m = _SPEC.match(clause)
		if not m:
			continue
		op, other = m.group(1), version_key(m.group(2))
		ok = {
			">=": v >= other,
			"<=": v <= other,
			">": v > other,
			# As PEP 440's: <17.0.0 is not met by 17.0.0's own pre-releases (develop).
			"<": v < other and not (v[3] < 0 and v[:3] == other[:3]),
			"==": v[:3] == other[:3],
			"!=": v[:3] != other[:3],
			"~=": v >= other and v[0] == other[0],
		}[op]
		if not ok:
			return False
	return True


@dataclass(frozen=True)
class Choice:
	"""The branch an app takes on this bench, and what it needs there."""

	branch: str = ""
	needs: tuple[str, ...] = ()
	why: str = ""


def choose(app: Mapping[str, Any], version: str | None) -> Choice:
	if version is None:
		branches = app.get("branches") or []
		return Choice(branch=branches[0] if branches else app.get("default_branch", ""))
	major = version_key(version)[0]
	develop = version_key(version)[3] < 0
	releases = sorted(
		app.get("releases") or [],
		key=lambda r: (r.get("channel") != "stable", [-n for n in version_key(str(r.get("version", "0")))]),
	)
	release = next((r for r in releases if satisfies(version, str(r.get("frappe_core", "")))), None)
	needs = tuple(sorted((release or {}).get("dependencies") or {}))
	if f"version-{major}" in (app.get("branches") or []) and not develop:
		return Choice(branch=f"version-{major}", needs=needs)
	if release is not None and release.get("branch"):
		return Choice(branch=str(release["branch"]), needs=needs)
	if develop and app.get("default_branch") == "develop":
		return Choice(branch="develop", needs=needs)
	have = ", ".join(app.get("branches") or []) or "no release branches"
	return Choice(why=f"nothing for Frappe {major} ({have})")


def frappe_version(bench: Path) -> str | None:
	try:
		text = (bench / "apps" / "frappe" / "frappe" / "__init__.py").read_text(encoding="utf-8")
	except OSError:
		return None
	m = _VERSION.search(text)
	return m.group(1) if m else None


def _fetch(url: str) -> str:
	try:
		with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
			return response.read(300_000).decode("utf-8", errors="replace")
	except (urllib.error.URLError, TimeoutError, OSError, ValueError):
		return ""


class FrappeProduct:
	def __init__(self, config: Mapping[str, Any], spec: Spec) -> None:
		self.cli = spec.cli
		self.bench = Path(spec.root or ".")
		data = json.loads(Path(config["catalog"]).read_text(encoding="utf-8"))
		self.apps: dict[str, dict[str, Any]] = {a["name"]: a for a in data["apps"]}
		self.version = frappe_version(self.bench)
		self.choices = {name: choose(app, self.version) for name, app in self.apps.items()}
		self._readmes: dict[str, str] = {}
		self._catalog: Catalog | None = None

	def catalog(self) -> Catalog:
		if self._catalog is None:
			items = []
			for name, app in self.apps.items():
				choice = self.choices[name]
				facts = [("Branch here", choice.branch)] if choice.branch else []
				if choice.needs:
					facts.append(("Needs", ", ".join(choice.needs)))
				if app.get("stars"):
					facts.append(("Stars", str(app["stars"])))
				if app.get("documentation"):
					facts.append(("Docs", f"<{app['documentation']}>"))
				if not app.get("listed"):
					facts.append(("Listing", "not in the marketplace registry"))
				items.append(
					Item(
						id=name,
						title=str(app.get("title") or name),
						summary=str(app.get("description") or ""),
						group=app["repo"].split("/")[0],
						categories=tuple(app.get("categories") or ()),
						tags=("application",) if app.get("category") == "Applications" else (),
						url=f"https://github.com/{app['repo']}",
						depends=choice.needs,
						available=bool(choice.branch),
						note=choice.why,
						rank=int(app.get("stars") or 0),
						facts=tuple(facts),
					)
				)
			self._catalog = Catalog(noun="app", group_label="Publisher", items=tuple(items))
		return self._catalog

	def installed(self) -> set[str]:
		try:
			text = (self.bench / "sites" / "apps.txt").read_text(encoding="utf-8")
		except OSError:
			return set()
		return {line.strip() for line in text.splitlines() if line.strip()}

	def _order(self, ids: Sequence[str]) -> tuple[list[str], list[str]]:
		"""(every app to add, what it needs first; the picked ones not here)."""
		installed = self.installed()
		order: list[str] = []
		missing: list[str] = []

		def visit(name: str, trail: tuple[str, ...]) -> None:
			if name in order or name in installed or name in trail:
				return
			if name not in self.apps or not self.choices[name].branch:
				if name not in missing:
					missing.append(name)
				return
			for need in self.choices[name].needs:
				visit(need, (*trail, name))
			order.append(name)

		for name in ids:
			visit(name, ())
		return order, missing

	def closure(self, ids: Sequence[str], bundles: Sequence[str]) -> Closure:
		order, missing = self._order(ids)
		brings = tuple(n for n in order if n not in ids)
		notes = [f"Frappe here: {self.version or 'unknown (apps/frappe has no __version__)'}"]
		notes += [f"{n}: {self.apps[n]['repo']} on {self.choices[n].branch}" for n in order]
		if brings:
			notes.append(f"Needed by what you picked, so added first: {', '.join(brings)}")
		notes.append("Then the stack restarts from a re-evaluated shell, which installs them on the site.")
		return Closure(brings=brings, notes=tuple(notes), unavailable=tuple(missing))

	def _source(self, name: str) -> str:
		"""What get-app is given: the frappe organisation's repository by its name
		(get-app's own short form), anything else as owner/repo."""
		owner, repo = self.apps[name]["repo"].split("/", 1)
		return repo if owner == "frappe" else f"{owner}/{repo}"

	def add(self, ids: Sequence[str], bundles: Sequence[str]) -> list[Step]:
		order, _ = self._order(ids)
		return [
			Step((self.cli, "get-app", "--branch", self.choices[n].branch, self._source(n)), f"add {n}")
			for n in order
		]

	def remove(self, ids: Sequence[str]) -> list[Step]:
		steps = []
		for name in ids:
			steps.append(Step((self.cli, "uninstall-app", name, "--yes"), f"uninstall {name} from the site"))
			steps.append(Step((self.cli, "remove-app", name), f"take {name} out of the bench"))
		return steps

	def after_add(self) -> list[Action]:
		return [
			Action(
				label="Rebuild & restart (installs on the site)",
				argv=("devtui:rebuild",),
				help="re-evaluate the shell and restart the stack; its apps-reconcile task installs the new apps",
			)
		]

	def forms(self) -> list[Form]:
		return [
			Form(
				label="Add from a Git URL",
				fields=(
					Field(
						"url",
						"URL, owner/repo, or a frappe app's name",
						"https://github.com/owner/app",
						required=True,
					),
					Field("branch", "Branch", "the remote's default when empty"),
				),
				argv=(self.cli, "get-app", "--branch={branch}", "{url}"),
				help="Any Frappe app in a git repository: added as a submodule and to the uv workspace.",
			)
		]

	def detail(self, item: Item) -> str:
		if item.id in self._readmes:
			return self._readmes[item.id]
		app = self.apps.get(item.id)
		branch = self.choices[item.id].branch or (app or {}).get("default_branch", "")
		text = ""
		if app and branch:
			for name in ("README.md", "readme.md", "README.rst"):
				body = _fetch(f"https://raw.githubusercontent.com/{app['repo']}/{branch}/{name}")
				if body:
					text = body[:30000] if name.endswith(".md") else "```text\n" + body[:30000] + "\n```"
					break
		self._readmes[item.id] = text
		return text


def load(config: Mapping[str, Any], spec: Spec) -> FrappeProduct:
	return FrappeProduct(config, spec)
