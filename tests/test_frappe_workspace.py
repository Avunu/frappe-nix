"""Self-contained tests for lib/frappe-workspace.py's bench-dev group.

The root's `bench-dev` dependency group is what `bench setup requirements --dev`
installs: every workspace member's [tool.bench.dev-dependencies]. The
assertions are about what matters if it is wrong: a specifier read the way
bench reads it, two apps naming one package both kept to theirs, the dev
group's own pins left alone, an app's removal taking its entries with it, a
root whose apps declare nothing rendered byte for byte as before, and an app
that has not opted in to the app standards (S35) keeping the root its
committed lock was resolved for.

Run directly (``python test_frappe_workspace.py <path-to-frappe-workspace.py>``,
with tomlkit importable) or via ``nix flake check``.
"""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "lib", "frappe-workspace.py")
TEMPLATE = Path(
	sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "templates", "bench", "pyproject.toml")
)

FAILURES = []


def check(label, condition, detail=""):
	if condition:
		print(f"ok   {label}")
	else:
		print(f"FAIL {label} {detail}")
		FAILURES.append(label)


def load():
	spec = importlib.util.spec_from_file_location("frappe_workspace", TOOL)
	assert spec and spec.loader, TOOL
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


fw = load()

# The bench template with its tokens filled the way lib/app-workspace.nix
# fills them, so it is the root a bench or an app-mode workspace starts from.
RENDERED = (
	TEMPLATE.read_text()
	.replace("@PROJECT_NAME@", "fixture-bench")
	.replace("@REQUIRES_PYTHON@", ">=3.14")
	.replace('"@OVERRIDES@"', "[]")
	.replace("@PYTAG@", "py314")
	.replace("@PYVER@", "3.14")
)

# frappe 16's table as it ships (frappe/pyproject.toml), Faker's capital and all.
FRAPPE_DEV = """
[tool.bench.dev-dependencies]
coverage = "~=7.10.0"
Faker = "~=18.10.1"
pyngrok = "~=7.5.0"
unittest-xml-reporting = "~=3.2.0"
watchdog = "~=6.0.0"
hypothesis = "~=6.77.0"
responses = "==0.23.1"
freezegun = "~=1.5.5"
"""


