"""N5's acceptance cases for ``frappe-icon`` (docs/app-standards/spec.md §7 "N5: product tools"):
the structural rules on the good pair and on each bad fixture, the tile generator, the
bounding boxes, and the PNG decoding the raster rules use. The raster rules themselves need
resvg and run in tests/standards/product.nix.
"""

import struct
import unittest
import zlib

from frappe_nix_tools.icon import geometry, raster, svg
from scaffold_helpers import AppCase
from test_listing import LOGO, SYMBOLIC

TILE = "#336699"
GLYPH = "#FFFFFF"


def symbolic(body: str, viewbox: str = "0 0 32 32") -> str:
	return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">{body}</svg>\n'


SHIELD = '<path fill="currentColor" d="M16 4L26 8V15C26 21 21.5 25.5 16 28C10.5 25.5 6 21 6 15V8Z"/>'


class TestStructural(unittest.TestCase):
	def test_the_good_pair(self):
		self.assertEqual(svg.symbolic_problems(SYMBOLIC, "s"), [])
		self.assertEqual(svg.tile_problems(LOGO, "l", TILE, GLYPH), [])
		self.assertEqual(svg.tile(SYMBOLIC, TILE, GLYPH), LOGO)

	def test_bad_symbolic_icons(self):
		cases = {
			"text": (symbolic(SHIELD + '<text x="4" y="20" fill="currentColor">A</text>'), "<text>"),
			"viewBox": (symbolic(SHIELD, "0 0 32 24"), "viewBox is '0 0 32 24'"),
			"stroke": (symbolic(SHIELD.replace("/>", ' stroke="black"/>')), "has stroke 'black'"),
			"safe area": (
				symbolic('<rect fill="currentColor" x="1" y="4" width="20" height="20"/>'),
				"safe area",
			),
			"fill": (symbolic(SHIELD.replace("currentColor", "#ff0000")), "has fill '#ff0000'"),
			"no fill": (symbolic(SHIELD.replace(' fill="currentColor"', "")), "no fill"),
			"style": (symbolic("<style>path{fill:red}</style>" + SHIELD), "<style>"),
			"class": (symbolic(SHIELD.replace("<path ", '<path class="x" ')), "class attribute"),
			"gradient": (symbolic('<linearGradient id="g"/>' + SHIELD), "a gradient"),
		}
		for name, (text, expected) in cases.items():
			with self.subTest(name):
				problems = svg.symbolic_problems(text, name)
				self.assertTrue(any(expected in p for p in problems), problems)

	def test_bad_tiles(self):
		stale = LOGO.replace("M16 4L26", "M16 5L26")
		self.assertNotEqual(stale, svg.tile(SYMBOLIC, TILE, GLYPH))
		self.assertTrue(
			any(
				"not org.brand.tile-color" in p
				for p in svg.tile_problems(LOGO.replace(TILE, "#000000"), "l", TILE, GLYPH)
			)
		)
		off_centre = LOGO.replace("translate(512 512)", "translate(540 512)")
		self.assertTrue(any("centred at" in p for p in svg.tile_problems(off_centre, "l", TILE, GLYPH)))
		small = LOGO.replace("scale(23.875)", "scale(20)")
		self.assertTrue(any("longer side" in p for p in svg.tile_problems(small, "l", TILE, GLYPH)))
		radius = LOGO.replace('rx="293"', 'rx="200"')
		self.assertTrue(any("rx is 200" in p for p in svg.tile_problems(radius, "l", TILE, GLYPH)))

	def test_unreadable_svg(self):
		with self.assertRaises(svg.IconError):
			svg.symbolic_problems("<svg", "s")
		with self.assertRaises(svg.IconError):
			svg.symbolic_problems('<!DOCTYPE svg [<!ENTITY x "y">]><svg/>', "s")


def bbox(points: list[geometry.Point]) -> geometry.Box:
	found = geometry.box(points)
	assert found is not None
	return found


