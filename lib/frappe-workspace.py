"""Reconcile a bench's app registration — the one implementation of the
contract between `apps/`, `pyproject.toml` and `sites/apps.{txt,json}`.

Every subcommand is idempotent and format-preserving (tomlkit), so it is safe
to run against a bench that is already correct: `frappe-init` uses it for both
scaffolding and migration, `bench-get-app` / `bench-new-app` / `bench-update`
use it at runtime, and lib/bench.nix runs `sync-registry` when it assembles
the bench package.
"""

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import tomlkit

# Distribution-name normalization (PEP 503), so a `[tool.uv.sources]` key
# written here matches what uv resolves the workspace member to.
_NORMALIZE = re.compile(r"[-_.]+")


def normalize(name):
    return _NORMALIZE.sub("-", name).lower()


def load(path):
    return tomlkit.parse(Path(path).read_text())


def save(doc, path):
    Path(path).write_text(tomlkit.dumps(doc))


def table_at(doc, *keys):
    """Get-or-create a nested table, e.g. table_at(doc, "tool", "uv", "sources")."""
    node = doc
    for key in keys:
        if key not in node:
            node[key] = tomlkit.table()
        node = node[key]
    return node


def app_dist_name(app_dir):
    """The app's own [project].name, which is what uv resolves it to.

    Falls back to the directory name for apps that predate PEP 621 — those are
    excluded from the workspace anyway, but the caller may still ask.
    """
    pyproject = Path(app_dir) / "pyproject.toml"
    if pyproject.is_file():
        try:
            name = tomlkit.parse(pyproject.read_text())["project"]["name"]
            if name:
                return str(name)
        except Exception:
            pass
    return Path(app_dir).name


# ── subcommands ───────────────────────────────────────────────────────────


def cmd_dist_name(args):
    """Print an app directory's [project].name.

    Shell has no TOML parser, and `frappe-init --app` needs this to tell a Frappe
    app repository from any other directory: an app is a pyproject.toml whose
    [project].name matches a sibling package holding hooks.py.
    """
    print(app_dist_name(Path(args.app_dir)))
    return 0


