"""Bounding boxes of SVG shapes, in pure Python (spec §5.3, "a path-bbox parser").

A path becomes absolute segments (moves, lines, quadratic and cubic Béziers; an arc becomes a
fine polyline along it). Its box in the root's user space is exact for lines and Béziers
under any affine transform: the image of a Bézier is the Bézier of the mapped control
points, and its extremes are the end points and the parameters where the derivative is zero.
Circles and ellipses are sampled finely along their outline.
"""

import itertools
import math
import re
from collections.abc import Iterable

Point = tuple[float, float]
Matrix = tuple[float, float, float, float, float, float]  # a b c d e f, as SVG's matrix()
Box = tuple[float, float, float, float]  # min x, min y, max x, max y
Segment = tuple[str, list[Point]]  # ("M" | "L", [p]), ("C", [p0, p1, p2, p3]), ("Q", [p0, p1, p2])

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"([MmLlHhVvCcSsQqTtAaZz])|({_NUMBER})")
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
ARC_STEPS = 64
ELLIPSE_STEPS = 256
ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


class GeometryError(ValueError):
	"""A path, a transform or a number the parser cannot read."""


def numbers(text: str) -> list[float]:
	return [float(n) for n in re.findall(_NUMBER, text or "")]


def multiply(m: Matrix, n: Matrix) -> Matrix:
	"""``m · n``: ``n`` applied first, then ``m``."""
	a, b, c, d, e, f = m
	a2, b2, c2, d2, e2, f2 = n
	return (
		a * a2 + c * b2,
		b * a2 + d * b2,
		a * c2 + c * d2,
		b * c2 + d * d2,
		a * e2 + c * f2 + e,
		b * e2 + d * f2 + f,
	)


def apply(m: Matrix, p: Point) -> Point:
	a, b, c, d, e, f = m
	return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def transform(text: str | None) -> Matrix:
	"""A ``transform`` attribute as one matrix."""
	out = IDENTITY
	if not text:
		return out
	if _TRANSFORM.sub("", text).replace(",", " ").strip():
		raise GeometryError(f"transform {text!r} is not one this parser reads")
	for name, args in _TRANSFORM.findall(text):
		v = numbers(args)
		if name == "matrix" and len(v) == 6:
			step: Matrix = (v[0], v[1], v[2], v[3], v[4], v[5])
		elif name == "translate" and len(v) in (1, 2):
			step = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) == 2 else 0.0)
		elif name == "scale" and len(v) in (1, 2):
			step = (v[0], 0.0, 0.0, v[1] if len(v) == 2 else v[0], 0.0, 0.0)
		elif name == "rotate" and len(v) in (1, 3):
			r = math.radians(v[0])
			step = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)
			if len(v) == 3:
				step = multiply(
					multiply((1.0, 0.0, 0.0, 1.0, v[1], v[2]), step), (1.0, 0.0, 0.0, 1.0, -v[1], -v[2])
				)
		elif name == "skewX" and len(v) == 1:
			step = (1.0, 0.0, math.tan(math.radians(v[0])), 1.0, 0.0, 0.0)
		elif name == "skewY" and len(v) == 1:
			step = (1.0, math.tan(math.radians(v[0])), 0.0, 1.0, 0.0, 0.0)
		else:
			raise GeometryError(f"transform {name}({args}) has the wrong number of arguments")
		out = multiply(out, step)
	return out


def _arc(p0: Point, rx: float, ry: float, phi_deg: float, large: bool, sweep: bool, p1: Point) -> list[Point]:
	"""Points along an SVG arc, after its start (endpoint to centre parameterisation, SVG 1.1 F.6.5)."""
	if p0 == p1:
		return []
	rx, ry = abs(rx), abs(ry)
	if not rx or not ry:
		return [p1]
	phi = math.radians(phi_deg % 360)
	cos, sin = math.cos(phi), math.sin(phi)
	dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
	x1, y1 = cos * dx + sin * dy, -sin * dx + cos * dy
	scale = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
	if scale > 1:
		rx, ry = rx * math.sqrt(scale), ry * math.sqrt(scale)
	num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
	den = rx * rx * y1 * y1 + ry * ry * x1 * x1
	coef = math.sqrt(max(0.0, num / den)) if den else 0.0
	if large == sweep:
		coef = -coef
	cx1, cy1 = coef * rx * y1 / ry, -coef * ry * x1 / rx
	cx = cos * cx1 - sin * cy1 + (p0[0] + p1[0]) / 2
	cy = sin * cx1 + cos * cy1 + (p0[1] + p1[1]) / 2

	def angle(u: Point, v: Point) -> float:
		return math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])

	start = ((x1 - cx1) / rx, (y1 - cy1) / ry)
	theta = angle((1.0, 0.0), start)
	delta = angle(start, ((-x1 - cx1) / rx, (-y1 - cy1) / ry))
	if not sweep and delta > 0:
		delta -= 2 * math.pi
	elif sweep and delta < 0:
		delta += 2 * math.pi
	out = []
	for i in range(1, ARC_STEPS + 1):
		a = theta + delta * i / ARC_STEPS
		x, y = rx * math.cos(a), ry * math.sin(a)
		out.append((cos * x - sin * y + cx, sin * x + cos * y + cy))
	return out


