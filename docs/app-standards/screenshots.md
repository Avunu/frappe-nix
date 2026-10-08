---
title: Screenshots
description: "Repeatable screenshots from a demo site: the screenshot spec, frappe-demo and frappe-shots."
order: 9
tags: [app-standards, screenshots, demo]
updated: 2026-10-07
---

`frappe-demo` builds a demo site the same way every time, and `frappe-shots` photographs it the same way every time, so the screenshots in `docs/screenshots/` change only when the app does. Both are dev-shell commands of an app that turned their modules on: `demo`, and `screenshots`, which needs `demo`. Both are off in the built-in profiles.

```toml
[tool.frappe-nix.demo]
enable = true
company-name = "Demo Company"   # the setup wizard's company, when erpnext is installed
company-abbr = "DC"
date = "2026-01-15"             # the demo's "today"
seed = 1                        # Python's random seed
erpnext-demo = false            # erpnext's own demo transactions

[tool.frappe-nix.screenshots]
enable = true
timezone = "America/New_York"
locale = "en-US"
```

## frappe-demo

```bash
frappe-demo                       # bring the bench up, provision the site if it is missing, then the demo
frappe-demo --fresh               # a new site first: drops the site's database (see below)
frappe-demo --date 2026-03-01 --seed 7 --erpnext-demo
frappe-demo --no-up               # the bench is up already
```

It completes the setup wizard with fixed answers (the demo module's language, country, currency, timezone and, with erpnext, company, abbreviation, fiscal year and chart of accounts), adds erpnext's demo data when asked, then calls your app's `<app>/demo.py`:

```python
import frappe


def setup(ctx):
	"""ctx: today (a datetime.date), seed, company (None without erpnext), erpnext_demo."""
	for i, title in enumerate(("Water the plants", "Renew the domain")):
		name = f"DEMO-{i + 1}"
		if frappe.db.exists("Board Card", name):
			continue  # idempotent: a second run creates nothing
		frappe.get_doc(
			{"doctype": "Board Card", "name": name, "title": title, "due": frappe.utils.add_days(ctx["today"], i)}
		).insert(ignore_permissions=True, set_name=name)
```

The hook runs as Administrator. It must be idempotent, must not reach external services (mock their clients), must take every date from `ctx["today"]` and must not commit: frappe-demo commits once, at the end, and then writes `sites/<site>/demo.json` with the date, the seed and the app versions. The defaults come from the screenshot spec's `demo` block when the app has one, else from the module. Exit codes: 0, 1 the hook raised, 10 an environment error.

## The screenshot spec

`marketplace/screenshots.ts` is yours; `marketplace/shots.d.ts`, its types, is kept by sync:

```ts
import type { ShotSpec } from "./shots.d.ts";

export default {
	demo: { date: "2026-01-15", seed: 1 },
	shots: [
		{
			name: "board",
			route: "/app/board-card/view/kanban/Board",
			alt: "The kanban board with three swimlanes",
			waitFor: ".kanban-column",
			mask: [".frappe-timestamp"],
			readme: "hero",
			featured: true,
		},
	],
} satisfies ShotSpec;
```

Node strips the types, so the spec runs without a build. Each shot has a `name` (`a-z`, `0-9`, `-`), a `route`, an `alt` text, and optionally `waitFor` or `waitForFn`, `actions` (click, hover, press, type, scroll, wait, waitFor, eval), a `clip` selector or `fullPage`, `mask` and `hide` selectors, `themes`, a `viewport`, and `threshold` and `maxDiffRatio` for the comparison. One shot may be the README's `hero`.

## frappe-shots

```bash
frappe-shots                      # update docs/screenshots (the default, --update)
frappe-shots --check              # compare only; diffs in .dev-dist/shots/diff/, exit 1 when any
frappe-shots --only board --theme dark
frappe-shots --reuse-site         # the bench and site as they are, no fresh demo
frappe-shots --recreate-site      # drop $FRAPPE_SITE and make the demo on it, without asking
frappe-shots --video tour         # record one of the spec's videos to .dev-dist/shots/tour.mp4
```

Unless `--reuse-site`, it runs `frappe-demo --fresh` on `$FRAPPE_SITE`, the dev shell's own site, with the demo script on libfaketime's clock from 09:00 on the demo day in the screenshots' timezone (it then advances), so every demo record is dated on that day, then `bench build`; a bench it had to start is stopped again at the end. Then it drives chromium over the Chrome DevTools Protocol: for each theme and shot it sets the desk theme and `prefers-color-scheme`, the timezone, locale and viewport, opens the route, turns off animations, transitions and the caret, waits for web fonts, a quiet network and `waitFor`, runs the actions, hides and masks, and captures. The page's clock starts at noon on the demo day in the same timezone, so "3 days ago" reads the same in every run, on any machine.

Recreating a site drops its database and resets Administrator's password. In CI (`CI=true`) that is what happens; at a desk, `frappe-shots` and `frappe-demo --fresh` only drop a site that already exists when you pass `--recreate-site` or answer yes at the prompt, and without a terminal they stop with exit 64. Use `frappe-shots --reuse-site` to photograph the site as it is.

Chromium, libwebp, ffmpeg, libfaketime, Node and a fixed set of fonts (Inter, IBM Plex, Noto) come from frappe-nix's own nixpkgs, so a run on a laptop and a run in CI give the same pixels. The masters are PNGs in `.dev-dist/shots/`; the committed copies are lossless WebP in `docs/screenshots/`, with `manifest.json` listing each one's name, theme, alt text, size and hash for the README and any website. A screenshot changes only when more than `maxDiffRatio` (0.1 % by default) of its pixels differ.

The nightly workflow runs `frappe-shots --update` and opens a pull request with the changes, labelled `screenshots`; it is never merged automatically. Exit codes: 0 no differences, or updated; 1 differences under `--check`; 2 a spec or configuration error; 3 the environment; 4 a capture error (navigation, a timeout or a page error); 64 an existing site kept.