def cmd_ensure_root(args):
    """Fill in the root-level keys the Nix side reads directly.

    lib/python.nix does `fromTOML` on this file and indexes [project].name and
    [dependency-groups]; lib/bench.nix and uv2nix need [tool.uv.workspace]. A
    pyproject.toml that predates frappe-nix (a user's own project file) is
    reconciled rather than overwritten, so only absent keys are filled.
    """
    doc = load(args.pyproject)
    changed = []

    project = table_at(doc, "project")
    if "name" not in project:
        project["name"] = args.name
        changed.append("[project].name")
    if "version" not in project:
        project["version"] = "0.1.0"
        changed.append("[project].version")
    if "requires-python" not in project:
        project["requires-python"] = args.requires_python
        changed.append("[project].requires-python")
    elif str(project["requires-python"]) != args.requires_python:
        print(
            f"  note: [project].requires-python is {project['requires-python']!s}, "
            f"the {args.preset} preset expects {args.requires_python}",
            file=sys.stderr,
        )

    deps = project.setdefault("dependencies", tomlkit.array())
    have = {normalize(re.split(r"[<>=!~\[ ]", str(d))[0]) for d in deps}
    # frappe-runtime is unconditional, like frappe-bench: frappe-nix.runtime is on
    # by default, and a bench that turns it off merely carries an unused package.
    # Reconciling it here rather than asking for a hand edit is what lets an
    # existing bench pick it up by re-running frappe-init.
    for required in ("frappe-bench>=5.29.0", "frappe-runtime", "setuptools"):
        if normalize(re.split(r"[<>=!~\[ ]", required)[0]) not in have:
            deps.append(required)
            changed.append(f"[project].dependencies += {required}")

    groups = table_at(doc, "dependency-groups")
    if "dev" not in groups:
        groups["dev"] = tomlkit.array(
            '["pre-commit>=4.5.1", "pydantic>=2.12.5", "pytest>=9.0.2", '
            '"responses", "ruff>=0.15.0", "semgrep"]'
        )
        changed.append("[dependency-groups].dev")

    uv = table_at(doc, "tool", "uv")
    # Not optional: the workspace root is a virtual package. lib/python.nix
    # filters the root package out of the venv by name, and a non-virtual root
    # would have uv try to build the bench directory itself.
    if uv.get("package") is not False:
        uv["package"] = False
        changed.append("[tool.uv].package = false")
    if "override-dependencies" not in uv and args.overrides:
        uv["override-dependencies"] = tomlkit.array(args.overrides)
        changed.append("[tool.uv].override-dependencies")

    if args.template:
        template = tomlkit.parse(Path(args.template).read_text())
        template_uv = template.get("tool", {}).get("uv", {})

        wanted = template_uv.get("extra-build-dependencies", {})
        existing = table_at(doc, "tool", "uv", "extra-build-dependencies")
        added = 0
        for key, value in wanted.items():
            if key not in existing:
                existing[key] = value
                added += 1
        if added:
            changed.append(f"[tool.uv.extra-build-dependencies] += {added} entries")

        # Non-app sources the template ships (frappe-runtime's git source). App
        # sources are sync-apps' job; this only fills what the template names and
        # the bench lacks, so a bench that points frappe-runtime somewhere else
        # keeps its own entry.
        wanted = template_uv.get("sources", {})
        existing = table_at(doc, "tool", "uv", "sources")
        for key, value in wanted.items():
            if key not in existing:
                existing[key] = value
                changed.append(f"[tool.uv.sources].{key}")

    if "build-system" not in doc:
        build = tomlkit.table()
        build["requires"] = tomlkit.array('["hatchling"]')
        build["build-backend"] = "hatchling.build"
        doc["build-system"] = build
        changed.append("[build-system]")

    table_at(doc, "tool", "uv", "workspace").setdefault("members", tomlkit.array())
    table_at(doc, "tool", "uv", "sources")

    save(doc, args.pyproject)
    for line in changed:
        print(f"  + {line}")
    return 0


def cmd_add_app(args):
    """Register one app as a uv workspace member. Idempotent."""
    doc = load(args.pyproject)
    members = table_at(doc, "tool", "uv", "workspace").setdefault("members", tomlkit.array())
    sources = table_at(doc, "tool", "uv", "sources")

    entry = f"apps/{args.app}"
    if entry not in members:
        members.append(entry)

    # Key on the app's own distribution name: for apps where it differs from the
    # directory name (print_designer → print-designer, forks, …) a dir-named
    # entry is inert and uv silently resolves the app from PyPI instead.
    dist = args.source_name or app_dist_name(Path(args.pyproject).parent / entry)
    if dist not in sources:
        source = tomlkit.inline_table()
        source["workspace"] = True
        sources[dist] = source
    else:
        sources[dist].setdefault("workspace", True)

    save(doc, args.pyproject)
    return 0


def cmd_sync_apps(args):
    """Register every eligible app, and report members pointing at nothing."""
    doc = load(args.pyproject)
    members = table_at(doc, "tool", "uv", "workspace").setdefault("members", tomlkit.array())
    sources = table_at(doc, "tool", "uv", "sources")
    root = Path(args.pyproject).parent

    for app in args.apps:
        entry = f"apps/{app}"
        if entry not in members:
            members.append(entry)
            print(f"  + workspace member {entry}")
        dist = app_dist_name(root / entry)
        if dist not in sources:
            source = tomlkit.inline_table()
            source["workspace"] = True
            sources[dist] = source
            print(f"  + [tool.uv.sources].{dist}")
        else:
            sources[dist].setdefault("workspace", True)

    for member in list(members):
        if not (root / str(member)).is_dir():
            print(f"  ⚠  workspace member {member} does not exist on disk", file=sys.stderr)

    save(doc, args.pyproject)
    return 0