class TestGeometry(unittest.TestCase):
	def test_cubic_extremes(self):
		box = bbox(geometry.extent(geometry.segments("M0 0C0 10 10 10 10 0")))
		self.assertAlmostEqual(box[3], 7.5)
		self.assertEqual((box[0], box[2]), (0.0, 10.0))

	def test_relative_and_shorthand(self):
		box = bbox(geometry.extent(geometry.segments("m2 2h10v10h-10z")))
		self.assertEqual(box, (2.0, 2.0, 12.0, 12.0))

	def test_arc_and_transform(self):
		segs = geometry.segments("M4 16A12 12 0 0 1 28 16")
		box = bbox(geometry.extent(segs))
		self.assertAlmostEqual(box[1], 4.0, places=2)
		moved = bbox(geometry.extent(segs, geometry.transform("translate(1 2) scale(2)")))
		self.assertAlmostEqual(moved[0], 9.0, places=6)

	def test_rotated_box_is_exact(self):
		square = geometry.segments("M0 0H10V10H0Z")
		box = bbox(geometry.extent(square, geometry.transform("rotate(45)")))
		self.assertAlmostEqual(box[2] - box[0], 10 * 2**0.5, places=6)

	def test_bad_path(self):
		with self.assertRaises(geometry.GeometryError):
			geometry.segments("M0 0 L x")


def png(width: int, height: int, alpha: list[list[int]]) -> bytes:
	"""A minimal 8-bit RGBA PNG (filter 0 rows) with black pixels of the given alpha."""
	raw = b"".join(b"\0" + b"".join(bytes([0, 0, 0, a]) for a in row) for row in alpha)

	def chunk(kind: bytes, body: bytes) -> bytes:
		return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

	return (
		b"\x89PNG\r\n\x1a\n"
		+ chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
		+ chunk(b"IDAT", zlib.compress(raw))
		+ chunk(b"IEND", b"")
	)


class TestRaster(unittest.TestCase):
	def test_decode_coverage_components(self):
		mask = [[255, 0, 0, 255], [255, 0, 0, 0], [0, 0, 0, 0], [0, 0, 255, 255]]
		width, height, rows = raster.decode(png(4, 4, mask))
		self.assertEqual((width, height), (4, 4))
		self.assertEqual(raster.alpha(rows), mask)
		self.assertAlmostEqual(raster.coverage(mask), 5 / 16)
		self.assertEqual(raster.components(mask), 3)
		# Diagonal neighbours join (8-connected).
		self.assertEqual(raster.components([[255, 0], [0, 255]]), 1)


PROFILE_WITHOUT_TILE = """schema = 1
name = "icons-only"
extends = "recommended@1.0"

[icons]
enable = true
"""


class TestIconCommand(AppCase):
	def test_the_colour_is_required(self):
		self.write(".standards-profile/profile.toml", PROFILE_WITHOUT_TILE)
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				'profile = "recommended"', 'profile = "./.standards-profile"'
			),
		)
		self.commit()
		code, out, err = self.fn("icon", "check", "--structural")
		self.assertEqual(code, 2, out + err)
		self.assertIn("org.brand.tile-color is empty", err)

	def test_check_tile_and_stale(self):
		self.write(
			".standards-profile/profile.toml",
			PROFILE_WITHOUT_TILE + '\n[org.brand]\ntile-color = "#336699"\n',
		)
		self.write(
			"pyproject.toml",
			self.read("pyproject.toml").replace(
				'profile = "recommended"', 'profile = "./.standards-profile"'
			),
		)
		self.write("demo_app/public/images/demo_app-symbolic.svg", SYMBOLIC)
		self.commit()
		code, out, err = self.fn("icon", "check", "--structural")
		self.assertEqual(code, 2, out + err)  # the tile is missing
		code, out, err = self.fn("icon", "tile")
		self.assertEqual(code, 0, out + err)
		self.assertEqual(self.read("demo_app/public/images/demo_app-logo.svg"), LOGO)
		code, out, err = self.fn("icon", "check", "--structural")
		self.assertEqual(code, 0, out + err)
		self.write("demo_app/public/images/demo_app-symbolic.svg", SYMBOLIC.replace("M16 4L26", "M16 5L26"))
		code, out, err = self.fn("icon", "check", "--structural")
		self.assertEqual(code, 1, out + err)
		self.assertIn("is stale", out)


if __name__ == "__main__":
	unittest.main()
