// lib/shots/cdp.ts's launch under a TMPDIR too long for a Unix socket (sun_path is 108
// bytes). Chromium makes its process-singleton socket under TMPDIR and aborts with "Socket
// path too long" when that path does not fit, which a worktree's TMPDIR easily makes it.
// Run by the standards-shots-cdp check with the real chromium: CDP names the engine's
// cdp.ts, CHROMIUM the browser, SHORT a short writable directory standing in for
// XDG_RUNTIME_DIR.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const { launch, runDirBase } = await import(process.env["CDP"]!);
const chromium = process.env["CHROMIUM"]!;
const short = process.env["SHORT"]!;
const ok = (msg: string) => console.log(`ok   ${msg}`);

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
