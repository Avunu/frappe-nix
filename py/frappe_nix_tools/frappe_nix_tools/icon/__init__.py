"""``frappe-nix icon`` (``frappe-icon``): the app's icon pair and the desktop-icon fixture (spec §5.3).

``svg`` holds the structural rules and the tile generator (pure Python, so they run in no-Nix
CI as listing rule L8), ``raster`` the resvg rules and the PNG outputs, ``geometry`` the
bounding boxes. The tile colours are the resolved ``org.brand`` (S37): an empty
``org.brand.tile-color`` is exit 2 naming it.

With ``app_type == "application"``, frappe v16 imports ``<app>/desktop_icon/<app>.json`` from
the installed package, so ``build --write-fixture`` writes it and the app commits it (S22);
``check`` compares it, ignoring ``creation`` and ``modified``.
"""

import datetime
import json
from pathlib import Path

from frappe_nix_tools.common.report import ConfigError
from frappe_nix_tools.icon import raster, svg
from frappe_nix_tools.listing.target import Target

FIXTURE_TIMES = ("creation", "modified")


def images(t: Target) -> tuple[str, str]:
	"""``(symbolic, logo)``, relative to the app."""
	base = f"{t.name}/public/images/{t.name}"
	return f"{base}-symbolic.svg", f"{base}-logo.svg"


def fixture_path(t: Target) -> str:
	return f"{t.name}/desktop_icon/{t.name}.json"


def colours(t: Target) -> tuple[str, str]:
	"""``(tile, glyph)`` from ``org.brand``."""
	brand = t.org.get("brand") or {}
	tile = brand.get("tile-color", "")
	if not tile:
		raise ConfigError(
			"org.brand.tile-color is empty; set it in your profile or [tool.frappe-nix.org.brand] (the icon"
			" tile's colour, §5.3)"
		)
	return tile, brand.get("glyph-color") or "#FFFFFF"


def _read(t: Target, rel: str) -> str:
	try:
		return (t.root / rel).read_text()
	except FileNotFoundError as e:
		raise svg.IconError(
			f"{rel} is missing (spec §5.3: the app ships its symbolic icon and the tile)"
		) from e
	except (OSError, UnicodeDecodeError) as e:
		raise svg.IconError(f"{rel}: {e}") from e


def tile_text(t: Target) -> str:
	tile, glyph = colours(t)
	symbolic, _ = images(t)
	return svg.tile(_read(t, symbolic), tile, glyph, symbolic)


def fixture_doc(t: Target) -> dict:
	"""The desktop-icon fixture's content (without its two timestamps)."""
	entry = t.listing.get("apps_screen_entry") or {}
	return {
		"app": t.name,
		"doctype": "Desktop Icon",
		"name": t.ctx.title,
		"label": t.ctx.title,
		"icon_type": "App",
		"link_type": "External",
		"link": entry.get("route") or f"/app/{t.app.hyphen}",
		"logo_url": f"/assets/{t.name}/images/{t.name}-logo.svg",
		"hidden": 0,
		"standard": 1,
		"idx": 100,
		"roles": [],
		"docstatus": 0,
		"owner": "Administrator",
		"modified_by": "Administrator",
	}


def _dump(doc: dict) -> str:
	"""Frappe's export form: 1-space JSON, sorted keys, no final newline."""
	return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False)


def fixture_problems(t: Target) -> list[tuple[str, str]]:
	if t.ctx.app_type != "application":
		return []
	rel = fixture_path(t)
	try:
		have = json.loads((t.root / rel).read_text())
	except FileNotFoundError:
		return [(rel, "is missing: run `frappe-icon build --write-fixture` and commit it (S22)")]
	except (OSError, json.JSONDecodeError) as e:
		return [(rel, f"is not readable JSON: {e}")]
	if not isinstance(have, dict) or {k: v for k, v in have.items() if k not in FIXTURE_TIMES} != fixture_doc(
		t
	):
		return [(rel, "is stale: run `frappe-icon build --write-fixture` and commit it")]
	return []


def write_fixture(t: Target) -> bool:
	"""Write the fixture, keeping ``creation`` and, unless the content changed, ``modified``."""
	rel = fixture_path(t)
	path = t.root / rel
	doc = fixture_doc(t)
	now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
	try:
		old = json.loads(path.read_text())
	except (FileNotFoundError, json.JSONDecodeError):
		old = {}
	old = old if isinstance(old, dict) else {}
	same = {k: v for k, v in old.items() if k not in FIXTURE_TIMES} == doc
	doc["creation"] = old.get("creation") or now
	doc["modified"] = old.get("modified") if same and old.get("modified") else now
	text = _dump(doc)
	if path.is_file() and path.read_text() == text:
		return False
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(text)
	return True


def check(t: Target, *, structural: bool) -> list[tuple[str, str]]:
	"""``(path, problem)`` for every rule that fails; an unreadable file is exit 2 (``IconError``)."""
	tile, glyph = colours(t)
	symbolic, logo = images(t)
	out = [(symbolic, p) for p in svg.symbolic_problems(_read(t, symbolic), symbolic)]
	logo_text = _read(t, logo)
	out += [(logo, p) for p in svg.tile_problems(logo_text, logo, tile, glyph)]
	if logo_text != svg.tile(_read(t, symbolic), tile, glyph, symbolic):
		out.append(
			(
				logo,
				"is stale: it is not what `frappe-icon tile` makes from the symbolic icon; run it and commit",
			)
		)
	out += fixture_problems(t)
	if not structural:
		out += [(symbolic, p) for p in raster.problems(t.root / symbolic)]
	return out


def build(t: Target, out: Path) -> list[Path]:
	_, logo = images(t)
	colours(t)
	_read(t, logo)
	return raster.build(t.root / logo, out)
