// A zero-dependency Chrome DevTools Protocol driver (Node 24 has a global WebSocket), the
// engine frappe-shots drives chromium with (docs/app-standards/spec.md §5.5). Moved here from
// an app's own table suites, where it was proven against the Frappe desk: an ephemeral debug
// port per launch, a sweep of abandoned profiles on the way in, and a login that confirms
// the session before it returns.
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import type { ChildProcess } from "node:child_process";

/**
 * Parameters for a CDP command. The protocol takes one JSON object per method
 * and this driver forwards whatever a suite hands it, so the values stay open.
 */
export type CdpParams = Record<string, unknown>;

/**
 * A protocol notification — anything that arrives with a `method` and no `id`.
 *
 * `params` stays `unknown` deliberately: `Page.loadEventFired` carries nothing
 * this driver reads, `Log.entryAdded` carries an entry that `consoleErrors()`
 * narrows at the point of use, and a suite is free to enable a domain this file
 * has never heard of.
 */
export interface CdpEvent {
	readonly method: string;
	readonly params?: unknown;
}

/** The `Log.entryAdded` entry, once `isLogEntryAdded` has proven its shape. */
export interface CdpLogEntry {
	readonly level: string;
	readonly source: string;
	readonly text: string;
}

/** A `Log.entryAdded` notification whose entry has been proven readable. */
export interface CdpLogEntryAddedEvent extends CdpEvent {
	readonly method: "Log.entryAdded";
	readonly params: { readonly entry: CdpLogEntry };
}

/**
 * What `page.eval` accepts: a source string, or a function that is stringified
 * and invoked as `(${expr})()` — nothing from its closure survives the trip.
 */
export type EvalInput = string | ((...args: never[]) => unknown);

/** Options for {@link Page.goto}. */
export interface GotoOptions {
	/** `"none"` returns as soon as the navigation has been dispatched. */
	waitUntil?: "load" | "none";
}

/** Options for {@link Page.eval}. */
export interface EvalOptions {
	/** Settle a promise the expression returns before reading its value. */
	awaitPromise?: boolean;
}

/** Options for {@link Page.waitFor}. */
export interface WaitForOptions {
	timeout?: number;
	interval?: number;
}

/** Options for {@link Page.screenshot}. */
export interface ScreenshotOptions {
	fullPage?: boolean;
}

/** Options for {@link launch}. */
export interface LaunchOptions {
	/** A fixed debug port. Omit it for an ephemeral one — see `freePort`. */
	port?: number;
	headless?: boolean;
	/** An existing profile to reuse; a temporary one is minted otherwise. */
	userDataDir?: string;
	/** More chromium flags (frappe-shots' rendering flags). */
	args?: string[];
	/** The chromium binary; `chromium` from PATH by default. */
	binary?: string;
}

/** What {@link launch} hands back: the browser process and where it listens. */
export interface Browser {
	proc: ChildProcess;
	port: number;
	dir: string;
}

/**
 * One attached page.
 *
 * `eval` and `waitFor` are generic over the value the page returns. The wire
 * carries `returnByValue` JSON with no shape the driver can know, so the caller
 * names what it expects — and gets `unknown` if it names nothing.
 */
export interface Page {
	/** Raw protocol call. Resolves with the command's `result`, unnarrowed. */
	send(method: string, params?: CdpParams): Promise<unknown>;
	/** Notifications received since the last `goto` cleared them. */
	events: CdpEvent[];
	targetId: string;
	goto(url: string, options?: GotoOptions): Promise<void>;
	eval<T = unknown>(expr: EvalInput, options?: EvalOptions): Promise<T>;
	waitFor<T = unknown>(expr: EvalInput, options?: WaitForOptions): Promise<T>;
	hover(x: number, y: number): Promise<void>;
	drag(x1: number, y1: number, x2: number, y2: number, steps?: number): Promise<void>;
	screenshot(file: string, options?: ScreenshotOptions): Promise<string>;
	consoleErrors(): string[];
	networkErrors(): string[];
	close(): void;
}

