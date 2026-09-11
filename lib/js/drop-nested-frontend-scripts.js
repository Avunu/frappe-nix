// Remove the package.json scripts that drive an excluded nested frontend.
//
// A frontend named in `nodeNestedFrontendExcludes` is never installed, so its
// node_modules -- and every binary in it -- is absent from the bench. That is
// fine for the frontend's own assets, which is all the exclusion is meant to
// cost. It is not fine for a parent app whose own `build` script delegates to
// it: frappe's esbuild runs `yarn build` in every app that declares one
// (`run_build_command_for_apps`, --run-build-command), with no opt-out, so the
// missing binary fails the whole `bench build` rather than just that frontend.
//
// erpnext v16 is exactly this shape -- its entire build script is
// `cd banking && yarn build` -- and without this it dies as
//
//   Running build command for erpnext
//   $ cd banking && yarn build
//   /bin/sh: vite: not found
//   error Command failed with exit code 127.
//
// Usage: node drop-nested-frontend-scripts.js <package.json> <subdir>...

const fs = require("fs");

const [pkgPath, ...subs] = process.argv.slice(2);

if (!fs.existsSync(pkgPath)) {
  process.exit(0);
}

const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
const scripts = pkg.scripts || {};

// Word-split rather than substring-match, so an excluded "ui" matches
// `cd ui && ...` but not `--outDir ui-dist`.
const mentions = (cmd) => {
  const words = new Set(cmd.split(/[\s/'"&;|=]+/));
  return subs.find((sub) => words.has(sub));
};

// Only the two lifecycle scripts anything here runs: `build` via frappe's
// esbuild, `postinstall` via a yarn install that is not passed --ignore-scripts.
const dropped = [];
for (const name of ["build", "postinstall"]) {
  const cmd = scripts[name];
  if (typeof cmd !== "string") continue;

  const sub = mentions(cmd);
  if (!sub) continue;

  delete scripts[name];
  dropped.push({ name, sub, cmd });
}

if (dropped.length === 0) {
  process.exit(0);
}

pkg.scripts = scripts;
fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n");

for (const d of dropped) {
  console.error(
    "dropped " + pkgPath + " script " + JSON.stringify(d.name) + " (" +
      JSON.stringify(d.cmd) + "): it drives the excluded nested frontend " +
      JSON.stringify(d.sub) + ", which is not installed. Anything else that " +
      "script did is dropped with it."
  );
}
