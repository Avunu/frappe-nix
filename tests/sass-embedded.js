// Checks for lib/sass-embedded.nix: the packaged module finds nixpkgs' native
// compiler and does what frappe's postcss plugin asks of `sass` — the legacy
// render() with includePaths, a JS importer (frappe's strips "~"), and
// stats.includedFiles, which esbuild's watcher rebuilds on.
//
// usage: node sass-embedded.js <path-to-node_modules/sass-embedded>
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

const sass = require(path.resolve(process.argv[2]));
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sass-embedded-"));
const write = (rel, text) => {
  fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
  fs.writeFileSync(path.join(root, rel), text);
};

write("vendor/theme/_colors.scss", "$brand: #336699;\n");
write("app/_partial.scss", ".partial { margin: 0; }\n");
write(
  "app/main.scss",
  `@import "~theme/colors";
@import "partial";
.button { color: $brand; .icon { width: 1rem; } }
`
);

let fails = 0;
const check = (desc, ok, detail) => {
  console.log(ok ? `  \x1b[32m✓\x1b[0m ${desc}` : `  \x1b[31m✗\x1b[0m ${desc}${detail ? `\n      ${detail}` : ""}`);
  if (!ok) fails += 1;
};

check("it reports itself as sass-embedded", /sass-embedded/.test(sass.info), sass.info);

sass.render(
  {
    file: path.join(root, "app/main.scss"),
    includePaths: [path.join(root, "vendor")],
    quietDeps: true,
    silenceDeprecations: ["legacy-js-api", "import"],
    // frappe's esbuild/sass_options.js importer, verbatim in effect.
    importer(url) {
      if (url.startsWith("~")) url = url.slice(1);
      return { file: url };
    },
  },
  (err, result) => {
    check("the native compiler renders through the legacy API", !err, err && err.message);
    if (!err) {
      const css = result.css.toString();
      check("a JS importer resolves a \"~\" import", css.includes("#336699") || css.includes("#369"), css);
      check("nesting and partials compile", css.includes(".button .icon") && css.includes(".partial"), css);
      const included = result.stats.includedFiles.map((f) => path.relative(root, f)).sort();
      check(
        "includedFiles names the partials, for the watcher",
        ["app/_partial.scss", "app/main.scss", "vendor/theme/_colors.scss"].every((f) => included.includes(f)),
        JSON.stringify(included)
      );
    }
    fs.rmSync(root, { recursive: true, force: true });
    console.log("");
    if (fails) {
      console.log(`${fails} check(s) failed.`);
      process.exit(1);
    }
    console.log("All sass-embedded checks passed.");
  }
);
