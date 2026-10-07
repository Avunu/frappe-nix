"""Local regions: the part of a ``whole`` file the app may edit (spec §3.2).

::

    # frappe-nix:local-begin <name>
    …kept verbatim…
    # frappe-nix:local-end <name>

The template fixes the names and their order. Sync renders the template, then puts back
what the current file has between each pair of markers. A region missing from the
current file is empty; an unknown name, a duplicate or an unbalanced marker is exit 2.
"""

import re

from frappe_nix_tools.common.report import ConfigError

_MARKER = re.compile(r"^[ \t]*# frappe-nix:local-(?P<kind>begin|end) (?P<name>[A-Za-z0-9_-]+)[ \t]*$")


def parse(text: str, path: str, allowed: tuple[str, ...]) -> dict[str, list[str]]:
	"""The lines inside each region of ``text`` (without the markers), by name."""
	out: dict[str, list[str]] = {}
	current: str | None = None
	for number, line in enumerate(text.splitlines(), 1):
		m = _MARKER.match(line)
		if not m:
			if current is not None:
				out[current].append(line)
			continue
		kind, name = m["kind"], m["name"]
		if kind == "begin":
			if current is not None:
				raise ConfigError(f"{path}:{number}: local region {name!r} begins inside {current!r}")
			if name not in allowed:
				raise ConfigError(
					f"{path}:{number}: unknown local region {name!r} (this file has: {', '.join(allowed) or 'none'})"
				)
			if name in out:
				raise ConfigError(f"{path}:{number}: local region {name!r} appears twice")
			out[name] = []
			current = name
		else:
			if current != name:
				raise ConfigError(f"{path}:{number}: local-end {name!r} does not close an open region")
			current = None
	if current is not None:
		raise ConfigError(f"{path}: local region {current!r} is never closed")
	return out


def splice(rendered: str, current: str | None, path: str, allowed: tuple[str, ...]) -> str:
	"""``rendered`` with each region's body taken from ``current`` (the file on disk)."""
	if not allowed:
		return rendered
	bodies = parse(current, path, allowed) if current is not None else {}
	lines = rendered.splitlines(keepends=True)
	out: list[str] = []
	inside: str | None = None
	for line in lines:
		m = _MARKER.match(line.rstrip("\n"))
		if inside is not None:
			if m and m["kind"] == "end":
				out += [b + "\n" for b in bodies.get(inside, [])]
				out.append(line)
				inside = None
			continue
		out.append(line)
		if m and m["kind"] == "begin":
			inside = m["name"]
	return "".join(out)


def bodies(text: str, path: str, allowed: tuple[str, ...]) -> str:
	"""The text of every region of ``text`` joined: what the app contributed."""
	return "\n".join("\n".join(lines) for lines in parse(text, path, allowed).values())


def filled(text: str, path: str, allowed: tuple[str, ...]) -> list[str]:
	"""The names of the regions of ``text`` that hold anything but blank lines."""
	return [name for name, lines in parse(text, path, allowed).items() if any(line.strip() for line in lines)]
