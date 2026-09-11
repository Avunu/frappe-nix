#!/usr/bin/env python3
"""Rewrite upstream's realtime test suite to target `frappe_runtime`.

Called by sync-upstream.sh. Two jobs:

1. Point the imports and `patch()` target strings at our package. The endpoint
   URL strings ("/api/method/frappe.realtime.get_user_info") are left alone —
   they name whitelisted methods on the web side, not modules here.
2. Drop `TestPublisherHelpers`. It covers `publish_to_user` and friends from
   upstream's `frappe/realtime/__init__.py`, which is the publish half we do not
   ship; Frappe's own `frappe/realtime.py` stays authoritative for that.
"""

import re
import sys
from pathlib import Path

MODULES = (
	"auth bridge config context dispatch handlers registry server socket util"
).split()


def rewrite(text: str) -> str:
	mods = "|".join(MODULES)
	# from frappe.realtime.<mod> import ...
	text = re.sub(rf"\bfrom frappe\.realtime\.({mods})\b", r"from frappe_runtime.\1", text)
	# from frappe.realtime import <mod> [as alias]
	text = re.sub(rf"\bfrom frappe\.realtime import ({mods})\b", r"from frappe_runtime import \1", text)
	# "frappe.realtime.<mod>...." used as a patch() target
	text = re.sub(rf'(["\'])frappe\.realtime\.({mods})\.', r"\1frappe_runtime.\2.", text)
	return text


def drop_publisher_tests(text: str) -> str:
	lines = text.splitlines(keepends=True)
	start = next(
		(i for i, line in enumerate(lines) if line.startswith("class TestPublisherHelpers")),
		None,
	)
	if start is None:
		return text
	end = next(
		(i for i in range(start + 1, len(lines)) if lines[i].startswith(("class ", "if __name__"))),
		len(lines),
	)
	note = (
		"# NOTE: upstream's TestPublisherHelpers was removed here. It covers the\n"
		"# publish helpers in frappe/realtime/__init__.py, which this package does not\n"
		"# ship -- Frappe's own frappe/realtime.py remains the publisher.\n\n\n"
	)
	return "".join(lines[:start]) + note + "".join(lines[end:])


def main() -> None:
	src, dst = Path(sys.argv[1]), Path(sys.argv[2])
	text = drop_publisher_tests(rewrite(src.read_text()))
	leftover = re.findall(r"^.*\bfrom frappe\.realtime\b.*$", text, re.M)
	if leftover:
		sys.exit("rewrite_tests.py: unrewritten import(s):\n" + "\n".join(leftover))
	dst.parent.mkdir(parents=True, exist_ok=True)
	dst.write_text(text)
	print(f"rewrite_tests.py: wrote {dst} ({len(text.splitlines())} lines)")


if __name__ == "__main__":
	main()