def segments(d: str) -> list[Segment]:
	"""Path data as absolute segments; anything that is not path data is a ``GeometryError``."""
	if re.sub(rf"[MmLlHhVvCcSsQqTtAaZz]|{_NUMBER}|[\s,]", "", d or ""):
		raise GeometryError(f"path data {d[:40]!r} holds something that is neither a command nor a number")
	tokens = _TOKEN.findall(d or "")
	out: list[Segment] = []
	i = 0
	cur: Point = (0.0, 0.0)
	start: Point = (0.0, 0.0)
	control: Point | None = None  # the last curve's second control point, for S and T
	previous = ""
	cmd = ""

	def take(n: int) -> list[float]:
		nonlocal i
		if i + n > len(tokens) or any(not tokens[i + k][1] for k in range(n)):
			raise GeometryError(f"path data {d[:40]!r}: {cmd} needs {n} numbers")
		values = [float(tokens[i + k][1]) for k in range(n)]
		i += n
		return values

	while i < len(tokens):
		if tokens[i][0]:
			cmd = tokens[i][0]
			i += 1
		elif not cmd:
			raise GeometryError(f"path data {d[:40]!r} starts with a number")
		c = cmd.upper()
		ox, oy = cur if cmd.islower() else (0.0, 0.0)
		if c == "Z":
			out.append(("L", [start]))
			cur, control, previous = start, None, c
			continue
		values = take(ARGS[c])
		if c == "M":
			cur = start = (ox + values[0], oy + values[1])
			out.append(("M", [cur]))
			cmd = "l" if cmd.islower() else "L"  # further pairs are lines
			control = None
		elif c == "L":
			cur = (ox + values[0], oy + values[1])
			out.append(("L", [cur]))
			control = None
		elif c == "H":
			cur = (ox + values[0], cur[1])
			out.append(("L", [cur]))
			control = None
		elif c == "V":
			cur = (cur[0], oy + values[0])
			out.append(("L", [cur]))
			control = None
		elif c in ("C", "S"):
			if c == "C":
				p1 = (ox + values[0], oy + values[1])
				rest = values[2:]
			else:
				p1 = (
					(2 * cur[0] - control[0], 2 * cur[1] - control[1])
					if control and previous in ("C", "S")
					else cur
				)
				rest = values
			p2, p3 = (ox + rest[0], oy + rest[1]), (ox + rest[2], oy + rest[3])
			out.append(("C", [cur, p1, p2, p3]))
			control, cur = p2, p3
		elif c in ("Q", "T"):
			if c == "Q":
				p1 = (ox + values[0], oy + values[1])
				rest = values[2:]
			else:
				p1 = (
					(2 * cur[0] - control[0], 2 * cur[1] - control[1])
					if control and previous in ("Q", "T")
					else cur
				)
				rest = values
			p2 = (ox + rest[0], oy + rest[1])
			out.append(("Q", [cur, p1, p2]))
			control, cur = p1, p2
		elif c == "A":
			end = (ox + values[5], oy + values[6])
			out += [
				("L", [p])
				for p in _arc(cur, values[0], values[1], values[2], bool(values[3]), bool(values[4]), end)
			]
			cur, control = end, None
		previous = c
	return out


def _turning(coords: list[float]) -> list[float]:
	"""The parameters in (0, 1) where a quadratic or cubic Bézier coordinate turns."""
	if len(coords) == 3:
		p0, p1, p2 = coords
		den = p0 - 2 * p1 + p2
		return [t for t in ([(p0 - p1) / den] if den else []) if 0 < t < 1]
	p0, p1, p2, p3 = coords
	a = 3 * (-p0 + 3 * p1 - 3 * p2 + p3)
	b = 6 * (p0 - 2 * p1 + p2)
	c = 3 * (p1 - p0)
	if abs(a) < 1e-12:
		return [t for t in ([-c / b] if b else []) if 0 < t < 1]
	disc = b * b - 4 * a * c
	if disc < 0:
		return []
	root = math.sqrt(disc)
	return [t for t in ((-b + root) / (2 * a), (-b - root) / (2 * a)) if 0 < t < 1]


def _bezier(points: list[Point], t: float) -> Point:
	while len(points) > 1:
		points = [
			((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1]) for a, b in itertools.pairwise(points)
		]
	return points[0]


def extent(segs: list[Segment], m: Matrix = IDENTITY) -> list[Point]:
	"""Points whose bounding box is the mapped path's."""
	out: list[Point] = []
	for kind, points in segs:
		mapped = [apply(m, p) for p in points]
		if kind in ("M", "L"):
			out += mapped
			continue
		out += [mapped[0], mapped[-1]]
		for axis in (0, 1):
			out += [_bezier(mapped, t) for t in _turning([p[axis] for p in mapped])]
	return out


def ellipse(cx: float, cy: float, rx: float, ry: float, m: Matrix = IDENTITY) -> list[Point]:
	return [
		apply(
			m,
			(
				cx + rx * math.cos(2 * math.pi * k / ELLIPSE_STEPS),
				cy + ry * math.sin(2 * math.pi * k / ELLIPSE_STEPS),
			),
		)
		for k in range(ELLIPSE_STEPS)
	]


def box(points: Iterable[Point]) -> Box | None:
	pts = list(points)
	if not pts:
		return None
	xs, ys = [p[0] for p in pts], [p[1] for p in pts]
	return (min(xs), min(ys), max(xs), max(ys))


def union(boxes: Iterable[Box | None]) -> Box | None:
	real = [b for b in boxes if b is not None]
	if not real:
		return None
	return (
		min(b[0] for b in real),
		min(b[1] for b in real),
		max(b[2] for b in real),
		max(b[3] for b in real),
	)
