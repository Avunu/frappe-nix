// Checks for lib/js/esbuild-preload.js: the right-to-left stylesheet build is
// skipped when asked, only for frappe's esbuild/esbuild.js, and nothing else
// about the module changes. A stand-in esbuild exports its API as getters, the
// way the real one does — which is why the preload cannot patch it in place.
//
// usage: node esbuild-preload.js <path-to-lib/js/esbuild-preload.js>
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const preload = path.resolve(process.argv[2]);
const root = fs.mkdtempSync(path.join(os.tmpdir(), "esbuild-preload-"));

const write = (rel, text) => {
  fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
  fs.writeFileSync(path.join(root, rel), text);
};

write(
  "node_modules/esbuild/index.js",
  `const calls = [];
const build = (options) => {
  calls.push(Object.keys(options.entryPoints));
  return Promise.resolve({ errors: [], warnings: [], metafile: { inputs: {}, outputs: { "out.css": {} } } });
};
Object.defineProperty(exports, "build", { enumerable: true, get: () => build });
Object.defineProperty(exports, "calls", { enumerable: true, get: () => calls });
Object.defineProperty(exports, "version", { enumerable: true, get: () => "0.14.54" });
`
);

const driver = `const esbuild = require("esbuild");
(async () => {
  const rtl = await esbuild.build({ entryPoints: { "app/dist/css-rtl/desk.bundle": "desk.bundle.scss" } });
  const ltr = await esbuild.build({ entryPoints: { "app/dist/css/desk.bundle": "desk.bundle.scss" } });
  const js = await esbuild.build({ entryPoints: { "app/dist/js/desk.bundle": "desk.bundle.js" } });
  console.log(JSON.stringify({
    rtlOutputs: Object.keys(rtl.metafile.outputs).length,
    ltrOutputs: Object.keys(ltr.metafile.outputs).length,
    jsOutputs: Object.keys(js.metafile.outputs).length,
    reachedEsbuild: esbuild.calls.length,
    version: esbuild.version,
  }));
})();
`;
write("esbuild/esbuild.js", driver);
write("scripts/other.js", driver);

const run = (script, skip) =>
  JSON.parse(
    execFileSync(process.execPath, ["--require", preload, script], {
      cwd: root,
      env: { ...process.env, FRAPPE_NIX_SKIP_RTL: skip ? "1" : "" },
      encoding: "utf8",
    })
  );

let fails = 0;
const check = (desc, expected, got) => {
  if (JSON.stringify(expected) === JSON.stringify(got)) {
    console.log(`  \x1b[32m✓\x1b[0m ${desc}`);
  } else {
    console.log(`  \x1b[31m✗\x1b[0m ${desc}\n      expected: ${JSON.stringify(expected)}\n      got:      ${JSON.stringify(got)}`);
    fails += 1;
  }
};

const skipped = run("esbuild/esbuild.js", true);
check("the RTL build answers with no outputs", 0, skipped.rtlOutputs);
check("and never reaches esbuild", 2, skipped.reachedEsbuild);
check("the LTR stylesheet and JS builds run as before", [1, 1], [skipped.ltrOutputs, skipped.jsOutputs]);
check("the rest of the module is passed through", "0.14.54", skipped.version);

const notAsked = run("esbuild/esbuild.js", false);
check("without FRAPPE_NIX_SKIP_RTL nothing is skipped", [1, 3], [notAsked.rtlOutputs, notAsked.reachedEsbuild]);

const elsewhere = run("scripts/other.js", true);
check("another caller of esbuild is left alone", [1, 3], [elsewhere.rtlOutputs, elsewhere.reachedEsbuild]);

// require("sass"): frappe's JS build of Sass, or the native one named by
// FRAPPE_NIX_SASS. Both stand-ins echo which one ran and the options it got.
const sassStub = (name) => `exports.info = ${JSON.stringify(name)};
exports.render = (options, done) => done(null, { impl: ${JSON.stringify(name)}, options });
exports.renderSync = (options) => ({ impl: ${JSON.stringify(name)}, options });
`;
write("node_modules/sass/index.js", sassStub("js"));
write("native/sass-embedded/index.js", sassStub("native"));
write(
  "node_modules/@frappe/esbuild-plugin-postcss2/dist/index.js",
  `const sass = require("sass");
sass.render({ file: "x.scss", silenceDeprecations: ["import"] }, (err, res) => {
  const sync = sass.renderSync({ file: "x.scss" });
  console.log(JSON.stringify({
    info: sass.info,
    impl: res.impl,
    silenced: res.options.silenceDeprecations,
    syncImpl: sync.impl,
    syncSilenced: sync.options.silenceDeprecations,
  }));
});
`
);

const plugin = (sass) =>
  JSON.parse(
    execFileSync(process.execPath, ["--require", preload, "node_modules/@frappe/esbuild-plugin-postcss2/dist/index.js"], {
      cwd: root,
      env: { ...process.env, FRAPPE_NIX_SASS: sass },
      encoding: "utf8",
    })
  );

const native = plugin(path.join(root, "native/sass-embedded"));
check("with FRAPPE_NIX_SASS, require(\"sass\") gets the native module", ["native", "native", "native"], [native.info, native.impl, native.syncImpl]);
check(
  "render() and renderSync() have the legacy-API notice silenced, keeping the caller's own",
  [["import", "legacy-js-api"], ["legacy-js-api"]],
  [native.silenced, native.syncSilenced]
);
const js = plugin("");
check("without it, frappe's own sass is untouched", ["js", "js", ["import"]], [js.info, js.impl, js.silenced]);

fs.rmSync(root, { recursive: true, force: true });
console.log("");
if (fails) {
  console.log(`${fails} check(s) failed.`);
  process.exit(1);
}
console.log("All esbuild-preload checks passed.");