def cmd_shim_app(args):
    """Write a minimal PEP 621 pyproject.toml for a setup.py-only app.

    Only ever called for *vendored* apps: writing this into a submodule's
    worktree would leave it outside the pinned commit, so a clean `nix build`
    on any other machine would not see it.
    """
    app_dir = Path(args.app_dir)
    target = app_dir / "pyproject.toml"
    if target.exists():
        return 0

    deps = tomlkit.array()
    requirements = app_dir / "requirements.txt"
    if requirements.is_file():
        for line in requirements.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                deps.append(line)

    doc = tomlkit.document()
    doc.add(
        tomlkit.comment(
            " Generated by `frappe-init --migrate` for an app that shipped only a"
        )
    )
    doc.add(tomlkit.comment(" setup.py. Replace with the app's own metadata when you can."))
    project = tomlkit.table()
    project["name"] = args.name
    project["version"] = "0.0.1"
    project["description"] = f"{args.name} (metadata shimmed by frappe-nix)"
    project["requires-python"] = args.requires_python
    project["dependencies"] = deps
    doc["project"] = project

    build = tomlkit.table()
    build["requires"] = tomlkit.array('["setuptools"]')
    # setuptools.build_meta still executes the app's setup.py, so its entry
    # points and package_data keep working; PEP 621 metadata wins on conflict.
    build["build-backend"] = "setuptools.build_meta"
    doc["build-system"] = build

    find = tomlkit.table()
    find["include"] = tomlkit.array(json.dumps([f"{args.name}*"]))
    table_at(doc, "tool", "setuptools", "packages")["find"] = find

    save(doc, target)
    print(f"  + {target} (shim)")
    return 0


# ── registry: sites/apps.txt + sites/apps.json ────────────────────────────
#
# The two files bench keeps under sites/ and frappe reads from there. apps.txt
# is load-bearing: frappe.get_all_apps() is that file, and `install-app`
# refuses a name it does not list. apps.json is bench's provenance record —
# nothing in frappe reads it, but bench does, and so does frappe-init's
# version detection when it migrates a classic bench.
#
# Both are outputs here, never inputs: the registered apps are exactly the
# [tool.uv.workspace].members, i.e. what is actually installed in the
# virtualenv. A directory under apps/ that is not a member is on PYTHONPATH
# and nothing more.

_VERSION_RE = r"""^(\s*%s\s*=\s*['"])(.+?)(['"])"""


def warn(msg):
    print(f"  ⚠  {msg}", file=sys.stderr)


def member_dirs(doc):
    """Directory names of the explicit `apps/<x>` workspace members, in order.

    Only two-segment `apps/<x>` entries count. A glob is ignored with a warning:
    frappe-nix writes explicit entries, and lib/bench.nix mirrors this rule
    without a glob matcher, so accepting one here would let the two disagree.
    """
    members = doc.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
    out = []
    for member in members:
        member = str(member).strip("/")
        parts = member.split("/")
        if any(c in member for c in "*?["):
            warn(f"workspace member {member!r} is a glob — register apps explicitly")
            continue
        if len(parts) != 2 or parts[0] != "apps":
            continue
        if parts[1] not in out:
            out.append(parts[1])
    return out


def registered_apps(members, apps_dir):
    """Members that are Frappe apps on disk, `frappe` first."""
    apps = []
    for app in members:
        app_dir = apps_dir / app
        if not app_dir.is_dir():
            warn(f"workspace member apps/{app} is not on disk — skipped")
        elif not (app_dir / app / "hooks.py").is_file():
            warn(f"workspace member apps/{app} has no {app}/hooks.py — not a Frappe app, skipped")
        else:
            apps.append(app)
    if "frappe" in apps:
        apps.remove("frappe")
        apps.insert(0, "frappe")
    else:
        warn("frappe is not a workspace member — sites/apps.txt will not list it")
    return apps


