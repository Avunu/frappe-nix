// Stands in for `vite build` in frappe-nix's tests, which have no
// node_modules and no network: it writes the files vite.config.ts and
// portal/vite.config.ts would, under the same names, with content hashes and
// a .vite/manifest.json in each outDir. Same input, same names.
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const hash = (text) => createHash("sha256").update(text).digest("base64url").slice(0, 8);

const write = (file, text) => {
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, text);
};

const source = (rel) => fs.readFileSync(rel, "utf8");

// The desk bundle (vite.config.ts): an entry, its stylesheet and a shared chunk.
const dist = "spa_app/public/dist";
const shared = 'export const name = "spa";\n';
const sharedFile = `js/index-${hash(shared)}.js`;
const js = `import { name } from "./${path.basename(sharedFile)}";\n${source("spa_app/public/js/spa/spa.entry.ts")}`;
const css = source("spa_app/public/js/spa/spa.css");
const jsFile = `js/spa.bundle.${hash(js)}.js`;
const cssFile = `css/spa.bundle.${hash(css)}.css`;
write(path.join(dist, sharedFile), shared);
write(path.join(dist, jsFile), js);
write(path.join(dist, cssFile), css);
write(
	path.join(dist, ".vite/manifest.json"),
	`${JSON.stringify(
		{
			"_index.js": { file: sharedFile, name: "index" },
			"spa_app/public/js/spa/spa.entry.ts": {
				file: jsFile,
				name: "spa",
				src: "spa_app/public/js/spa/spa.entry.ts",
				isEntry: true,
				imports: ["_index.js"],
				css: [cssFile],
			},
		},
		null,
		2,
	)}\n`,
);

// The portal SPA (portal/vite.config.ts): its own outDir, emptied first.
const portal = "spa_app/public/portal";
fs.rmSync(portal, { recursive: true, force: true });
const main = source("portal/src/main.ts");
const mainFile = `assets/index-${hash(main)}.js`;
write(path.join(portal, mainFile), main);
write(
	path.join(portal, "index.html"),
	source("portal/index.html").replace("/src/main.ts", `/assets/spa_app/portal/${mainFile}`),
);
write(
	path.join(portal, ".vite/manifest.json"),
	`${JSON.stringify({ "index.html": { file: mainFile, src: "index.html", isEntry: true } }, null, 2)}\n`,
);

console.log(`build.mjs: ${jsFile}, ${cssFile}, portal/${mainFile}`);