/** A command awaiting its reply, keyed by protocol message id. */
interface PendingCall {
	resolve: (value: unknown) => void;
	reject: (reason: Error) => void;
}

/**
 * Narrow a freshly parsed JSON value to something whose properties can be read.
 * Everything arriving over the socket passes through here before it is touched.
 */
function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

/** A parsed message carrying a `method` is a notification, not a command reply. */
function isCdpEvent(value: Record<string, unknown>): value is Record<string, unknown> & CdpEvent {
	return typeof value["method"] === "string";
}

/** Prove the three `Log.entryAdded` fields the error reporters below read. */
function isLogEntryAdded(event: CdpEvent): event is CdpLogEntryAddedEvent {
	if (event.method !== "Log.entryAdded" || !isRecord(event.params)) return false;
	const entry = event.params["entry"];
	return (
		isRecord(entry) &&
		typeof entry["level"] === "string" &&
		typeof entry["source"] === "string" &&
		typeof entry["text"] === "string"
	);
}

/**
 * The message for a page-side throw: CDP's own `description` when it has one
 * ("TypeError: x is not a function\n    at ..."), else the raw details.
 */
function describeException(exceptionDetails: unknown): string {
	const exception = isRecord(exceptionDetails) ? exceptionDetails["exception"] : undefined;
	const description = isRecord(exception) ? exception["description"] : undefined;
	if (typeof description === "string" && description) return description;
	return JSON.stringify(exceptionDetails);
}

/**
 * An unused TCP port.
 *
 * Suites used to hardcode a debug port each. Any two overlapping runs — a suite
 * and an ad-hoc screenshot script, or two runs of one suite — then
 * pointed at the same port, and `launch()` happily ATTACHED to the other run's
 * browser instead of starting its own. The symptom was a whole suite failing on
 * `waitFor` timeouts because it was driving somebody else's page, which reads
 * exactly like a product regression. An ephemeral port per launch removes the
 * class of failure.
 */
async function freePort(): Promise<number> {
	return new Promise((resolve, reject) => {
		const server = net.createServer();
		server.on("error", reject);
		server.listen(0, "127.0.0.1", () => {
			// `address()` is `string | AddressInfo | null` because the one method
			// also answers for pipe servers and for one that never bound. A TCP
			// listen on 127.0.0.1 only ever lands in the AddressInfo branch.
			const address = server.address();
			if (address === null || typeof address === "string") {
				server.close(() =>
					reject(new Error(`could not read an ephemeral port (got ${JSON.stringify(address)})`)),
				);
				return;
			}
			const { port } = address;
			server.close(() => resolve(port));
		});
	});
}

/**
 * Delete Chromium profiles left by earlier runs.
 *
 * Cleaning up on exit is not enough on its own: a suite killed by a timeout —
 * or by the runner — never reaches its own teardown, and each abandoned profile
 * is tens of megabytes. A session of repeated runs left 217 of them totalling
 * 3.5 GB, which contributed to the box running out of memory and Chromium
 * evaluations timing out. Sweeping on the way IN is the only cleanup that
 * survives being killed.
 *
 * Only profiles older than the cutoff go, so a concurrent run is never touched.
 */
function sweepStaleProfiles(maxAgeMs = 60 * 60 * 1000): void {
	const tmp = os.tmpdir();
	let entries: string[] = [];
	try {
		entries = fs.readdirSync(tmp).filter((n) => n.startsWith("cdp-"));
	} catch {
		return;
	}
	const cutoff = Date.now() - maxAgeMs;
	for (const name of entries) {
		const full = path.join(tmp, name);
		try {
			if (fs.statSync(full).mtimeMs < cutoff) fs.rmSync(full, { recursive: true, force: true });
		} catch {
			/* in use, or gone already */
		}
	}
}

