"""The bench's apps and its workspace root, in Python (PLAN.md, Track D, D3):
``bench get-app``, ``bench remove-app``, ``bench new-app``, ``reconcile-apps``
and the shell-entry root sync.

Each was bash in lib/scripts.nix, lib/rename/reconcile.nix and
lib/root-sync.nix; tests/bench-get-app.sh, bench-remove-app.sh,
bench-new-app.sh, reconcile-apps.sh, root-sync.sh and the rename checks are
their acceptance suite, run unchanged against these. Every clone is
python-devops-nix's lib.sources (`frappe-nix-sources clone`), in the shape shell
entry makes.
"""
