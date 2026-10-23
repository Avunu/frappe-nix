"""``frappe-nix-bench-patches <shipped-patches.txt> [bench-root]``: reconcile a
bench root's patches.txt with the patch list frappe-bench ships. Run from the
dev shell's enterShell. Was bash in lib/bench-patches.nix;
tests/bench-patches.sh is its acceptance suite.

``bench update`` begins with ``bench.patches.run()``, which imports and runs
every entry in ``<bench-package>/patches/patches.txt`` that the bench root's own
patches.txt does not already record as done. bench deleted the v3 and v4 patch
*modules* in 2022 (frappe/bench a84239d) but left their names in the shipped
list, so a bench root without that record dies on the very first entry
(``ModuleNotFoundError: No module named 'bench.patches.v3'``) and stays dead:
``run()``'s ``finally`` rewrites the root file from the still-empty executed
list, so every retry fails the same way.

A classic bench never hits this because ``bench init`` copies the shipped list
into the bench root (``Bench.setup.patches``): a bench created today is born
with every entry marked done. frappe-nix builds its benches from Nix and never
runs ``bench init``, and patches.txt is gitignored, so the record is absent in a
fresh scaffold and in every fresh clone of an existing bench.

Recording *all* of them, not just the v3 and v4 entries that cannot import, is
deliberate, and is what ``bench init`` does. Every surviving (v5) patch does
something frappe-nix owns: ``sudo bench setup supervisor`` and ``sudoers``, the
backup crontab, ``live_reload`` in common_site_config.json (a reconciled,
committed file here), relocating archived_sites/. Under Nix they are not
historical no-ops, they are wrong.

Runs on every shell entry: standard library only, and quiet unless it changes
something.
"""

import os
import sys
from collections.abc import Sequence

NAME = "frappe-nix-bench-patches"


def entries(text: str) -> list[str]:
	"""The patch entries of a patches.txt, as bench compares them: each line
	stripped of surrounding blanks, empty lines and lines that *start* with '#'
	skipped. The rest is verbatim, so the "#2" on ``bench.patches.v4.install_yarn
	#2`` is part of the entry, not a comment."""
	out = []
	for raw in text.split("\n"):
		line = raw.strip(" \t")
		if line and not line.startswith("#"):
			out.append(line)
	return out


def _blank(line: str) -> bool:
	return not line.strip(" \t\n\r\v\f")


def reconcile(shipped: str, root: str) -> int | None:
	"""Record every SHIPPED entry ROOT/patches.txt lacks; how many, or None when
	there is no shipped list (no frappe-bench in this environment, so nothing
	claims to run them)."""
	if not os.path.isfile(shipped):
		return None
	with open(shipped, errors="surrogateescape") as f:
		wanted = entries(f.read())
	target = os.path.join(root, "patches.txt")
	try:
		with open(target, errors="surrogateescape") as f:
			existing = f.read()
	except FileNotFoundError:
		existing = None
	have = set(existing.split("\n")) if existing is not None else set()
	missing = [line for line in wanted if line not in have]
	if not missing:
		return 0
	# Rewrite rather than append: a bench left behind by a failed `bench update`
	# holds a lone blank line, and appending to a file with no trailing newline
	# would splice two entries into one. Existing entries keep their order — a
	# migrated bench's record of what it really ran cannot be reconstructed.
	kept = [line for line in (existing or "").split("\n") if not _blank(line)]
	import tempfile  # only now: it costs more than the rest of this together

	fd, tmp = tempfile.mkstemp(prefix="patches.txt.", dir=root or ".")
	try:
		with os.fdopen(fd, "w", errors="surrogateescape") as f:
			f.writelines(line + "\n" for line in [*kept, *missing])
		os.replace(tmp, target)
	except BaseException:
		os.unlink(tmp)
		raise
	return len(missing)


def main(argv: Sequence[str] | None = None) -> int:
	args = list(sys.argv[1:] if argv is None else argv)
	if not args:
		print(f"usage: {NAME} <shipped-patches.txt> [bench-root]", file=sys.stderr)
		return 2
	recorded = reconcile(args[0], args[1] if len(args) > 1 else ".")
	if recorded:
		print(
			f"patches.txt: recorded {recorded} bench patch(es) as done (frappe-nix benches do not run them)"
		)
	return 0


if __name__ == "__main__":
	sys.exit(main())
