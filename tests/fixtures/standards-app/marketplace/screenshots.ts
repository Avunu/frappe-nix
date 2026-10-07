// Copyright (c) 2026, Example Org and contributors
// For license information, please see license.txt

import type { ShotSpec } from "./shots.d.ts";

export default {
	demo: { date: "2026-01-15", seed: 1 },
	shots: [
		{
			name: "fixture-note-list",
			route: "/app/fixture-note",
			alt: "The Fixture Note list with the demo notes",
			waitFor: ".frappe-list",
			readme: "hero",
			featured: true,
		},
	],
} satisfies ShotSpec;
