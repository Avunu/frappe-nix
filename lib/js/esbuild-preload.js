// Loaded into frappe's esbuild/esbuild.js with `node --require` by the dev
// shell's watch process (lib/bench-watch.py). It changes the build without
// changing frappe: esbuild.js is the framework's, and every frappe version
// ships its own copy.
//
// FRAPPE_NIX_SKIP_RTL=1: answer the right-to-left stylesheet build with an
// empty result instead of running it. esbuild.js builds every stylesheet a
// second time through rtlcss, into css-rtl/, which doubles the Sass work —
// the slowest part of a build — for a variant a left-to-right dev site never
// loads. An empty metafile leaves assets-rtl.json exactly as it was, and
// build-cleanup only deletes files beside the outputs a build produced, so
// the last `bench build`'s RTL files stay where they are.
//
// esbuild's exports are getters, so the module cannot be patched in place.
// The require() from esbuild.js is answered with a copy whose `build` is
// wrapped, and every other caller of esbuild — yarn included, since
// NODE_OPTIONS reaches it too — gets the module untouched.
"use strict";

const Module = require("module");

const skipRtl = process.env.FRAPPE_NIX_SKIP_RTL === "1";

const isRtlBuild = (options) => {
  const outputs = Object.keys((options && options.entryPoints) || {});
  return outputs.length > 0 && outputs.every((name) => name.includes("/css-rtl/"));
};

const load = Module._load;
Module._load = function (request, parent, isMain) {
  const mod = load.apply(this, arguments);
  if (
    !skipRtl ||
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
      if (isRtlBuild(options)) {
        return Promise.resolve({
          errors: [],
          warnings: [],
          metafile: { inputs: {}, outputs: {} },
        });
      }
      return mod.build(options);
    },
  };
};
