# SPA App

A fixture for frappe-nix's asset tests (`docs/app-standards/assets.md`): a desk
bundle built by the root `vite.config.ts`, and a portal SPA in `portal/` with
no `package.json` of its own.

`build.mjs` stands in for `vite build`, so the tests need no network: it writes
what Vite would, hashed bundles and `.vite/manifest.json`, under
`spa_app/public/dist/` and `spa_app/public/portal/`. The `build` script then
runs the managed `scripts/vite-register.mjs`, which registers `spa.bundle.js`
and `spa.bundle.css` in the bench's `sites/assets/assets.json`.
