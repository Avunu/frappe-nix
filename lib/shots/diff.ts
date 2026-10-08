// Image comparison for frappe-shots (docs/app-standards/spec.md §5.5): PNG decoding with
// pngjs, the per-pixel comparison with pixelmatch, and the WebP round trip through libwebp's
// cwebp and dwebp (lossless, so a committed screenshot decodes to exactly the master it
// was made from).
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";

/** A decoded image: RGBA, 8 bits per channel. */
export interface Image {
	width: number;
	height: number;
	data: Buffer;
}

/** What comparing a capture with the committed image found. */
export interface Comparison {
	/** The share of pixels that differ, 1 when there is nothing to compare or the sizes differ. */
	ratio: number;
	/** The pixels that differ. */
	pixels: number;
	/** pixelmatch's diff image, when both images exist and have one size. */
	diff?: Image;
}

export function readPng(file: string): Image {
	const png = PNG.sync.read(fs.readFileSync(file));
	return { width: png.width, height: png.height, data: png.data };
}

export function writePng(file: string, image: Image): void {
	const png = new PNG({ width: image.width, height: image.height });
	image.data.copy(png.data);
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, PNG.sync.write(png));
}

/** A committed `.webp`, decoded with dwebp into a temporary PNG. */
export function readWebp(file: string): Image {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "frappe-shots-"));
	try {
		const png = path.join(dir, "decoded.png");
		execFileSync("dwebp", ["-quiet", file, "-o", png]);
		return readPng(png);
	} finally {
		fs.rmSync(dir, { recursive: true, force: true });
	}
}

/** The master PNG as a lossless WebP: `-exact` keeps transparent pixels' colours, `-z 9` packs hardest. */
export function writeWebp(png: string, webp: string): void {
	fs.mkdirSync(path.dirname(webp), { recursive: true });
	execFileSync("cwebp", ["-quiet", "-lossless", "-exact", "-z", "9", png, "-o", webp]);
}

export function compare(capture: Image, committed: Image | null, threshold: number): Comparison {
	if (committed === null) return { ratio: 1, pixels: capture.width * capture.height };
	if (committed.width !== capture.width || committed.height !== capture.height) {
		return { ratio: 1, pixels: capture.width * capture.height };
	}
	const diff: Image = {
		width: capture.width,
		height: capture.height,
		data: Buffer.alloc(capture.width * capture.height * 4),
	};
	const pixels = pixelmatch(capture.data, committed.data, diff.data, capture.width, capture.height, {
		threshold,
	});
	return { ratio: pixels / (capture.width * capture.height), pixels, diff };
}
