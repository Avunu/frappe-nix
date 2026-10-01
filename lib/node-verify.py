#!/usr/bin/env python3
"""Find — and repair — a damaged yarn cache or node_modules.

Yarn trusts what it wrote. `node_modules/.yarn-integrity` is a hash of the
lockfile and the install's settings, never of the files, so an install that was
cut short (a killed process, a full disk, a network that gave up) leaves a tree
yarn calls up-to-date for as long as the lockfile does not move. What that
looks like from outside:

    Cannot find module 'sass'                     an empty package directory
    Bus error (core dumped) from `vite build`     a truncated native binary
    Rolldown failed to resolve "hast-util-raw"    a cache record that lists no
                                                  dependencies for a package
                                                  that has them

none of which names the install, and none of which a re-run repairs.

Checked, in the yarn cache and in every app's node_modules (nested frontends
included):

  * a package directory with no package.json (empty, or extracted in part);
  * a native binary (.node, .so, or an ELF executable) whose ELF headers point
    past the end of the file;
  * in the cache, a record (.yarn-metadata.json) whose dependency lists differ
    from its own package's package.json — yarn resolves from the record, so a
    package cached like that installs without what it needs, and a non-frozen
    install (a nested frontend's postinstall) then rewrites the lockfile to
    match.

Repaired by deleting what is damaged and nothing else: the cache entry, the
installed copies of that name@version, and the .yarn-integrity and
.frappe-nix-installed markers that say "installed", so the next
`frappe-nix-node-modules` reinstalls the apps concerned from the rest of the
cache. All of it regenerable; nothing under an app's own source is touched.

    node-verify.py [--check] [--full] [--cache DIR] [--lockfiles] <bench-root> <app>...

--check reports and changes nothing (exit 1 if anything is damaged).
--full scans even when nothing has been installed since the last clean scan.
--lockfiles also lists lockfiles and manifests that differ from the commit.
Silent when nothing is wrong.

The scan reads every file-tree under every node_modules, a few seconds, and this
runs on every shell entry — so a clean scan leaves a fingerprint of what could
have changed (see fingerprint), and while that is unchanged the scan is
skipped. Damage of this kind happens while an install is writing, and an
install moves the fingerprint: it adds cache entries, and rewrites
.yarn-integrity when it completes.
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys

# Never descended into: nothing under them is an input to yarn, or belongs to
# the app (see lib/node-modules.nix for the same list).
PRUNE = {".git", ".frappe-nix", ".devenv", ".direnv"}
BINARY_SUFFIXES = (".node", ".so")


def say(msg):
    print(f"frappe-nix: {msg}", file=sys.stderr)


def elf_truncated(path):
    """(size, needed) when a 64-bit ELF file ends before its own headers say it
    does; None for anything else — including a file that is not ELF at all."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(64)
            if len(head) < 64 or head[:4] != b"\x7fELF" or head[4] != 2 or head[5] not in (1, 2):
                return None
            # The platform variants a package ships include big-endian ones
            # (s390x), cached but never installed here.
            e = "<" if head[5] == 1 else ">"
            phoff, shoff = struct.unpack_from(e + "QQ", head, 0x20)
            phentsize, phnum, shentsize, shnum = struct.unpack_from(e + "HHHH", head, 0x36)
            need = shoff + shentsize * shnum if shoff else 0
            f.seek(phoff)
            phdrs = f.read(phentsize * phnum)
            if len(phdrs) < phentsize * phnum:
                return (size, phoff + phentsize * phnum)
            for i in range(phnum):
                off = struct.unpack_from(e + "Q", phdrs, i * phentsize + 8)[0]
                filesz = struct.unpack_from(e + "Q", phdrs, i * phentsize + 32)[0]
                need = max(need, off + filesz)
    except (OSError, struct.error):
        return None
    return (size, need) if size < need else None


def is_binary_name(name):
    return name.endswith(BINARY_SUFFIXES) or ".so." in name


def truncated_binaries(pkg_dir):
    """Truncated binaries in one package, not descending into its own
    node_modules (scanned as packages of their own)."""
    found = []
    for d, dirs, files in os.walk(pkg_dir):
        dirs[:] = [x for x in dirs if x != "node_modules"]
        for n in files:
            if not is_binary_name(n):
                continue
            p = os.path.join(d, n)
            if os.path.islink(p):
                continue
            r = elf_truncated(p)
            if r:
                found.append((p, r))
    return found


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def package_dirs(nm):
    """Every package directory directly in a node_modules (scopes flattened)."""
    try:
        names = sorted(os.listdir(nm))
    except OSError:
        return
    for n in names:
        if n.startswith("."):
            continue
        p = os.path.join(nm, n)
        if os.path.islink(p) or not os.path.isdir(p):
            continue
        if n.startswith("@"):
            for m in sorted(os.listdir(p)):
                q = os.path.join(p, m)
                if not m.startswith(".") and not os.path.islink(q) and os.path.isdir(q):
                    yield q
        else:
            yield p


