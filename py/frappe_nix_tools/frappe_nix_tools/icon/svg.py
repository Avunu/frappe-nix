"""The structural icon checks and the tile generator (spec §5.3), in pure Python.

The app ships two SVGs under ``<app>/public/images/``:

- ``<app>-symbolic.svg``, its own source: a 32 x 32 glyph in ``currentColor``, outlines only
  (no stroke, text, images, styles, classes, gradients, filters, masks or clip paths), inside
  the safe area [2, 30] on both axes;
- ``<app>-logo.svg``, the marketplace and apps-screen tile ``frappe-icon tile`` makes from it
  and the app commits: a 1024 x 1024 rounded square (``rx = ry = 293``, 28.6 %) in
  ``org.brand.tile-color``, the glyph in ``org.brand.glyph-color``, centred, its longer side
  573 (56 %). The check compares it with a fresh ``tile`` byte for byte.

The colours are the resolved profile's (S37); nothing here names one.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from frappe_nix_tools.icon import geometry
from frappe_nix_tools.icon.geometry import Box, Matrix

SVG_NS = "http://www.w3.org/2000/svg"
ALLOWED = {"svg", "g", "path", "circle", "rect", "ellipse", "polygon", "polyline", "title"}
FORBIDDEN = {
	"text": "<text> (convert text to outlines)",
	"image": "<image> (an icon is vector outlines only)",
	"style": "<style> (set fill on the shapes)",
	"linearGradient": "a gradient",
	"radialGradient": "a gradient",
	"filter": "a filter",
	"mask": "a mask",
	"clipPath": "a clip path",
}
SYMBOLIC_VIEWBOX = (0.0, 0.0, 32.0, 32.0)
SAFE = (2.0, 30.0)
TILE_SIZE = 1024
TILE_RADIUS = 293  # 28.6 % of 1024
TILE_GLYPH = 573  # 56 % of 1024
GLYPH_TOLERANCE = 12
CENTRE_TOLERANCE = 4


class IconError(ValueError):
	"""A file that is missing or cannot be read as SVG (exit 2)."""


@dataclass(frozen=True)
class Shape:
	"""One drawn element: its tag, its effective fill and stroke, and its box in root space."""

	tag: str
	fill: str
	stroke: str
	box: Box | None


def local(tag: str) -> str:
	return tag.rsplit("}", 1)[-1]


def parse(text: str, label: str) -> ET.Element:
	if "<!ENTITY" in text or "<!DOCTYPE" in text:
		raise IconError(f"{label}: a DOCTYPE or entity declaration is not allowed in an icon")
	try:
		root = ET.fromstring(text)
	except ET.ParseError as e:
		raise IconError(f"{label} is not well-formed XML: {e}") from e
	if local(root.tag) != "svg":
		raise IconError(f"{label}: the root element is <{local(root.tag)}>, not <svg>")
	return root


def viewbox(root: ET.Element) -> tuple[float, ...] | None:
	values = geometry.numbers(root.get("viewBox", ""))
	return tuple(values) if len(values) == 4 else None


def _number(el: ET.Element, attr: str, default: float = 0.0) -> float:
	value = el.get(attr)
	if value is None:
		return default
	found = geometry.numbers(value)
	if len(found) != 1 or not re.fullmatch(r"\s*[-+0-9.eE]+(px)?\s*", value):
		raise IconError(f"<{local(el.tag)} {attr}={value!r}> is not a plain number")
	return found[0]


def _points(el: ET.Element, m: Matrix) -> list[geometry.Point]:
	tag = local(el.tag)
	if tag == "path":
		try:
			return geometry.extent(geometry.segments(el.get("d", "")), m)
		except geometry.GeometryError as e:
			raise IconError(str(e)) from e
	if tag == "rect":
		x, y = _number(el, "x"), _number(el, "y")
		w, h = _number(el, "width"), _number(el, "height")
		return [geometry.apply(m, p) for p in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))]
	if tag == "circle":
		r = _number(el, "r")
		return geometry.ellipse(_number(el, "cx"), _number(el, "cy"), r, r, m)
	if tag == "ellipse":
		return geometry.ellipse(_number(el, "cx"), _number(el, "cy"), _number(el, "rx"), _number(el, "ry"), m)
	if tag in ("polygon", "polyline"):
		values = geometry.numbers(el.get("points", ""))
		return [geometry.apply(m, (values[i], values[i + 1])) for i in range(0, len(values) - 1, 2)]
	return []


def shapes(root: ET.Element) -> list[Shape]:
	"""Every drawn element under ``root`` with its inherited fill and stroke and its box."""
	out: list[Shape] = []

	def walk(el: ET.Element, m: Matrix, fill: str, stroke: str) -> None:
		try:
			m = geometry.multiply(m, geometry.transform(el.get("transform")))
		except geometry.GeometryError as e:
			raise IconError(str(e)) from e
		fill = el.get("fill", fill)
		stroke = el.get("stroke", stroke)
		tag = local(el.tag)
		if tag in ("path", "rect", "circle", "ellipse", "polygon", "polyline"):
			out.append(Shape(tag, fill, stroke, geometry.box(_points(el, m))))
		for child in el:
			walk(child, m, fill, stroke)

	# An SVG's initial fill is black, and its initial stroke none.
	walk(root, geometry.IDENTITY, "black", "none")
	return out


def fmt(value: float) -> str:
	"""A number as the tile writes it: at most six decimals, no trailing zeros, never ``-0``."""
	text = f"{value:.6f}".rstrip("0").rstrip(".")
	return "0" if text in ("-0", "") else text


def symbolic_problems(text: str, label: str) -> list[str]:
	"""The symbolic icon's rules (§5.3); an empty list when it passes."""
	root = parse(text, label)
	out = []
	vb = viewbox(root)
	if vb != SYMBOLIC_VIEWBOX:
		out.append(f'viewBox is {root.get("viewBox")!r}, not "0 0 32 32"')
	for el in root.iter():
		tag = local(el.tag)
		if tag in FORBIDDEN:
			out.append(f"has {FORBIDDEN[tag]}")
		elif tag not in ALLOWED:
			out.append(f"has <{tag}>; only {', '.join(sorted(ALLOWED))} are allowed")
		if "class" in el.attrib:
			out.append(f"<{tag}> has a class attribute; styles are not allowed")
		if "style" in el.attrib:
			out.append(f"<{tag}> has a style attribute; set fill as an attribute")
		for attr in el.attrib:
			if local(attr) in ("mask", "clip-path", "filter"):
				out.append(f"<{tag}> uses {local(attr)}")
	found = shapes(root)
	for s in found:
		if s.fill not in ("currentColor", "none"):
			what = "has no fill (black by default)" if s.fill == "black" else f"has fill {s.fill!r}"
			out.append(f'<{s.tag}> {what}: every fill is "currentColor" or "none"')
		if s.stroke != "none":
			out.append(f"<{s.tag}> has stroke {s.stroke!r}: convert strokes to outlines (filled paths)")
	bbox = geometry.union(s.box for s in found if s.fill != "none")
	if bbox is None:
		out.append("draws nothing")
	elif min(bbox[0], bbox[1]) < SAFE[0] - 1e-6 or max(bbox[2], bbox[3]) > SAFE[1] + 1e-6:
		out.append(
			"the glyph reaches outside the safe area [2, 30]: its box is"
			f" ({fmt(bbox[0])}, {fmt(bbox[1])}) to ({fmt(bbox[2])}, {fmt(bbox[3])})"
		)
	return sorted(set(out), key=out.index)


