// lib/shots/diff.ts: the comparison and the verdict behind frappe-shots' change threshold
// (docs/app-standards/screenshots.md, "When a screenshot changes"). Run by the
// standards-shots-diff check: DIFF names the engine's diff.ts (beside its Nix-built
// node_modules), and libwebp's cwebp and dwebp are on PATH.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const { compare, judge, readWebp, writePng, writeWebp } = await import(process.env["DIFF"]!);
const ok = (msg: string) => console.log(`ok   ${msg}`);

interface Image {
	width: number;
	height: number;
	data: Buffer;
}

/** A WIDTH x HEIGHT image of one opaque colour. */
function solid(width: number, height: number, [r, g, b]: [number, number, number]): Image {
	const data = Buffer.alloc(width * height * 4);
	for (let i = 0; i < width * height; i++) data.set([r, g, b, 255], i * 4);
	return { width, height, data };
}

/** IMAGE with the first ROWS rows painted COLOUR. */
function paintRows(image: Image, rows: number, [r, g, b]: [number, number, number]): Image {
	const data = Buffer.from(image.data);
	for (let i = 0; i < image.width * rows; i++) data.set([r, g, b, 255], i * 4);
	return { ...image, data };
}

const grey = solid(100, 100, [200, 200, 200]);

// Identical images: nothing differs.
assert.equal(compare(grey, solid(100, 100, [200, 200, 200]), 0.1).ratio, 0);
ok("identical images differ by 0");

// A shift of 2 per channel is under pixelmatch's per-pixel tolerance: rendering noise does not count.
assert.equal(compare(grey, solid(100, 100, [202, 201, 199]), 0.1).pixels, 0);
ok("a small per-channel shift is within the tolerance");

// 25 of 100 rows repainted: exactly a quarter of the pixels differ, and the diff image is there.
const quarter = compare(paintRows(grey, 25, [20, 40, 160]), grey, 0.1);
assert.equal(quarter.pixels, 2500);
assert.equal(quarter.ratio, 0.25);
assert.ok(quarter.diff && quarter.diff.width === 100);
ok("a quarter of the rows repainted is a ratio of 0.25");

// Nothing committed, or a new size: everything differs.
assert.equal(compare(grey, null, 0.1).ratio, 1);
assert.equal(compare(grey, solid(100, 90, [200, 200, 200]), 0.1).ratio, 1);
ok("no committed image, or another size, is a ratio of 1");

// The verdict: maxDiffRatio 0.001, a change threshold of 0.2.
const at = (ratio: number) => ({ ratio, pixels: Math.round(ratio * 10000) });
assert.equal(judge(at(1), false, 0.001, 0.2), "new");
assert.equal(judge(at(0), true, 0.001, 0.2), "same");
assert.equal(judge(at(0.001), true, 0.001, 0.2), "same");
assert.equal(judge(at(0.05), true, 0.001, 0.2), "minor");
assert.equal(judge(at(0.199), true, 0.001, 0.2), "minor");
assert.equal(judge(at(0.2), true, 0.001, 0.2), "changed");
assert.equal(judge(at(1), true, 0.001, 0.2), "changed");
ok("with a threshold of 0.2: under 20 % is kept, 20 % and over (and a new size) is replaced, a new shot always");

// A threshold of 0 (the default) replaces whatever maxDiffRatio lets through, as before.
assert.equal(judge(at(0.0005), true, 0.001, 0), "same");
assert.equal(judge(at(0.002), true, 0.001, 0), "changed");
ok("a threshold of 0 replaces every shot past maxDiffRatio");

// A threshold under maxDiffRatio does not lower it: maxDiffRatio still decides what differs.
assert.equal(judge(at(0.05), true, 0.1, 0.01), "same");
ok("maxDiffRatio still bounds what differs");

// The WebP round trip is lossless: a committed screenshot decodes to its master exactly.
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "shots-diff-"));
try {
	const master = path.join(dir, "m.png");
	const webp = path.join(dir, "m.webp");
	const image = paintRows(grey, 37, [12, 120, 80]);
	writePng(master, image);
	writeWebp(master, webp);
	const decoded = readWebp(webp);
	assert.equal(compare(image, decoded, 0).pixels, 0);
	assert.ok(decoded.data.equals(image.data));
	ok("a master written as WebP decodes to the same pixels");
} finally {
	fs.rmSync(dir, { recursive: true, force: true });
}
