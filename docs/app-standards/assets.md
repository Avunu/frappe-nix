---
title: App assets
description: "How an app's Vite outputs are registered on every bench, and how frontends are named and built."
order: 4
tags: [app-standards, assets, vite, bundles]
updated: 2026-10-07
---

Frappe serves an app's scripts and stylesheets by name. `app_include_js = ["timer.bundle.js"]` in `hooks.py`, and `{{ bundled_asset('timer.bundle.js') }}` in a `www` template, are looked up in the bench's `sites/assets/assets.json`, which maps each name to the hashed file a build wrote: `/assets/my_app/dist/js/timer.bundle.4KQ2LZ7M.js`. Frappe's own esbuild pipeline writes that file from what esbuild built. It knows nothing about a bundle the app builds with Vite, so on a stock bench such a bundle has no entry and its page asks for `/assets/my_app/dist/js/timer.bundle.js`, which is a 404.

frappe-nix registers Vite bundles in `assets.json` with one piece of code, [`lib/js/vite-register.cjs`](../../lib/js/vite-register.cjs), called from two places:

- **The app's own build.** An app that opted in to the app standards, with a Vite config and the `vite-register` module on (it is on in `recommended`), gets the managed file `scripts/vite-register.mjs`, and its `build` script ends with `node scripts/vite-register.mjs`. Frappe's esbuild writes `assets.json` and then runs every app's `yarn build` in `apps/<app>`, so the keys land after its own on any bench: a stock `bench init` bench, Frappe Cloud, a registry install.
- **frappe-nix's esbuild preload.** On a bench frappe-nix builds (the dev shell's `bench build`, `builtBench`, the images and the NixOS module), the preload runs the same registration after each app's `yarn build`. This applies to every app in app mode, whether or not it opted in: it only adds the keys of hashed Vite bundles that had none, and it never touches a file in the app.

Running both changes nothing the second time. A key is written only when its value differs, `assets.json` is rewritten (to a temporary file, then renamed) only when a key changed, and `assets-rtl.json` is left alone.

## What gets registered

The registration reads every Vite manifest under `<app>/public/dist/`: `manifest*.json` and `.vite/manifest*.json`, at any depth. For each entry with `isEntry: true` it takes the entry's `file` and each of its `css` files, and registers each one whose name has the shape `<name>.bundle.<hash>.js` or `.css` (a hash of six or more of `A-Z a-z 0-9 _ -`):

```text
public/dist/js/timer.bundle.4KQ2LZ7M.js    →  "timer.bundle.js":  "/assets/my_app/dist/js/timer.bundle.4KQ2LZ7M.js"
public/dist/css/timer.bundle.Hc9Qx1Zd.css  →  "timer.bundle.css": "/assets/my_app/dist/css/timer.bundle.Hc9Qx1Zd.css"
public/dist/js/index-B2xK9aQe.js            (a shared chunk: not an entry's name, not registered)
```

These are the names frappe's own `bench build --using-cached` gives the same files, so a bench that installs prebuilt assets resolves them identically. A Vite key replaces an esbuild key of the same name, and the build log says so:

```text
vite-register: my_app: timer.bundle.js -> /assets/my_app/dist/js/timer.bundle.4KQ2LZ7M.js (vite)
```

`public/dist/` survives between builds, and frappe's build cleanup deletes an old `dist/js/<name>.bundle.*` but never the Vite manifest that named it, so a manifest can be stale. Two rules keep a stale one from pointing a key at the wrong file:

- An entry whose file is not on disk registers nothing (`vite-register: my_app: skipping js/timer.bundle.4KQ2LZ7M.js: not on disk`).
- A key whose current file (under `sites/assets/`) is newer than the Vite file stays as it is (`vite-register: my_app: timer.bundle.js kept: … is older`). The app's `yarn build` runs after esbuild, so a bundle Vite just built is always the newer one.

When `sites/assets/<app>` is a real directory rather than the usual link to the app's `public/` (after `bench build --hard-link`, or in an image that copied `public/` before the app's build ran), the registration also copies `public/dist/` into it, and every other directory directly under `public/` that a Vite build wrote with a manifest (`public/portal/` for a portal SPA). Files already hard-linked there are left alone.