export async function launch({
	port,
	headless = true,
	userDataDir,
	args: extra = [],
	binary = "chromium",
}: LaunchOptions = {}): Promise<Browser> {
	sweepStaleProfiles();
	port = port || (await freePort());
	const dir = userDataDir || fs.mkdtempSync(path.join(os.tmpdir(), "cdp-"));
	const args = [
		`--remote-debugging-port=${port}`,
		`--user-data-dir=${dir}`,
		"--no-first-run",
		"--no-default-browser-check",
		"--disable-gpu",
		"--disable-dev-shm-usage",
		"--no-sandbox",
		"--window-size=1600,1000",
		...extra,
		"about:blank",
	];
	if (headless && !extra.some((a) => a.startsWith("--headless"))) args.unshift("--headless=new");
	// The stdio tuple is what makes `proc.stderr` a stream rather than `null`:
	// slot 2 is "pipe", so the drain below has something to attach to.
	const proc = spawn(binary, args, { stdio: ["ignore", "pipe", "pipe"] });
	proc.stderr.on("data", () => {});
	for (let i = 0; i < 100; i++) {
		try {
			const r = await fetch(`http://127.0.0.1:${port}/json/version`);
			if (r.ok) break;
		} catch {
			/* not up yet */
		}
		await new Promise((r) => setTimeout(r, 200));
	}
	// Best-effort teardown for the normal path. `proc.kill()` is asynchronous, so
	// the profile is often still held when the suite's own process exits — hence
	// the sweep above, which is what actually keeps /tmp bounded.
	const cleanup = () => {
		try {
			if (!userDataDir) fs.rmSync(dir, { recursive: true, force: true });
		} catch {
			/* still in use; the next run's sweep will take it */
		}
	};
	proc.on("exit", cleanup);
	process.on("exit", cleanup);

	return { proc, port, dir };
}