DEP_KEYS = ("dependencies", "optionalDependencies", "peerDependencies")


def scan_cache(cache):
    """[(entry dir, reason, name, version)] for every damaged cache entry."""
    bad = []
    if not os.path.isdir(cache):
        return bad
    for bucket in sorted(os.listdir(cache)):
        b = os.path.join(cache, bucket)
        if not os.path.isdir(b):
            continue
        for entry in sorted(os.listdir(b)):
            nm = os.path.join(b, entry, "node_modules")
            if not os.path.isdir(nm):
                continue
            for pkg in package_dirs(nm):
                pj, meta = os.path.join(pkg, "package.json"), os.path.join(pkg, ".yarn-metadata.json")
                reason = name = version = None
                if not os.path.isfile(pj):
                    reason = "no package.json"
                elif not os.path.isfile(meta):
                    reason = "no .yarn-metadata.json"
                else:
                    try:
                        manifest, package = read_json(meta)["manifest"], read_json(pj)
                        name, version = manifest.get("name"), manifest.get("version")
                        for k in DEP_KEYS:
                            if (package.get(k) or {}) != (manifest.get(k) or {}):
                                reason = f"its record lists different {k} than its package.json"
                                break
                    except (OSError, ValueError, KeyError):
                        reason = "unreadable record"
                if not reason:
                    bins = truncated_binaries(pkg)
                    if bins:
                        p, (size, need) = bins[0]
                        reason = f"{os.path.basename(p)} is {size} of {need} bytes"
                if reason:
                    bad.append((os.path.join(b, entry), reason, name, version))
                break  # an entry holds one package
    return bad


def node_modules_roots(app_dir):
    """Every top-level node_modules under an app: its own and each nested
    frontend's, found without looking inside one.

    Not a linked one: `bench build`, and shell entry, link <app>/public/
    node_modules at the app's own, which os.walk lists among the directories
    and would have scanned — and reported — twice."""
    roots = []
    for d, dirs, _ in os.walk(app_dir):
        dirs[:] = [x for x in dirs if x not in PRUNE]
        if "node_modules" in dirs:
            nm = os.path.join(d, "node_modules")
            if not os.path.islink(nm):
                roots.append(nm)
            dirs.remove("node_modules")
    return roots


def scan_modules(app_dir):
    """(damaged, index): damaged is [(package dir, reason)]; index maps
    (name, version) -> [package dir] for everything readable."""
    damaged, index = [], {}

    def walk(nm):
        for pkg in package_dirs(nm):
            pj = os.path.join(pkg, "package.json")
            if not os.path.isfile(pj):
                damaged.append((pkg, "no package.json"))
            else:
                try:
                    p = read_json(pj)
                    index.setdefault((p.get("name"), p.get("version")), []).append(pkg)
                except (OSError, ValueError):
                    damaged.append((pkg, "unreadable package.json"))
                bins = truncated_binaries(pkg)
                if bins:
                    f, (size, need) = bins[0]
                    damaged.append((pkg, f"{os.path.basename(f)} is {size} of {need} bytes"))
            inner = os.path.join(pkg, "node_modules")
            if os.path.isdir(inner):
                walk(inner)

    for root in node_modules_roots(app_dir):
        walk(root)
    return damaged, index


def drifted_lockfiles(app_dir):
    """Lockfiles and manifests an app's git says differ from its commit."""
    if not os.path.exists(os.path.join(app_dir, ".git")):
        return []
    out = subprocess.run(
        ["git", "-C", app_dir, "status", "--porcelain", "--", "*yarn.lock", "*package.json", "*package-lock.json"],
        capture_output=True,
        text=True,
    ).stdout
    return [line[3:] for line in out.splitlines() if line.strip()]