## Conventions for an app with Vite

- **Name the outputs `<name>.bundle.[hash]`, under `public/dist/`, with a manifest.** The build writes `public/dist/js/<name>.bundle.[hash].js` and `public/dist/css/<name>.bundle.[hash].css`, with `build.manifest: true`.
- **Never name a Vite source `*.bundle.*` under `public/`.** Frappe's esbuild compiles every `public/**/*.bundle.*` it finds, so it would build the same entry a second time with the wrong toolchain. Keep sources as `*.entry.ts` (or under `src/`).
- **Refer to bundles by name.** `hooks.py` lists `"<name>.bundle.js"`, and `www` templates use `{{ bundled_asset('<name>.bundle.js') }}`, never a fixed `/assets/<app>/dist/...` path: production serves `/assets` with a one-year cache, so a path without the hash keeps serving the old file after a release.
- **An SPA that builds into its own directory under `public/`** (`public/portal/`, with its HTML under `www/`) sets `build.manifest: true` too, so a `--hard-link` bench gets its files. The registration copies each top-level directory of `public/` that holds a `.vite/` directory (Vite 5) or a `manifest*.json` (Vite 4).
- **The `build` script ends with `node scripts/vite-register.mjs`.** `frappe-init --sync` appends it when it is missing, and `frappe-nix compat` (rule C8) fails the commit and the `lint` gate without it. The step runs from the app's own directory, so a build that changes directory first runs in a subshell: sync turns `cd frontend && yarn build` into `(cd frontend && yarn build) && node scripts/vite-register.mjs`, and C8 fails a `cd` that the step would run after.

A root `vite.config.ts` for a desk bundle:

```ts
import { defineConfig } from "vite";

export default defineConfig({
	build: {
		outDir: "my_app/public/dist",
		emptyOutDir: false, // esbuild's bundles live here too
		manifest: true,
		rollupOptions: {
			input: { timer: "my_app/public/js/timer/timer.entry.ts" },
			output: {
				entryFileNames: "js/[name].bundle.[hash].js",
				chunkFileNames: "js/[name]-[hash].js",
				assetFileNames: "css/[name].bundle.[hash][extname]",
			},
		},
	},
});
```

and its `package.json` scripts:

```json
{
	"scripts": {
		"build": "vite build && node scripts/vite-register.mjs"
	}
}
```

[`tests/fixtures/spa-app`](../../tests/fixtures/spa-app) is a complete example: a desk bundle from a root Vite config and a portal SPA in `portal/` with no `package.json` of its own.

## `scripts/vite-register.mjs`

The file is rendered when the `vite-register` module is on and the app tracks a `vite.config.*` (or `vite.*.config.*`) at its root or in a nested frontend. It is a whole managed file: edits are drift, and `frappe-init --sync` puts it back. Its body is `lib/js/vite-register.cjs` byte for byte, with an entry point that:

- finds the bench's `sites/`: `$FRAPPE_BENCH_ROOT/sites` when that is set, else `../../sites` from the logical working directory (`apps/<app>` may be a link), else from the physical one, taking the first that holds `apps.txt`;
- finds the app as the one directory beside it with a `hooks.py` and a `modules.txt`;
- prints `vite-register: no bench found; skipped` and exits 0 outside a bench, so `yarn build` in a bare checkout still works;
- turns a registration that fails (an `assets.json` that does not parse, a copy it may not write) into a warning naming the app, and exits 0, as the preload does: the app's `yarn build` never fails for it.

It is in oxfmt's output form under the managed `.oxfmtrc.jsonc`: tabs by default, two spaces when `js.format-tabs = false`, and any `js.format-width` from 80 up. An app with `js.tool = "none"` that formats with its own tool adds `scripts/vite-register.mjs` to that tool's ignore list.

`update-assets.mjs`, the per-app script some apps carried for the same job, is retired: sync deletes it while `vite-register` is on, and drops its steps from the `package.json` scripts.

