// frappe-shots' engine (docs/app-standards/spec.md §5.5): repeatable screenshots of a demo
// site, driven over the Chrome DevTools Protocol (./cdp.ts) and compared with the committed
// ones (./diff.ts). lib/sh/frappe-shots.sh brings the bench up and runs frappe-demo first,
// with the demo script under libfaketime (./clock.ts); this file only drives the browser:
//
//   node runner.ts --spec <screenshots.ts> --base <url> --mode update|check
//     [--only a,b] [--theme light|dark] [--out docs/screenshots] [--masters .dev-dist/shots]
//     [--video <name>] [--timezone Z] [--locale L] [--clock <YYYY-MM-DD>] [--password P]
//
// For each theme and shot: the user's desk theme and prefers-color-scheme, the timezone,
// locale, viewport and device scale; the route; no animations, transitions or caret; web
// fonts, then a quiet network (500 ms), then waitFor or waitForFn (30 s); the actions; hide
// and mask; the capture. The PNG master goes to the masters directory; the committed
// <name>-<theme>.webp is decoded and compared with pixelmatch. Update mode writes a new
// lossless WebP where the share of differing pixels passes maxDiffRatio, and the manifest;
// check mode writes only diff PNGs.
//
// Exit codes: 0 no differences, or updated; 1 differences in check mode; 2 a spec error;
// 3 an environment error; 4 a capture error (navigation, timeout or a page error).
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";
import { launch, login, newPage } from "./cdp.ts";
import { clockShim, zonedTime } from "./clock.ts";
import type { Page } from "./cdp.ts";
import { compare, readPng, readWebp, writePng, writeWebp } from "./diff.ts";
import type { Action, Shot, ShotSpec, Theme, Viewport } from "./shots.d.ts";

const SPEC_ERROR = 2;
const ENV_ERROR = 3;
const CAPTURE_ERROR = 4;
const NO_MOTION =
	"*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}";
const MASK = "#8C8C8C";
const DEFAULT_VIEWPORT: Viewport = { width: 1440, height: 900, deviceScaleFactor: 2 };

class ExitError extends Error {
	code: number;
	constructor(code: number, message: string) {
		super(message);
		this.code = code;
	}
}

interface Options {
	spec: string;
	base: string;
	mode: "update" | "check";
	only: string[];
	theme: Theme | null;
	out: string;
	masters: string;
	video: string | null;
	timezone: string;
	locale: string;
	clock: string;
	password: string;
}

interface ManifestEntry {
	name: string;
	theme: Theme;
	alt: string;
	featured: boolean;
	readme: string | null;
	width: number;
	height: number;
	sha256: string;
}

function options(): Options {
	const { values } = parseArgs({
		options: {
			spec: { type: "string" },
			base: { type: "string" },
			mode: { type: "string", default: "update" },
			only: { type: "string", default: "" },
			theme: { type: "string", default: "" },
			out: { type: "string", default: "docs/screenshots" },
			masters: { type: "string", default: ".dev-dist/shots" },
			video: { type: "string", default: "" },
			timezone: { type: "string", default: "America/New_York" },
			locale: { type: "string", default: "en-US" },
			clock: { type: "string", default: "" },
			password: { type: "string", default: "admin" },
		},
	});
	if (!values.spec || !values.base) throw new ExitError(SPEC_ERROR, "--spec and --base are required");
	if (values.mode !== "update" && values.mode !== "check") {
		throw new ExitError(SPEC_ERROR, `--mode is update or check, not ${values.mode}`);
	}
	if (values.theme && values.theme !== "light" && values.theme !== "dark") {
		throw new ExitError(SPEC_ERROR, `--theme is light or dark, not ${values.theme}`);
	}
	return {
		spec: path.resolve(values.spec),
		base: values.base.replace(/\/+$/, ""),
		mode: values.mode,
		only: values.only ? values.only.split(",").filter(Boolean) : [],
		theme: values.theme ? (values.theme as Theme) : null,
		out: path.resolve(values.out),
		masters: path.resolve(values.masters),
		video: values.video || null,
		timezone: values.timezone,
		locale: values.locale,
		clock: values.clock,
		password: values.password,
	};
}

