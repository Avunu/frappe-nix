"""``frappe-init``: the scaffolder behind ``nix run github:Avunu/frappe-nix``
(PLAN.md, Track D, D2).

It writes a new bench, converts an existing ``bench init`` bench in place, or
puts a dev environment in one Frappe app's own repository, and hands
``--sync``/``--check`` over to ``frappe-nix sync``. The mode comes from the
target directory (detect.py) unless a flag forces one.

Modules, in the order a run reaches them: args (the command line), config
and presets (what Nix baked), detect (read-only probes), template, secrets and
apps (the steps), pipeline (what scaffold and migrate share), scaffold, migrate
and app_mode (the modes), command (dispatch). It imports no Frappe and runs
on frappe-nix's tool interpreter; git, uv, frappe-nix-workspace and
frappe-nix run as subprocesses.
"""
