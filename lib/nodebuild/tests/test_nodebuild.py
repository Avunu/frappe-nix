"""Self-contained tests for frappe_nodebuild.

Runs without Frappe and without node. A stub ``frappe.build`` (or, for Frappe
16.50 and later, ``frappe.bundler`` behind a ``frappe.build`` shim) is written to
disk so the post-import hook sees a real import, and each case runs in a fresh
interpreter, since install() and the hook act once per process. The assertions
are about the ``NODE_OPTIONS`` frappe's build would hand node — the only
property that matters.

Run directly (``python test_nodebuild.py``) or via ``nix flake check``.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(HERE)

FAILURES = []

PRELOAD = "/nix/store/0000000000000000000000000000000-esbuild-preload.js"

STOCK_BUILD = """
def get_node_env():
    return {"NODE_OPTIONS": "--max_old_space_size=4096"}

def bundle():
    # frappe.build.bundle() resolves the name at call time, as here.
    return get_node_env()
"""

MOVED_BUILD = """
def node_env():
    return {"NODE_OPTIONS": "--max_old_space_size=4096"}
"""

# Frappe 16.50: the build code is frappe/bundler.py, and frappe/build/ is the
# Build module's package, whose __init__ re-exports it. The star import copies
# get_node_env under frappe.build; bundle() keeps calling bundler's own.
BUNDLER_LAYOUT = {
    "bundler.py": STOCK_BUILD,
    "build/__init__.py": "from frappe.bundler import *\n",
}
BUNDLER_MOVED_LAYOUT = {
    "bundler.py": MOVED_BUILD,
    "build/__init__.py": "",
}


def check(label, condition, detail=""):
    if condition:
        print(f"ok   {label}")
    else:
        print(f"FAIL {label} {detail}")
        FAILURES.append(label)


def run(build_source, preload, script, install_first=True):
    """Run ``script`` with a stub frappe build module (or modules) on sys.path.

    install() runs first, as the .pth bootstrap does, unless ``install_first``
    is False, when ``script`` calls it itself.
    """
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "frappe"))
        with open(os.path.join(root, "frappe", "__init__.py"), "w") as f:
            f.write("")
        # A string is the pre-16.50 layout: frappe/build.py.
        files = build_source if isinstance(build_source, dict) else {"build.py": build_source}
        for rel, source in files.items():
            path = os.path.join(root, "frappe", rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(textwrap.dedent(source))

        env = {k: v for k, v in os.environ.items() if k != "FRAPPE_NIX_ESBUILD_PRELOAD"}
        if preload is not None:
            env["FRAPPE_NIX_ESBUILD_PRELOAD"] = preload
        env["PYTHONPATH"] = os.pathsep.join([PACKAGE_ROOT, root])

        prelude = "import json, frappe_nodebuild\n"
        if install_first:
            prelude += "frappe_nodebuild.install()\n"
        # -S: no site module, so no .pth file runs — whichever interpreter runs
        # this, including a bench virtualenv that carries this graft (or
        # frappe_devguard, which would reject the stub frappe) already.
        return subprocess.run(
            [sys.executable, "-S", "-c", prelude + textwrap.dedent(script)],
            env=env,
            capture_output=True,
            text=True,
        )


def node_options(build_source, preload, script="import frappe.build as b; print(json.dumps(b.get_node_env()))"):
    result = run(build_source, preload, script, install_first="frappe_nodebuild.install()" not in script)
    if result.returncode != 0:
        return f"<exit {result.returncode}: {result.stderr.strip().splitlines()[-1:]}>"
    return json.loads(result.stdout)["NODE_OPTIONS"]


print("== preload configured ==")
check(
    "get_node_env() keeps frappe's heap size and adds the preload",
    node_options(STOCK_BUILD, PRELOAD) == f"--max_old_space_size=4096 --require={PRELOAD}",
    node_options(STOCK_BUILD, PRELOAD),
)
check(
    "bundle(), resolving get_node_env at call time, gets the patched one",
    node_options(
        STOCK_BUILD, PRELOAD, "import frappe.build as b; print(json.dumps(b.bundle()))"
    )
    == f"--max_old_space_size=4096 --require={PRELOAD}",
)
check(
    "install() after frappe.build was already imported still patches it",
    node_options(
        STOCK_BUILD,
        PRELOAD,
        "import frappe.build as b\nfrappe_nodebuild.install()\nprint(json.dumps(b.get_node_env()))",
    )
    == f"--max_old_space_size=4096 --require={PRELOAD}",
)
patched_twice = node_options(
    STOCK_BUILD,
    PRELOAD,
    "import frappe.build as b\nfrom frappe_nodebuild import _patch_build\n_patch_build(b)\n"
    "print(json.dumps(b.get_node_env()))",
)
check(
    "patched twice, the preload is required once",
    patched_twice == f"--max_old_space_size=4096 --require={PRELOAD}",
    patched_twice,
)

print("== Frappe 16.50: frappe.bundler, behind a frappe.build shim ==")
BUNDLE = "import frappe.bundler as b; print(json.dumps(b.bundle()))"
WANT = f"--max_old_space_size=4096 --require={PRELOAD}"
check(
    "bundle() gets the preload: it calls bundler's own get_node_env, not the shim's copy",
    node_options(BUNDLER_LAYOUT, PRELOAD, BUNDLE) == WANT,
    node_options(BUNDLER_LAYOUT, PRELOAD, BUNDLE),
)
check(
    "reaching it through the shim, which re-exports bundle(), does too",
    node_options(BUNDLER_LAYOUT, PRELOAD, "import frappe.build as b; print(json.dumps(b.bundle()))") == WANT,
)
shim_env = node_options(
    BUNDLER_LAYOUT,
    PRELOAD,
    "import frappe.bundler\nimport frappe.build as b\nprint(json.dumps(b.get_node_env()))",
)
check("the shim's copy, patched on top of an already patched original, requires the preload once", shim_env == WANT, shim_env)
check(
    "unset, the 16.50 layout is untouched",
    node_options(BUNDLER_LAYOUT, None, BUNDLE) == "--max_old_space_size=4096",
    node_options(BUNDLER_LAYOUT, None, BUNDLE),
)
bundler_moved = run(BUNDLER_MOVED_LAYOUT, PRELOAD, "import frappe.bundler")
check(
    "frappe.bundler without get_node_env fails the import, naming the module and the variable",
    bundler_moved.returncode != 0
    and "frappe.bundler.get_node_env" in bundler_moved.stderr
    and "FRAPPE_NIX_ESBUILD_PRELOAD" in bundler_moved.stderr,
    bundler_moved.stderr.strip().splitlines()[-1:] if bundler_moved.stderr else "",
)

print("== not configured ==")
check(
    "unset, frappe's NODE_OPTIONS is untouched",
    node_options(STOCK_BUILD, None) == "--max_old_space_size=4096",
    node_options(STOCK_BUILD, None),
)
check(
    "blank, frappe's NODE_OPTIONS is untouched",
    node_options(STOCK_BUILD, "  ") == "--max_old_space_size=4096",
    node_options(STOCK_BUILD, "  "),
)
moved_unset = run(MOVED_BUILD, None, "import frappe.build")
check("unset, a moved target is ignored", moved_unset.returncode == 0, moved_unset.stderr)

print("== target moved ==")
moved = run(MOVED_BUILD, PRELOAD, "import frappe.build")
check(
    "set, importing frappe.build fails and names the variable",
    moved.returncode != 0 and "FRAPPE_NIX_ESBUILD_PRELOAD" in moved.stderr,
    moved.stderr.strip().splitlines()[-1:] if moved.stderr else "",
)

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) failed.")
    sys.exit(1)
print("All frappe_nodebuild checks passed.")
