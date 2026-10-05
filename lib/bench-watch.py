"""The dev shell's `watch` process: `bench watch`, made affordable.

Three differences from frappe.build.watch(), which this otherwise follows line
for line (v15 and v16):

- The esbuild target. frappe.build.watch() never passes one, so esbuild.js
  falls back to es2017 whatever esbuild_target or ESBUILD_TARGET say, and
  es2017 can't lower async generators or BigInt literals (see the
  esbuildTarget option). The first compile then fails outright on bundles
  `bench build` handles fine, leaving sites/assets/assets.json naming files
  that no longer exist. Resolved the way `bench build` resolves it
  (frappe/commands/utils.py): common_site_config.json's esbuild_target first,
  then ESBUILD_TARGET.

- Which apps. Upstream watches every app on the bench, and keeps every one of
  their dependency graphs in memory for as long as it runs. Most of that is
  apps nobody here edits: measured on one bench, frappe's own bundles were
  1.4 GB of a 2.5 GB watcher. By default this skips any app whose hooks.py
  names a publisher in --exclude-publisher (Frappe Technologies, from the
  module): their sources change only when a pin moves, and `bench update`
  rebuilds after every pull. An explicit --apps list overrides the rule.

- Right-to-left stylesheets, with --skip-rtl. esbuild.js compiles every
  stylesheet twice, once more through rtlcss, which doubles the Sass work —
  the slowest part of the build — for a variant a left-to-right dev site never
  loads. The --preload module turns that second build into a no-op; `bench
  build` still produces it.

- Native Sass, with --sass. frappe compiles stylesheets with Dart Sass compiled
  to JavaScript; --sass names a sass-embedded module (lib/sass-embedded.nix)
  that the --preload module hands to frappe's require("sass") instead — the
  same API on the native compiler, 3–6x faster on the stylesheets measured.

Run from sites/, as bench runs every frappe command, for frappe.init("") to find
the sites.
"""

import argparse
import ast
import os
import signal


def publisher_of(hooks_path):
    """`app_publisher` from an app's hooks.py, read without importing it.

    Parsed rather than imported: importing hooks.py runs it, and an app whose
    hooks fail to import should be watched, not take the watcher down. Every
    Frappe app sets it to a plain string — `bench new-app` writes it that way.
    """
    try:
        with open(hooks_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), hooks_path)
    except (OSError, SyntaxError, ValueError):
        return ""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "app_publisher" for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    return ""


def select_apps(all_apps, explicit, excluded_publishers, publisher):
    """(watched, skipped), both in all_apps order.

    `explicit` (a list, or None) wins outright. Otherwise an app is skipped
    when any excluded publisher appears in its own, ignoring case — "Frappe
    Technologies" covers "Frappe Technologies Pvt. Ltd." too.
    """
    if explicit is not None:
        return [a for a in explicit], [a for a in all_apps if a not in explicit]

    needles = [p.casefold() for p in excluded_publishers if p]
    watched, skipped = [], []
    for app in all_apps:
        own = publisher(app).casefold()
        (skipped if any(n in own for n in needles) else watched).append(app)
    return watched, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(prog="frappe-nix-bench-watch")
    parser.add_argument("--apps", help="comma-separated apps to watch, overriding --exclude-publisher")
    parser.add_argument("--exclude-publisher", action="append", default=[])
    parser.add_argument("--skip-rtl", action="store_true")
    parser.add_argument("--sass", help="sass-embedded module directory to compile stylesheets with")
    parser.add_argument("--preload", help="module the build's node loads with --require")
    args = parser.parse_args(argv)

    import frappe
    import frappe.build
    from frappe.commands import popen
    from frappe.utils import cint, get_bench_path

    frappe.init("")
    frappe.build.setup()

    apps_dir = os.path.join(get_bench_path(), "apps")
    explicit = None if args.apps is None else [a for a in args.apps.split(",") if a]
    watched, skipped = select_apps(
        frappe.get_all_apps(True),
        explicit,
        args.exclude_publisher,
        lambda app: publisher_of(os.path.join(apps_dir, app, app, "hooks.py")),
    )

    if skipped:
        print(
            f"watch: not watching {', '.join(skipped)}. "
            "After changing one, run `bench build --app <name>`.",
            flush=True,
        )
    if not watched:
        # Stay up: process-compose would read an exit as a crash and restart us.
        print("watch: no apps to watch.", flush=True)
        signal.pause()
        return

    command = "yarn run watch --apps " + ",".join(watched)
    if cint(os.environ.get("LIVE_RELOAD", frappe.conf.live_reload)):
        command += " --live-reload"
    target = frappe.conf.get("esbuild_target") or os.environ.get("ESBUILD_TARGET")
    if target:
        command += f" --esbuild-target {target}"

    env = frappe.build.get_node_env()
    if args.preload:
        flag = f"--require={args.preload}"
        options = env.get("NODE_OPTIONS", "")
        # frappe_nodebuild has usually added it already.
        if flag not in options.split():
            env["NODE_OPTIONS"] = f"{options} {flag}".strip()
    if args.skip_rtl:
        env["FRAPPE_NIX_SKIP_RTL"] = "1"
    if args.sass:
        env["FRAPPE_NIX_SASS"] = args.sass

    frappe.build.check_node_executable()
    popen(command, cwd=frappe.get_app_path("frappe", ".."), env=env)


if __name__ == "__main__":
    main()