def _version_from_text(text, field="__version__"):
    match = re.search(_VERSION_RE % field, text, flags=re.M)
    return match.group(2) if match else None


def app_version(app_dir):
    """The app's version, the way bench's get_current_version finds it.

    [project].version first; every real Frappe app declares it dynamic, so the
    `__version__` assignment in the package's __init__.py is the usual source.
    setup.py is the pre-PEP 621 fallback. setup.cfg is skipped: reading it
    needs setuptools.
    """
    app = app_dir.name
    pyproject = app_dir / "pyproject.toml"
    if pyproject.is_file():
        try:
            version = tomlkit.parse(pyproject.read_text()).get("project", {}).get("version")
            if version:
                return str(version)
        except Exception:
            pass
    init = app_dir / app / "__init__.py"
    if init.is_file():
        version = _version_from_text(init.read_text())
        if version:
            return version
    setup = app_dir / "setup.py"
    if setup.is_file():
        version = _version_from_text(setup.read_text(), field="version")
        if version:
            return version
    warn(f"apps/{app}: no version found (pyproject.toml, {app}/__init__.py, setup.py)")
    return None


def app_required(app_dir):
    """hooks.py's top-level `required_apps`, without importing it."""
    app = app_dir.name
    hooks = app_dir / app / "hooks.py"
    try:
        tree = ast.parse(hooks.read_text(), filename=str(hooks))
    except (OSError, SyntaxError) as e:
        warn(f"apps/{app}/{app}/hooks.py could not be parsed ({e}); required = []")
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "required_apps" for t in targets):
            continue
        try:
            required = ast.literal_eval(value)
        except (ValueError, TypeError):
            return []
        if isinstance(required, (list, tuple)) and all(isinstance(r, str) for r in required):
            return list(required)
        return []
    return []


def parse_gitmodules(path):
    """`{ <path value>: {branch, url, ...} }` from a .gitmodules file.

    Hand-rolled on purpose: configparser reads git's tab-indented keys as
    continuation lines of the previous value.
    """
    entries = {}
    current = None
    for line in Path(path).read_text().splitlines():
        header = re.match(r'^\s*\[submodule\s+"(.+)"\]\s*$', line)
        if header:
            current = {}
            entries[header.group(1)] = current
            continue
        kv = re.match(r"^\s*(\w+)\s*=\s*(.*?)\s*$", line)
        if kv and current is not None:
            current[kv.group(1)] = kv.group(2)
    # Key on the recorded path, which is what apps/<x> is looked up by; the
    # section name usually matches but is not required to.
    return {e.get("path", name): e for name, e in entries.items()}