// ── the spec ─────────────────────────────────────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

const ACTION_KEYS = new Set(["click", "hover", "press", "type", "scroll", "wait", "waitFor", "eval"]);

function checkActions(where: string, actions: unknown, problems: string[]): void {
	if (actions === undefined) return;
	if (!Array.isArray(actions)) {
		problems.push(`${where}: actions must be a list`);
		return;
	}
	actions.forEach((a, i) => {
		const keys = isRecord(a) ? Object.keys(a) : [];
		if (keys.length !== 1 || !ACTION_KEYS.has(keys[0] ?? "")) {
			problems.push(`${where}: actions[${i}] must be one of ${[...ACTION_KEYS].join(", ")}`);
		}
	});
}

/** The spec's runtime validation: the types are stripped, so this is what holds a spec to them. */
export function validate(spec: unknown): ShotSpec {
	const problems: string[] = [];
	if (!isRecord(spec)) throw new ExitError(SPEC_ERROR, "the spec's default export is not an object");
	const shots = spec["shots"];
	if (!Array.isArray(shots) || shots.length === 0) problems.push("shots must be a non-empty list");
	const names = new Set<string>();
	let heroes = 0;
	for (const [i, shot] of (Array.isArray(shots) ? shots : []).entries()) {
		const where = `shots[${i}]`;
		if (!isRecord(shot)) {
			problems.push(`${where} is not an object`);
			continue;
		}
		const name = shot["name"];
		if (typeof name !== "string" || !/^[a-z0-9-]+$/.test(name)) problems.push(`${where}: name must match /^[a-z0-9-]+$/`);
		else if (names.has(name)) problems.push(`${where}: name ${name} is used twice`);
		else names.add(name);
		if (typeof shot["route"] !== "string" || !shot["route"].startsWith("/")) problems.push(`${where}: route must start with "/"`);
		if (typeof shot["alt"] !== "string" || !shot["alt"].trim()) problems.push(`${where}: alt is required`);
		if (shot["readme"] === "hero") heroes += 1;
		else if (shot["readme"] !== undefined && shot["readme"] !== "feature") problems.push(`${where}: readme is "hero" or "feature"`);
		const themes = shot["themes"];
		if (themes !== undefined && (!Array.isArray(themes) || themes.some((t) => t !== "light" && t !== "dark"))) {
			problems.push(`${where}: themes are "light" and "dark"`);
		}
		for (const key of ["mask", "hide"]) {
			const list = shot[key];
			if (list !== undefined && (!Array.isArray(list) || list.some((s) => typeof s !== "string"))) {
				problems.push(`${where}: ${key} must be a list of selectors`);
			}
		}
		for (const key of ["threshold", "maxDiffRatio"]) {
			const n = shot[key];
			if (n !== undefined && (typeof n !== "number" || n < 0 || n > 1)) problems.push(`${where}: ${key} must be a number in [0, 1]`);
		}
		checkActions(where, shot["actions"], problems);
	}
	if (heroes > 1) problems.push(`at most one shot is readme: "hero" (${heroes} are)`);
	const videos = spec["videos"];
	if (videos !== undefined) {
		if (!Array.isArray(videos)) problems.push("videos must be a list");
		else
			videos.forEach((v, i) => {
				if (!isRecord(v) || typeof v["name"] !== "string" || typeof v["route"] !== "string") {
					problems.push(`videos[${i}] needs name and route`);
				} else checkActions(`videos[${i}]`, v["steps"], problems);
			});
	}
	if (problems.length) throw new ExitError(SPEC_ERROR, `the screenshot spec is invalid:\n  ${problems.join("\n  ")}`);
	return spec as unknown as ShotSpec;
}

// ── the page ─────────────────────────────────────────────────────────────────────────────