def bench(apps):
	"""A bench root in a fresh directory: the rendered template and apps/<name>/pyproject.toml each.

	``apps`` maps a name to the text after [project], or None for a
	directory without a pyproject.toml (a submodule not checked out).
	"""
	root = Path(tempfile.mkdtemp())
	(root / "pyproject.toml").write_text(RENDERED)
	for name, extra in apps.items():
		app = root / "apps" / name
		app.mkdir(parents=True)
		if extra is not None:
			(app / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n{extra}')
	return root


ERR = io.StringIO()


def run(*argv):
	"""The tool's main() with ``argv``: (exit status, what it printed); its stderr in ERR."""
	saved = sys.argv
	out = io.StringIO()
	ERR.seek(0)
	ERR.truncate()
	sys.argv = ["frappe-nix-workspace", *argv]
	try:
		with contextlib.redirect_stdout(out), contextlib.redirect_stderr(ERR):
			fw.main()
	except SystemExit as e:
		return e.code or 0, out.getvalue()
	finally:
		sys.argv = saved
	return 0, out.getvalue()


def groups(root):
	return tomllib.loads((root / "pyproject.toml").read_text()).get("dependency-groups", {})


def lock_with_root(path, dev=(), bench_dev=()):
	"""A uv.lock whose virtual root lists ``dev`` and ``bench_dev`` in [package.metadata.requires-dev]."""
	lines = [
		'version = 1\n\n[[package]]\nname = "fixture-bench"\nversion = "0.1.0"\nsource = { virtual = "." }\n'
	]
	lines.append("\n[package.metadata.requires-dev]\n")
	lines.append("dev = [" + ", ".join(f'{{ name = "{n}" }}' for n in dev) + "]\n")
	if bench_dev:
		lines.append("bench-dev = [" + ", ".join(f'{{ name = "{n}" }}' for n in bench_dev) + "]\n")
	Path(path).write_text("".join(lines))
	return path


# ── an entry, as bench reads it ────────────────────────────────────────────

for key, value, want in (
	("freezegun", "~=1.5.5", ("freezegun", frozenset(), "~=1.5.5", "")),
	("responses", "==0.23.1", ("responses", frozenset(), "==0.23.1", "")),
	("hypothesis", "6.77.0", ("hypothesis", frozenset(), "==6.77.0", "")),
	("Faker", ">=18,<19", ("faker", frozenset(), ">=18,<19", "")),
	("pyngrok", "<8", ("pyngrok", frozenset(), "<8", "")),
	("watchdog", "", ("watchdog", frozenset(), "", "")),
	("watchdog", "*", ("watchdog", frozenset(), "", "")),
	# A marker after the `;`, which bench passes to pip as written.
	("z", "==1; python_version<'3.12'", ("z", frozenset(), "==1", "python_version<'3.12'")),
	("z", "1 ; python_version<'3.12'", ("z", frozenset(), "==1", "python_version<'3.12'")),
	# Extras in the name (cloud_storage's moto[s3]).
	("moto[s3]", "~=4.1.6", ("moto", frozenset({"s3"}), "~=4.1.6", "")),
	("Moto [S3, ec2]", "", ("moto", frozenset({"s3", "ec2"}), "", "")),
	# Nothing a requirement can be written from.
	("y", {"version": "1"}, None),
	("y", 1, None),
	("x @ https://example.invalid/x.whl", "", None),
	("_private", "1", None),
	("moto[s3", "1", None),
	("moto[s/3]", "1", None),
	("x", "@ https://example.invalid/x.whl", None),
	("x", "1.0 || 2.0", None),
):
	got = fw.bench_dev_entry(key, value)
	check(f"bench_dev_entry({key!r}, {value!r}) is {want}", got == want, f"got {got!r}")


# ── the group, from the members ────────────────────────────────────────────

root = bench(
	{
		"frappe": FRAPPE_DEV,
		"erpnext": "",
		"my_app": '[tool.bench.dev-dependencies]\nfreezegun = "1.5.5"\nfaker = "~=18.10.1"\ntime-machine = ""\n',
	}
)
status, _ = run("sync-apps", "--pyproject", str(root / "pyproject.toml"), "frappe", "erpnext", "my_app")
got = groups(root).get("bench-dev")
check(
	"sync-apps writes every member's [tool.bench.dev-dependencies], sorted, normalized",
	status == 0
	and got
	== [
		"faker~=18.10.1",
		"freezegun~=1.5.5,==1.5.5",
		"hypothesis~=6.77.0",
		"pyngrok~=7.5.0",
		"time-machine",
		"watchdog~=6.0.0",
	],
	f"got {got!r}",
)
template_dev = tomllib.loads(RENDERED)["dependency-groups"]["dev"]
check(
	"the dev group is left as the template has it, and keeps its own pins",
	groups(root)["dev"] == template_dev,
	f"got {groups(root)['dev']!r}",
)
check(
	"a package the dev group already names (coverage, responses, unittest-xml-reporting) is left to it",
	not {"coverage", "responses", "unittest-xml-reporting"} & {fw.requirement_name(r) for r in got or []},
)
check(
	"the root's [project].dependencies (what production installs) are untouched",
	tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
	== tomllib.loads(RENDERED)["project"]["dependencies"],
)

before = (root / "pyproject.toml").read_text()
status, out = run("ensure-root", "--pyproject", str(root / "pyproject.toml"))
check(
	"ensure-root over a root already in step changes nothing and says nothing",
	status == 0 and (root / "pyproject.toml").read_text() == before and out == "",
	out,
)

# A member the bench lost track of on disk: what it wants is unknown, so the
# group stays as it was rather than being rewritten without it.
(root / "apps" / "my_app" / "pyproject.toml").rename(root / "apps" / "my_app.toml")
(root / "apps" / "my_app").rmdir()
status, _ = run("ensure-root", "--pyproject", str(root / "pyproject.toml"))
check(
	"a member not on disk leaves the group as it was",
	(root / "pyproject.toml").read_text() == before,
)
check(
	"…and says so",
	"apps/my_app is not on disk" in ERR.getvalue() and ".bench-dev left as it was" in ERR.getvalue(),
	ERR.getvalue(),
)
# A submodule registered but not checked out: its directory is there, empty.
(root / "apps" / "my_app").mkdir()
status, _ = run("ensure-root", "--pyproject", str(root / "pyproject.toml"))
check(
	"a member with no pyproject.toml (a submodule not checked out) leaves the group as it was",
	(root / "pyproject.toml").read_text() == before
	and "apps/my_app has no readable pyproject.toml" in ERR.getvalue(),
	ERR.getvalue(),
)
(root / "apps" / "my_app").rmdir()

# bench-remove-app takes the directory out first, then the member.
status, out = run("remove-app", "--pyproject", str(root / "pyproject.toml"), "--app", "my_app")
got = groups(root).get("bench-dev")
check(
	"remove-app takes the app's entries with it",
	got == ["faker~=18.10.1", "freezegun~=1.5.5", "hypothesis~=6.77.0", "pyngrok~=7.5.0", "watchdog~=6.0.0"],
	f"got {got!r}",
)

status, out = run("remove-app", "--pyproject", str(root / "pyproject.toml"), "--app", "frappe")
check(
	"with no member declaring any, the group is gone",
	"bench-dev" not in groups(root),
	f"got {groups(root)!r}",
)

# add-app, as bench-get-app registers an app.
(root / "apps" / "frappe2").mkdir()
(root / "apps" / "frappe2" / "pyproject.toml").write_text(f'[project]\nname = "frappe2"\n{FRAPPE_DEV}')
status, out = run("add-app", "--pyproject", str(root / "pyproject.toml"), "--app", "frappe2")
check(
	"add-app writes the new member's entries",
	"freezegun~=1.5.5" in groups(root).get("bench-dev", []),
	f"got {groups(root)!r}",
)


# ── two apps' entries for one package ─────────────────────────────────────

root = bench(
	{
		"frappe": FRAPPE_DEV,
		"one": """[tool.bench.dev-dependencies]
z = "==1; python_version<'3.12'"
"moto[s3]" = "~=4.1.6"
"pytest[xdist]" = "*"
y = { version = "1" }
"x @ https://example.invalid/x.whl" = ""
""",
		"two": '[tool.bench.dev-dependencies]\nz = ">=0.5"\nmoto = "~=4.1"\n"Moto[EC2]" = ""\n',
	}
)
status, _ = run("sync-apps", "--pyproject", str(root / "pyproject.toml"), "frappe", "one", "two")
got = groups(root).get("bench-dev")
check(
	"one package under two markers stays two entries, each a valid requirement",
	got is not None and "z>=0.5" in got and "z==1; python_version<'3.12'" in got,
	f"got {got!r}",
)
check(
	"one package with and without extras is one entry: the extras united, both pins kept",
	got is not None and "moto[ec2,s3]~=4.1.6,~=4.1" in got and not any(r.startswith("moto~") for r in got),
	f"got {got!r}",
)
check(
	"a package the dev group names is left to it, extras or not (pytest[xdist])",
	got is not None and not any(fw.requirement_name(r) == "pytest" for r in got),
	f"got {got!r}",
)
check(
	"an entry no requirement can be written from is skipped, with a warning naming it",
	got is not None
	and not any(fw.requirement_name(r) in ("x", "y") for r in got)
	and "apps/one: [tool.bench.dev-dependencies] 'y'" in ERR.getvalue()
	and "'x @ https://example.invalid/x.whl'" in ERR.getvalue(),
	f"got {got!r}, stderr {ERR.getvalue()!r}",
)

# Two apps' pins that cannot both hold: a pin in the dev group settles it,
# since bench-dev leaves a package dev names to that pin.
root = bench({"frappe": FRAPPE_DEV, "old": '[tool.bench.dev-dependencies]\nfreezegun = "1.2.2"\n'})
run("sync-apps", "--pyproject", str(root / "pyproject.toml"), "frappe", "old")
check(
	"two apps' conflicting pins are both kept (uv lock then says so)",
	"freezegun~=1.5.5,==1.2.2" in groups(root).get("bench-dev", []),
	f"got {groups(root)!r}",
)
text = (
	(root / "pyproject.toml")
	.read_text()
	.replace('    "coverage>=7.10",', '    "coverage>=7.10",\n    "freezegun~=1.5",', 1)
)
(root / "pyproject.toml").write_text(text)
run("ensure-root", "--pyproject", str(root / "pyproject.toml"))
check(
	"…and a pin in the dev group takes the package out of bench-dev",
	"freezegun~=1.5" in groups(root)["dev"]
	and not any(fw.requirement_name(r) == "freezegun" for r in groups(root).get("bench-dev", [])),
	f"got {groups(root)!r}",
)


# ── a root whose apps declare nothing ──────────────────────────────────────

root = bench({"frappe": "", "my_app": "[tool.bench]\nfoo = 1\n"})
run("sync-apps", "--pyproject", str(root / "pyproject.toml"), "frappe", "my_app")
synced = (root / "pyproject.toml").read_text()
status, _ = run("ensure-root", "--pyproject", str(root / "pyproject.toml"))
check(
	"no member declares any: no bench-dev group, and ensure-root leaves the root byte for byte",
	"bench-dev" not in synced and (root / "pyproject.toml").read_text() == synced,
)


# ── an app that has not opted in (S35) ─────────────────────────────────────


def gated(*flags):
	"""A generated root for frappe + my_app, as lib/app-workspace.nix runs it: sync-apps, then ensure-root FLAGS."""
	root = bench({"frappe": FRAPPE_DEV, "my_app": ""})
	run("sync-apps", "--pyproject", str(root / "pyproject.toml"), "frappe", "my_app")
	run("ensure-root", "--pyproject", str(root / "pyproject.toml"), *flags)
	return root


plain = bench({"frappe": "", "my_app": ""})
run("sync-apps", "--pyproject", str(plain / "pyproject.toml"), "frappe", "my_app")
run("ensure-root", "--pyproject", str(plain / "pyproject.toml"), "--test-tools-unlocked")
main_root = (plain / "pyproject.toml").read_text()

root = gated("--test-tools-unlocked")
check(
	"no lock yet: no bench-dev group, and the same root as for apps that declare nothing",
	"bench-dev" not in groups(root) and (root / "pyproject.toml").read_text() == main_root,
)

lockdir = Path(tempfile.mkdtemp())
main_lock = lock_with_root(lockdir / "main.lock", dev=["pydantic", "pytest", "responses"])
root = gated("--test-tools-lock", str(main_lock))
check(
	"a lock main wrote (no bench-dev in its root): the root is main's, byte for byte",
	(root / "pyproject.toml").read_text() == main_root,
)

listed = lock_with_root(
	lockdir / "listed.lock",
	dev=["coverage", "unittest-xml-reporting"],
	bench_dev=["faker", "freezegun", "hypothesis", "pyngrok", "watchdog"],
)
root = gated("--test-tools-lock", str(listed))
check(
	"a lock whose root lists them: the group is kept",
	groups(root).get("bench-dev")
	== ["faker~=18.10.1", "freezegun~=1.5.5", "hypothesis~=6.77.0", "pyngrok~=7.5.0", "watchdog~=6.0.0"],
	f"got {groups(root)!r}",
)
partly = lock_with_root(lockdir / "partly.lock", dev=[], bench_dev=["freezegun"])
root = gated("--test-tools-lock", str(partly))
check(
	"a lock whose root lists some: only those",
	groups(root).get("bench-dev") == ["freezegun~=1.5.5"],
	f"got {groups(root)!r}",
)


# ── an app that opted in: its tools/pyproject.toml pins win ────────────────

root = bench({"frappe": FRAPPE_DEV, "my_app": '[tool.bench.dev-dependencies]\nruff = "*"\n'})
tools = root / "tools.toml"
tools.write_text('[project]\nname = "tools"\ndependencies = ["ruff", "prek", "semgrep"]\n')
run("sync-apps", "--pyproject", str(root / "pyproject.toml"), "frappe", "my_app")
run("ensure-root", "--pyproject", str(root / "pyproject.toml"), "--app-tools", str(tools))
after_first = (root / "pyproject.toml").read_text()
run("ensure-root", "--pyproject", str(root / "pyproject.toml"), "--app-tools", str(tools))
names = {fw.requirement_name(r) for g in groups(root).values() for r in g}
check(
	"an opted-in root keeps the bench-dev group, without a tool the app pins itself",
	"freezegun" in names and "ruff" not in names,
	f"got {groups(root)!r}",
)
check(
	"and a second ensure-root leaves it byte for byte",
	(root / "pyproject.toml").read_text() == after_first,
)


if FAILURES:
	print(f"\n{len(FAILURES)} check(s) failed", file=sys.stderr)
	sys.exit(1)
print("\nall checks passed")