Turning the module off (`[tool.frappe-nix.vite-register] enable = false`) deletes `scripts/vite-register.mjs` and drops the ` && node scripts/vite-register.mjs` step from the end of `build` (and the subshell sync put a directory-changing build in); the rest of the script is the app's and stays.

## The preload on Nix benches

The preload ([`lib/js/esbuild-preload.js`](../../lib/js/esbuild-preload.js), described in [Asset builds](../development/assets.md)) wraps the `child_process` calls frappe's `esbuild.js` runs each app's build with: `execSync` up to version-16, and `spawn` on `develop`, which builds several apps at once. After a command that matches `yarn build` or `yarn run build` succeeds in `apps/<app>` (any app but `frappe`), whichever way it ran, it registers that app's Vite bundles in `$FRAPPE_BENCH_ROOT/sites` (or the bench `esbuild.js` lives in). A registration that fails, an `assets.json` that does not parse for instance, is a warning in the build log, never a failed build. An app whose build failed is not registered.

To build exactly as a stock bench does, without any of the preload's corrections:

```bash
env -u FRAPPE_NIX_ESBUILD_PRELOAD bench build --app <APP>
```

## Prebuilt assets (pilot)

[pilot](https://github.com/frappe/pilot)'s prebuilt-assets workflow archives `<app>/public/dist`, each declared SPA and the app's `assets.json` entries, built on a stock bench, so the app's own `scripts/vite-register.mjs` is what puts its Vite keys in that archive. An app whose `build` script builds an SPA declares it in `pyproject.toml`, which pilot requires:

```toml
[tool.bench.assets]
build_dir = "./frontend"
out_dir = "./my_app/public/frontend"
index_html_path = "./my_app/www/my_app.html"
```

Declare `[tool.bench.assets]` only with the `pilot-assets` module on, which also renders the workflow that publishes the archive.

## A docs site is not a frontend

frappe-nix builds every top-level directory of an app that has a `package.json` as a nested frontend: it gets a `node_modules`, a fallback lock under `nix/node-locks/`, and the app's `build` runs in the bench. A documentation site in `docs-site/` is built and published by its docs tool's own workflow, never by the bench. While the `docs-site` module is on, the managed `flake.nix` leaves it out:

```nix
frappe-nix.app = {
  # ...
  excludeNodeTargets = [ "docs-site" ];
};
```

`frappe-nix.app.excludeNodeTargets` is a list of top-level directory names, empty by default, so an app that has not opted in, or has the module off, keeps building a `docs-site/` frontend as before. An app can leave out other directories the same way from `nix/local.nix`; it is the same as listing `"<app>/<dir>"` in `nodeNestedFrontendExcludes`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `GET /assets/<app>/dist/js/<name>.bundle.js` is a 404 | No key for the bundle in `sites/assets/assets.json`. | Check that the Vite build writes a manifest (`build.manifest: true`) under `public/dist/` and that the output is named `<name>.bundle.[hash].js`, then rebuild. On a stock bench, check that `build` ends with `node scripts/vite-register.mjs`. |
| The page loads an old bundle after a deploy | A template or hook names a fixed path. | Use `bundled_asset('<name>.bundle.js')` and the bundle name in `hooks.py`. |
| `vite-register: no bench found; skipped` | `yarn build` ran outside a bench. | Nothing to do; under `bench build` it registers. |
| `vite-register: no Frappe app package in <dir>` | The repository has no single directory with a `hooks.py` and a `modules.txt`. | Run the build from the app's repository root. |
| `vite-register: skipping <manifest>: …` | A `manifest*.json` under `public/dist/` that is not JSON. | Remove it, or name it otherwise. |
| `vite-register: <app>: skipping <file>: not on disk`, or `<key> kept: … is older` | A Vite manifest under `public/dist/` left from an earlier build: the bundle it names is gone, or esbuild now builds that name. | Delete the stale manifest (or the whole `public/dist/`) and rebuild. |
| A bundle is built twice, once broken | A Vite source is named `*.bundle.*` under `public/`. | Rename the source to `*.entry.ts`. |
| `frappe-nix compat` reports C8 | `build` no longer ends with the registration. | Run `frappe-init --sync`, or append ` && node scripts/vite-register.mjs`. |
