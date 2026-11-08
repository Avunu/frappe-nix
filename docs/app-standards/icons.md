---
title: Icons
description: "The symbolic icon and logo tile an app ships, and the checks frappe-icon runs on them."
order: 8
tags: [app-standards, icons]
updated: 2026-11-06
---

An app ships two SVGs, and `frappe-icon` keeps them right. The module is `icons`, off in the built-in profiles; it reads the tile's colours from the profile's `[org.brand]`:

```toml
[tool.frappe-nix.icons]
enable = true

[tool.frappe-nix.org.brand]      # or the org profile's [org.brand]
tile-color = "#336699"           # required: an empty value is exit 2 naming org.brand.tile-color
glyph-color = "#FFFFFF"          # the default
```

| File | What |
|---|---|
| `<app>/public/images/<app>-symbolic.svg` | The source: a 32 x 32 glyph drawn in `currentColor`, outlines only, inside the safe area [2, 30]. |
| `<app>/public/images/<app>-logo.svg` | The tile the marketplace card and the apps screen show, made from the symbolic icon by `frappe-icon tile` and committed. |
| `<app>/desktop_icon/<app>.json` | For an application-type app (`type = "application"` in `listing.toml`): the desktop icon frappe v16 imports from the installed package, written by `frappe-icon build --write-fixture` and committed. |

```bash
frappe-icon tile                   # write <app>-logo.svg from <app>-symbolic.svg
frappe-icon check                  # every rule, raster ones included (resvg)
frappe-icon check --structural     # no rasterising: what CI's marketplace job runs (L8)
frappe-icon build                  # .dev-dist/icons/logo-512.png, favicon-32.png, favicon-16.png
frappe-icon build --write-fixture  # and the desktop-icon fixture (applications)
```

## The logo in the About dialog

Frappe's About dialog lists the installed apps, and takes each logo from the first entry of `add_to_apps_screen`, then from `app_logo_url`. An app with neither gets a coloured letter. `app_logo_url` isn't the answer, because Frappe also picks the site's own logo, on the login page for one, from everyone's `app_logo_url`: a third app declaring it turns ERPNext's logo back into Frappe's.

So every listed app has an `add_to_apps_screen` entry, and `frappe-listing check` asks for it (L2). An app with `apps_screen = true` has one anyway. An app with `apps_screen = false` keeps off the apps screen with a `has_permission` that always says no:

```python
# <app>/hooks.py
add_to_apps_screen = [
	{
		"name": "my_app",
		"logo": "/assets/my_app/images/my_app-logo.svg",
		"title": "My App",
		"route": "/desk",  # nothing links to it, but Frappe's desktop icon seeding reads it
		"has_permission": "my_app.apps_screen.not_listed",
	}
]
```

```python
# <app>/apps_screen.py
def not_listed() -> bool:
	return False
```

`<app>/__init__.py` can't hold the function: L5 allows only the version block there. Frappe treats a `has_permission` that says no as "this user can't open the app", so the app stays off the apps screen, the app switcher and the default-app logic. Its doctypes and pages work as before, and if it has a module sidebar, that shows the logo too. An app that ships a `Dock` record belongs on the apps screen: its rail still shows, but Frappe leaves it out of `app_data`, which the dock manager reads. On a site using the Desktop Icon grid, Frappe also seeds a hidden App icon for the app, which nobody sees.

## The rules

The symbolic icon:

- `viewBox="0 0 32 32"`;
- only `<svg>`, `<g>`, `<path>`, `<circle>`, `<rect>`, `<ellipse>`, `<polygon>`, `<polyline>` and `<title>`: no text (convert it to outlines), images, styles, classes, gradients, filters, masks or clip paths;
- every fill `currentColor` or `none`, and no stroke (convert strokes to filled outlines);
- the glyph's box inside [2, 30] on both axes.

The tile:

- `viewBox="0 0 1024 1024"`, and first a 1024 x 1024 `<rect>` with `rx` and `ry` 293 (28.6 %) in `org.brand.tile-color`;
- the glyph in `org.brand.glyph-color`, centred within 4 units, its longer side 573 (56 %) within 12;
- byte for byte what `frappe-icon tile` makes now, so a changed symbolic icon, or a changed brand colour in the profile, shows up as a stale tile.

The raster rules render the symbolic icon black on transparent with resvg, which comes from frappe-nix's own nixpkgs:

- at 16 and 32 px it covers between 10 % and 65 % of the square;
- it has as many separate shapes at 32 px as at 256 px, so no feature merges or disappears at the marketplace card's size.

The structural rules are pure Python and run anywhere (CI's no-Nix `marketplace` job runs them as listing rule L8). The raster rules need resvg, so they run in the dev shell, in `nix run .#frappe-icon -- check` and in the nightly `links` job; they are not part of a required check.

Exit codes: 0 pass; 1 a rule failed, each one listed; 2 a file is missing or unreadable, or the tile colour is empty; 3 resvg is missing.