async function networkIdle(page: Page, quietMs = 500, timeoutMs = 30000): Promise<void> {
	const deadline = Date.now() + timeoutMs;
	let seen = -1;
	let quietSince = Date.now();
	while (Date.now() < deadline) {
		const inflight = new Set<string>();
		for (const e of page.events) {
			const params = isRecord(e.params) ? e.params : {};
			const id = typeof params["requestId"] === "string" ? params["requestId"] : "";
			if (e.method === "Network.requestWillBeSent") inflight.add(id);
			else if (e.method === "Network.loadingFinished" || e.method === "Network.loadingFailed") inflight.delete(id);
		}
		if (page.events.length !== seen) {
			seen = page.events.length;
			quietSince = Date.now();
		}
		if (inflight.size === 0 && Date.now() - quietSince >= quietMs) return;
		await new Promise((wake) => setTimeout(wake, 100));
	}
	throw new ExitError(CAPTURE_ERROR, `the network did not go quiet within ${timeoutMs / 1000} s`);
}

async function centre(page: Page, selector: string): Promise<{ x: number; y: number }> {
	const box = await page.waitFor<{ x: number; y: number } | null>(
		`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return null;
		  el.scrollIntoView({ block: "center" }); const r = el.getBoundingClientRect();
		  return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`,
	);
	if (!box) throw new ExitError(CAPTURE_ERROR, `no element matches ${selector}`);
	return box;
}

async function act(page: Page, action: Action): Promise<void> {
	if ("click" in action) {
		const { x, y } = await centre(page, action.click);
		for (const type of ["mousePressed", "mouseReleased"]) {
			await page.send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
		}
	} else if ("hover" in action) {
		const { x, y } = await centre(page, action.hover);
		await page.hover(x, y);
	} else if ("press" in action) {
		for (const type of ["keyDown", "keyUp"]) await page.send("Input.dispatchKeyEvent", { type, key: action.press });
	} else if ("type" in action) {
		await page.eval(`document.querySelector(${JSON.stringify(action.type.selector)})?.focus()`);
		await page.send("Input.insertText", { text: action.type.text });
	} else if ("scroll" in action) {
		const target = action.scroll.selector
			? `document.querySelector(${JSON.stringify(action.scroll.selector)})`
			: "document.scrollingElement";
		await page.eval(`(${target})?.scrollTo(0, ${Number(action.scroll.y)})`);
	} else if ("wait" in action) {
		await new Promise((wake) => setTimeout(wake, action.wait));
	} else if ("waitFor" in action) {
		await page.waitFor(`!!document.querySelector(${JSON.stringify(action.waitFor)})`);
	} else if ("eval" in action) {
		await page.eval(action.eval);
	}
	await new Promise((wake) => setTimeout(wake, 150));
}

async function prepare(page: Page, spec: ShotSpec, theme: Theme, viewport: Viewport, opts: Options): Promise<void> {
	await page.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
	await page.send("Emulation.setTimezoneOverride", { timezoneId: spec.timezone ?? opts.timezone }).catch(() => undefined);
	await page.send("Emulation.setLocaleOverride", { locale: spec.locale ?? opts.locale }).catch(() => undefined);
	await page.send("Emulation.setDeviceMetricsOverride", {
		width: viewport.width,
		height: viewport.height,
		deviceScaleFactor: viewport.deviceScaleFactor ?? 1,
		mobile: false,
	});
}

async function setDeskTheme(page: Page, user: string, theme: Theme): Promise<void> {
	const value = theme === "dark" ? "Dark" : "Light";
	// The desk hands the page its CSRF token; a POST from the session needs it.
	await page.goto(`${(page as Page & { base: string }).base}/app`);
	await page.waitFor("!!(window.frappe && frappe.csrf_token)");
	const answer = await page.eval<string>(
		`(async () => {
			const r = await fetch('/api/method/frappe.client.set_value', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Frappe-CSRF-Token': (window.frappe && frappe.csrf_token) || '' },
				body: JSON.stringify({ doctype: 'User', name: ${JSON.stringify(user)}, fieldname: 'desk_theme', value: ${JSON.stringify(value)} }),
			});
			return r.status + ' ' + (await r.text()).slice(0, 200);
		})()`,
	);
	if (!answer.startsWith("200")) throw new ExitError(CAPTURE_ERROR, `setting ${user}'s desk theme to ${value} failed: ${answer}`);
}

