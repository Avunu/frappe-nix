// Make a node_modules/.yarn-integrity written by the offline install agree
// with the lock the bench tree carries.
//
// yarn records, per lock entry, the `resolved` string of the lock it installed
// from. The offline install runs on a copy of the lock that fixup-yarn-lock
// has rewritten to mirror file names, so that is what gets recorded -- and
// `yarn check --integrity` or `yarn install` in the bench tree, where the lock
// is the app's own, then reports "Lock files don't match" and (in Frappe's
// build) goes on to install from the network. This puts each entry's own
// `resolved` back. Nothing else in the file is touched.
//
// Usage: node restore-yarn-integrity.js <.yarn-integrity> <yarn.lock>

const fs = require("fs");

const [integrityPath, lockPath] = process.argv.slice(2);
if (!integrityPath || !lockPath) {
  console.error("usage: restore-yarn-integrity.js <.yarn-integrity> <yarn.lock>");
  process.exit(2);
}

// yarn.lock v1: an entry starts at column 0 with its comma-separated patterns
// (quoted when they hold anything but [A-Za-z0-9._-]) and ends in a colon; its
// fields are indented by two spaces.
const resolved = new Map();
let patterns = [];
for (const line of fs.readFileSync(lockPath, "utf8").split("\n")) {
  if (line === "" || line.startsWith("#")) continue;
  if (!line.startsWith(" ")) {
    patterns = line
      .replace(/:\s*$/, "")
      .split(/,\s*/)
      .map((p) => p.replace(/^"(.*)"$/, "$1"));
    for (const p of patterns) resolved.set(p, "");
    continue;
  }
  const m = /^ {2}resolved\s+"?([^"]*)"?\s*$/.exec(line);
  if (m) for (const p of patterns) resolved.set(p, m[1]);
}

const integrity = JSON.parse(fs.readFileSync(integrityPath, "utf8"));
const entries = integrity.lockfileEntries || {};
const unknown = Object.keys(entries).filter((p) => !resolved.has(p));
if (unknown.length > 0) {
  console.error(`restore-yarn-integrity: ${lockPath} has no entry for: ${unknown.join(", ")}`);
  process.exit(1);
}
for (const p of Object.keys(entries)) entries[p] = resolved.get(p);

fs.writeFileSync(integrityPath, JSON.stringify(integrity, null, 2));
