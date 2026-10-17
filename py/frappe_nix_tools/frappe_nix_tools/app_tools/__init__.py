"""The app standards' dev-shell tools: ``frappe-test`` (spec §5.1), ``frappe-demo``
(§5.4) and ``frappe-shots`` (§5.5) (PLAN.md, Track D, D6).

Each runs as ``python -I -m frappe_nix_tools.app_tools.<tool>`` from an
environment that holds frappe-nix-tools (lib/scripts.d/frappe-test.nix,
lib/standards/tools/frappe-demo.nix and frappe-shots.nix), inside an app-mode dev
shell, which supplies devenv, process-compose, provision-site, mariadb-admin and
the bench. None imports Frappe: what needs it runs in the bench's own
interpreter, as a subprocess.
"""
