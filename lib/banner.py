"""The frappe-nix dev-shell welcome banner, printed once on `enterShell`.

Takes every fact as a CLI flag -- some are Nix-eval-time constants
(--bench-name, --app-mode), others are read back from the running shell
(--port, via jq against sites/common_site_config.json, since the port
allocator only runs under `devenv up` and a value baked in at eval time
could be stale) -- so this stays a one-shot render with no bench-runtime
knowledge of its own.
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table


def _commands(app_mode: bool, site_name: str) -> list[tuple[str, str]]:
    rows = [("devenv up", "start all services")]
    if site_name:
        rows.append(("default site", site_name))
    if app_mode:
        rows += [
            ("bench-update", "migrate + build"),
            ("nix flake update", "move the pinned apps"),
            ("nix run .#relock", "after any pin or manifest change"),
        ]
    else:
        rows += [
            ("bench-update", "pull + migrate + build"),
            ("bench-update --pull", "pull app submodules only"),
        ]
    rows += [
        ("bench-migrate", "run DB migrations"),
        ("bench-build", "build JS/CSS assets"),
        ("bench-clear-cache", "clear Frappe cache"),
        ("bench-console", "open Frappe Python REPL"),
    ]
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bench-name", required=True)
    parser.add_argument("--python-bin", required=True)
    parser.add_argument("--bench-root", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--app-mode", action="store_true")
    parser.add_argument("--app-name", default="")
    parser.add_argument("--site-name", default="")
    parser.add_argument("--sockets", action="store_true")
    parser.add_argument("--devenv-runtime", default="")
    parser.add_argument("--mail", action="store_true")
    parser.add_argument("--mail-host", default="")
    parser.add_argument("--mail-http-port", default="")
    parser.add_argument("--mail-pop3", action="store_true")
    parser.add_argument("--runtime-warn", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    console = Console()

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    for command, description in _commands(args.app_mode, args.site_name):
        # Table cells parse rich markup by default, and "--pull"/"[db]"-style
        # bracketed placeholders in these command strings look exactly like
        # markup tags -- escape() keeps them literal.
        table.add_row(escape(command), escape(description))

    console.print()
    console.print(
        Panel(
            table,
            title=escape(f"{args.bench_name} — Frappe Bench dev environment"),
            title_align="left",
            border_style="cyan",
        )
    )

    console.print(f"  Python: {args.python_bin}")
    console.print(f"  Bench root: {args.bench_root}")
    if args.app_mode:
        console.print(f"  App: apps/{args.app_name} → this repository (edits are live)")
        console.print("        the bench is generated; deleting it costs a re-copy, not the database")
    console.print(f"  URL: http://127.0.0.1:{args.port}")
    if args.sockets:
        console.print(f"  Sockets: {args.devenv_runtime}/{{mysql,redis,socketio,web}}.sock")
    if args.mail:
        console.print(f"  Mail: ALL outgoing email → Mailpit (http://{args.mail_host}:{args.mail_http_port})")
        incoming = "served from Mailpit POP3" if args.mail_pop3 else "blocked"
        console.print(f"        incoming (IMAP/POP3) is {incoming}")
    if args.site_name:
        console.print(f"  Site: {args.site_name}")
    if args.runtime_warn:
        console.print()
        console.print(
            "  ⚠  frappe-runtime is not in this shell's Python environment (uv.lock predates it):",
            style="yellow",
        )
        console.print(
            "     'devenv up' runs the split web/socketio/worker/scheduler processes until you",
            style="yellow",
        )
        console.print("     re-enter the shell after the reconcile above has landed.", style="yellow")
    console.print()


if __name__ == "__main__":
    main()