async function capture(page: Page, shot: Shot, spec: ShotSpec, file: string): Promise<void> {
	// goto() clears the events once the page has loaded: what follows is this shot's.
	await page.goto(`${(page as Page & { base: string }).base}${shot.route}`);
	await page.eval(
		`(() => { const s = document.createElement('style'); s.id = '__frappe_shots';
		  s.textContent = ${JSON.stringify(NO_MOTION + (spec.css ?? ""))}; document.head.appendChild(s); return true; })()`,
	);
	await page.eval("document.fonts.ready.then(() => true)");
	await networkIdle(page);
	if (shot.waitFor) await page.waitFor(`!!document.querySelector(${JSON.stringify(shot.waitFor)})`);
	if (shot.waitForFn) await page.waitFor(shot.waitForFn);
	for (const action of shot.actions ?? []) await act(page, action);
	await networkIdle(page);
	await page.eval(
		`(() => {
			for (const sel of ${JSON.stringify(shot.hide ?? [])}) document.querySelectorAll(sel).forEach((el) => { el.style.visibility = 'hidden'; });
			for (const sel of ${JSON.stringify(shot.mask ?? [])}) document.querySelectorAll(sel).forEach((el) => {
				const r = el.getBoundingClientRect(); const m = document.createElement('div');
				Object.assign(m.style, { position: 'absolute', left: (r.left + scrollX) + 'px', top: (r.top + scrollY) + 'px',
					width: r.width + 'px', height: r.height + 'px', background: '${MASK}', zIndex: '2147483647', pointerEvents: 'none' });
				document.body.appendChild(m);
			});
			document.activeElement && document.activeElement.blur && document.activeElement.blur();
			return true;
		})()`,
	);
	// An uncaught exception is a capture error; console.error is a warning (the desk logs some
	// on its own, such as a realtime socket that reconnects).
	const thrown = page.events.filter((e) => e.method === "Runtime.exceptionThrown");
	if (thrown.length) {
		const details = thrown.map((e) => JSON.stringify(e.params).slice(0, 300)).slice(0, 3);
		throw new ExitError(CAPTURE_ERROR, `${shot.name}: the page threw: ${details.join(" || ")}`);
	}
	const fresh = page.consoleErrors();
	if (fresh.length) console.warn(`warning: ${shot.name}: the page logged errors: ${fresh.slice(0, 3).join(" || ")}`);
	const params: Record<string, unknown> = { format: "png" };
	if (shot.clip) {
		const r = await page.eval<{ x: number; y: number; width: number; height: number } | null>(
			`(() => { const el = document.querySelector(${JSON.stringify(shot.clip)}); if (!el) return null;
			  const r = el.getBoundingClientRect(); return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height }; })()`,
		);
		if (!r) throw new ExitError(CAPTURE_ERROR, `${shot.name}: no element matches clip ${shot.clip}`);
		params["clip"] = { ...r, scale: 1 };
		params["captureBeyondViewport"] = true;
	} else if (shot.fullPage) {
		const metrics = await page.send("Page.getLayoutMetrics");
		const size = isRecord(metrics) && isRecord(metrics["cssContentSize"]) ? metrics["cssContentSize"] : null;
		if (size) params["clip"] = { x: 0, y: 0, width: size["width"], height: size["height"], scale: 1 };
		params["captureBeyondViewport"] = true;
	}
	const shotData = await page.send("Page.captureScreenshot", params);
	const data = isRecord(shotData) ? shotData["data"] : undefined;
	if (typeof data !== "string") throw new ExitError(CAPTURE_ERROR, `${shot.name}: the capture returned no image`);
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, Buffer.from(data, "base64"));
}

