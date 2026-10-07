"""The managed-file engine behind ``ironclad sync`` (docs/ironclad/spec.md §2, §3).

- ``manifest``: the union of ``ironclad/data/manifest.d/*.json`` (entries and the retire list);
- ``discover`` and ``context``: the facts read from the tracked tree and the template context;
- ``render`` and ``strategies``: the five strategies, local regions, floors and headers;
- ``engine``: one in-memory plan of every managed file, which ``--write`` applies and
  ``--check`` reports;
- ``bootstrap``: the two phases around the plan (the flake and its lock; the tool locks,
  node-lock seeds and relock).
"""
