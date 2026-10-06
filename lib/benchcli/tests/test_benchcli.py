"""Self-contained tests for frappe_benchcli.

Runs without Frappe or bench. A stub ``bench.cli`` is written to disk so the
post-import hook sees a real import, and ``bench-update`` / ``bench-build`` are
shell scripts on a PATH of their own, so the hand-over is a real exec. Each case
runs in a fresh interpreter: the hook acts once per process, and a hand-over
replaces the process.

Run directly (``python test_benchcli.py [lib/scripts.nix]``) or via ``nix flake
check``. Given the path to ``lib/scripts.nix``, it also checks that ``ROUTES``
and the umbrella wrapper's ``case`` arms name the same scripts.
"""

import os
import re
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(HERE)

FAILURES = []

STOCK_CLI = """
import sys

def cli():
    print("STOCK", *sys.argv[1:])
    return 0
"""

MOVED_CLI = """
def main():
    return 0
"""

# Each prints what it was called with; bench-build also exits 3, so the exit
# status of the script is seen to be the status of the command.
SCRIPTS = {
    "bench-update": '#!/bin/sh\necho "bench-update $*"\n',
    "bench-build": '#!/bin/sh\necho "bench-build $*"\nexit 3\n',
}


def check(label, condition, detail=""):
    if condition:
        print(f"ok   {label}")
    else:
        print(f"FAIL {label} {detail}")
        FAILURES.append(label)


def run(argv, *, cli_source=STOCK_CLI, scripts=True, raw=None, install_first=True, body=None):
    """Run ``bench <argv>`` through a stub bench.cli under frappe_benchcli.

    install() runs first, as the .pth bootstrap does, unless ``install_first``
    is False. ``body`` replaces the console script's call to ``cli()``.
    """
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "bench"))
        with open(os.path.join(root, "bench", "__init__.py"), "w") as f:
            f.write("")
        with open(os.path.join(root, "bench", "cli.py"), "w") as f:
            f.write(textwrap.dedent(cli_source))

        bindir = os.path.join(root, "bin")
        os.makedirs(bindir)
        if scripts:
            for name, text in SCRIPTS.items():
                path = os.path.join(bindir, name)
                with open(path, "w") as f:
                    f.write(text)
                os.chmod(path, 0o755)

        # The scripts name /bin/sh absolutely, so a PATH of bindir alone is
        # enough, and it keeps a missing script missing.
        env = {k: v for k, v in os.environ.items() if k != "_FRAPPE_BENCH_RAW"}
        env["PATH"] = bindir
        if raw is not None:
            env["_FRAPPE_BENCH_RAW"] = raw
        env["PYTHONPATH"] = os.pathsep.join([PACKAGE_ROOT, root])

        prelude = "import sys, frappe_benchcli\n"
        if install_first:
            prelude += "frappe_benchcli.install()\n"
        # sys.argv as the console script sees it: the program, then the command.
        prelude += f"sys.argv = {['bench', *argv]!r}\n"
        call = body or "from bench.cli import cli\nraise SystemExit(cli())"
        # -S: no site module, so no .pth file runs — whichever interpreter runs
        # this, including a bench virtualenv that carries this graft already.
        return subprocess.run(
            [sys.executable, "-S", "-c", prelude + textwrap.dedent(call)],
            env=env,
            capture_output=True,
            text=True,
        )


def last(text):
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


print("== handed over ==")
update = run(["update", "--pull"])
check("`bench update --pull` runs bench-update with --pull", update.stdout.strip() == "bench-update --pull", update.stdout)
check("the stock command does not run", "STOCK" not in update.stdout)
check("it says why, once, on stderr", update.stderr.count("frappe_benchcli:") == 1 and "`bench update`" in update.stderr, update.stderr)
build = run(["build", "--app", "wiki"])
check("`bench build --app wiki` runs bench-build with its arguments", build.stdout.strip() == "bench-build --app wiki", build.stdout)
check("the script's exit status is the command's", build.returncode == 3, build.returncode)
check("a bare `bench update` passes no arguments", run(["update"]).stdout.strip() == "bench-update")

print("== left to bench ==")
for label, argv, kwargs in (
    ("another command", ["migrate"], {}),
    ("no command", [], {}),
    ("a flag before the command, as the wrapper treats it", ["--verbose", "build"], {}),
    ("update with _FRAPPE_BENCH_RAW set", ["update"], {"raw": "1"}),
    ("build with _FRAPPE_BENCH_RAW set", ["build", "--app", "wiki"], {"raw": "1"}),
):
    result = run(argv, **kwargs)
    expected = "STOCK" + "".join(f" {a}" for a in argv)
    check(f"{label}: stock bench runs", result.stdout.strip() == expected and result.stderr == "", (result.stdout, result.stderr))

print("== script not on PATH ==")
missing = run(["update"], scripts=False)
check("exits 2", missing.returncode == 2, missing.returncode)
check("names the script and the way out", "bench-update" in missing.stderr and "dev shell" in missing.stderr, missing.stderr)
check("does not run the stock command", "STOCK" not in missing.stdout)
check("another command is unaffected", run(["migrate"], scripts=False).stdout.strip() == "STOCK migrate")

print("== hook ==")
late = run(
    ["update", "--pull"],
    install_first=False,
    body="import bench.cli as m\nfrappe_benchcli.install()\nraise SystemExit(m.cli())",
)
check(
    "install() after bench.cli was imported still patches it",
    late.stdout.strip() == "bench-update --pull",
    late.stdout + late.stderr,
)
twice = run(
    ["migrate"],
    body="import bench.cli as m, frappe_benchcli\nfrappe_benchcli._patch_cli(m)\n"
    "layers = 0\nf = m.cli\nwhile hasattr(f, '__wrapped__'):\n    layers += 1\n    f = f.__wrapped__\n"
    "print('layers', layers)",
)
check("patched twice, wrapped once", last(twice.stdout) == "layers 1", twice.stdout + twice.stderr)

print("== target moved ==")
moved = run(["update"], cli_source=MOVED_CLI, body="import bench.cli")
check(
    "importing bench.cli fails and names the switch",
    moved.returncode != 0 and "_FRAPPE_BENCH_RAW" in moved.stderr,
    last(moved.stderr),
)

print("== routes match the wrapper ==")
if len(sys.argv) > 1:
    sys.path.insert(0, PACKAGE_ROOT)
    import frappe_benchcli

    text = open(sys.argv[1]).read()
    for command, script in frappe_benchcli.ROUTES.items():
        arm = re.search(rf'^\s*{re.escape(command)}\)\s+shift;\s+exec\s+(\S+)\s+"\$@"\s*;;', text, re.M)
        check(
            f"the wrapper sends `bench {command}` to {script}",
            bool(arm) and arm.group(1) == script,
            arm.group(1) if arm else "no such arm in scripts.nix",
        )
else:
    print("skip (pass lib/scripts.nix to check)")

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) failed.")
    sys.exit(1)
print("All frappe_benchcli checks passed.")