async function record(page: Page, flow: { name: string; route: string; steps: Action[]; seconds?: number }, out: string): Promise<void> {
	const frames = fs.mkdtempSync(path.join(out, `.${flow.name}-`));
	let n = 0;
	await page.send("Page.startScreencast", { format: "png", everyNthFrame: 1 });
	const collect = setInterval(() => {
		for (const e of page.events.splice(0)) {
			if (e.method !== "Page.screencastFrame" || !isRecord(e.params)) continue;
			const { data, sessionId } = e.params as { data: string; sessionId: number };
			fs.writeFileSync(path.join(frames, `${String(n++).padStart(6, "0")}.png`), Buffer.from(data, "base64"));
			void page.send("Page.screencastFrameAck", { sessionId });
		}
	}, 50);
	try {
		await page.goto(`${(page as Page & { base: string }).base}${flow.route}`);
		for (const step of flow.steps) await act(page, step);
		await new Promise((wake) => setTimeout(wake, (flow.seconds ?? 2) * 1000));
	} finally {
		clearInterval(collect);
		await page.send("Page.stopScreencast");
	}
	execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-framerate", "25", "-i", path.join(frames, "%06d.png"),
		"-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", path.join(out, `${flow.name}.mp4`)]);
	fs.rmSync(frames, { recursive: true, force: true });
}

