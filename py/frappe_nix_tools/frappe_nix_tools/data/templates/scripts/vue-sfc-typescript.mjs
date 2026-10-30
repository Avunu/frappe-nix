{# The managed scripts/vue-sfc-typescript.mjs (docs/app-standards/assets.md, spec §2.8): Vue's
   SFC compiler on the typescript5 alias, for a Vite config to import. Live with module
   typescript when the app's own package builds Vue single-file components with Vite
   (vue_typescript5 and discover.vite). No indentation, so it is oxfmt's output under
   js.format-tabs either way. #}
// Points Vue's SFC compiler at TypeScript 5, the `typescript5` alias in
// devDependencies. The compiler reads the types `defineProps<T>()` and
// `defineEmits<T>()` import from other files with TypeScript's JS API, which
// the app's native TypeScript 7 build does not ship, so without this
// @vitejs/plugin-vue fails on them. The Vite config imports it first:
//
//   import "./scripts/vue-sfc-typescript.mjs";
//
// See https://github.com/Avunu/frappe-nix/tree/main/docs/app-standards/assets.md
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

// The CommonJS build, which @vitejs/plugin-vue loads too: its own default
// (`typescript`) is registered on load, so this one is the last.
require("vue/compiler-sfc").registerTS(() => require("typescript5"));
