// What a release-please release PR does to an app's extra files: run release-please's own
// generic updater (the one `"type": "generic"` names) over each extra file the app's
// release-please-config.json lists, at a new version. run.sh's `listing` suite then checks that
// `frappe-nix listing readme --check` still passes (docs/app-standards/spec.md §7 N5, §2.19).
//
//   node release-please-bump.cjs <release-please package dir> <app dir> <version>
"use strict";
const fs = require("node:fs");
const path = require("node:path");

const [pkg, app, version] = process.argv.slice(2);
if (!pkg || !app || !version) {
	console.error("usage: node release-please-bump.cjs <release-please dir> <app dir> <version>");
	process.exit(2);
}
const { Generic } = require(path.join(pkg, "build/src/updaters/generic.js"));
const { Version } = require(path.join(pkg, "build/src/version.js"));

const config = JSON.parse(fs.readFileSync(path.join(app, "release-please-config.json"), "utf8"));
const extra = (config.packages?.["."]?.["extra-files"] ?? []).map((f) => (typeof f === "string" ? { type: "generic", path: f } : f));
const updater = new Generic({ version: Version.parse(version) });
for (const file of extra) {
	if (file.type !== "generic") continue;
	const where = path.join(app, file.path);
	const before = fs.readFileSync(where, "utf8");
	const after = updater.updateContent(before);
	fs.writeFileSync(where, after);
	console.log(`${before === after ? "unchanged" : "updated  "} ${file.path}`);
}