def fingerprint(bench, cache, apps):
    """What an install would change: the cache's entry lists, and each
    node_modules' own markers. Cheap — no package is opened."""
    parts = []
    if cache and os.path.isdir(cache):
        for bucket in sorted(os.listdir(cache)):
            b = os.path.join(cache, bucket)
            if os.path.isdir(b):
                st = os.stat(b)
                parts.append(f"cache {bucket} {st.st_mtime_ns} {st.st_nlink}")
    for app in apps:
        app_dir = os.path.join(bench, "apps", app)
        for root in node_modules_roots(app_dir) if os.path.isdir(app_dir) else []:
            for marker in (".yarn-integrity", ".frappe-nix-installed"):
                try:
                    st = os.stat(os.path.join(root, marker))
                    parts.append(f"{root}/{marker} {st.st_mtime_ns} {st.st_size}")
                except OSError:
                    parts.append(f"{root}/{marker} absent")
    return "\n".join(parts)


def top_node_modules(path):
    """The outermost node_modules a package directory sits in."""
    parts = path.split(os.sep)
    i = parts.index("node_modules")
    return os.sep.join(parts[: i + 1])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report, change nothing, exit 1 if damaged")
    ap.add_argument("--cache", default=os.environ.get("YARN_CACHE_FOLDER", ""))
    ap.add_argument("--full", action="store_true", help="scan even if nothing was installed since the last clean scan")
    ap.add_argument("--lockfiles", action="store_true", help="also list lockfiles that differ from the commit")
    ap.add_argument("bench_root")
    ap.add_argument("apps", nargs="*")
    args = ap.parse_args()

    bench = os.path.abspath(args.bench_root)

    stamp = os.path.join(args.cache, ".frappe-nix-verified") if args.cache and os.path.isdir(args.cache) else None
    fp = fingerprint(bench, args.cache, args.apps)
    if stamp and not args.full and not args.check and not args.lockfiles:
        try:
            with open(stamp, encoding="utf-8") as f:
                if f.read() == fp:
                    return 0
        except OSError:
            pass

    bad_cache = scan_cache(args.cache) if args.cache else []

    damaged = {}  # app -> [(package dir, reason)]
    index = {}  # app -> {(name, version): [dirs]}
    for app in args.apps:
        app_dir = os.path.join(bench, "apps", app)
        if os.path.isdir(app_dir):
            damaged[app], index[app] = scan_modules(app_dir)

    # An installed copy of a package whose cache record is wrong was installed
    # from that record, and looks fine from the outside.
    stale_installs = {}  # app -> [package dir]
    for _, _, name, version in bad_cache:
        if name is None:
            continue
        for app, idx in index.items():
            stale_installs.setdefault(app, []).extend(idx.get((name, version), []))

    n_cache = len(bad_cache)
    n_mod = sum(len(v) for v in damaged.values())
    n_stale = sum(len(v) for v in stale_installs.values())

    if n_cache or n_mod or n_stale:
        if args.check:
            say("damaged node dependencies (not repaired: --check):")
        else:
            say("repairing damaged node dependencies:")
        for entry, reason, _, _ in bad_cache:
            say(f"  yarn cache {os.path.basename(entry)[:70]}: {reason}")
        for app, items in damaged.items():
            for pkg, reason in items:
                say(f"  apps/{app}: {os.path.relpath(pkg, os.path.join(bench, 'apps', app))}: {reason}")
        for app, items in stale_installs.items():
            for pkg in items:
                say(f"  apps/{app}: {os.path.relpath(pkg, os.path.join(bench, 'apps', app))}: installed from a damaged cache record")

    redo = set()
    if not args.check:
        for entry, _, _, _ in bad_cache:
            shutil.rmtree(entry, ignore_errors=True)
        for app in args.apps:
            for pkg in [p for p, _ in damaged.get(app, [])] + stale_installs.get(app, []):
                if os.path.isdir(pkg):
                    shutil.rmtree(pkg, ignore_errors=True)
                    root = top_node_modules(pkg)
                    for marker in (os.path.join(root, ".yarn-integrity"),):
                        if os.path.exists(marker):
                            os.remove(marker)
                    redo.add(app)
        for app in redo:
            sentinel = os.path.join(bench, "apps", app, "node_modules", ".frappe-nix-installed")
            if os.path.exists(sentinel):
                os.remove(sentinel)
        if redo:
            say(f"  reinstalling: {', '.join(sorted(redo))}")

    if args.lockfiles:
        for app in args.apps:
            for f in drifted_lockfiles(os.path.join(bench, "apps", app)):
                say(f"apps/{app}/{f} differs from its commit (a nested frontend's non-frozen install rewrites it); left as it is")

    if not (n_cache or n_mod or n_stale):
        if stamp:
            try:
                with open(stamp, "w", encoding="utf-8") as f:
                    f.write(fp)
            except OSError:
                pass
        return 0
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
