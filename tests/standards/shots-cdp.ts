// lib/shots/cdp.ts's launch under a TMPDIR too long for a Unix socket (sun_path is 108
// bytes). Chromium makes its process-singleton socket under TMPDIR and aborts with "Socket
// path too long" when that path does not fit, which a worktree's TMPDIR easily makes it.
// Then its inflight(), what frappe-shots' quiet network counts, on a page that starts a Web
// Worker: the worker's script does not stay in flight, a fetch of the page's does.
// Run by the standards-shots-cdp check with the real chromium: CDP names the engine's
// cdp.ts, CHROMIUM the browser, SHORT a short writable directory standing in for
// XDG_RUNTIME_DIR.
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";

const { inflight, launch, newPage, runDirBase } = await import(process.env["CDP"]!);
const chromium = process.env["CHROMIUM"]!;
const short = process.env["SHORT"]!;
const ok = (msg: string) => console.log(`ok   ${msg}`);

// A page that starts a dedicated worker from a script URL and fetches /held, which the
// server answers only once it is told to.
async function workerPage(port: number): Promise<void> {
	let release = () => {};
	const held = new Promise<void>((r) => (release = r));
	const server = http.createServer((req, res) => {
		if (req.url === "/") {
			res.writeHead(200, { "Content-Type": "text/html" });
			res.end(`<!doctype html><title>-</title><script>
				new Worker("/worker.js").onmessage = (e) => { document.title = e.data; };
				fetch("/held");
			</script>`);
		} else if (req.url === "/worker.js") {
			res.writeHead(200, { "Content-Type": "text/javascript" });
			res.end(`postMessage("up");`);
		} else if (req.url === "/held") {
			void held.then(() => res.end("{}"));
		} else {
			res.writeHead(404);
			res.end();
		}
	});
	await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
	const site = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
	const page = await newPage(port);
	try {
		await page.goto(`${site}/`, { waitUntil: "none" });
		await page.waitFor(`document.title === "up"`);
		const sent = (url: string) =>
			page.events.find((e) => e.method === "Network.requestWillBeSent" && e.params.request.url === site + url)?.params;
		const worker = sent("/worker.js");
		assert.ok(worker, "the worker's script was not announced to the page");
		assert.equal(worker.loaderId, "");
		for (let i = 0; i < 50 && !sent("/held"); i++) await new Promise((r) => setTimeout(r, 100));
		const fetched = sent("/held");
		assert.ok(fetched, "the page's fetch of /held was not seen");
		assert.deepEqual([...inflight(page.events)], [fetched.requestId], "only the held fetch is in flight");
		ok("a started worker's script is not in flight; the page's held fetch is");
		release();
		for (let i = 0; i < 50 && inflight(page.events).size; i++) await new Promise((r) => setTimeout(r, 100));
		assert.deepEqual([...inflight(page.events)], [], "requests are still in flight once the fetch is answered");
		ok("nothing is in flight once the fetch is answered");
	} finally {
		page.close();
		server.closeAllConnections();
		server.close();
	}
}

const long = path.join(process.cwd(), "t".repeat(60), "u".repeat(60), "v".repeat(60));
fs.mkdirSync(long, { recursive: true });
assert.ok(long.length > 107);

// A short TMPDIR is used as it is.
assert.equal(runDirBase({ TMPDIR: short }), short);
ok("a short TMPDIR is the base");
// A long one falls back to XDG_RUNTIME_DIR.
assert.equal(runDirBase({ TMPDIR: long, XDG_RUNTIME_DIR: short }), short);
ok("a long TMPDIR falls back to a short XDG_RUNTIME_DIR");

process.env["TMPDIR"] = long;
process.env["XDG_RUNTIME_DIR"] = short;

// The real browser starts and answers, and its singleton socket is under the run directory.
const browser = await launch({ binary: chromium });
try {
	assert.ok(browser.dir.startsWith(short + "/cdp-"), `profile ${browser.dir} is not under ${short}`);
	const r = await fetch(`http://127.0.0.1:${browser.port}/json/version`);
	assert.ok(r.ok);
	const socket = fs.readlinkSync(path.join(browser.dir, "SingletonSocket"));
	assert.ok(socket.startsWith(browser.dir + "/"), `the singleton socket ${socket} is outside the run directory`);
	assert.ok(socket.length <= 107, `the singleton socket path is ${socket.length} bytes`);
	ok(`chromium starts under a ${long.length}-byte TMPDIR; its socket is ${socket}`);
	await workerPage(browser.port);
} finally {
	browser.proc.kill();
	await new Promise((r) => browser.proc.once("exit", r));
}
// The exit handler removes the run directory with the browser.
for (let i = 0; i < 50 && fs.existsSync(browser.dir); i++) await new Promise((r) => setTimeout(r, 100));
assert.ok(!fs.existsSync(browser.dir), `${browser.dir} is left behind`);
assert.deepEqual(
	fs.readdirSync(long).filter((n) => n.startsWith("org.chromium")),
	[],
	"chromium wrote its singleton under the long TMPDIR",
);
ok("the run directory goes with the browser, and nothing lands in the long TMPDIR");
