# rename-esign

A stand-in for Avunu/esign, the shape `frappe-rename-app code` has to get right
(docs/ironclad/spec.md §5.10, §7 N1): bundles named after the app
(`esign.desk.bundle.js`, `esign.control.bundle.js`, …) that `hooks.py` names by
file name, a module folder named like the package (`esign/esign/`), dotted paths
in hooks, patches, Python, JS and Jinja, `/assets/esign/` URLs, a standard JSON,
and one bare `esign.legacy.bundle.js` that names no file, which the rename must
leave alone and report. It is never installed; tests/ironclad/rename.nix renames
a copy of it.
