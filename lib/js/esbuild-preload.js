// Loaded into frappe's esbuild/esbuild.js with `node --require`: by every
// `bench build` and `bench watch`, through frappe_nodebuild (lib/nodebuild),
// and by the dev shell's watch process (lib/bench-watch.py). It changes the
// build without changing frappe: esbuild.js is the framework's, and every
// frappe version ships its own copy.
//
// Two corrections apply to every build esbuild.js starts:
//
// nodePaths, frappe's first. esbuild.js resolves a bare import that an app's
// own node_modules cannot answer through nodePaths: every app's node_modules,
// in the order fs.readdirSync() lists apps/ rather than apps.txt's. An app
// that imports a package frappe provides (vue, pinia) without declaring it
// gets the copy from whichever app with its own sorts first, which can be one
// ahead of frappe. A package that only frappe ships still resolves its own
// imports from frappe's node_modules, so one bundle can carry two copies of
// the same library; for vue that is two reactivity systems, and state that
// changes without rendering. With frappe's first, an undeclared dependency is
// frappe's, which is what an app built on frappe expects; one an app declares
// still comes from its own node_modules, which esbuild searches first.
//
// Object rest and spread, lowered. Past es2017 (frappe-nix builds es2022,
// see esbuildTarget), `({ ...args }) => …` stays native, and a native
// rest-only pattern throws on undefined, where es2017's __objRest() helper
// returned {}. Code written against frappe's default can call such a function
// without the argument and never notice, so that syntax is lowered as es2017
// lowers it, and only that syntax: async generators and BigInt literals,
// which es2017 cannot lower, stay native. A build's own `supported` entries
// win.
//
// FRAPPE_NIX_SKIP_RTL=1: answer the right-to-left stylesheet build with an
// empty result instead of running it. esbuild.js builds every stylesheet a
// second time through rtlcss, into css-rtl/, which doubles the Sass work —
// the slowest part of a build — for a variant a left-to-right dev site never
// loads. An empty metafile leaves assets-rtl.json exactly as it was, and
// build-cleanup only deletes files beside the outputs a build produced, so
// the last `bench build`'s RTL files stay where they are.
//
// FRAPPE_NIX_SASS=<dir>: answer require("sass") with the sass-embedded module
// in <dir> (lib/sass-embedded.nix) — the same JS API, compiled by native Dart
// Sass instead of Dart Sass compiled to JavaScript, 3–6x faster on the
// stylesheets measured. frappe's postcss plugin calls the legacy render(),
// which sass-embedded announces as deprecated on every call; that notice is
// about the plugin, not the stylesheet, so it is silenced. Deprecations in the
// stylesheets themselves still print.
//
// esbuild's exports are getters, so the module cannot be patched in place.
// The require() from esbuild.js is answered with a copy whose `build` is
// wrapped, and every other caller of esbuild — yarn included, since
// NODE_OPTIONS reaches it too — gets the module untouched.
"use strict";

const Module = require("module");
const path = require("path");

const skipRtl = process.env.FRAPPE_NIX_SKIP_RTL === "1";
const sassDir = process.env.FRAPPE_NIX_SASS || "";

const isRtlBuild = (options) => {
  const outputs = Object.keys((options && options.entryPoints) || {});
  return outputs.length > 0 && outputs.every((name) => name.includes("/css-rtl/"));
};

const isFrappeNodeModules = (dir) =>
  path.basename(dir) === "node_modules" && path.basename(path.dirname(dir)) === "frappe";

const frappeFirst = (nodePaths) =>
  Array.isArray(nodePaths)
    ? [...nodePaths.filter(isFrappeNodeModules), ...nodePaths.filter((dir) => !isFrappeNodeModules(dir))]
    : nodePaths;

const corrected = (options) => ({
  ...options,
  nodePaths: frappeFirst(options.nodePaths),
  supported: { "object-rest-spread": false, ...options.supported },
});

let nativeSass;
const loadNativeSass = () => {
  if (!nativeSass) {
    const sass = require(sassDir);
    const quiet = (options) => ({
      ...options,
      silenceDeprecations: [...((options && options.silenceDeprecations) || []), "legacy-js-api"],
    });
    nativeSass = {
      ...sass,
      render: (options, callback) => sass.render(quiet(options), callback),
      renderSync: (options) => sass.renderSync(quiet(options)),
    };
  }
  return nativeSass;
};

const load = Module._load;
Module._load = function (request, parent, isMain) {
  if (sassDir && request === "sass") {
    return loadNativeSass();
  }
  const mod = load.apply(this, arguments);
  if (
    request !== "esbuild" ||
    !parent ||
    !parent.filename ||
    !parent.filename.endsWith("/esbuild/esbuild.js")
  ) {
    return mod;
  }
  return {
    ...mod,
    build(options) {
      if (skipRtl && isRtlBuild(options)) {
        return Promise.resolve({
          errors: [],
          warnings: [],
          metafile: { inputs: {}, outputs: {} },
        });
      }
      return mod.build(corrected(options));
    },
  };
};