export async function newPage(port: number): Promise<Page> {
	const created = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" });
	// `/json/new` answers with bare JSON. The two fields this driver actually
	// needs are proven here rather than assumed; before, a malformed reply failed
	// one line later inside the WebSocket constructor instead.
	const target: unknown = await created.json();
	const targetId = isRecord(target) ? target["id"] : undefined;
	const debuggerUrl = isRecord(target) ? target["webSocketDebuggerUrl"] : undefined;
	if (typeof targetId !== "string" || typeof debuggerUrl !== "string") {
		throw new Error(`CDP /json/new returned an unusable target: ${JSON.stringify(target)}`);
	}
	const ws = new WebSocket(debuggerUrl);
	await new Promise((res, rej) => {
		ws.addEventListener("open", res);
		ws.addEventListener("error", rej);
	});

	let id = 0;
	const pending = new Map<number, PendingCall>();
	const events: CdpEvent[] = [];
	ws.addEventListener("message", (m: MessageEvent<unknown>) => {
		// CDP frames are text. `String` is what `JSON.parse` would have applied to
		// anything else anyway, so the coercion is a type-level formality.
		const msg: unknown = JSON.parse(typeof m.data === "string" ? m.data : String(m.data));
		if (!isRecord(msg)) return;
		const rawId = msg["id"];
		const mid = typeof rawId === "number" ? rawId : undefined;
		const settle = mid === undefined ? undefined : pending.get(mid);
		if (mid !== undefined && settle !== undefined) {
			pending.delete(mid);
			const error = msg["error"];
			if (error) settle.reject(new Error(JSON.stringify(error)));
			else settle.resolve(msg["result"]);
		} else if (isCdpEvent(msg)) {
			events.push(msg);
		}
	});
	const send = (method: string, params: CdpParams = {}): Promise<unknown> =>
		new Promise((resolve, reject) => {
			const mid = ++id;
			pending.set(mid, { resolve, reject });
			ws.send(JSON.stringify({ id: mid, method, params }));
			setTimeout(() => {
				if (pending.has(mid)) {
					pending.delete(mid);
					reject(new Error(`timeout: ${method}`));
				}
			}, 60000);
		});

	await send("Page.enable");
	await send("Runtime.enable");
	await send("Network.enable");
	await send("Log.enable");

	const page: Page = {
		send,
		events,
		targetId,
		async goto(url: string, { waitUntil = "load" }: GotoOptions = {}): Promise<void> {
			await send("Page.navigate", { url });
			if (waitUntil === "none") return;
			const deadline = Date.now() + 45000;
			while (Date.now() < deadline) {
				const done = events.some((e) => e.method === "Page.loadEventFired");
				if (done) break;
				await new Promise((wake) => setTimeout(wake, 100));
			}
			events.length = 0;
		},
		async eval<T = unknown>(expr: EvalInput, { awaitPromise = true }: EvalOptions = {}): Promise<T> {
			const r = await send("Runtime.evaluate", {
				expression: typeof expr === "function" ? `(${expr})()` : expr,
				returnByValue: true,
				awaitPromise,
			});
			if (!isRecord(r)) {
				throw new Error(`Runtime.evaluate returned an unreadable reply: ${JSON.stringify(r)}`);
			}
			const exceptionDetails = r["exceptionDetails"];
			if (exceptionDetails) {
				throw new Error(describeException(exceptionDetails));
			}
			const result = r["result"];
			if (!isRecord(result)) {
				throw new Error(`Runtime.evaluate returned no result object: ${JSON.stringify(r)}`);
			}
			// The one place a wire value is taken on trust. `returnByValue` JSON has
			// no shape the driver can check, so the caller's `T` — `unknown` unless it
			// named something — is the contract, and every narrowing happens there.
			return result["value"] as T;
		},
		async waitFor<T = unknown>(
			expr: EvalInput,
			{ timeout = 30000, interval = 200 }: WaitForOptions = {},
		): Promise<T> {
			const deadline = Date.now() + timeout;
			let last: unknown;
			while (Date.now() < deadline) {
				try {
					const seen = await page.eval<T>(expr);
					last = seen;
					if (seen) return seen;
				} catch (e) {
					last = e instanceof Error ? e.message : String(e);
				}
				await new Promise((wake) => setTimeout(wake, interval));
			}
			// What the page looked like when the wait gave up — the URL, title
			// and visible text say "setup wizard", "server error" or "login" at a
			// glance, and the console errors say why a bundle did not boot.
			let where = "";
			try {
				where = await page.eval<string>(
					`location.href + ' | ' + document.title + ' | ' + (document.body ? document.body.innerText.replace(/\\s+/g, ' ').slice(0, 400) : '<no body>')`,
				);
			} catch (e) {
				where = `<page unreadable: ${e instanceof Error ? e.message : String(e)}>`;
			}
			const errors = page.consoleErrors().slice(-5).join(" || ");
			throw new Error(
				`waitFor timed out: ${String(expr).slice(0, 200)} (last: ${JSON.stringify(last)?.slice(0, 300)})\n  page: ${where}\n  console: ${errors || "none"}`,
			);
		},
		/**
		 * Move the real pointer. CSS `:hover` does not respond to synthetic
		 * MouseEvents dispatched from page script, so row-hover styling can only be
		 * tested through the input domain.
		 */
		async hover(x: number, y: number): Promise<void> {
			await send("Input.dispatchMouseEvent", {
				type: "mouseMoved",
				x: Math.round(x),
				y: Math.round(y),
				buttons: 0,
			});
			await new Promise((wake) => setTimeout(wake, 120));
		},
		/** Press, move and release the real pointer — a genuine drag gesture. */
		async drag(x1: number, y1: number, x2: number, y2: number, steps = 6): Promise<void> {
			const send3 = (type: string, x: number, y: number, extra: CdpParams = {}) =>
				send("Input.dispatchMouseEvent", {
					type,
					x: Math.round(x),
					y: Math.round(y),
					button: "left",
					clickCount: 1,
					buttons: type === "mouseReleased" ? 0 : 1,
					...extra,
				});
			await send3("mousePressed", x1, y1);
			for (let i = 1; i <= steps; i++) {
				await send3("mouseMoved", x1 + ((x2 - x1) * i) / steps, y1 + ((y2 - y1) * i) / steps);
				await new Promise((wake) => setTimeout(wake, 20));
			}
			await send3("mouseReleased", x2, y2);
			await new Promise((wake) => setTimeout(wake, 150));
		},
		async screenshot(file: string, { fullPage = false }: ScreenshotOptions = {}): Promise<string> {
			const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: fullPage });
			const data = isRecord(r) ? r["data"] : undefined;
			if (typeof data !== "string") {
				throw new Error(`Page.captureScreenshot returned no image data for ${file}`);
			}
			fs.writeFileSync(file, Buffer.from(data, "base64"));
			return file;
		},
		/**
		 * JavaScript errors only.
		 *
		 * `Log.entryAdded` also carries network entries — a 4xx from an unrelated
		 * frappe endpoint (background count queries, telemetry) shows up as an
		 * "error" and would make these suites flaky. What matters here is whether
		 * the table code threw, so network noise is reported separately.
		 */
		consoleErrors(): string[] {
			return events
				.filter(
					(e): e is CdpLogEntryAddedEvent =>
						isLogEntryAdded(e) && e.params.entry.level === "error" && e.params.entry.source !== "network",
				)
				.map((e) => `${e.params.entry.source}: ${e.params.entry.text}`);
		},
		networkErrors(): string[] {
			return events
				.filter(
					(e): e is CdpLogEntryAddedEvent =>
						isLogEntryAdded(e) && e.params.entry.level === "error" && e.params.entry.source === "network",
				)
				.map((e) => e.params.entry.text);
		},
		close: () => ws.close(),
	};
	return page;
}

