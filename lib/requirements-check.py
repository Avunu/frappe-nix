#!/usr/bin/env python3
"""Is every Python requirement the bench's apps declare tracked and installed?

The virtualenv is built by Nix from uv.lock, so nothing here installs: the
repair for a finding is a lock or a re-entered shell, and this says which.
Run by the interpreter of that environment, from the bench root.

  1. Every requirement an app's pyproject.toml declares is in uv.lock. An app
     moved to a newer commit can add one the lock predates; Nix then fails to
     evaluate with `attribute 'x' missing`, which names neither the app nor
     `uv lock` (lib/lock-audit.nix turns that into a sentence when it can).
  2. Every app in sites/apps.txt imports in this environment — i.e. it is a
     workspace member the environment was built with, not merely a directory
     under apps/.

Exit 1 if anything is missing.
"""

import importlib.util
import os
import re
import sys
import tomllib


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(spec):
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return norm(m.group(1)) if m else None


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    problems = []

    try:
        with open(os.path.join(root, "sites", "apps.txt"), encoding="utf-8") as f:
            apps = [a.strip() for a in f if a.strip()]
    except OSError:
        apps = []

    locked = None
    try:
        with open(os.path.join(root, "uv.lock"), "rb") as f:
            locked = {norm(p["name"]) for p in tomllib.load(f).get("package", [])}
    except OSError:
        problems.append("uv.lock is missing: run `uv lock`")

    for app in apps:
        pyproject = os.path.join(root, "apps", app, "pyproject.toml")
        if locked is not None and os.path.isfile(pyproject):
            try:
                with open(pyproject, "rb") as f:
                    declared = tomllib.load(f).get("project", {}).get("dependencies", [])
            except (OSError, tomllib.TOMLDecodeError):
                declared = []
            missing = sorted({n for n in map(requirement_name, declared) if n and n not in locked})
            if missing:
                problems.append(f"apps/{app} requires {', '.join(missing)}, which uv.lock does not have: run `uv lock`")
        if importlib.util.find_spec(app.replace("-", "_")) is None:
            problems.append(f"{app} is in sites/apps.txt but does not import in this environment: is it a [tool.uv.workspace] member? then `uv lock` and re-enter the shell")

    for p in problems:
        print(f"  ✗ {p}", file=sys.stderr)
    if not problems:
        print(f"  ✓ python: {len(apps)} app(s), every requirement locked, every app importable")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
