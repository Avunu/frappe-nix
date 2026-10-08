// Registers an app's Vite bundles in sites/assets/assets.json
// (docs/app-standards/spec.md S30, §5.11; docs/app-standards/assets.md).
//
// frappe resolves `bundled_asset("<name>.bundle.js")` and every
// `app_include_js` through sites/assets/assets.json, which esbuild.js writes
// from its own outputs only. A Vite build that emits
// public/dist/js/<name>.bundle.<hash>.js is invisible to it: the page asks
// for /assets/<app>/dist/js/<name>.bundle.js and gets a 404. register()
// reads the Vite manifests under public/dist and adds a
// "<name>.bundle.<ext>" key for every entry chunk and its stylesheets,
// exactly as frappe's own `--using-cached` path names them.
//
// The one implementation, with two callers:
//
// - lib/js/esbuild-preload.js runs it after each app's `yarn build` inside
//   frappe's esbuild.js, on every bench frappe-nix builds;
// - an opted-in app with a Vite config gets the region between the markers
//   below as the managed scripts/vite-register.mjs, whose `build` script ends
//   with `node scripts/vite-register.mjs`, so a stock bench (Frappe Cloud, a
//   registry install) registers the same keys.
//
// Running both changes nothing the second time: a key is written only when
// its value differs, and assets.json is rewritten (to a temporary file, then
// renamed) only when a key changed. assets-rtl.json is never touched.
//
// The text between the markers is copied byte for byte into
// py/frappe_nix_tools/frappe_nix_tools/data/templates/scripts/vite-register.mjs,
// and the standards-vite-register-copy check fails when the two differ. It
// fits in 80 columns and is in oxfmt's output form, tabs or spaces, so the
// managed copy is stable under an app's formatter.
"use strict";

const fs = require("node:fs");
const path = require("node:path");

// vite-register:begin
// A bundle Vite emitted: <name>.bundle.<hash>.<js|css>.
const BUNDLE = /^(?<name>[^/]+)\.bundle\.[A-Za-z0-9_-]{6,}\.(?<ext>js|css)$/;

const isDir = (dir) => {
	try {
		return fs.statSync(dir).isDirectory();
	} catch {
		return false;
	}
};

const filesUnder = (dir) => {
	const out = [];
	for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
		const full = path.join(dir, entry.name);
		if (entry.isDirectory() && entry.name !== "node_modules") {
			out.push(...filesUnder(full));
		} else if (entry.isFile()) {
			out.push(full);
		}
	}
	return out.sort();
};

// manifest*.json and .vite/manifest*.json anywhere under public/dist.
const isManifest = (file) => /^manifest.*\.json$/.test(path.basename(file));
const manifestsUnder = (dist) => filesUnder(dist).filter(isManifest);

// A manifest's paths are relative to the outDir it was written for.
const outDirOf = (manifest) => {
	const dir = path.dirname(manifest);
	return path.basename(dir) === ".vite" ? path.dirname(dir) : dir;
};

const readManifest = (file, log) => {
	try {
		const doc = JSON.parse(fs.readFileSync(file, "utf8"));
		return doc && typeof doc === "object" ? doc : {};
	} catch (error) {
		log(`vite-register: skipping ${file}: ${error.message}`);
		return {};
	}
};

// The keys an app's Vite manifests ask for, in a stable order.
const bundleKeys = ({ appDir, app, log }) => {
	const dist = path.join(appDir, app, "public", "dist");
	const keys = {};
	if (!isDir(dist)) {
		return keys;
	}
	for (const manifest of manifestsUnder(dist)) {
		const outDir = outDirOf(manifest);
		const chunks = Object.values(readManifest(manifest, log));
		for (const chunk of chunks) {
			if (!chunk || chunk.isEntry !== true) {
				continue;
			}
			const files = [chunk.file, ...(chunk.css || [])];
			for (const file of files) {
				if (typeof file !== "string") {
					continue;
				}
				const rel = path.relative(dist, path.join(outDir, file));
				const match = BUNDLE.exec(path.basename(rel));
				if (!match || rel.startsWith("..")) {
					continue;
				}
				const { name, ext } = match.groups;
				const url = rel.split(path.sep).join("/");
				keys[`${name}.bundle.${ext}`] = `/assets/${app}/dist/${url}`;
			}
		}
	}
	return keys;
};

const readJson = (file) => {
	if (!fs.existsSync(file)) {
		return {};
	}
	return JSON.parse(fs.readFileSync(file, "utf8"));
};