/**
 * Log into the desk and CONFIRM it worked.
 *
 * The previous version submitted the form, slept 3s and returned true. When the
 * bench was busy that returned before the session existed, and the suite then
 * failed on a page-load timeout several assertions later — which reads like a
 * product bug rather than "we were never logged in". Each suite also opens a
 * fresh browser profile, so a full run authenticates six times and the
 * `tabSessions` table grows accordingly; transient login failures are expected
 * and worth retrying rather than propagating.
 */
export async function login(page: Page, base: string, user = "Administrator", pwd = "admin"): Promise<true> {
	const deadline = Date.now() + 90000;
	let lastSeen: string | null = null;
	let lastProbe: string | undefined;

	for (let attempt = 1; Date.now() < deadline; attempt++) {
		// `/login` only to give the page an origin to fetch from; the login itself
		// is the API call, so the server's answer (401 wrong password, 417,
		// 503…) is what ends up in the log rather than a form that silently
		// stayed put.
		await page.goto(`${base}/login`);
		try {
			lastProbe = await page.eval<string>(
				`(async () => {
           const r = await fetch('/api/method/login', {
             method: 'POST',
             headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
             body: JSON.stringify({ usr: ${JSON.stringify(user)}, pwd: ${JSON.stringify(pwd)} }),
           });
           return 'login ' + r.status + ' ' + (await r.text()).slice(0, 300);
         })()`,
			);
		} catch (e) {
			lastProbe = `login threw ${e instanceof Error ? e.message : String(e)}`;
		}

		const until = Date.now() + 20000;
		while (Date.now() < until) {
			await new Promise((r) => setTimeout(r, 500));
			try {
				// A Guest answers "Guest"; `null` here means the API did not answer
				// 2xx at all, which is why the status and body are kept for the log.
				lastSeen = await page.eval<string | null>(
					`(async () => {
             const r = await fetch('/api/method/frappe.auth.get_logged_user');
             if (!r.ok) { window.__cdpLoginProbe = r.status + ' ' + (await r.text()).slice(0, 300); return null; }
             return (await r.json()).message || null;
           })()`,
				);
			} catch (e) {
				lastSeen = null;
				lastProbe = e instanceof Error ? e.message : String(e);
			}
			if (lastSeen === user) return true;
		}
		try {
			const probe = await page.eval<string | undefined>(`window.__cdpLoginProbe`);
			if (probe) lastProbe = probe;
		} catch {
			/* the page may be mid-navigation */
		}
		console.error(
			`login attempt ${attempt} did not take (saw ${JSON.stringify(lastSeen)}; ${lastProbe ?? "no HTTP detail"}), retrying`,
		);
	}

	throw new Error(
		`could not log into ${base} as ${user} (last get_logged_user: ${JSON.stringify(lastSeen)})`,
	);
}
