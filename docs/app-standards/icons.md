---
title: Icons
description: "The symbolic icon and logo tile an app ships, and the checks frappe-icon runs on them."
order: 8
tags: [app-standards, icons]
updated: 2026-10-07
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
