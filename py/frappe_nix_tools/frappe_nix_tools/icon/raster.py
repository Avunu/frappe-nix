"""The raster icon checks and the build outputs (spec §5.3), with ``resvg``.

``resvg`` comes from the nixpkgs frappe-nix locks (``frappe-icon``'s wrapper puts it on
PATH), so a raster check gives the same pixels in the dev shell, in ``selftest-product`` and
in the nightly ``links`` job. The PNGs it writes are decoded here, in pure Python, to measure:

- the alpha coverage of the symbolic icon, black on transparent, at 16 and 32 px, which must
  lie in [0.10, 0.65] (a glyph that is a speck or a block is unreadable as a favicon);
- the number of 8-connected shapes at 32 px, which must equal the number at 256 px, so no
  feature merges or disappears at the marketplace card's size.
"""

import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

from frappe_nix_tools.common.report import EnvError

COVERAGE = (0.10, 0.65)
SIZES = (16, 32, 256)
# What `build` writes: (file, size), from the tile.
OUTPUTS = (("logo-512.png", 512), ("favicon-32.png", 32), ("favicon-16.png", 16))


def resvg(svg: Path, png: Path, size: int) -> None:
	"""Render ``svg`` at ``size`` x ``size`` into ``png``."""
	binary = shutil.which("resvg")
	if binary is None:
		raise EnvError(
			"resvg is not on PATH: run the raster checks with `nix run .#frappe-icon -- check` (or --structural)"
		)
	proc = subprocess.run(
		[binary, "--width", str(size), "--height", str(size), str(svg), str(png)],
		capture_output=True,
		text=True,
		check=False,
	)
	if proc.returncode != 0:
		raise EnvError(f"resvg {svg.name} at {size} px failed: {proc.stderr.strip()}")


def _paeth(a: int, b: int, c: int) -> int:
	p = a + b - c
	pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
	if pa <= pb and pa <= pc:
		return a
	return b if pb <= pc else c


def decode(data: bytes) -> tuple[int, int, list[bytearray]]:
	"""``(width, height, rows)`` of an 8-bit RGBA, non-interlaced PNG (what resvg writes); each row
	is ``width * 4`` bytes."""
	if data[:8] != b"\x89PNG\r\n\x1a\n":
		raise ValueError("not a PNG")
	pos, idat = 8, b""
	width = height = 0
	while pos < len(data):
		(length,) = struct.unpack(">I", data[pos : pos + 4])
		kind = data[pos + 4 : pos + 8]
		body = data[pos + 8 : pos + 8 + length]
		if kind == b"IHDR":
			width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
			if (depth, color, interlace) != (8, 6, 0):
				raise ValueError(
					f"a PNG of depth {depth}, colour type {color}, interlace {interlace}; want 8-bit RGBA"
				)
		elif kind == b"IDAT":
			idat += body
		elif kind == b"IEND":
			break
		pos += 12 + length
	raw = zlib.decompress(idat)
	stride = width * 4
	rows: list[bytearray] = []
	prev = bytearray(stride)
	at = 0
	for _ in range(height):
		kind, line = raw[at], bytearray(raw[at + 1 : at + 1 + stride])
		at += 1 + stride
		for i in range(stride):
			left = line[i - 4] if i >= 4 else 0
			up = prev[i]
			corner = prev[i - 4] if i >= 4 else 0
			if kind == 1:
				line[i] = (line[i] + left) & 0xFF
			elif kind == 2:
				line[i] = (line[i] + up) & 0xFF
			elif kind == 3:
				line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
			elif kind == 4:
				line[i] = (line[i] + _paeth(left, up, corner)) & 0xFF
		rows.append(line)
		prev = line
	return width, height, rows


def alpha(rows: list[bytearray]) -> list[list[int]]:
	return [list(row[3::4]) for row in rows]


def coverage(mask: list[list[int]]) -> float:
	"""The share of the square the glyph covers, by alpha."""
	total = sum(sum(row) for row in mask)
	return total / 255 / (len(mask) * len(mask[0])) if mask and mask[0] else 0.0


def components(mask: list[list[int]], threshold: int = 128) -> int:
	"""The number of 8-connected shapes with alpha ≥ ``threshold``."""
	height, width = len(mask), len(mask[0]) if mask else 0
	seen = [[False] * width for _ in range(height)]
	count = 0
	for y in range(height):
		for x in range(width):
			if seen[y][x] or mask[y][x] < threshold:
				continue
			count += 1
			stack = [(y, x)]
			seen[y][x] = True
			while stack:
				cy, cx = stack.pop()
				for dy in (-1, 0, 1):
					for dx in (-1, 0, 1):
						ny, nx = cy + dy, cx + dx
						if (
							0 <= ny < height
							and 0 <= nx < width
							and not seen[ny][nx]
							and mask[ny][nx] >= threshold
						):
							seen[ny][nx] = True
							stack.append((ny, nx))
	return count


def problems(symbolic: Path) -> list[str]:
	"""The raster rules for the symbolic icon (black on transparent)."""
	out = []
	masks: dict[int, list[list[int]]] = {}
	with tempfile.TemporaryDirectory(prefix="frappe-icon-") as tmp:
		for size in SIZES:
			png = Path(tmp) / f"{size}.png"
			resvg(symbolic, png, size)
			_, _, rows = decode(png.read_bytes())
			masks[size] = alpha(rows)
	for size in (16, 32):
		share = coverage(masks[size])
		if not COVERAGE[0] <= share <= COVERAGE[1]:
			out.append(
				f"at {size} px the glyph covers {share:.2f} of the square, outside [{COVERAGE[0]:.2f}, {COVERAGE[1]:.2f}]"
			)
	small, large = components(masks[32]), components(masks[256])
	if small != large:
		out.append(
			f"at 32 px the glyph has {small} separate shape(s), at 256 px {large}: a feature merges or"
			" disappears at the marketplace card's size"
		)
	return out


def build(tile_svg: Path, out: Path) -> list[Path]:
	"""``logo-512.png``, ``favicon-32.png`` and ``favicon-16.png`` from the tile, into ``out``."""
	out.mkdir(parents=True, exist_ok=True)
	written = []
	for name, size in OUTPUTS:
		resvg(tile_svg, out / name, size)
		written.append(out / name)
	return written
