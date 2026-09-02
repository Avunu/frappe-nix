"""Reconcile a bench's app registration — the one implementation of the
three-way contract between `apps/`, `pyproject.toml` and `sites/apps.txt`.

Every subcommand is idempotent and format-preserving (tomlkit), so it is safe
to run against a bench that is already correct: `frappe-init` uses it for both
scaffolding and migration, and `bench-get-app` / `bench-new-app` use it at
runtime.
"""

import argparse
import json
import re
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
    for required in ("frappe-bench>=5.29.0", "setuptools"):
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

    if args.extra_build_dependencies:
        template = tomlkit.parse(Path(args.extra_build_dependencies).read_text())
        wanted = template.get("tool", {}).get("uv", {}).get("extra-build-dependencies", {})
        existing = table_at(doc, "tool", "uv", "extra-build-dependencies")
        added = 0
        for key, value in wanted.items():
            if key not in existing:
                existing[key] = value
                added += 1
        if added:
            changed.append(f"[tool.uv.extra-build-dependencies] += {added} entries")

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


def cmd_apps_txt(args):
    """Order-preserving union into sites/apps.txt, with `frappe` first.

    Never truncates: an existing bench's apps.txt encodes the install order its
    site was built with. Frappe writes the file without a trailing newline, so a
    naive append concatenates onto the last entry.
    """
    path = Path(args.file)
    existing = []
    if path.is_file():
        existing = [line.strip() for line in path.read_text().splitlines() if line.strip()]

    ordered = []
    for app in existing + args.add:
        if app not in ordered:
            ordered.append(app)
    # Frappe must install first; everything else keeps the order the bench had.
    if "frappe" in ordered:
        ordered.remove("frappe")
        ordered.insert(0, "frappe")

    for app in ordered:
        if app not in existing:
            print(f"  + sites/apps.txt: {app}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{app}\n" for app in ordered))
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
    p.add_argument("--extra-build-dependencies", default="")
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

    p = sub.add_parser("apps-txt")
    p.add_argument("--file", default="sites/apps.txt")
    p.add_argument("--add", nargs="*", default=[])
    p.set_defaults(func=cmd_apps_txt)

    p = sub.add_parser("shim-app")
    p.add_argument("--app-dir", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--requires-python", required=True)
    p.set_defaults(func=cmd_shim_app)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