def _git(app_dir, *args):
    try:
        out = subprocess.run(
            ["git", "-C", str(app_dir), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


def live_git(app_dir):
    """HEAD's commit and branch from a checkout, or None when there is none.

    Only consulted when `.git` exists (a directory, or a submodule's gitfile),
    and any failure — no git on PATH included — reads as "unknown". A bench
    package built in the Nix sandbox has neither, and must not need either.
    """
    if not (app_dir / ".git").exists():
        return None
    return {
        "commit_hash": _git(app_dir, "rev-parse", "HEAD"),
        "branch": _git(app_dir, "symbolic-ref", "--quiet", "--short", "HEAD"),
    }


def _first(*values):
    for v in values:
        if v is not None:
            return v
    return None


def resolve_provenance(app, app_dir, overrides, gitmodules, seed):
    """`(is_repo, {"commit_hash", "branch"})`, first known source per field.

    commit_hash: --provenance → live git → --seed
    branch:      --provenance → .gitmodules → live git → --seed

    .gitmodules outranks the live branch so that a dev bench regenerates the
    same bytes the package build computes — the build sees .gitmodules but no
    .git — and `git status` then says exactly whether the committed record is
    current. The live branch still covers a checkout that is not a submodule.
    """
    override = overrides.get(app) or {}
    live = live_git(app_dir) or {}
    module = gitmodules.get(f"apps/{app}") or {}
    seeded = seed.get(app) or {}
    seeded_res = seeded.get("resolution")
    # bench writes the *string* "not a repo" / "not calculated" there.
    if not isinstance(seeded_res, dict):
        seeded_res = {}

    commit = _first(override.get("commit_hash"), live.get("commit_hash"), seeded_res.get("commit_hash"))
    branch = _first(override.get("branch"), module.get("branch"), live.get("branch"), seeded_res.get("branch"))
    is_repo = override.get("is_repo")
    if is_repo is None:
        is_repo = commit is not None or branch is not None
    return bool(is_repo), {"commit_hash": commit, "branch": branch}


def registry_entries(apps, apps_dir, overrides, gitmodules, seed):
    """apps.json's content, in apps.txt order, in bench's own shape."""
    entries = {}
    for idx, app in enumerate(apps, start=1):
        app_dir = apps_dir / app
        is_repo, resolution = resolve_provenance(app, app_dir, overrides, gitmodules, seed)
        entries[app] = {
            "is_repo": is_repo,
            "resolution": resolution,
            "required": app_required(app_dir),
            "idx": idx,
            "version": app_version(app_dir),
        }
    return entries


def write_if_changed(path, text, label):
    """Write only when the bytes differ, so a rerun leaves mtimes and git alone."""
    path = Path(path)
    if path.is_file() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"  + {label}")
    return True


def _load_json(path, what):
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text() or "{}")
    except (OSError, ValueError) as e:
        warn(f"{what} {path} unreadable ({e}) — ignored")
        return {}
    return data if isinstance(data, dict) else {}


def cmd_sync_registry(args):
    """Regenerate sites/apps.txt and sites/apps.json from the workspace members."""
    pyproject = Path(args.pyproject)
    try:
        doc = load(pyproject)
    except (OSError, ValueError) as e:
        print(f"error: cannot read {pyproject}: {e}", file=sys.stderr)
        return 1
    apps_dir = Path(args.apps_dir)
    sites_dir = Path(args.sites_dir)

    apps = registered_apps(member_dirs(doc), apps_dir)

    gitmodules_path = args.gitmodules
    if gitmodules_path is None:
        candidate = pyproject.resolve().parent / ".gitmodules"
        gitmodules_path = str(candidate) if candidate.is_file() else ""
    gitmodules = parse_gitmodules(gitmodules_path) if gitmodules_path else {}
    overrides = _load_json(args.provenance, "--provenance")
    seed = _load_json(args.seed, "--seed")

    entries = registry_entries(apps, apps_dir, overrides, gitmodules, seed)

    write_if_changed(sites_dir / "apps.txt", "".join(f"{app}\n" for app in apps), "sites/apps.txt")
    write_if_changed(sites_dir / "apps.json", json.dumps(entries, indent=4) + "\n", "sites/apps.json")
    return 0


# ── apps: what each apps/<x> is to git ────────────────────────────────────
#
# A bench carries its apps in one of two supported shapes: a registered
# submodule (.gitmodules names it; `bench-update --pull` moves it, `nix build`
# fetches it) or committed source — a *local* app, made by `bench-new-app` or
# by `frappe-init --migrate` vendoring one that had no remote. There is a
# third shape git will happily produce and nothing else can use: a nested
# repository that was `git add`ed as-is, which the index records as a gitlink
# with no .gitmodules entry. `git submodule foreach` / `update --init` die on
# it ("No url found for submodule path"), and the flake's source tree carries
# an empty directory, so `nix build` produces a bench without the app.
#
# Every consumer that used to iterate `git submodule …` goes through this
# instead, so a nested repo is reported and stepped around rather than fatal.

APP_KINDS = ("submodule", "submodule-uninitialized", "local", "nested-repo")


