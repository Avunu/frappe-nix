"""The managed-file engine behind ``frappe-nix sync`` (docs/app-standards/spec.md §2, §3).

- ``manifest``: the union of ``frappe_nix_tools/data/manifest.d/*.json`` (entries, the retire
  list and the floors), plus the org profile's ``[[extra-files]]`` and ``[[retire]]``;
- ``discover`` and ``context``: the facts read from the tracked tree and the template context,
  built on the resolved configuration (``frappe_nix_tools.common.config``);
- ``render``, ``regions``, ``floors``, ``blocks``, ``tomlmerge``, ``package_json``, ``configs``
  and ``semantic``: the five strategies, local regions, floors, headers and the comparisons;
- ``engine``: one in-memory plan of every managed file, which ``--write`` applies and
  ``--check`` reports, with retraction for modules that turned off;
- ``bootstrap``: the two phases around the plan (the flake and its lock; the tool locks,
  node-lock seeds and relock);
- ``defaults``: the warning when plain ``recommended`` moves to a newer snapshot.
"""
