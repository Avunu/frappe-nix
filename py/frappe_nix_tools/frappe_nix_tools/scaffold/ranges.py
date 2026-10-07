"""The ``[tool.bench.frappe-dependencies]`` rules: compat C4 and frappe-listing L3 (spec §5.2).

One implementation for both, so the prek hook and the marketplace gate never disagree.
"""

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from frappe_nix_tools.common import known_apps
from frappe_nix_tools.common.report import ConfigError
from frappe_nix_tools.scaffold import hooks

BENCH_APPS = ("frappe", "erpnext", "hrms", "payments")


def problems(
	pyproject: dict,
	required_apps: list[str],
	major: int,
	*,
	siblings: list[dict] | None = None,
	known: dict | None = None,
) -> list[str]:
	"""Every way the app's declared Frappe dependencies break L3; empty when they hold.

	A required app's expected range is its resolved sibling's (the object form or a
	``known-apps`` entry may give one off the Frappe convention, §2.1), else the merged
	known apps' rule for its spelling."""
	by_name = {s["name"]: s for s in siblings or []}
	apps = known_apps.merged(known)
	out: list[str] = []
	deps = pyproject.get("tool", {}).get("bench", {}).get("frappe-dependencies")
	if not isinstance(deps, dict):
		return ["[tool.bench.frappe-dependencies] is missing"]
	if any(hooks.bare(r) == "frappe" for r in required_apps):
		out.append("hooks.required_apps must not name frappe")
	want = {"frappe": "frappe"}
	for spelling in required_apps:
		if hooks.bare(spelling) != "frappe":
			want[hooks.bare(spelling)] = spelling
	if set(deps) != set(want):
		out.append(
			f"[tool.bench.frappe-dependencies] keys are {sorted(deps)}, but must be exactly frappe plus"
			f" hooks.required_apps: {sorted(want)}"
		)
	for name, spelling in want.items():
		value = deps.get(name)
		if not isinstance(value, str):
			continue
		if name in by_name:
			expected = by_name[name]["range"]
		else:
			try:
				expected = known_apps.resolve(spelling, major, apps).range
			except ConfigError as e:
				out.append(str(e))
				continue
		if value != expected:
			out.append(f"frappe-dependencies.{name} = {value!r}, expected {expected!r}")
		try:
			spec = SpecifierSet(value)
		except InvalidSpecifier:
			out.append(
				f"frappe-dependencies.{name} = {value!r} is not a version range (use the comma form, S28)"
			)
			continue
		lower = [s for s in spec if s.operator in (">=", ">", "==", "~=")]
		convention = expected.startswith(f">={major}.")
		if convention and lower and not lower[0].version.split(".")[0] == str(major):
			out.append(f"frappe-dependencies.{name}: the lower bound's major must be {major}")
	named = []
	for dep in pyproject.get("project", {}).get("dependencies", []) or []:
		head = dep.strip().split(";")[0]
		for app in BENCH_APPS:
			if head.lower() == app or head.lower().startswith(tuple(f"{app}{c}" for c in " <>=!~[(")):
				named.append(app)
	if named:
		out.append(f"[project].dependencies must not name {', '.join(sorted(set(named)))}")
	return out
