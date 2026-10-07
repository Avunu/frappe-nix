"""The vendor-neutral scan behind ``standards-vendor-neutral`` and ``standards-docs`` (spec S37).

    vendor_neutral.py code <root> <denylist>   # frappe-nix's built-ins: no match anywhere in scope
    vendor_neutral.py docs <root> <denylist>   # docs/app-standards: matches only in example blocks

Each match prints as ``<path>:<line>: <pattern> matches <text>`` and the exit status is 1.

In ``code`` mode the scope is §7 N3a's: all of ``lib/**``, and the package's code and data
without its ``tests/`` and fixtures directories.

In ``docs`` mode a match is allowed on a line containing "Avunu profile example", inside
the fenced code block whose opening fence follows that line (after blank lines only), and
in the frappe-types dependency's source URL (spec §2.9), which is cut out of a line before
it is matched, so the rest of that line is still checked. The spec itself is the contract,
which describes the Avunu profile as its worked example, and is not linted.

Every file in scope is read as bytes and decoded leniently, so no stray byte (not UTF-8,
or NUL) hides the rest of it. A binary that ever needs leaving out is excluded by path.
"""

import re
import sys
from pathlib import Path

CODE_SCOPE = (
	"py/frappe_nix_tools/frappe_nix_tools/**",
	"lib/**",
	"templates/app/**",
	"repo-policy/repo-settings.json",
	"repo-policy/rulesets/**",
	".github/workflows/app-*.yml",
	".github/workflows/fleet-audit.yml",
)
CODE_EXCLUDE = (
	"py/frappe_nix_tools/frappe_nix_tools/**/tests/**",
	"py/frappe_nix_tools/frappe_nix_tools/**/fixtures/**",
	"**/__pycache__/**",
)
DOCS_SCOPE = ("docs/app-standards/*.md",)
DOCS_EXCLUDE = ("docs/app-standards/spec.md",)
EXAMPLE = re.compile(r"avunu profile example", re.IGNORECASE)
# The frappe-types dependency's source (spec §2.9): the URL alone, never its line.
DOCS_ALLOWED = re.compile(r"(?:https?://)?github\.com/Avunu/frappe-types(?![\w-])", re.IGNORECASE)


def patterns(denylist: Path) -> list[re.Pattern]:
	out = []
	for raw in denylist.read_text().splitlines():
		line = raw.strip()
		if line and not line.startswith("#"):
			out.append(re.compile(line, re.IGNORECASE))
	return out


def _glob(pattern: str) -> re.Pattern:
	"""A glob over ``/``-separated paths: ``**`` crosses directories, ``*`` does not."""
	out = ""
	for part in re.split(r"(\*\*/|\*\*|\*)", pattern):
		out += {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*"}.get(part, re.escape(part))
	return re.compile(out)


def _match(rel: str, globs: tuple[str, ...]) -> bool:
	return any(_glob(g).fullmatch(rel) for g in globs)


def files(root: Path, scope: tuple[str, ...], exclude: tuple[str, ...]) -> list[Path]:
	out = []
	for path in sorted(root.rglob("*")):
		rel = path.relative_to(root).as_posix()
		if path.is_file() and _match(rel, scope) and not _match(rel, exclude):
			out.append(path)
	return out


def lines(path: Path) -> list[str]:
	return path.read_bytes().decode("utf-8", errors="replace").splitlines()


def scan(root: Path, denylist: Path, mode: str) -> list[str]:
	pats = patterns(denylist)
	findings = []
	scope, exclude = (CODE_SCOPE, CODE_EXCLUDE) if mode == "code" else (DOCS_SCOPE, DOCS_EXCLUDE)
	for path in files(root, scope, exclude):
		rel = path.relative_to(root).as_posix()
		in_fence = example = armed = False
		for n, text in enumerate(lines(path), 1):
			fence = text.lstrip().startswith("```")
			if mode == "docs":
				if fence:
					if in_fence:
						in_fence = example = False
					else:
						in_fence, example, armed = True, armed, False
					continue
				if not in_fence:
					if EXAMPLE.search(text):
						# The line that introduces an example names its owner.
						armed = True
						continue
					if text.strip():
						armed = False
				if example:
					continue
				text = DOCS_ALLOWED.sub("", text)
			for pat in pats:
				m = pat.search(text)
				if m:
					findings.append(f"{rel}:{n}: {pat.pattern} matches {m.group(0)!r}")
	return findings


def main(argv: list[str]) -> int:
	mode, root, denylist = argv
	if mode not in ("code", "docs"):
		print(f"unknown mode {mode!r}", file=sys.stderr)
		return 2
	findings = scan(Path(root), Path(denylist), mode)
	for f in findings:
		print(f)
	return 1 if findings else 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
