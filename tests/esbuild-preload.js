// Checks for lib/js/esbuild-preload.js: frappe's esbuild/esbuild.js gets
// frappe's node_modules first and object rest/spread lowered, the right-to-left
// stylesheet build is skipped when asked, a failed per-app build command is carried past
// when asked, each app's Vite bundles are registered after its `yarn build`
// (lib/js/vite-register.cjs, always), and nothing else about the module —
// or about any other caller's build — changes. A stand-in esbuild exports its API as getters, the
// way the real one does — which is why the preload cannot patch it in place.
//
// usage: node esbuild-preload.js <path-to-lib/js>/esbuild-preload.js
// (the directory's copy: the preload requires its neighbour vite-register.cjs)
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync, spawnSync } = require("child_process");

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
  calls.push({ outputs: Object.keys(options.entryPoints), nodePaths: options.nodePaths, supported: options.supported });
  return Promise.resolve({ errors: [], warnings: [], metafile: { inputs: {}, outputs: { "out.css": {} } } });
};
Object.defineProperty(exports, "build", { enumerable: true, get: () => build });
Object.defineProperty(exports, "calls", { enumerable: true, get: () => calls });
Object.defineProperty(exports, "version", { enumerable: true, get: () => "0.14.54" });
`
);

// esbuild.js's nodePaths: every app's node_modules in apps/ listing order, then
// every app's root. alpha_app lists ahead of frappe; custom_frappe ends in
// "frappe" without being it.
const NODE_PATHS = [
  "/bench/apps/alpha_app/node_modules",
  "/bench/apps/custom_frappe/node_modules",
  "/bench/apps/frappe/node_modules",
  "/bench/apps/zeta_app/node_modules",
  "/bench/apps/alpha_app",
  "/bench/apps/frappe",
];

const driver = `const esbuild = require("esbuild");
const nodePaths = ${JSON.stringify(NODE_PATHS)};
(async () => {
  const rtl = await esbuild.build({ entryPoints: { "app/dist/css-rtl/desk.bundle": "desk.bundle.scss" }, nodePaths });
  const ltr = await esbuild.build({ entryPoints: { "app/dist/css/desk.bundle": "desk.bundle.scss" }, nodePaths });
  const js = await esbuild.build({ entryPoints: { "app/dist/js/desk.bundle": "desk.bundle.js" }, nodePaths });
  const own = await esbuild.build({
    entryPoints: { "app/dist/js/own.bundle": "own.bundle.js" },
    nodePaths,
    supported: { "object-rest-spread": true, bigint: false },
  });
  console.log(JSON.stringify({
    rtlOutputs: Object.keys(rtl.metafile.outputs).length,
    ltrOutputs: Object.keys(ltr.metafile.outputs).length,
    jsOutputs: Object.keys(js.metafile.outputs).length,
    reachedEsbuild: esbuild.calls.length,
    jsBuild: esbuild.calls.find((call) => call.outputs[0] === "app/dist/js/desk.bundle"),
    ownBuild: esbuild.calls.find((call) => call.outputs[0] === "app/dist/js/own.bundle"),
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
check("and never reaches esbuild", 3, skipped.reachedEsbuild);
check("the LTR stylesheet and JS builds run as before", [1, 1], [skipped.ltrOutputs, skipped.jsOutputs]);
check("the rest of the module is passed through", "0.14.54", skipped.version);

const notAsked = run("esbuild/esbuild.js", false);
check("without FRAPPE_NIX_SKIP_RTL nothing is skipped", [1, 4], [notAsked.rtlOutputs, notAsked.reachedEsbuild]);
check(
  "frappe's node_modules leads nodePaths, the rest keep their order",
  [
    "/bench/apps/frappe/node_modules",
    "/bench/apps/alpha_app/node_modules",
    "/bench/apps/custom_frappe/node_modules",
    "/bench/apps/zeta_app/node_modules",
    "/bench/apps/alpha_app",
    "/bench/apps/frappe",
  ],
  notAsked.jsBuild.nodePaths
);
check("object rest/spread is lowered", { "object-rest-spread": false }, notAsked.jsBuild.supported);
check(
  "a build's own supported entries win",
  { "object-rest-spread": true, bigint: false },
  notAsked.ownBuild.supported
);

const elsewhere = run("scripts/other.js", true);
check("another caller of esbuild is left alone", [1, 4], [elsewhere.rtlOutputs, elsewhere.reachedEsbuild]);
check(
  "and its options reach esbuild untouched",
  [NODE_PATHS, undefined],
  [elsewhere.jsBuild.nodePaths, elsewhere.jsBuild.supported]
);

// FRAPPE_NIX_KEEP_GOING: esbuild.js's per-app loop runs each app's commands
// with execSync in that app's directory. Three apps, the middle one failing in
// its first command, each command leaving a line in apps/log.
const KEEP_APPS = ["a1", "b2", "c3"];
const keepDriver = `const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const apps = path.join(__dirname, "..", "apps");
for (const app of fs.readdirSync(apps).filter((name) => name !== "log").sort()) {
  process.chdir(path.join(apps, app));
  for (const command of fs.readFileSync("commands", "utf8").trim().split("\\n")) {
    execSync(command, { encoding: "utf8", stdio: "inherit" });
  }
}
console.log("DONE");
process.exit(0);
`;
write("keep/esbuild/esbuild.js", keepDriver);
write("keep/scripts/other.js", keepDriver);

const setApps = (b2First) => {
  fs.rmSync(path.join(root, "keep/apps"), { recursive: true, force: true });
  for (const app of KEEP_APPS) {
    const first = app === "b2" ? b2First : `echo ${app}-install >> ../log`;
    write(`keep/apps/${app}/commands`, `${first}\necho ${app}-build >> ../log\n`);
  }
  fs.writeFileSync(path.join(root, "keep/apps/log"), "");
};
const keepRun = (script, keep, b2First) => {
  setApps(b2First);
  const result = spawnSync(process.execPath, ["--require", preload, script], {
    cwd: root,
    env: { ...process.env, FRAPPE_NIX_KEEP_GOING: keep ? "1" : "" },
    encoding: "utf8",
  });
  const log = fs.readFileSync(path.join(root, "keep/apps/log"), "utf8").trim().split("\n").filter(Boolean);
  return { status: result.status, done: result.stdout.includes("DONE"), log, stderr: result.stderr };
};
const FAIL_B2 = "echo b2-install >> ../log; exit 7";

const carried = keepRun("keep/esbuild/esbuild.js", true, FAIL_B2);
check(
  "with FRAPPE_NIX_KEEP_GOING, the apps after a failed one are still built",
  ["a1-install", "a1-build", "b2-install", "c3-install", "c3-build"],
  carried.log
);
check("the failed app's remaining commands are skipped, not run against its failed install", false, carried.log.includes("b2-build"));
check("the driver reaches the end, and the process still exits 1", [true, 1], [carried.done, carried.status]);
check(
  "the failure is named where it happens, and again in the summary",
  [true, true, true],
  [
    carried.stderr.includes(`✗ b2: \`${FAIL_B2}\` exited 7`),
    carried.stderr.includes("1 app build(s) failed: b2"),
    carried.stderr.includes("bench build --app <name>"),
  ]
);

const stock = keepRun("keep/esbuild/esbuild.js", false, FAIL_B2);
check("without it, the first failure ends the loop, as in frappe", [["a1-install", "a1-build", "b2-install"], false, true], [stock.log, stock.done, stock.status !== 0]);

const other = keepRun("keep/scripts/other.js", true, FAIL_B2);
check("another script's execSync is left alone", [["a1-install", "a1-build", "b2-install"], false, true], [other.log, other.done, other.status !== 0]);

const clean = keepRun("keep/esbuild/esbuild.js", true, "echo b2-install >> ../log");
check("when nothing fails there is nothing to report, and the exit is 0", [true, 0, ""], [clean.done, clean.status, clean.stderr]);
check("and every command ran", 6, clean.log.length);

const killed = keepRun("keep/esbuild/esbuild.js", true, "echo b2-install >> ../log; kill -9 $$");
check(
  "a command ended by a signal still ends the build",
  [["a1-install", "a1-build", "b2-install"], false, true],
  [killed.log, killed.done, killed.status !== 0]
);

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


// Vite registration (spec S30, §5.11). A bench whose esbuild.js stand-in runs
// each app's command in apps/<app>, as frappe's run_build_command_for_apps()
// does, with a stub yarn whose `build` runs the app's build.sh.
const viteRoot = path.join(root, "vite");
const vwrite = (rel, text) => {
  fs.mkdirSync(path.dirname(path.join(viteRoot, rel)), { recursive: true });
  fs.writeFileSync(path.join(viteRoot, rel), text);
};
vwrite(
  "bin/yarn",
  `#!/bin/sh
[ "$1" = run ] && shift
case "$1" in
  build) exec sh ./build.sh ;;
  *) echo "yarn $*" ;;
esac
`
);
fs.chmodSync(path.join(viteRoot, "bin/yarn"), 0o755);
const viteDriver = `const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const bench = process.env.FRAPPE_BENCH_ROOT || path.resolve(__dirname, "..", "..", "..");
for (const app of JSON.parse(process.env.VITE_APPS)) {
  process.chdir(path.join(bench, "apps", app));
  execSync(process.env.VITE_COMMAND || "yarn build", { encoding: "utf8", stdio: "inherit" });
}
console.log("DONE");
process.exit(0);
`;
const manifest = (entries) => JSON.stringify(entries, null, 2);
const ESBUILD_KEYS = {
  "desk.bundle.js": "/assets/frappe/dist/js/desk.bundle.FRAPPE1.js",
  "foo.bundle.js": "/assets/spa/dist/js/foo.bundle.ESBLD1.js",
};
const RTL = JSON.stringify({ "rtl_desk.bundle.css": "/assets/frappe/dist/css-rtl/desk.bundle.RTL111.css" }, null, 4);
const makeBench = (name) => {
  const b = `${name}`;
  vwrite(`${b}/apps/frappe/esbuild/esbuild.js`, viteDriver);
  vwrite(`${b}/apps/frappe/build.sh`, "true\n");
  vwrite(
    `${b}/apps/frappe/frappe/public/dist/.vite/manifest.json`,
    manifest({ "x.ts": { file: "js/frappe_vite.bundle.FRAPPE2.js", isEntry: true } })
  );
  vwrite(`${b}/apps/frappe/frappe/public/dist/js/frappe_vite.bundle.FRAPPE2.js`, "frappe();\n");
  vwrite(`${b}/apps/frappe/scripts/other.js`, viteDriver);
  // spa: Vite 5's .vite/manifest.json (an entry, its stylesheet, a chunk that is
  // no bundle), Vite 4's manifest.json in a nested outDir, and a manifest that
  // does not parse.
  vwrite(`${b}/apps/spa/build.sh`, "echo spa-built\n");
  vwrite(
    `${b}/apps/spa/spa/public/dist/.vite/manifest.json`,
    manifest({
      "src/foo.entry.ts": {
        file: "js/foo.bundle.AbC123.js",
        isEntry: true,
        imports: ["_index-AbC.js"],
        css: ["css/foo.bundle.XyZ789.css"],
      },
      "_index-AbC.js": { file: "js/index-AbC.js" },
      "src/bar.entry.ts": { file: "js/index-AbC.js", isEntry: true },
      "src/lazy.ts": { file: "js/lazy.bundle.LaZy12.js", isDynamicEntry: true },
    })
  );
  vwrite(`${b}/apps/spa/spa/public/dist/js/foo.bundle.AbC123.js`, "console.log('foo');\n");
  vwrite(`${b}/apps/spa/spa/public/dist/css/foo.bundle.XyZ789.css`, ".foo{}\n");
  vwrite(`${b}/apps/spa/spa/public/dist/js/index-AbC.js`, "export {};\n");
  vwrite(
    `${b}/apps/spa/spa/public/dist/legacy/manifest.json`,
    manifest({ "old.ts": { file: "js/old.bundle.Qwerty1.js", isEntry: true } })
  );
  vwrite(`${b}/apps/spa/spa/public/dist/legacy/js/old.bundle.Qwerty1.js`, "old();\n");
  vwrite(`${b}/apps/spa/spa/public/dist/broken/manifest.json`, "{");
  vwrite(`${b}/apps/spa/spa/public/portal/.vite/manifest.json`, manifest({ "index.html": { file: "assets/index-PoRt12.js", isEntry: true } }));
  vwrite(`${b}/apps/spa/spa/public/portal/assets/index-PoRt12.js`, "portal();\n");
  // kiosk: a second SPA built by Vite 4, its manifest.json at the outDir's top.
  vwrite(`${b}/apps/spa/spa/public/kiosk/manifest.json`, manifest({ "index.html": { file: "assets/index-KiOsK1.js", isEntry: true } }));
  vwrite(`${b}/apps/spa/spa/public/kiosk/assets/index-KiOsK1.js`, "kiosk();\n");
  vwrite(`${b}/apps/spa/spa/public/images/logo.svg`, "<svg/>\n");
  // zfail: its build fails.
  vwrite(`${b}/apps/zfail/build.sh`, "exit 3\n");
  vwrite(
    `${b}/apps/zfail/zfail/public/dist/.vite/manifest.json`,
    manifest({ "z.ts": { file: "js/z.bundle.ZzZzZz.js", isEntry: true } })
  );
  vwrite(`${b}/apps/zfail/zfail/public/dist/js/z.bundle.ZzZzZz.js`, "z();\n");
  vwrite(`${b}/sites/apps.txt`, "frappe\nspa\nlinked\nzfail\n");
  vwrite(`${b}/sites/assets/assets.json`, JSON.stringify(ESBUILD_KEYS, null, 4));
  vwrite(`${b}/sites/assets/assets-rtl.json`, RTL);
  // linked: apps/linked is a link to a checkout elsewhere, as in the dev shell.
  vwrite(`${b}-src/linked/build.sh`, "true\n");
  vwrite(
    `${b}-src/linked/linked/public/dist/.vite/manifest.json`,
    manifest({ "l.ts": { file: "js/linked.bundle.LnK123.js", isEntry: true } })
  );
  vwrite(`${b}-src/linked/linked/public/dist/js/linked.bundle.LnK123.js`, "linked();\n");
  fs.symlinkSync(path.join(viteRoot, `${b}-src/linked`), path.join(viteRoot, `${b}/apps/linked`));
  return path.join(viteRoot, b);
};

const viteRun = (bench, { apps, keep = false, command = "", script = "apps/frappe/esbuild/esbuild.js", env = {} }) => {
  const result = spawnSync(process.execPath, ["--require", preload, path.join(bench, script)], {
    cwd: bench,
    env: {
      ...process.env,
      PATH: `${path.join(viteRoot, "bin")}:${process.env.PATH}`,
      FRAPPE_BENCH_ROOT: "",
      FRAPPE_NIX_KEEP_GOING: keep ? "1" : "",
      VITE_APPS: JSON.stringify(apps),
      VITE_COMMAND: command,
      ...env,
    },
    encoding: "utf8",
  });
  return { ...result, out: `${result.stdout}${result.stderr}` };
};
const assetsOf = (bench) => fs.readFileSync(path.join(bench, "sites/assets/assets.json"), "utf8");
const keysOf = (bench) => JSON.parse(assetsOf(bench));

const vb = makeBench("bench");
const first = viteRun(vb, { apps: ["frappe", "spa", "linked"] });
check("the build runs to the end without FRAPPE_NIX_KEEP_GOING", [0, true], [first.status, first.stdout.includes("DONE")]);
const expected = {
  "desk.bundle.js": "/assets/frappe/dist/js/desk.bundle.FRAPPE1.js",
  "foo.bundle.js": "/assets/spa/dist/js/foo.bundle.AbC123.js",
  "foo.bundle.css": "/assets/spa/dist/css/foo.bundle.XyZ789.css",
  "old.bundle.js": "/assets/spa/dist/legacy/js/old.bundle.Qwerty1.js",
  "linked.bundle.js": "/assets/linked/dist/js/linked.bundle.LnK123.js",
};
check(
  "each app's Vite entries and their stylesheets are registered under /assets/<app>/dist/, by their bundle names",
  expected,
  keysOf(vb)
);
check("frappe's own keys are untouched, and frappe's own build registers nothing", [ESBUILD_KEYS["desk.bundle.js"], undefined], [keysOf(vb)["desk.bundle.js"], keysOf(vb)["frappe_vite.bundle.js"]]);
check("a chunk that is no <name>.bundle.<hash> (index-AbC.js), and a dynamic import, are ignored", [false, false], ["index.js" in keysOf(vb), "lazy.bundle.js" in keysOf(vb)]);
check(
  "a Vite key replacing esbuild's is logged",
  true,
  first.stdout.includes("vite-register: spa: foo.bundle.js -> /assets/spa/dist/js/foo.bundle.AbC123.js (vite)")
);
check("a manifest that does not parse is skipped with a note, the others still count", true, first.stdout.includes("vite-register: skipping") && first.stdout.includes("broken/manifest.json"));
check("assets.json keeps frappe's format: four spaces, no final newline", JSON.stringify(expected, null, 4), assetsOf(vb));
check("assets-rtl.json is left alone", RTL, fs.readFileSync(path.join(vb, "sites/assets/assets-rtl.json"), "utf8"));
check("a linked app (the dev shell's) registers under its apps/ name", expected["linked.bundle.js"], keysOf(vb)["linked.bundle.js"]);

const before = assetsOf(vb);
const mtime = fs.statSync(path.join(vb, "sites/assets/assets.json")).mtimeMs;
const second = viteRun(vb, { apps: ["frappe", "spa", "linked"] });
check(
  "a second build changes no byte, rewrites nothing and logs no key",
  [before, mtime, false],
  [assetsOf(vb), fs.statSync(path.join(vb, "sites/assets/assets.json")).mtimeMs, second.stdout.includes("(vite)")]
);
check("a symlinked sites/assets/<app> (bench build's default) gets no copy", false, fs.existsSync(path.join(vb, "sites/assets/spa")));

const runVariant = viteRun(makeBench("bench-run"), { apps: ["spa"], command: "yarn run build" });
check("`yarn run build` counts as the build", true, runVariant.stdout.includes("foo.bundle.js -> "));
const notBuild = makeBench("bench-install");
viteRun(notBuild, { apps: ["spa"], command: "yarn install --frozen-lockfile" });
check("any other command registers nothing", JSON.stringify(ESBUILD_KEYS, null, 4), assetsOf(notBuild));
const otherScript = makeBench("bench-other");
viteRun(otherScript, { apps: ["spa"], script: "apps/frappe/scripts/other.js" });
check("a script other than esbuild.js registers nothing", JSON.stringify(ESBUILD_KEYS, null, 4), assetsOf(otherScript));

const keepBench = makeBench("bench-keep");
const kept = viteRun(keepBench, { apps: ["frappe", "zfail", "spa"], keep: true });
check(
  "with FRAPPE_NIX_KEEP_GOING, a failed build registers nothing, the next app still does, and the exit is 1",
  [undefined, expected["foo.bundle.js"], 1],
  [keysOf(keepBench)["z.bundle.js"], keysOf(keepBench)["foo.bundle.js"], kept.status]
);
const stockFail = makeBench("bench-stockfail");
const stopped = viteRun(stockFail, { apps: ["zfail", "spa"] });
check("without it, a failed build still ends the run, unregistered", [true, undefined], [stopped.status !== 0, keysOf(stockFail)["foo.bundle.js"] === expected["foo.bundle.js"] ? "registered" : undefined]);

// FRAPPE_BENCH_ROOT names the bench, as it does for esbuild.js itself.
const home = makeBench("bench-home");
const elsewhereBench = makeBench("bench-elsewhere");
viteRun(home, { apps: ["spa"], env: { FRAPPE_BENCH_ROOT: elsewhereBench } });
check(
  "with FRAPPE_BENCH_ROOT, the registration lands in that bench's sites/",
  [JSON.stringify(ESBUILD_KEYS, null, 4), expected["foo.bundle.js"]],
  [assetsOf(home), keysOf(elsewhereBench)["foo.bundle.js"]]
);

// `bench build --hard-link`: sites/assets/<app> is a real directory, filled
// before the app's own build ran.
const hard = makeBench("bench-hard");
const hardAssets = path.join(hard, "sites/assets/spa");
fs.mkdirSync(path.join(hardAssets, "dist/js"), { recursive: true });
fs.linkSync(path.join(hard, "apps/spa/spa/public/dist/js/index-AbC.js"), path.join(hardAssets, "dist/js/index-AbC.js"));
viteRun(hard, { apps: ["spa"] });
check(
  "with a real sites/assets/<app>, public/dist and each Vite outDir with a manifest (portal/'s .vite/, kiosk/'s top-level manifest.json) are copied in",
  [true, true, true, true, false],
  [
    fs.existsSync(path.join(hardAssets, "dist/js/foo.bundle.AbC123.js")),
    fs.existsSync(path.join(hardAssets, "dist/.vite/manifest.json")),
    fs.existsSync(path.join(hardAssets, "portal/assets/index-PoRt12.js")),
    fs.existsSync(path.join(hardAssets, "kiosk/assets/index-KiOsK1.js")),
    fs.existsSync(path.join(hardAssets, "images")),
  ]
);
check(
  "a file already hard-linked there is left whole",
  "export {};\n",
  fs.readFileSync(path.join(hard, "apps/spa/spa/public/dist/js/index-AbC.js"), "utf8")
);

const broken = makeBench("bench-broken");
fs.writeFileSync(path.join(broken, "sites/assets/assets.json"), "{ not json");
const brokenRun = viteRun(broken, { apps: ["spa", "linked"] });
check(
  "an assets.json that does not parse is a warning naming the app, the build still succeeds, and the file is left as it was",
  [0, true, "{ not json"],
  [brokenRun.status, brokenRun.stderr.includes("vite-register: spa:"), fs.readFileSync(path.join(broken, "sites/assets/assets.json"), "utf8")]
);

// A manifest outlives its files: public/dist survives between builds, and
// frappe's build cleanup deletes an app's old dist/js/<name>.bundle.* but never
// the Vite manifest that named one. Such an entry registers nothing.
const stale = makeBench("bench-stale");
const staleDist = path.join(stale, "apps/spa/spa/public/dist");
fs.writeFileSync(path.join(staleDist, "js/foo.bundle.ESBLD1.js"), "esbuild();\n");
fs.rmSync(path.join(staleDist, "js/foo.bundle.AbC123.js"));
const staleRun = viteRun(stale, { apps: ["spa"] });
check(
  "an entry whose file is gone keeps esbuild's key, is logged as skipped, and its stylesheet that exists still registers",
  [0, ESBUILD_KEYS["foo.bundle.js"], true, expected["foo.bundle.css"]],
  [
    staleRun.status,
    keysOf(stale)["foo.bundle.js"],
    staleRun.stdout.includes("vite-register: spa: skipping js/foo.bundle.AbC123.js: not on disk"),
    keysOf(stale)["foo.bundle.css"],
  ]
);

// A Vite file still on disk but older than the one esbuild just built under
// the same name (a Vite outDir that frappe's cleanup does not reach) is stale
// too; a Vite file newer than esbuild's wins, as a fresh `yarn build` is.
const aged = (name, viteTime, esbuildTime) => {
  const bench = makeBench(name);
  const dist = path.join(bench, "apps/spa/spa/public/dist");
  fs.symlinkSync(path.join(bench, "apps/spa/spa/public"), path.join(bench, "sites/assets/spa"));
  fs.writeFileSync(path.join(dist, "js/foo.bundle.ESBLD1.js"), "esbuild();\n");
  fs.utimesSync(path.join(dist, "js/foo.bundle.AbC123.js"), viteTime, viteTime);
  fs.utimesSync(path.join(dist, "js/foo.bundle.ESBLD1.js"), esbuildTime, esbuildTime);
  return [bench, viteRun(bench, { apps: ["spa"] })];
};
const [older, olderRun] = aged("bench-older", new Date("2020-01-01"), new Date("2021-01-01"));
check(
  "a Vite file older than esbuild's file under the same key leaves esbuild's key, and says so",
  [ESBUILD_KEYS["foo.bundle.js"], true],
  [keysOf(older)["foo.bundle.js"], olderRun.stdout.includes("vite-register: spa: foo.bundle.js kept: /assets/spa/dist/js/foo.bundle.AbC123.js is older")]
);
const [newer] = aged("bench-newer", new Date("2021-01-01"), new Date("2020-01-01"));
check("a Vite file newer than esbuild's takes the key", expected["foo.bundle.js"], keysOf(newer)["foo.bundle.js"]);

// lib/js/vite-register.cjs's own entry points.
const vr = require(path.join(path.dirname(preload), "vite-register.cjs"));
check(
  "findSites: $FRAPPE_BENCH_ROOT first, then the logical ../../sites, then the physical one; null outside a bench",
  [path.join(vb, "sites"), path.join(vb, "sites"), path.join(vb, "sites"), null],
  [
    vr.findSites({ env: { FRAPPE_BENCH_ROOT: vb }, cwd: "/" }),
    vr.findSites({ env: { PWD: path.join(vb, "apps/linked") }, cwd: path.join(viteRoot, "bench-src/linked") }),
    vr.findSites({ env: {}, cwd: path.join(vb, "apps/spa") }),
    vr.findSites({ env: {}, cwd: path.join(viteRoot, "bench-src/linked") }),
  ]
);
check(
  "the bundle pattern wants a hash of six or more characters",
  [true, true, false, false],
  ["a.bundle.AbC123.js", "a.bundle.A_b-C9xY.css", "a.bundle.min.js", "a.bundle.AbC123.map"].map((name) => vr.BUNDLE.test(name))
);

fs.rmSync(root, { recursive: true, force: true });
console.log("");
if (fails) {
  console.log(`${fails} check(s) failed.`);
  process.exit(1);
}
console.log("All esbuild-preload checks passed.");