// frappe's own format: four spaces, no final newline.
const writeJson = (file, value) => {
	const tmp = `${file}.${process.pid}.tmp`;
	fs.writeFileSync(tmp, JSON.stringify(value, null, 4));
	fs.renameSync(tmp, file);
};

const sameFile = (a, b) => {
	try {
		const [x, y] = [fs.statSync(a), fs.statSync(b)];
		return x.ino === y.ino && x.dev === y.dev;
	} catch {
		return false;
	}
};

// Copy src into dest, file by file. A file that is a hard link of its
// source is left alone: copying onto it would truncate both.
const copyTree = (src, dest) => {
	for (const file of filesUnder(src)) {
		const target = path.join(dest, path.relative(src, file));
		if (sameFile(file, target)) {
			continue;
		}
		fs.mkdirSync(path.dirname(target), { recursive: true });
		fs.copyFileSync(file, target);
	}
};

// public/dist, and each other directory under public/ a Vite build wrote
// with `build.manifest` on (taskview's public/portal/).
const viteOutDirs = (publicDir) => {
	const out = ["dist"];
	for (const entry of fs.readdirSync(publicDir, { withFileTypes: true })) {
		const vite = path.join(publicDir, entry.name, ".vite");
		if (entry.isDirectory() && entry.name !== "dist" && isDir(vite)) {
			out.push(entry.name);
		}
	}
	return out.filter((name) => isDir(path.join(publicDir, name)));
};

// When sites/assets/<app> is a real directory rather than the link to the
// app's public/ (`bench build --hard-link`, or an image that copied public/
// before the app's build ran), the build's outputs are copied into it.
const copyOutputs = ({ appDir, app, sitesDir }) => {
	const target = path.join(sitesDir, "assets", app);
	let stat;
	try {
		stat = fs.lstatSync(target);
	} catch {
		return [];
	}
	if (stat.isSymbolicLink() || !stat.isDirectory()) {
		return [];
	}
	const publicDir = path.join(appDir, app, "public");
	if (!isDir(publicDir)) {
		return [];
	}
	const dirs = viteOutDirs(publicDir);
	for (const name of dirs) {
		copyTree(path.join(publicDir, name), path.join(target, name));
	}
	return dirs;
};

const register = ({ appDir, app, sitesDir, log = console.log }) => {
	const keys = bundleKeys({ appDir, app, log });
	const file = path.join(sitesDir, "assets", "assets.json");
	const changed = [];
	if (Object.keys(keys).length > 0) {
		const assets = readJson(file);
		for (const [key, value] of Object.entries(keys)) {
			if (assets[key] !== value) {
				assets[key] = value;
				changed.push(key);
				log(`vite-register: ${app}: ${key} -> ${value} (vite)`);
			}
		}
		if (changed.length > 0) {
			fs.mkdirSync(path.dirname(file), { recursive: true });
			writeJson(file, assets);
		}
	}
	const copied = copyOutputs({ appDir, app, sitesDir });
	return { keys, changed, copied };
};

const realpath = (dir) => {
	try {
		return fs.realpathSync(dir);
	} catch {
		return null;
	}
};

// The bench's sites/ for a build running in <bench>/apps/<app>: the first of
// $FRAPPE_BENCH_ROOT/sites, the logical ../../sites (apps/<app> may be a
// link) and the physical one that holds apps.txt; null outside a bench.
const findSites = ({ env, cwd }) => {
	const candidates = [];
	if (env.FRAPPE_BENCH_ROOT) {
		candidates.push(path.join(env.FRAPPE_BENCH_ROOT, "sites"));
	}
	candidates.push(path.resolve(env.PWD || cwd, "../../sites"));
	candidates.push(path.join(realpath(cwd) || cwd, "../../sites"));
	const hasApps = (dir) => fs.existsSync(path.join(dir, "apps.txt"));
	return candidates.find(hasApps) || null;
};

// The Frappe app a repository root holds: the directory beside it with a
// hooks.py and a modules.txt (one per app, by frappe's layout).
const appOf = (appDir) => {
	const has = (e, file) => fs.existsSync(path.join(appDir, e.name, file));
	const found = [];
	for (const e of fs.readdirSync(appDir, { withFileTypes: true })) {
		if (e.isDirectory() && has(e, "hooks.py") && has(e, "modules.txt")) {
			found.push(e.name);
		}
	}
	return found.length === 1 ? found[0] : null;
};
// vite-register:end

module.exports = { BUNDLE, appOf, bundleKeys, findSites, register };
