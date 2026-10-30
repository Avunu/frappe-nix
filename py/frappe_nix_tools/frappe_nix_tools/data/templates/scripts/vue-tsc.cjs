{# The managed scripts/vue-tsc.cjs (docs/app-standards/assets.md, spec §2.8): vue-tsc on the
   typescript5 alias. Live with module typescript when the app's own package builds Vue
   single-file components (vue_typescript5). No indentation, so it is oxfmt's output under
   js.format-tabs either way. #}
// Runs vue-tsc on TypeScript 5, the `typescript5` alias in devDependencies:
// `node scripts/vue-tsc.cjs --noEmit -p tsconfig.json`, an SPA's `check`.
// The app's `typescript` is the native TypeScript 7 build, which ships no
// JS API, and vue-tsc can only drive the JS compiler.
// See https://github.com/Avunu/frappe-nix/tree/main/docs/app-standards/assets.md
const process = require("node:process");

process.argv = [process.argv[0], "vue-tsc", ...process.argv.slice(2)];
require("vue-tsc").run(require.resolve("typescript5/lib/tsc"));