function sha256(file: string): string {
	return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

async function main(): Promise<number> {
	const opts = options();
	let loaded: unknown;
	try {
		loaded = (await import(pathToFileURL(opts.spec).href)).default;
	} catch (e) {
		throw new ExitError(SPEC_ERROR, `${opts.spec} does not load: ${e instanceof Error ? e.message : String(e)}`);
	}
	const spec = validate(loaded);
	const user = spec.user ?? "Administrator";
	const themesDefault: Theme[] = spec.themes ?? ["light", "dark"];
	const shots = spec.shots.filter((s) => !opts.only.length || opts.only.includes(s.name));
	const unknown = opts.only.filter((n) => !spec.shots.some((s) => s.name === n));
	if (unknown.length) throw new ExitError(SPEC_ERROR, `--only names no shot: ${unknown.join(", ")}`);
	// The page's clock: noon on the demo day where the screenshots are set. The demo data is
	// from 09:00 in the same zone (frappe-shots.sh, ./clock.ts), so the gap between a record
	// and the page's "now" is the same on every machine.
	const clock = opts.clock || spec.demo?.date || "";
	let start: number | null = null;
	if (clock) {
		try {
			start = zonedTime(clock, "12:00:00", spec.timezone ?? opts.timezone);
		} catch (e) {
			throw new ExitError(SPEC_ERROR, `no demo clock: ${e instanceof Error ? e.message : String(e)}`);
		}
	}

	let browser;
	try {
		browser = await launch({
			args: [
				"--headless=new",
				"--hide-scrollbars",
				"--force-color-profile=srgb",
				"--disable-lcd-text",
				"--font-render-hinting=none",
				"--disable-gpu",
				"--disable-features=Translate,MediaRouter,OptimizationHints",
				"--lang=" + (spec.locale ?? opts.locale),
				// The site is reached by its own name (its Host header picks it), served on loopback.
				`--host-resolver-rules=MAP ${new URL(opts.base).hostname} 127.0.0.1`,
			],
		});
	} catch (e) {
		throw new ExitError(ENV_ERROR, `chromium does not start: ${e instanceof Error ? e.message : String(e)}`);
	}
	const page = Object.assign(await newPage(browser.port), { base: opts.base });
	let changed = 0;
	let differing = 0;
	try {
		if (start !== null) await page.send("Page.addScriptToEvaluateOnNewDocument", { source: clockShim(start) });
		await login(page, opts.base, user, opts.password);
		const manifestPath = path.join(opts.out, "manifest.json");
		const previous: ManifestEntry[] = fs.existsSync(manifestPath) ? JSON.parse(fs.readFileSync(manifestPath, "utf8")) : [];
		const entries = new Map(previous.map((e) => [`${e.name}-${e.theme}`, e]));
		const themes: Theme[] = opts.theme ? [opts.theme] : ["light", "dark"];
		for (const theme of themes) {
			const these = shots.filter((s) => (s.themes ?? themesDefault).includes(theme));
			if (!these.length) continue;
			await setDeskTheme(page, user, theme);
			for (const shot of these) {
				const viewport = shot.viewport ?? spec.viewport ?? DEFAULT_VIEWPORT;
				await prepare(page, spec, theme, viewport, opts);
				const id = `${shot.name}-${theme}`;
				const master = path.join(opts.masters, `${id}.png`);
				await capture(page, shot, spec, master);
				const webp = path.join(opts.out, `${id}.webp`);
				const image = readPng(master);
				const committed = fs.existsSync(webp) ? readWebp(webp) : null;
				const result = compare(image, committed, shot.threshold ?? 0.1);
				const allowed = shot.maxDiffRatio ?? 0.001;
				const differs = committed === null || result.ratio > allowed;
				console.log(`${differs ? "DIFF" : "same"} ${id}: ${(result.ratio * 100).toFixed(3)}% of pixels differ (allowed ${(allowed * 100).toFixed(3)}%)`);
				if (differs && opts.mode === "update") {
					writeWebp(master, webp);
					changed += 1;
				} else if (differs) {
					differing += 1;
					if (result.diff) writePng(path.join(opts.masters, "diff", `${id}.png`), result.diff);
				}
				if (opts.mode === "update" && fs.existsSync(webp)) {
					entries.set(id, {
						name: shot.name,
						theme,
						alt: shot.alt,
						featured: Boolean(shot.featured),
						readme: shot.readme ?? null,
						width: image.width,
						height: image.height,
						sha256: sha256(webp),
					});
				}
			}
		}
		if (opts.mode === "update") {
			const names = new Set(spec.shots.map((s) => s.name));
			const sorted = [...entries.values()]
				.filter((e) => names.has(e.name))
				.sort((a, b) => (a.name === b.name ? a.theme.localeCompare(b.theme) : a.name.localeCompare(b.name)));
			fs.mkdirSync(opts.out, { recursive: true });
			const text = JSON.stringify(sorted, null, "\t") + "\n";
			if (!fs.existsSync(manifestPath) || fs.readFileSync(manifestPath, "utf8") !== text) fs.writeFileSync(manifestPath, text);
		}
		if (opts.video) {
			const flow = (spec.videos ?? []).find((v) => v.name === opts.video);
			if (!flow) throw new ExitError(SPEC_ERROR, `--video ${opts.video} names no video in the spec`);
			await setDeskTheme(page, user, "light");
			await prepare(page, spec, "light", spec.viewport ?? DEFAULT_VIEWPORT, opts);
			await record(page, flow, opts.masters);
			console.log(`video: ${path.join(opts.masters, `${flow.name}.mp4`)}`);
		}
	} finally {
		page.close();
		browser.proc.kill();
	}
	if (opts.mode === "update") console.log(`frappe-shots: ${changed} screenshot(s) updated`);
	else console.log(differing ? `frappe-shots: ${differing} screenshot(s) differ (diffs in ${path.join(opts.masters, "diff")})` : "frappe-shots: no differences");
	return differing ? 1 : 0;
}

main().then(
	(code) => process.exit(code),
	(e: unknown) => {
		if (e instanceof ExitError) {
			console.error(`frappe-shots: ${e.message}`);
			process.exit(e.code);
		}
		console.error(`frappe-shots: ${e instanceof Error ? e.stack ?? e.message : String(e)}`);
		process.exit(CAPTURE_ERROR);
	},
);