def _escape(value: str) -> str:
	return value.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


def _serialise(el: ET.Element) -> str:
	tag = local(el.tag)
	attrs = "".join(
		f' {local(k)}="{_escape(v)}"'
		for k, v in el.attrib.items()
		if not (local(k) == "fill" and v == "currentColor")
	)
	children = "".join(_serialise(c) for c in el if local(c.tag) != "title")
	return f"<{tag}{attrs}>{children}</{tag}>" if children else f"<{tag}{attrs}/>"


def tile(symbolic: str, tile_color: str, glyph_color: str, label: str = "symbolic icon") -> str:
	"""``<app>-logo.svg`` from the symbolic icon: deterministic, so the check compares bytes."""
	root = parse(symbolic, label)
	bbox = geometry.union(s.box for s in shapes(root) if s.fill != "none")
	if bbox is None:
		raise IconError(f"{label} draws nothing to put on the tile")
	width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
	scale = TILE_GLYPH / max(width, height)
	cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
	glyph = "".join(_serialise(c) for c in root if local(c.tag) != "title")
	half = TILE_SIZE // 2
	return (
		f'<svg xmlns="{SVG_NS}" viewBox="0 0 {TILE_SIZE} {TILE_SIZE}">'
		f'<rect width="{TILE_SIZE}" height="{TILE_SIZE}" rx="{TILE_RADIUS}" ry="{TILE_RADIUS}" fill="{tile_color}"/>'
		f'<g fill="{glyph_color}" transform="translate({half} {half}) scale({fmt(scale)}) translate({fmt(-cx)} {fmt(-cy)})">'
		f"{glyph}</g></svg>\n"
	)