def classify_apps(apps_dir, gitmodules):
    """[(name, kind, branch, url)] for every apps/<x>, sorted, plus registered
    submodules not on disk. `branch` and `url` are .gitmodules' or ""."""
    apps_dir = Path(apps_dir)
    registered = {
        path[len("apps/") :]: entry
        for path, entry in gitmodules.items()
        if path.startswith("apps/") and "/" not in path[len("apps/") :]
    }
    on_disk = sorted(p.name for p in apps_dir.iterdir() if p.is_dir()) if apps_dir.is_dir() else []
    rows = []
    for name in on_disk:
        has_git = (apps_dir / name / ".git").exists()
        if name in registered:
            kind = "submodule" if has_git else "submodule-uninitialized"
            entry = registered[name]
            rows.append((name, kind, entry.get("branch", ""), entry.get("url", "")))
        elif has_git:
            rows.append((name, "nested-repo", "", ""))
        else:
            rows.append((name, "local", "", ""))
    for name in sorted(set(registered) - set(on_disk)):
        entry = registered[name]
        rows.append((name, "submodule-uninitialized", entry.get("branch", ""), entry.get("url", "")))
    return rows


def cmd_apps(args):
    """Print `<name>\t<kind>\t<branch>\t<url>` per app, for shell loops."""
    apps_dir = Path(args.apps_dir)
    gitmodules_path = args.gitmodules
    if gitmodules_path is None:
        candidate = apps_dir.resolve().parent / ".gitmodules"
        gitmodules_path = str(candidate) if candidate.is_file() else ""
    gitmodules = parse_gitmodules(gitmodules_path) if gitmodules_path else {}
    for name, kind, branch, url in classify_apps(apps_dir, gitmodules):
        if args.kind and kind not in args.kind:
            continue
        print(f"{name}\t{kind}\t{branch}\t{url}")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="frappe-nix-workspace")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dist-name")
    p.add_argument("--app-dir", required=True)
    p.set_defaults(func=cmd_dist_name)

    p = sub.add_parser("ensure-root")
    p.add_argument("--pyproject", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--requires-python", required=True)
    p.add_argument("--overrides", default="")
    p.add_argument("--preset", default="")
    # The rendered bench template: the source of the extra-build-dependencies
    # and non-app [tool.uv.sources] entries an existing bench is reconciled to.
    p.add_argument("--template", default="")
    p.set_defaults(func=cmd_ensure_root)

    p = sub.add_parser("add-app")
    p.add_argument("--pyproject", required=True)
    p.add_argument("--app", required=True)
    p.add_argument("--source-name", default="")
    p.set_defaults(func=cmd_add_app)

    p = sub.add_parser("sync-apps")
    p.add_argument("--pyproject", required=True)
    p.add_argument("apps", nargs="*")
    p.set_defaults(func=cmd_sync_apps)

    p = sub.add_parser("apps")
    p.add_argument("--apps-dir", default="apps")
    # None: look beside the apps dir. "": look nowhere.
    p.add_argument("--gitmodules", default=None)
    p.add_argument("--kind", action="append", choices=APP_KINDS, default=[])
    p.set_defaults(func=cmd_apps)

    p = sub.add_parser("sync-registry")
    p.add_argument("--pyproject", required=True)
    p.add_argument("--apps-dir", required=True)
    p.add_argument("--sites-dir", required=True)
    # None: look beside the pyproject. "": look nowhere.
    p.add_argument("--gitmodules", default=None)
    # {"<app>": {"commit_hash", "branch", "is_repo"}} — facts only the caller
    # knows (a flake input's rev), ranked above anything read from disk.
    p.add_argument("--provenance", default="")
    # A previous apps.json, ranked below everything else. The package build
    # passes the bench's committed one, so a commit recorded in a dev bench
    # survives into a sandbox that has no .git to read it from.
    p.add_argument("--seed", default="")
    p.set_defaults(func=cmd_sync_registry)

    p = sub.add_parser("shim-app")
    p.add_argument("--app-dir", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--requires-python", required=True)
    p.set_defaults(func=cmd_shim_app)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