def tile_problems(text: str, label: str, tile_color: str, glyph_color: str) -> list[str]:
	"""The tile's rules (§5.3), except freshness, which compares it with ``tile``."""
	root = parse(text, label)
	out = []
	if viewbox(root) != (0.0, 0.0, float(TILE_SIZE), float(TILE_SIZE)):
		out.append(f'viewBox is {root.get("viewBox")!r}, not "0 0 1024 1024"')
	children = [c for c in root if local(c.tag) != "title"]
	first = children[0] if children else None
	if first is None or local(first.tag) != "rect":
		out.append("the first element is not the tile's <rect>")
	else:
		try:
			sizes = {a: _number(first, a) for a in ("width", "height", "rx", "ry")}
		except IconError as e:
			sizes = {}
			out.append(str(e))
		if sizes and (sizes["width"] != TILE_SIZE or sizes["height"] != TILE_SIZE):
			out.append(f"the tile <rect> is {fmt(sizes['width'])} x {fmt(sizes['height'])}, not 1024 x 1024")
		for a in ("rx", "ry"):
			if sizes and abs(sizes[a] - TILE_RADIUS) > 1:
				out.append(f"the tile <rect> {a} is {fmt(sizes[a])}, not {TILE_RADIUS} (±1)")
		fill = first.get("fill", "")
		if fill.lower() != tile_color.lower():
			out.append(f"the tile's fill is {fill!r}, not org.brand.tile-color {tile_color!r}")
	glyph_root = ET.Element("svg")
	glyph_root.extend(children[1:])
	glyph = shapes(glyph_root)
	for s in glyph:
		if s.fill != "none" and s.fill.lower() != glyph_color.lower():
			out.append(f"a glyph <{s.tag}> has fill {s.fill!r}, not org.brand.glyph-color {glyph_color!r}")
		if s.stroke != "none":
			out.append(f"a glyph <{s.tag}> has a stroke")
	bbox = geometry.union(s.box for s in glyph if s.fill != "none")
	if bbox is None:
		out.append("the tile has no glyph")
	else:
		cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
		half = TILE_SIZE / 2
		if max(abs(cx - half), abs(cy - half)) > CENTRE_TOLERANCE:
			out.append(f"the glyph is centred at ({fmt(cx)}, {fmt(cy)}), not (512, 512) ±{CENTRE_TOLERANCE}")
		longer = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
		if abs(longer - TILE_GLYPH) > GLYPH_TOLERANCE:
			out.append(f"the glyph's longer side is {fmt(longer)}, not {TILE_GLYPH} (±{GLYPH_TOLERANCE})")
	return out
